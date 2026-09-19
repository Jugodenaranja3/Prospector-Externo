from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.b12_datax_package.main import (
    DEFAULT_AUDIT,
    DEFAULT_OUTPUT,
    DEFAULT_ZIP,
    build_package,
    load_json,
    verify_package,
)


DEFAULT_REPORT = Path(".runtime/b12_enterprise_audit/latest.json")


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(output_dir: Path) -> dict[str, Any]:
    manifest = load_json(output_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json inválido")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != 41:
        raise ValueError("manifest.json debe indexar 41 fuentes")
    return manifest


def exact_payload_groups(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in manifest["sources"]:
        if not isinstance(row, dict):
            continue

        digest = row.get("sha256")
        if not isinstance(digest, str) or not digest:
            continue

        groups[digest].append(row)

    result: list[dict[str, Any]] = []

    for digest, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue

        logical_ids = sorted(
            str(row.get("logical_source_id"))
            for row in rows
        )
        physical_ids = sorted(
            {
                str(row.get("physical_source_id"))
                for row in rows
            }
        )

        result.append(
            {
                "sha256": digest,
                "logical_source_ids": logical_ids,
                "physical_source_ids": physical_ids,
                "shared_physical_source": len(physical_ids) == 1,
            }
        )

    return result


def build_consumer_contract(
    manifest: dict[str, Any],
    duplicate_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "package_entrypoint": "manifest.json",
        "selection_key": "logical_source_id",
        "artifact_field": "artifact",
        "consumer_rules": [
            (
                "Open manifest.json first and resolve the requested "
                "logical_source_id."
            ),
            (
                "For DATAX_READY, consume the referenced artifact under "
                "sources/<logical_source_id>.json."
            ),
            (
                "The selected legacy artifact has ESTADISTICAS as its root "
                "and preserves the historical six-field record contract."
            ),
            (
                "For ACQUISITION_JOB_READY, consume the referenced special "
                "manifest; do not treat it as a materialized dataset."
            ),
            (
                "For EXTERNAL_BLOCKER, no data artifact exists; inspect "
                "external_blockers.json."
            ),
            (
                "Never infer a logical source from the physical source ID. "
                "Multiple logical sources may share one physical config."
            ),
        ],
        "legacy": {
            "root": "ESTADISTICAS",
            "source_pattern": "sources/{logical_source_id}.json",
            "count": manifest["counts"]["datax_ready"],
        },
        "special": {
            "transtats": "special/transtats/acquisition_job.json",
        },
        "external_blockers": "external_blockers.json",
        "exact_duplicate_payload_groups": duplicate_groups,
    }


def write_consumer_contract(
    output_dir: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    duplicates = exact_payload_groups(manifest)
    contract = build_consumer_contract(
        manifest,
        duplicates,
    )
    dump_json(
        output_dir / "consumer_contract.json",
        contract,
    )
    return duplicates


def rewrite_checksums(output_dir: Path) -> None:
    targets = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file()
        and path.name != "checksums.sha256"
    )

    lines = [
        f"{sha256_file(path)}  "
        f"{path.relative_to(output_dir).as_posix()}"
        for path in targets
    ]

    (output_dir / "checksums.sha256").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def rebuild_zip(
    output_dir: Path,
    zip_path: Path,
) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file():
                continue

            archive.write(
                path,
                arcname=path.relative_to(output_dir).as_posix(),
            )


def parse_checksums(
    output_dir: Path,
) -> dict[str, str]:
    checksum_path = output_dir / "checksums.sha256"
    if not checksum_path.exists():
        raise ValueError("checksums.sha256 ausente")

    result: dict[str, str] = {}

    for raw_line in checksum_path.read_text(
        encoding="utf-8"
    ).splitlines():
        if not raw_line.strip():
            continue

        try:
            digest, relative = raw_line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(
                f"Línea checksum inválida: {raw_line!r}"
            ) from exc

        if relative in result:
            raise ValueError(
                f"Checksum duplicado para {relative}"
            )

        result[relative] = digest

    return result


def validate_checksums(
    output_dir: Path,
) -> dict[str, Any]:
    expected = parse_checksums(output_dir)

    actual_files = {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
        and path.name != "checksums.sha256"
    }

    expected_files = set(expected)

    if expected_files != actual_files:
        raise ValueError(
            "Cobertura de checksums inconsistente: "
            f"missing={sorted(actual_files - expected_files)}, "
            f"extra={sorted(expected_files - actual_files)}"
        )

    mismatches: list[str] = []

    for relative, expected_digest in sorted(expected.items()):
        actual_digest = sha256_file(
            output_dir / Path(relative)
        )
        if actual_digest != expected_digest:
            mismatches.append(relative)

    if mismatches:
        raise ValueError(
            "Checksums inválidos: " + ", ".join(mismatches)
        )

    return {
        "covered_files": len(expected_files),
        "mismatches": 0,
    }


def validate_zip_parity(
    output_dir: Path,
    zip_path: Path,
) -> dict[str, Any]:
    directory_files = {
        path.relative_to(output_dir).as_posix(): path
        for path in output_dir.rglob("*")
        if path.is_file()
    }

    with zipfile.ZipFile(zip_path, "r") as archive:
        infos = archive.infolist()

        names = [info.filename for info in infos]

        if len(names) != len(set(names)):
            raise ValueError("ZIP contiene nombres duplicados")

        unsafe = [
            name
            for name in names
            if name.startswith("/")
            or ".." in Path(name).parts
        ]
        if unsafe:
            raise ValueError(
                f"ZIP contiene paths inseguros: {unsafe}"
            )

        if set(names) != set(directory_files):
            raise ValueError(
                "ZIP no coincide con el directorio: "
                f"missing={sorted(set(directory_files) - set(names))}, "
                f"extra={sorted(set(names) - set(directory_files))}"
            )

        content_mismatches: list[str] = []

        for name, disk_path in sorted(directory_files.items()):
            disk_digest = sha256_file(disk_path)

            zip_digest = hashlib.sha256(
                archive.read(name)
            ).hexdigest()

            if zip_digest != disk_digest:
                content_mismatches.append(name)

    if content_mismatches:
        raise ValueError(
            "ZIP contiene bytes distintos: "
            + ", ".join(content_mismatches)
        )

    return {
        "zip_entries": len(directory_files),
        "content_mismatches": 0,
    }


def source_index_consistency(
    output_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    csv_path = output_dir / "sources.csv"

    with csv_path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))

    manifest_ids = {
        str(row["logical_source_id"])
        for row in manifest["sources"]
    }
    csv_ids = {
        str(row["logical_source_id"])
        for row in rows
    }

    if csv_ids != manifest_ids:
        raise ValueError(
            "sources.csv y manifest.json no contienen "
            "las mismas fuentes"
        )

    if len(rows) != 41:
        raise ValueError(
            f"sources.csv debe tener 41 filas; tiene={len(rows)}"
        )

    return {
        "manifest_sources": len(manifest_ids),
        "csv_rows": len(rows),
    }


def summarize_duplicate_groups(
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    shared_physical = [
        group
        for group in groups
        if group["shared_physical_source"]
    ]
    cross_physical = [
        group
        for group in groups
        if not group["shared_physical_source"]
    ]

    return {
        "exact_duplicate_groups": len(groups),
        "shared_physical_groups": len(shared_physical),
        "cross_physical_groups": len(cross_physical),
        "groups": groups,
    }


def audit_enterprise_package(
    *,
    output_dir: Path,
    zip_path: Path,
) -> dict[str, Any]:
    base_verification = verify_package(output_dir)
    manifest = read_manifest(output_dir)

    consumer_contract_path = (
        output_dir / "consumer_contract.json"
    )
    if not consumer_contract_path.exists():
        raise ValueError("consumer_contract.json ausente")

    consumer_contract = load_json(
        consumer_contract_path
    )
    if (
        consumer_contract.get("package_entrypoint")
        != "manifest.json"
    ):
        raise ValueError(
            "consumer_contract no declara manifest.json "
            "como entrypoint"
        )

    if (
        consumer_contract.get("selection_key")
        != "logical_source_id"
    ):
        raise ValueError(
            "consumer_contract debe seleccionar por logical_source_id"
        )

    duplicate_summary = summarize_duplicate_groups(
        exact_payload_groups(manifest)
    )

    checksum_summary = validate_checksums(output_dir)
    zip_summary = validate_zip_parity(
        output_dir,
        zip_path,
    )
    index_summary = source_index_consistency(
        output_dir,
        manifest,
    )

    return {
        "status": "ENTERPRISE_PACKAGE_READY",
        "consumer_entrypoint": "manifest.json",
        "legacy_source_rule": (
            "Resolve logical_source_id in manifest.json and consume "
            "the artifact field."
        ),
        "base_verification": base_verification,
        "checksums": checksum_summary,
        "zip": zip_summary,
        "index": index_summary,
        "duplicates": duplicate_summary,
        "zip_sha256": sha256_file(zip_path),
        "package_bytes": sum(
            path.stat().st_size
            for path in output_dir.rglob("*")
            if path.is_file()
        ),
        "zip_bytes": zip_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B12B: hardening de entrega empresarial DATAX."
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=DEFAULT_AUDIT,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=DEFAULT_ZIP,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
    )
    args = parser.parse_args()

    repo_root = Path(".").resolve()

    audit_path = (
        args.audit
        if args.audit.is_absolute()
        else repo_root / args.audit
    )
    output_dir = (
        args.output_dir
        if args.output_dir.is_absolute()
        else repo_root / args.output_dir
    )
    zip_path = (
        args.zip
        if args.zip.is_absolute()
        else repo_root / args.zip
    )
    report_path = (
        args.report
        if args.report.is_absolute()
        else repo_root / args.report
    )

    # Rebuild from the authoritative B11 audit so the enterprise package is
    # reproducible rather than relying on a hand-edited prior directory.
    build_package(
        repo_root=repo_root,
        audit_path=audit_path,
        output_dir=output_dir,
        zip_path=zip_path,
    )

    manifest = read_manifest(output_dir)
    duplicate_groups = write_consumer_contract(
        output_dir,
        manifest,
    )

    rewrite_checksums(output_dir)
    rebuild_zip(output_dir, zip_path)

    report = audit_enterprise_package(
        output_dir=output_dir,
        zip_path=zip_path,
    )
    report["generated_at"] = (
        datetime.now(timezone.utc).isoformat()
    )
    report["duplicate_groups_written"] = len(
        duplicate_groups
    )

    dump_json(report_path, report)

    print("=" * 78)
    print("B12B — ENTERPRISE DATAX DELIVERY AUDIT")
    print("=" * 78)
    print(f"Status:                  {report['status']}")
    print(
        "Consumer entrypoint:     "
        f"{report['consumer_entrypoint']}"
    )
    print(
        "Operational sources:     "
        f"{report['index']['manifest_sources']}/41"
    )
    print(
        "Legacy files:            "
        f"{report['base_verification']['legacy_files']}"
    )
    print(
        "Legacy records:          "
        f"{report['base_verification']['legacy_records']}"
    )
    print(
        "Acquisition manifests:   "
        f"{report['base_verification']['special_acquisition']}"
    )
    print(
        "External blockers:       "
        f"{report['base_verification']['external_blockers']}"
    )
    print(
        "Checksum-covered files:  "
        f"{report['checksums']['covered_files']}"
    )
    print(
        "ZIP entries:             "
        f"{report['zip']['zip_entries']}"
    )
    print(
        "Exact duplicate groups:  "
        f"{report['duplicates']['exact_duplicate_groups']}"
    )
    print(
        "Shared-physical groups:  "
        f"{report['duplicates']['shared_physical_groups']}"
    )
    print(
        "Cross-physical groups:   "
        f"{report['duplicates']['cross_physical_groups']}"
    )
    print(
        "Package bytes:           "
        f"{report['package_bytes']}"
    )
    print(
        "ZIP bytes:               "
        f"{report['zip_bytes']}"
    )
    print(
        "ZIP SHA256:              "
        f"{report['zip_sha256']}"
    )
    print(f"Directory:               {output_dir}")
    print(f"ZIP:                     {zip_path}")
    print(f"Audit report:            {report_path}")
    print("Network:                 NO")
    print("Crawl:                   NO")

    if report["duplicates"]["cross_physical_groups"]:
        print()
        print("REVIEW — exact duplicates across different physical sources:")
        for group in report["duplicates"]["groups"]:
            if group["shared_physical_source"]:
                continue
            print(
                "  - "
                + ", ".join(group["logical_source_ids"])
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
