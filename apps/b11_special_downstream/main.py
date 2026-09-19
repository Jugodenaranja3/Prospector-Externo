from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_PATH = Path(".runtime/b11_output_audit/latest.json")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_snapshot(evidence_root: Path) -> Path:
    directory = evidence_root / "state" / "snapshots"
    candidates = sorted(
        directory.glob("*.json"),
        key=lambda path: (path.name, path.stat().st_mtime_ns),
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No existe snapshot para {evidence_root}")
    return candidates[0]


def build_transtats_manifest(
    *,
    repo_root: Path,
    audit: dict[str, Any],
) -> Path:
    rows = {
        row.get("logical_source_id"): row
        for row in audit.get("sources", [])
        if isinstance(row, dict)
    }

    row = rows.get("transtats")
    if not isinstance(row, dict):
        raise RuntimeError("B11 audit no contiene transtats")

    if row.get("workflow") != "custom":
        raise RuntimeError(
            f"TRANSTATS workflow inesperado: {row.get('workflow')!r}"
        )

    evidence_root = Path(str(row["evidence_root"]))
    if not evidence_root.is_absolute():
        evidence_root = repo_root / evidence_root

    snapshot_path = latest_snapshot(evidence_root)
    snapshot = load_json(snapshot_path)
    resources = snapshot.get("resources")

    if not isinstance(resources, list) or len(resources) != 1:
        raise RuntimeError(
            "TRANSTATS debe tener exactamente un acquisition job "
            f"en la evidencia actual; encontrados={len(resources or [])}"
        )

    resource = resources[0]
    if not isinstance(resource, dict):
        raise RuntimeError("Resource TRANSTATS inválido")

    if resource.get("discovery_method") != "custom_form_acquisition_job":
        raise RuntimeError(
            "TRANSTATS ya no coincide con la política especial esperada: "
            f"{resource.get('discovery_method')!r}"
        )

    url = resource.get("url")
    if not isinstance(url, str) or "DL_SelectFields" not in url:
        raise RuntimeError(
            f"URL de acquisition job TRANSTATS inesperada: {url!r}"
        )

    physical_source_id = str(
        row.get("physical_source_id") or "transtats"
    )

    output_path = (
        evidence_root
        / physical_source_id
        / "downstream"
        / "acquisition_job.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "logical_source_id": "transtats",
        "source_id": physical_source_id,
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
                "The current public evidence is an acquisition job/form, "
                "not a materialized downloadable file. It must not be "
                "misrepresented as CSV/XLSX/PDF in legacy ESTADISTICAS."
            ),
        },
        "next_capability": (
            "A future authorized acquisition executor may materialize "
            "the selected comma-delimited dataset and then pass that "
            "result through the normal DATAX projection."
        ),
        "source_snapshot": str(snapshot_path),
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B11C special downstream manifest generator."
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=AUDIT_PATH,
    )
    args = parser.parse_args()

    repo_root = Path(".").resolve()
    audit_path = (
        args.audit
        if args.audit.is_absolute()
        else repo_root / args.audit
    )
    audit = load_json(audit_path)

    output = build_transtats_manifest(
        repo_root=repo_root,
        audit=audit,
    )

    print("=" * 78)
    print("B11C — SPECIAL DOWNSTREAM MANIFEST")
    print("=" * 78)
    print("TRANSTATS: ACQUISITION_JOB_READY")
    print("Legacy fake file: NO")
    print("POST execution:   NO")
    print("Network:          NO")
    print(f"Manifest:         {output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
