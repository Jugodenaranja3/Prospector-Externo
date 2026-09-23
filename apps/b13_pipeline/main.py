from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(".")
PLAN_PATH = Path("config/source_operational_plan.yaml")
SOURCES_CONFIG_PATH = Path("config/sources.yaml")
EXECUTION_MAP_PATH = Path("config/source_execution_map.yaml")
KNOWN_EXTERNAL_BLOCKERS = {"mhe", "sigma"}
EXPECTED_INVENTORY = 52
EXPECTED_OPERATIONAL = 41
EXPECTED_STATUS_ONLY = 11

LEGACY_FIELDS = {
    "descripcion",
    "url_descarga",
    "fecha_actualizacion",
    "tipo_archivo",
    "url_origen",
    "metodo_deteccion",
}


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


def operational_roster(repo_root: Path) -> list[str]:
    """Deriva el roster desde configuración durable/versionada, no runtime B11."""
    import yaml

    plan_path = repo_root / PLAN_PATH
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    rows = plan.get("sources") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Plan operacional no contiene sources:list")

    result = sorted(
        str(row["source_id"])
        for row in rows
        if isinstance(row, dict)
        and row.get("next_phase") == "OPERATIONAL_CONFIG"
        and isinstance(row.get("source_id"), str)
    )

    if len(result) != EXPECTED_OPERATIONAL:
        raise ValueError(
            f"Se esperaban {EXPECTED_OPERATIONAL} fuentes operacionales; "
            f"encontradas={len(result)}"
        )

    if len(set(result)) != len(result):
        raise ValueError("Plan operacional contiene source_id duplicados")

    return result


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def legacy_records(document: Any) -> int:
    if not isinstance(document, dict):
        return 0

    stats = document.get("ESTADISTICAS")
    if not isinstance(stats, dict):
        return 0

    count = 0
    invalid = 0

    for row in walk_dicts(stats):
        keys = set(row)
        if not keys.intersection(LEGACY_FIELDS):
            continue

        count += 1
        if not LEGACY_FIELDS.issubset(keys):
            invalid += 1

    if invalid:
        raise ValueError(
            f"Legacy JSON contiene {invalid} registros incompletos"
        )

    return count


def latest_json(directory: Path) -> Path | None:
    if not directory.exists():
        return None

    candidates = [
        path for path in directory.glob("*.json")
        if path.is_file()
    ]
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda path: (path.name, path.stat().st_mtime_ns),
    )


def latest_report(evidence_root: Path) -> tuple[Path | None, dict[str, Any]]:
    path = latest_json(evidence_root / "reports")
    if path is None:
        return None, {}

    raw = load_json(path)
    if not isinstance(raw, dict):
        return path, {}

    rows = raw.get("source_results")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                return path, row

    return path, raw


def latest_snapshot(evidence_root: Path) -> tuple[Path | None, dict[str, Any]]:
    path = latest_json(
        evidence_root / "state" / "snapshots"
    )
    if path is None:
        return None, {}

    raw = load_json(path)
    return path, raw if isinstance(raw, dict) else {}


def physical_source_id(evidence_root: Path) -> str | None:
    sources_path = evidence_root / "state" / "sources.json"
    if sources_path.exists():
        raw = load_json(sources_path)
        if isinstance(raw, dict) and len(raw) == 1:
            key = next(iter(raw))
            if isinstance(key, str) and key:
                return key

    _, snapshot = latest_snapshot(evidence_root)
    value = snapshot.get("source_id")
    if isinstance(value, str) and value:
        return value

    return None


def grouping_contract(
    repo_root: Path,
    logical_id: str,
    physical_id: str,
) -> Path | None:
    candidates = [
        repo_root / "config" / "grouping" / f"{logical_id}.yaml",
        repo_root / "config" / "grouping" / f"{logical_id}.yml",
    ]

    if physical_id != logical_id:
        candidates.extend(
            [
                repo_root / "config" / "grouping" / f"{physical_id}.yaml",
                repo_root / "config" / "grouping" / f"{physical_id}.yml",
            ]
        )

    for path in candidates:
        if path.exists():
            return path

    return None


def canonical_legacy_file(
    evidence_root: Path,
    physical_id: str,
) -> tuple[Path | None, int]:
    preferred = (
        evidence_root
        / physical_id
        / "downstream"
        / "legacy_estadisticas.json"
    )

    candidates: list[Path] = []
    if preferred.exists():
        candidates.append(preferred)

    for path in evidence_root.rglob("legacy_estadisticas.json"):
        if path not in candidates:
            candidates.append(path)

    viable: list[tuple[Path, int]] = []

    for path in candidates:
        try:
            records = legacy_records(load_json(path))
        except Exception:
            continue

        if records > 0:
            viable.append((path, records))

    if preferred.exists():
        for path, records in viable:
            if path == preferred:
                return path, records

    if len(viable) == 1:
        return viable[0]

    return None, 0


def run_command(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        parts = [
            "$ " + subprocess.list2cmdline(command),
            "",
        ]
        if cp.stdout:
            parts.append(cp.stdout.rstrip())
        if cp.stderr:
            parts.append(cp.stderr.rstrip())
        parts.append("")
        parts.append(f"RETURN_CODE={cp.returncode}")
        log_path.write_text(
            "\n".join(parts) + "\n",
            encoding="utf-8",
        )

    return cp


def build_transtats_manifest(
    evidence_root: Path,
    physical_id: str,
) -> tuple[Path | None, str | None]:
    snapshot_path, snapshot = latest_snapshot(evidence_root)
    resources = snapshot.get("resources")

    if not isinstance(resources, list):
        return None, "SNAPSHOT_RESOURCES_MISSING"

    matches = [
        row
        for row in resources
        if isinstance(row, dict)
        and row.get("discovery_method")
        == "custom_form_acquisition_job"
    ]

    if len(matches) != 1:
        return None, (
            "EXPECTED_ONE_ACQUISITION_JOB_"
            f"FOUND_{len(matches)}"
        )

    resource = matches[0]
    url = resource.get("url")

    if not isinstance(url, str) or "DL_SelectFields" not in url:
        return None, "TRANSTATS_ACQUISITION_URL_UNEXPECTED"

    output = (
        evidence_root
        / physical_id
        / "downstream"
        / "acquisition_job.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "logical_source_id": "transtats",
        "source_id": physical_id,
        "decision": "ACQUISITION_JOB_READY",
        "kind": "download_form_acquisition_job",
        "title": (
            resource.get("title")
            or "US Bureau of Transportation Statistics"
        ),
        "url": url,
        "content_type": resource.get("content_type"),
        "discovery_method": resource.get("discovery_method"),
        "execution_policy": {
            "allowed_methods": ["GET", "HEAD"],
            "submission_policy": "metadata_only_no_post",
        },
        "legacy_projection": {
            "status": "NOT_APPLICABLE_YET",
            "reason": (
                "The public evidence is an acquisition job/form, not a "
                "materialized downloadable file."
            ),
        },
        "source_snapshot": (
            str(snapshot_path) if snapshot_path else None
        ),
    }

    dump_json(output, payload)
    return output, None


def project_success(
    *,
    repo_root: Path,
    logical_id: str,
    evidence_root: Path,
    physical_id: str,
    logs_dir: Path,
) -> dict[str, Any]:
    if logical_id == "transtats":
        manifest, error = build_transtats_manifest(
            evidence_root,
            physical_id,
        )
        if error:
            return {
                "classification": "SPECIALIZATION_FAILED",
                "legacy_records": 0,
                "artifact": None,
                "projection_error": error,
            }

        return {
            "classification": "ACQUISITION_JOB_READY",
            "legacy_records": 0,
            "artifact": str(manifest),
            "projection_error": None,
        }

    contract = grouping_contract(
        repo_root,
        logical_id,
        physical_id,
    )

    command = [
        sys.executable,
        "-m",
        "apps.datax_projection.main",
        "--output-dir",
        str(evidence_root),
        "--source",
        physical_id,
    ]

    if contract is not None:
        command.extend(
            [
                "--grouping-contract",
                str(contract),
            ]
        )

    cp = run_command(
        command,
        cwd=repo_root,
        log_path=logs_dir / f"{logical_id}_projection.log",
    )

    if cp.returncode != 0:
        return {
            "classification": "PROJECTION_FAILED",
            "legacy_records": 0,
            "artifact": None,
            "projection_error": (
                f"datax_projection rc={cp.returncode}"
            ),
        }

    legacy_path, records = canonical_legacy_file(
        evidence_root,
        physical_id,
    )

    if legacy_path is None or records <= 0:
        return {
            "classification": "PROJECTION_EMPTY",
            "legacy_records": 0,
            "artifact": None,
            "projection_error": None,
        }

    return {
        "classification": "DATAX_READY",
        "legacy_records": records,
        "artifact": str(legacy_path),
        "projection_error": None,
    }


def inspect_and_project(
    *,
    repo_root: Path,
    run_id: str,
    roster: list[str],
) -> list[dict[str, Any]]:
    output_root = (
        repo_root / "output" / "checkpointed" / run_id
    )
    logs_dir = (
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / run_id
        / "logs"
    )

    rows: list[dict[str, Any]] = []

    for logical_id in roster:
        evidence_root = output_root / logical_id

        report_path, source_result = latest_report(
            evidence_root
        )

        if report_path is None:
            rows.append(
                {
                    "logical_source_id": logical_id,
                    "physical_source_id": None,
                    "execution_status": None,
                    "failure_code": None,
                    "raw_resource_count": None,
                    "classification": "PENDING",
                    "legacy_records": 0,
                    "artifact": None,
                    "evidence_root": str(evidence_root),
                }
            )
            continue

        execution_status = source_result.get(
            "execution_status"
        )
        failure_code = source_result.get("failure_code")

        snapshot_path, snapshot = latest_snapshot(
            evidence_root
        )
        raw_count = snapshot.get("total_resources")
        if not isinstance(raw_count, int):
            resources = snapshot.get("resources")
            raw_count = (
                len(resources)
                if isinstance(resources, list)
                else None
            )

        physical_id = physical_source_id(
            evidence_root
        )

        base = {
            "logical_source_id": logical_id,
            "physical_source_id": physical_id,
            "execution_status": execution_status,
            "failure_code": failure_code,
            "raw_resource_count": raw_count,
            "evidence_root": str(evidence_root),
            "report_path": str(report_path),
            "snapshot_path": (
                str(snapshot_path)
                if snapshot_path is not None
                else None
            ),
        }

        if execution_status != "SUCCESS":
            if (
                logical_id in KNOWN_EXTERNAL_BLOCKERS
                and failure_code == "ROBOTS_UNREACHABLE"
            ):
                rows.append(
                    {
                        **base,
                        "classification": "EXTERNAL_BLOCKER",
                        "legacy_records": 0,
                        "artifact": None,
                    }
                )
            else:
                rows.append(
                    {
                        **base,
                        "classification": "EXECUTION_FAILED",
                        "legacy_records": 0,
                        "artifact": None,
                    }
                )
            continue

        if not physical_id:
            rows.append(
                {
                    **base,
                    "classification": "PHYSICAL_SOURCE_UNRESOLVED",
                    "legacy_records": 0,
                    "artifact": None,
                }
            )
            continue

        projection = project_success(
            repo_root=repo_root,
            logical_id=logical_id,
            evidence_root=evidence_root,
            physical_id=physical_id,
            logs_dir=logs_dir,
        )

        rows.append(
            {
                **base,
                **projection,
            }
        )

    return rows


def package_ready(rows: list[dict[str, Any]]) -> bool:
    allowed = {
        "DATAX_READY",
        "ACQUISITION_JOB_READY",
        "EXTERNAL_BLOCKER",
    }
    return (
        len(rows) == EXPECTED_OPERATIONAL
        and all(
            row.get("classification") in allowed
            for row in rows
        )
    )


def safe_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def write_checksums(package_dir: Path) -> None:
    targets = sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file()
        and path.name != "checksums.sha256"
    )

    lines = [
        f"{sha256_file(path)}  "
        f"{path.relative_to(package_dir).as_posix()}"
        for path in targets
    ]

    (package_dir / "checksums.sha256").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_zip(
    package_dir: Path,
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
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    arcname=path.relative_to(
                        package_dir
                    ).as_posix(),
                )


def exact_duplicate_groups(
    manifest_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in manifest_rows:
        digest = row.get("sha256")
        if isinstance(digest, str) and digest:
            groups[digest].append(row)

    result = []

    for digest, group in sorted(groups.items()):
        if len(group) < 2:
            continue

        physical = sorted(
            {
                str(row.get("physical_source_id"))
                for row in group
            }
        )

        result.append(
            {
                "sha256": digest,
                "logical_source_ids": sorted(
                    str(row["logical_source_id"])
                    for row in group
                ),
                "physical_source_ids": physical,
                "shared_physical_source": (
                    len(physical) == 1
                ),
            }
        )

    return result


def build_package(
    *,
    repo_root: Path,
    run_id: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    package_root = (
        repo_root
        / "output"
        / "b13-pipeline"
        / run_id
        / "datax-package"
    )
    package_dir = package_root / "latest"
    zip_path = package_root / "datax_package.zip"

    if package_dir.exists():
        shutil.rmtree(package_dir)

    sources_dir = package_dir / "sources"
    special_dir = (
        package_dir / "special" / "transtats"
    )
    sources_dir.mkdir(parents=True, exist_ok=True)
    special_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    legacy_total = 0

    for row in sorted(
        rows,
        key=lambda item: str(item["logical_source_id"]),
    ):
        logical_id = str(row["logical_source_id"])
        classification = str(row["classification"])

        manifest_row = {
            "logical_source_id": logical_id,
            "physical_source_id": row.get(
                "physical_source_id"
            ),
            "classification": classification,
            "execution_status": row.get(
                "execution_status"
            ),
            "failure_code": row.get("failure_code"),
            "raw_resource_count": row.get(
                "raw_resource_count"
            ),
            "legacy_records": int(
                row.get("legacy_records") or 0
            ),
            "artifact": None,
            "sha256": None,
        }

        if classification == "DATAX_READY":
            src = Path(str(row["artifact"]))
            destination = (
                sources_dir / f"{logical_id}.json"
            )
            safe_copy(src, destination)

            records = legacy_records(
                load_json(destination)
            )
            if records <= 0:
                raise RuntimeError(
                    f"{logical_id}: legacy vacío durante packaging"
                )

            legacy_total += records
            manifest_row["legacy_records"] = records
            manifest_row["artifact"] = (
                f"sources/{logical_id}.json"
            )
            manifest_row["sha256"] = sha256_file(
                destination
            )

        elif classification == "ACQUISITION_JOB_READY":
            src = Path(str(row["artifact"]))
            destination = (
                special_dir / "acquisition_job.json"
            )
            safe_copy(src, destination)
            manifest_row["artifact"] = (
                "special/transtats/acquisition_job.json"
            )
            manifest_row["sha256"] = sha256_file(
                destination
            )

        elif classification == "EXTERNAL_BLOCKER":
            blockers.append(
                {
                    "logical_source_id": logical_id,
                    "physical_source_id": row.get(
                        "physical_source_id"
                    ),
                    "failure_code": row.get(
                        "failure_code"
                    ),
                    "execution_status": row.get(
                        "execution_status"
                    ),
                }
            )

        else:
            raise RuntimeError(
                f"No se puede empaquetar {logical_id}: "
                f"{classification}"
            )

        manifest_rows.append(manifest_row)

    duplicate_groups = exact_duplicate_groups(
        manifest_rows
    )
    cross_physical = [
        group
        for group in duplicate_groups
        if not group["shared_physical_source"]
    ]

    manifest = {
        "schema_version": "1.0.0",
        "package_type": "b13_reproducible_datax_delivery",
        "pipeline_run_id": run_id,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "inventory": {
            "total": EXPECTED_INVENTORY,
            "operational": EXPECTED_OPERATIONAL,
            "status_only": EXPECTED_STATUS_ONLY,
        },
        "counts": dict(
            Counter(
                row["classification"]
                for row in rows
            )
        ),
        "legacy_records": legacy_total,
        "sources": manifest_rows,
        "duplicate_groups": duplicate_groups,
        "cross_physical_duplicate_groups": (
            cross_physical
        ),
        "consumer": {
            "entrypoint": "manifest.json",
            "selection_key": "logical_source_id",
            "legacy_pattern": (
                "sources/{logical_source_id}.json"
            ),
        },
    }
    dump_json(package_dir / "manifest.json", manifest)

    dump_json(
        package_dir / "external_blockers.json",
        {
            "count": len(blockers),
            "sources": blockers,
        },
    )

    with (package_dir / "sources.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        fields = [
            "logical_source_id",
            "physical_source_id",
            "classification",
            "execution_status",
            "failure_code",
            "raw_resource_count",
            "legacy_records",
            "artifact",
            "sha256",
        ]
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(
                {
                    key: row.get(key)
                    for key in fields
                }
            )

    readme = f"""# B13 reproducible DATAX package

Pipeline run: `{run_id}`

- 41 operational logical sources are accounted for.
- Legacy DATAX artifacts remain separated by logical source.
- TRANSTATS remains an acquisition-job manifest until materialization exists.
- External blockers remain explicit and are never converted to fake success.
- `manifest.json` is the package entry point.
- Total legacy records in this run: {legacy_total}.
"""
    (package_dir / "README.md").write_text(
        readme,
        encoding="utf-8",
        newline="\n",
    )

    write_checksums(package_dir)
    build_zip(package_dir, zip_path)

    return {
        "package_dir": str(package_dir),
        "zip_path": str(zip_path),
        "zip_sha256": sha256_file(zip_path),
        "legacy_records": legacy_total,
        "duplicate_groups": len(duplicate_groups),
        "cross_physical_duplicate_groups": len(
            cross_physical
        ),
        "package_ready": not cross_physical,
    }


def save_state(
    *,
    repo_root: Path,
    run_id: str,
    checkpoint_returncode: int | None,
    rows: list[dict[str, Any]],
    package: dict[str, Any] | None,
) -> dict[str, Any]:
    counts = Counter(
        str(row.get("classification"))
        for row in rows
    )

    terminal_good = {
        "DATAX_READY",
        "ACQUISITION_JOB_READY",
        "EXTERNAL_BLOCKER",
    }

    pending = [
        row["logical_source_id"]
        for row in rows
        if row.get("classification") == "PENDING"
    ]
    unresolved = [
        row["logical_source_id"]
        for row in rows
        if row.get("classification")
        not in terminal_good | {"PENDING"}
    ]

    if pending:
        status = "PARTIAL"
    elif unresolved:
        status = "FAILED"
    elif package is None:
        status = "PACKAGE_NOT_BUILT"
    elif not package.get("package_ready"):
        status = "PACKAGE_REVIEW"
    else:
        status = "READY"

    payload = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "pipeline_run_id": run_id,
        "status": status,
        "checkpoint_returncode": checkpoint_returncode,
        "inventory": {
            "total": EXPECTED_INVENTORY,
            "operational": EXPECTED_OPERATIONAL,
            "status_only": EXPECTED_STATUS_ONLY,
        },
        "counts": dict(sorted(counts.items())),
        "pending": pending,
        "unresolved": unresolved,
        "package": package,
        "sources": rows,
    }

    state_path = (
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / run_id
        / "state.json"
    )
    dump_json(state_path, payload)

    dump_json(
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / "latest.json",
        payload,
    )

    return payload


def print_summary(state: dict[str, Any]) -> None:
    print("=" * 78)
    print("B13 — REPRODUCIBLE END-TO-END PIPELINE")
    print("=" * 78)
    print(f"Run ID:                 {state['pipeline_run_id']}")
    print(f"Status:                 {state['status']}")
    print("Inventory:              52")
    print("Operational:            41")
    print("Status-only:            11")

    print()
    print("Classification:")
    for key in (
        "DATAX_READY",
        "ACQUISITION_JOB_READY",
        "EXTERNAL_BLOCKER",
        "PENDING",
        "EXECUTION_FAILED",
        "PROJECTION_FAILED",
        "PROJECTION_EMPTY",
        "SPECIALIZATION_FAILED",
        "PHYSICAL_SOURCE_UNRESOLVED",
    ):
        print(
            f"  {key:<28} "
            f"{state['counts'].get(key, 0)}"
        )

    print()
    print(
        "Pending:                "
        + (
            ", ".join(state["pending"])
            if state["pending"]
            else "(none)"
        )
    )
    print(
        "Unresolved:             "
        + (
            ", ".join(state["unresolved"])
            if state["unresolved"]
            else "(none)"
        )
    )

    package = state.get("package")
    if isinstance(package, dict):
        print()
        print(
            f"Legacy records:         "
            f"{package['legacy_records']}"
        )
        print(
            f"Duplicate groups:       "
            f"{package['duplicate_groups']}"
        )
        print(
            "Cross-physical dupes:   "
            f"{package['cross_physical_duplicate_groups']}"
        )
        print(
            f"Package:                "
            f"{package['package_dir']}"
        )
        print(
            f"ZIP:                    "
            f"{package['zip_path']}"
        )
        print(
            f"ZIP SHA256:             "
            f"{package['zip_sha256']}"
        )


def command_plan(repo_root: Path) -> int:
    required = [
        PLAN_PATH,
        SOURCES_CONFIG_PATH,
        EXECUTION_MAP_PATH,
    ]

    missing = [
        str(path)
        for path in required
        if not (repo_root / path).exists()
    ]
    if missing:
        raise FileNotFoundError(
            "Faltan prerequisitos: "
            + ", ".join(missing)
        )

    roster = operational_roster(repo_root)

    print("=" * 78)
    print("B13 — PIPELINE PLAN")
    print("=" * 78)
    print(f"Inventory:          {EXPECTED_INVENTORY}")
    print(f"Operational:        {len(roster)}")
    print(f"Status-only:        {EXPECTED_STATUS_ONLY}")
    print("Checkpoint runner:  apps.checkpointed_batch.main")
    print("Projection:         apps.datax_projection.main")
    print("Packaging:          B13 dynamic package")
    print("Resume:             YES")
    print("Network now:        NO")
    print("Live run network:   YES")
    print()
    print("Run command:")
    print(
        "  python -m apps.b13_pipeline.main run "
        "--run-id b13-e2e --max-sources 3"
    )
    print("Resume command:")
    print(
        "  python -m apps.b13_pipeline.main run "
        "--run-id b13-e2e --resume"
    )

    return 0


def command_run(
    repo_root: Path,
    *,
    run_id: str,
    resume: bool,
    max_sources: int | None,
) -> int:
    roster = operational_roster(repo_root)

    checkpoint_command = [
        sys.executable,
        "-m",
        "apps.checkpointed_batch.main",
        "--plan",
        str(PLAN_PATH),
        "--sources-config",
        str(SOURCES_CONFIG_PATH),
        "--execution-map",
        str(EXECUTION_MAP_PATH),
        "--backend",
        "json",
        "run",
        "--run-id",
        run_id,
    ]

    if resume:
        checkpoint_command.append("--resume")

    if max_sources is not None:
        if max_sources <= 0:
            raise ValueError("--max-sources debe ser > 0")
        checkpoint_command.extend(
            [
                "--max-sources",
                str(max_sources),
            ]
        )

    print("=" * 78)
    print("B13 — CHECKPOINTED LIVE EXECUTION")
    print("=" * 78)
    print(f"Run ID:       {run_id}")
    print(f"Resume:       {'YES' if resume else 'NO'}")
    print(
        f"Max sources:  "
        f"{max_sources if max_sources is not None else 'ALL'}"
    )

    checkpoint_log = (
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / run_id
        / "checkpointed_batch.log"
    )

    cp = run_command(
        checkpoint_command,
        cwd=repo_root,
        log_path=checkpoint_log,
    )

    print(
        "Checkpoint runner RC: "
        f"{cp.returncode}"
    )
    print(
        "Nota: RC no-cero no aborta automáticamente; "
        "el pipeline inspecciona cada evidencia."
    )

    print()
    print("Proyección/inspección offline de evidencias disponibles...")

    rows = inspect_and_project(
        repo_root=repo_root,
        run_id=run_id,
        roster=roster,
    )

    package = None
    if package_ready(rows):
        print(
            "41/41 operacionales terminales; "
            "construyendo paquete reproducible..."
        )
        package = build_package(
            repo_root=repo_root,
            run_id=run_id,
            rows=rows,
        )
    else:
        print(
            "Corrida parcial o con pendientes; "
            "el paquete final todavía no se construye."
        )

    state = save_state(
        repo_root=repo_root,
        run_id=run_id,
        checkpoint_returncode=cp.returncode,
        rows=rows,
        package=package,
    )

    print_summary(state)

    if state["status"] == "FAILED":
        return 2
    if state["status"] == "PACKAGE_REVIEW":
        return 3

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "B13: pipeline reproducible crawl -> projection -> "
            "package, con checkpoint/resume."
        )
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser(
        "plan",
        help="Valida y muestra el plan sin red.",
    )

    run_parser = sub.add_parser(
        "run",
        help="Ejecuta o reanuda el pipeline live.",
    )
    run_parser.add_argument(
        "--run-id",
        required=True,
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
    )
    run_parser.add_argument(
        "--max-sources",
        type=int,
        default=None,
    )

    args = parser.parse_args()
    repo_root = ROOT.resolve()

    if args.command == "plan":
        return command_plan(repo_root)

    return command_run(
        repo_root,
        run_id=args.run_id,
        resume=args.resume,
        max_sources=args.max_sources,
    )


if __name__ == "__main__":
    raise SystemExit(main())
