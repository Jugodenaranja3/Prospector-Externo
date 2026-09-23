from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.site_validation.core import (
    AuditRoster,
    IndependentReferenceProbe,
    baseline_legacy,
    baseline_raw,
    compare_urls,
    write_audit_output,
)

A9_SPECIAL_SOURCE_IDS = frozenset({"mhe", "sigma", "transtats"})


@dataclass(frozen=True)
class PhysicalAuditTarget:
    physical_source_id: str
    representative_source_id: str
    logical_source_ids: tuple[str, ...]
    entrypoint: str
    operational_status: str


def build_physical_targets(
    roster: AuditRoster,
    *,
    include_special: bool = False,
) -> list[PhysicalAuditTarget]:
    groups: dict[str, list[str]] = {}
    for source in roster.all_sources():
        if source.next_phase != "OPERATIONAL_CONFIG":
            continue
        physical_id = source.config_source_id or source.source_id
        groups.setdefault(physical_id, []).append(source.source_id)

    targets: list[PhysicalAuditTarget] = []
    for physical_id, logical_ids in groups.items():
        if not include_special and any(item in A9_SPECIAL_SOURCE_IDS for item in logical_ids):
            continue
        representative_id = physical_id if physical_id in logical_ids else logical_ids[0]
        representative = roster.source(representative_id)
        targets.append(
            PhysicalAuditTarget(
                physical_source_id=physical_id,
                representative_source_id=representative_id,
                logical_source_ids=tuple(logical_ids),
                entrypoint=representative.entrypoint,
                operational_status=representative.operational_status,
            )
        )
    return sorted(targets, key=lambda item: item.physical_source_id)


def _run_target(
    *,
    repo_root: Path,
    target: PhysicalAuditTarget,
    baseline_run: str,
    run_id: str,
    max_pages: int,
    max_resources: int,
    timeout: float,
    delay_seconds: float,
    resume: bool,
) -> dict[str, Any]:
    output_dir = repo_root / "output" / "site-validation" / run_id / target.physical_source_id
    summary_path = output_dir / "summary.json"
    if resume and summary_path.exists():
        existing = json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "physical_source_id": target.physical_source_id,
            "representative_source_id": target.representative_source_id,
            "logical_source_ids": list(target.logical_source_ids),
            "entrypoint": target.entrypoint,
            "status": "SKIPPED_EXISTING",
            "probe": existing.get("probe", {}),
            "comparison_counts": existing.get("comparison_counts", {}),
            "error": None,
        }

    roster = AuditRoster(repo_root)
    source = roster.source(target.representative_source_id)
    probe = IndependentReferenceProbe(
        max_pages=max_pages,
        max_resources=max_resources,
        timeout=timeout,
        delay_seconds=delay_seconds,
    )
    try:
        reference, stats, meta = probe.run(source.entrypoint)
        raw = baseline_raw(repo_root, baseline_run, source.source_id)
        legacy = baseline_legacy(repo_root, baseline_run, source.source_id)
        comparison = compare_urls(reference, raw, legacy, base_url=source.entrypoint)
        write_audit_output(
            output_dir=output_dir,
            source=source,
            reference=reference,
            stats=stats,
            meta=meta,
            raw=raw,
            legacy=legacy,
            comparison=comparison,
        )
        return {
            "physical_source_id": target.physical_source_id,
            "representative_source_id": target.representative_source_id,
            "logical_source_ids": list(target.logical_source_ids),
            "entrypoint": target.entrypoint,
            "status": "COMPLETED",
            "probe": {**asdict(stats), **meta},
            "comparison_counts": comparison["counts"],
            "error": None,
        }
    except Exception as exc:  # Batch audit must account for every target.
        output_dir.mkdir(parents=True, exist_ok=True)
        error_payload = {
            "physical_source_id": target.physical_source_id,
            "representative_source_id": target.representative_source_id,
            "logical_source_ids": list(target.logical_source_ids),
            "entrypoint": target.entrypoint,
            "status": "ERROR",
            "probe": {},
            "comparison_counts": {},
            "error": f"{type(exc).__name__}: {exc}",
        }
        (output_dir / "batch_error.json").write_text(
            json.dumps(error_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return error_payload


def run_operational_batch(
    *,
    repo_root: Path,
    baseline_run: str,
    run_id: str,
    workers: int,
    max_pages: int,
    max_resources: int,
    timeout: float,
    delay_seconds: float,
    resume: bool,
    include_special: bool = False,
) -> dict[str, Any]:
    roster = AuditRoster(repo_root)
    targets = build_physical_targets(roster, include_special=include_special)
    workers = max(1, min(int(workers), 8))
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="site-audit") as executor:
        pending = {
            executor.submit(
                _run_target,
                repo_root=repo_root,
                target=target,
                baseline_run=baseline_run,
                run_id=run_id,
                max_pages=max_pages,
                max_resources=max_resources,
                timeout=timeout,
                delay_seconds=delay_seconds,
                resume=resume,
            ): target
            for target in targets
        }
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            counts = result.get("comparison_counts") or {}
            print(
                f"[{result['status']:<16}] {result['physical_source_id']:<20} "
                f"ref={counts.get('reference_candidates', '-')} "
                f"raw={counts.get('baseline_raw', '-')} "
                f"match={counts.get('reference_matched_raw', '-')}"
            )

    results.sort(key=lambda row: row["physical_source_id"])
    completed = sum(row["status"] == "COMPLETED" for row in results)
    skipped = sum(row["status"] == "SKIPPED_EXISTING" for row in results)
    errors = sum(row["status"] == "ERROR" for row in results)
    logical_covered = sorted({sid for row in results for sid in row["logical_source_ids"]})
    payload = {
        "run_id": run_id,
        "baseline_run": baseline_run,
        "scope": "operational_physical_surfaces",
        "special_a9_excluded": [] if include_special else sorted(A9_SPECIAL_SOURCE_IDS),
        "physical_targets": len(targets),
        "logical_sources_represented": len(logical_covered),
        "completed": completed,
        "skipped_existing": skipped,
        "errors": errors,
        "workers": workers,
        "parameters": {
            "max_pages": max_pages,
            "max_resources": max_resources,
            "timeout": timeout,
            "delay_seconds": delay_seconds,
            "resume": resume,
        },
        "results": results,
    }

    root = repo_root / "output" / "site-validation" / run_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "batch_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# Operational Site Validation Batch — {run_id}",
        "",
        f"- Physical targets: **{len(targets)}**",
        f"- Logical sources represented: **{len(logical_covered)}**",
        f"- Completed: **{completed}**",
        f"- Skipped existing: **{skipped}**",
        f"- Errors: **{errors}**",
        f"- A9 special sources excluded: `{', '.join(sorted(A9_SPECIAL_SOURCE_IDS)) if not include_special else 'none'}`",
        "",
        "| Physical | Logical sources | Status | Reference | Raw unique | Ref ∩ raw |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in results:
        counts = row.get("comparison_counts") or {}
        lines.append(
            "| {physical} | {logical} | {status} | {ref} | {raw} | {match} |".format(
                physical=row["physical_source_id"],
                logical=", ".join(row["logical_source_ids"]),
                status=row["status"],
                ref=counts.get("reference_candidates", "-"),
                raw=counts.get("baseline_raw", "-"),
                match=counts.get("reference_matched_raw", "-"),
            )
        )
    (root / "BATCH_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload
