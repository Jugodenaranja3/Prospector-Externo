from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_AUDIT = Path(".runtime/b11_output_audit/latest.json")
DEFAULT_OUTPUT = Path("output/datax-package/latest")
DEFAULT_ZIP = Path("output/datax-package/datax_package_latest.zip")

REQUIRED_LEGACY_FIELDS = {
    "descripcion",
    "url_descarga",
    "fecha_actualizacion",
    "tipo_archivo",
    "url_origen",
    "metodo_deteccion",
}

EXPECTED_COUNTS = {
    "DATAX_READY": 38,
    "ACQUISITION_JOB_READY": 1,
    "EXTERNAL_BLOCKER": 2,
}
EXPECTED_ACQUISITION = {"transtats"}
EXPECTED_BLOCKERS = {"mhe", "sigma"}
OPERATIONAL_TOTAL = 41


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def validate_legacy_document(document: Any) -> int:
    if not isinstance(document, dict):
        raise ValueError("Legacy JSON no es un objeto")

    stats = document.get("ESTADISTICAS")
    if not isinstance(stats, dict):
        raise ValueError("Legacy JSON no contiene ESTADISTICAS")

    candidates = 0
    incomplete = 0

    for row in walk_dicts(stats):
        keys = set(row)
        if not keys.intersection(REQUIRED_LEGACY_FIELDS):
            continue

        candidates += 1
        if not REQUIRED_LEGACY_FIELDS.issubset(keys):
            incomplete += 1

    if candidates <= 0:
        raise ValueError("Legacy JSON contiene cero registros")

    if incomplete:
        raise ValueError(
            f"Legacy JSON contiene {incomplete} registros incompletos"
        )

    return candidates


def resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    return path


def choose_legacy_file(
    repo_root: Path,
    row: dict[str, Any],
) -> tuple[Path, int]:
    files = row.get("legacy_projection_files")
    if not isinstance(files, list):
        raise ValueError(
            f"{row.get('logical_source_id')}: legacy_projection_files ausente"
        )

    viable: list[tuple[Path, int]] = []

    for item in files:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue

        path = resolve_path(repo_root, raw_path)
        if not path.exists():
            continue

        try:
            document = load_json(path)
            records = validate_legacy_document(document)
        except Exception:
            continue

        viable.append((path, records))

    canonical = [
        item
        for item in viable
        if item[0].name == "legacy_estadisticas.json"
        and item[0].parent.name == "downstream"
    ]

    chosen_pool = canonical or viable

    if len(chosen_pool) != 1:
        raise ValueError(
            f"{row.get('logical_source_id')}: se esperaba exactamente "
            "un legacy canónico válido; "
            f"encontrados={len(chosen_pool)}"
        )

    return chosen_pool[0]


def choose_special_manifest(
    repo_root: Path,
    row: dict[str, Any],
) -> Path:
    raw_path = row.get("special_downstream_manifest")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(
            f"{row.get('logical_source_id')}: manifest especializado ausente"
        )

    path = resolve_path(repo_root, raw_path)
    if not path.exists():
        raise ValueError(
            f"{row.get('logical_source_id')}: no existe {path}"
        )

    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("Manifest especializado inválido")

    if payload.get("decision") != "ACQUISITION_JOB_READY":
        raise ValueError(
            "Manifest TRANSTATS no declara ACQUISITION_JOB_READY"
        )

    return path


def classification_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        value = str(row.get("classification") or "UNKNOWN")
        counts[value] += 1
    return dict(counts)


def validate_audit(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = audit.get("sources")
    if not isinstance(rows, list):
        raise ValueError("Audit B11 no contiene sources:list")

    typed_rows = [
        row for row in rows
        if isinstance(row, dict)
    ]

    if len(typed_rows) != OPERATIONAL_TOTAL:
        raise ValueError(
            f"Audit B11 debe tener 41 fuentes; tiene={len(typed_rows)}"
        )

    counts = classification_counts(typed_rows)

    for key, expected in EXPECTED_COUNTS.items():
        if counts.get(key, 0) != expected:
            raise ValueError(
                f"Conteo {key} inesperado: "
                f"{counts.get(key, 0)} != {expected}"
            )

    unresolved = {
        "RAW_READY_NO_PROJECTION",
        "SUCCESS_EMPTY",
        "EXECUTION_NOT_SUCCESS",
        "EVIDENCE_MISSING",
    }
    bad = [
        row.get("logical_source_id")
        for row in typed_rows
        if row.get("classification") in unresolved
    ]
    if bad:
        raise ValueError(
            "B11 contiene fuentes unresolved: " + ", ".join(map(str, bad))
        )

    acquisition = {
        str(row.get("logical_source_id"))
        for row in typed_rows
        if row.get("classification") == "ACQUISITION_JOB_READY"
    }
    if acquisition != EXPECTED_ACQUISITION:
        raise ValueError(
            f"Acquisition jobs inesperados: {sorted(acquisition)}"
        )

    blockers = {
        str(row.get("logical_source_id"))
        for row in typed_rows
        if row.get("classification") == "EXTERNAL_BLOCKER"
    }
    if blockers != EXPECTED_BLOCKERS:
        raise ValueError(
            f"Blockers externos inesperados: {sorted(blockers)}"
        )

    return sorted(
        typed_rows,
        key=lambda row: str(row.get("logical_source_id") or ""),
    )


def write_readme(
    target: Path,
    *,
    legacy_sources: int,
    legacy_records: int,
) -> None:
    text = f"""# DATAX delivery package

Este directorio es una entrega offline generada desde la evidencia cerrada de
B11. No contiene binarios remotos descargados.

## Contenido

- `manifest.json`: índice, provenance, conteos y hashes.
- `sources/*.json`: {legacy_sources} salidas legacy por fuente lógica.
  Cada archivo conserva como raíz `ESTADISTICAS`.
- `special/transtats/acquisition_job.json`: trabajo de adquisición público de
  TRANSTATS, conservado sin fingir que ya existe un CSV/XLSX materializado.
- `external_blockers.json`: MHE y SIGMA, documentados como bloqueos externos.
- `sources.csv`: índice tabular simple.
- `checksums.sha256`: integridad de todos los artefactos del paquete.

Registros legacy totales: {legacy_records}.

El contrato mínimo de cada registro legacy mantiene:

`descripcion`, `url_descarga`, `fecha_actualizacion`, `tipo_archivo`,
`url_origen`, `metodo_deteccion`.
"""
    (target / "README.md").write_text(
        text,
        encoding="utf-8",
        newline="\n",
    )


def build_package(
    *,
    repo_root: Path,
    audit_path: Path,
    output_dir: Path,
    zip_path: Path,
) -> dict[str, Any]:
    audit = load_json(audit_path)
    rows = validate_audit(audit)

    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".b12-staging-",
        dir=parent,
    ) as temporary:
        staging = Path(temporary)

        sources_dir = staging / "sources"
        special_dir = staging / "special" / "transtats"
        sources_dir.mkdir(parents=True, exist_ok=True)
        special_dir.mkdir(parents=True, exist_ok=True)

        manifest_sources: list[dict[str, Any]] = []
        external_blockers: list[dict[str, Any]] = []
        total_legacy_records = 0

        for row in rows:
            logical_id = str(row["logical_source_id"])
            classification = str(row["classification"])

            if classification == "DATAX_READY":
                source_path, records = choose_legacy_file(
                    repo_root,
                    row,
                )
                destination = sources_dir / f"{logical_id}.json"
                shutil.copyfile(source_path, destination)

                copied = load_json(destination)
                copied_records = validate_legacy_document(copied)
                if copied_records != records:
                    raise RuntimeError(
                        f"{logical_id}: records cambiaron durante copia"
                    )

                digest = sha256_file(destination)
                total_legacy_records += records

                manifest_sources.append(
                    {
                        "logical_source_id": logical_id,
                        "physical_source_id": row.get(
                            "physical_source_id"
                        ),
                        "classification": classification,
                        "workflow": row.get("workflow"),
                        "evidence_origin": row.get("evidence_origin"),
                        "raw_resource_count": row.get(
                            "raw_resource_count"
                        ),
                        "legacy_records": records,
                        "artifact": f"sources/{logical_id}.json",
                        "sha256": digest,
                    }
                )

            elif classification == "ACQUISITION_JOB_READY":
                source_manifest = choose_special_manifest(
                    repo_root,
                    row,
                )
                destination = (
                    special_dir / "acquisition_job.json"
                )
                shutil.copyfile(source_manifest, destination)

                manifest_sources.append(
                    {
                        "logical_source_id": logical_id,
                        "physical_source_id": row.get(
                            "physical_source_id"
                        ),
                        "classification": classification,
                        "workflow": row.get("workflow"),
                        "evidence_origin": row.get("evidence_origin"),
                        "raw_resource_count": row.get(
                            "raw_resource_count"
                        ),
                        "legacy_records": 0,
                        "artifact": (
                            "special/transtats/acquisition_job.json"
                        ),
                        "sha256": sha256_file(destination),
                    }
                )

            elif classification == "EXTERNAL_BLOCKER":
                blocker = {
                    "logical_source_id": logical_id,
                    "classification": classification,
                    "workflow": row.get("workflow"),
                    "execution_status": row.get("execution_status"),
                    "failure_code": row.get("failure_code"),
                    "external_blocker_classification": row.get(
                        "external_blocker_classification"
                    ),
                    "evidence_origin": row.get("evidence_origin"),
                }
                external_blockers.append(blocker)
                manifest_sources.append(
                    {
                        "logical_source_id": logical_id,
                        "physical_source_id": row.get(
                            "physical_source_id"
                        ),
                        "classification": classification,
                        "workflow": row.get("workflow"),
                        "evidence_origin": row.get("evidence_origin"),
                        "raw_resource_count": row.get(
                            "raw_resource_count"
                        ),
                        "legacy_records": 0,
                        "artifact": None,
                        "sha256": None,
                    }
                )

            else:
                raise RuntimeError(
                    f"Clasificación no soportada en B12: "
                    f"{logical_id}={classification}"
                )

        if total_legacy_records != int(
            audit["summary"]["total_legacy_projection_records"]
        ):
            raise RuntimeError(
                "La suma de registros del paquete no coincide con B11: "
                f"{total_legacy_records} != "
                f"{audit['summary']['total_legacy_projection_records']}"
            )

        dump_json(
            staging / "external_blockers.json",
            {
                "count": len(external_blockers),
                "sources": external_blockers,
            },
        )

        hash_groups: dict[str, list[str]] = defaultdict(list)
        for item in manifest_sources:
            digest = item.get("sha256")
            if isinstance(digest, str):
                hash_groups[digest].append(
                    str(item["logical_source_id"])
                )

        shared_content_groups = [
            {
                "sha256": digest,
                "logical_source_ids": sorted(ids),
            }
            for digest, ids in sorted(hash_groups.items())
            if len(ids) > 1
        ]

        package_manifest = {
            "schema_version": "1.0.0",
            "package_type": "datax_prospector_delivery",
            "source_audit_generated_at": audit.get("generated_at"),
            "operational_sources": OPERATIONAL_TOTAL,
            "counts": {
                "datax_ready": 38,
                "acquisition_job_ready": 1,
                "external_blocker": 2,
                "legacy_records": total_legacy_records,
            },
            "contract": {
                "legacy_root": "ESTADISTICAS",
                "required_fields": sorted(REQUIRED_LEGACY_FIELDS),
            },
            "sources": manifest_sources,
            "shared_content_groups": shared_content_groups,
        }
        dump_json(staging / "manifest.json", package_manifest)

        with (staging / "sources.csv").open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "logical_source_id",
                    "physical_source_id",
                    "classification",
                    "workflow",
                    "evidence_origin",
                    "raw_resource_count",
                    "legacy_records",
                    "artifact",
                    "sha256",
                ],
            )
            writer.writeheader()
            for item in manifest_sources:
                writer.writerow(
                    {
                        key: item.get(key)
                        for key in writer.fieldnames
                    }
                )

        write_readme(
            staging,
            legacy_sources=38,
            legacy_records=total_legacy_records,
        )

        checksum_targets = sorted(
            path
            for path in staging.rglob("*")
            if path.is_file()
            and path.name != "checksums.sha256"
        )
        checksum_lines = []
        for path in checksum_targets:
            relative = path.relative_to(staging).as_posix()
            checksum_lines.append(
                f"{sha256_file(path)}  {relative}"
            )
        (staging / "checksums.sha256").write_text(
            "\n".join(checksum_lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        if output_dir.exists():
            shutil.rmtree(output_dir)
        shutil.copytree(staging, output_dir)

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    arcname=path.relative_to(output_dir).as_posix(),
                )

    return {
        "output_dir": str(output_dir),
        "zip_path": str(zip_path),
        "legacy_sources": 38,
        "acquisition_sources": 1,
        "external_blockers": 2,
        "legacy_records": total_legacy_records,
        "package_files": sum(
            1 for path in output_dir.rglob("*") if path.is_file()
        ),
        "zip_sha256": sha256_file(zip_path),
    }


def verify_package(output_dir: Path) -> dict[str, Any]:
    manifest = load_json(output_dir / "manifest.json")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != OPERATIONAL_TOTAL:
        raise RuntimeError("Manifest no contiene 41 fuentes")

    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        raise RuntimeError("Manifest sin counts")

    expected = {
        "datax_ready": 38,
        "acquisition_job_ready": 1,
        "external_blocker": 2,
        "legacy_records": 5407,
    }
    if counts != expected:
        raise RuntimeError(
            f"Counts del paquete inesperados: {counts}"
        )

    source_files = list((output_dir / "sources").glob("*.json"))
    if len(source_files) != 38:
        raise RuntimeError(
            f"Se esperaban 38 legacy JSON; encontrados={len(source_files)}"
        )

    total = 0
    for path in source_files:
        total += validate_legacy_document(load_json(path))
    if total != 5407:
        raise RuntimeError(
            f"Registros legacy empaquetados inesperados: {total}"
        )

    special = load_json(
        output_dir
        / "special"
        / "transtats"
        / "acquisition_job.json"
    )
    if special.get("decision") != "ACQUISITION_JOB_READY":
        raise RuntimeError("TRANSTATS special manifest inválido")

    blockers = load_json(output_dir / "external_blockers.json")
    blocker_ids = {
        row.get("logical_source_id")
        for row in blockers.get("sources", [])
        if isinstance(row, dict)
    }
    if blocker_ids != EXPECTED_BLOCKERS:
        raise RuntimeError(
            f"Blockers del paquete inesperados: {sorted(blocker_ids)}"
        )

    return {
        "sources": len(sources),
        "legacy_files": len(source_files),
        "legacy_records": total,
        "special_acquisition": 1,
        "external_blockers": len(blocker_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Construye la entrega DATAX consolidada desde B11."
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
        "--verify-only",
        action="store_true",
    )
    args = parser.parse_args()

    repo_root = Path(".").resolve()
    audit_path = (
        args.audit if args.audit.is_absolute()
        else repo_root / args.audit
    )
    output_dir = (
        args.output_dir if args.output_dir.is_absolute()
        else repo_root / args.output_dir
    )
    zip_path = (
        args.zip if args.zip.is_absolute()
        else repo_root / args.zip
    )

    if args.verify_only:
        result = verify_package(output_dir)
        print("=" * 78)
        print("B12 — DATAX PACKAGE VERIFICATION")
        print("=" * 78)
        for key, value in result.items():
            print(f"{key:<22} {value}")
        return 0

    result = build_package(
        repo_root=repo_root,
        audit_path=audit_path,
        output_dir=output_dir,
        zip_path=zip_path,
    )
    verification = verify_package(output_dir)

    print("=" * 78)
    print("B12 — DATAX DELIVERY PACKAGE")
    print("=" * 78)
    print("DATAX legacy sources:     38")
    print("Acquisition job sources:   1")
    print("External blockers:         2")
    print("Operational accounted:    41/41")
    print(f"Legacy records:          {verification['legacy_records']}")
    print(f"Package files:           {result['package_files']}")
    print(f"Directory:               {output_dir}")
    print(f"ZIP:                     {zip_path}")
    print(f"ZIP SHA256:              {result['zip_sha256']}")
    print("Network:                 NO")
    print("Crawl:                   NO")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
