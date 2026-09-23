from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.site_validation.core import AuditRoster, baseline_legacy, baseline_raw
from src.site_validation.origin_recheck import (
    IndependentOriginRechecker,
    compare_origin_recheck,
    load_allowed_extensions,
    write_origin_recheck_output,
)

SPECIALIZED_STATUSES = frozenset({
    "OPERATIONAL_DATA_API",
    "OPERATIONAL_CUSTOM_DATA_API",
    "OPERATIONAL_CUSTOM_FORM_RESOURCE",
    "OPERATIONAL_JAVASCRIPT",
})


def latest_baseline_coverage(repo_root: Path, baseline_run: str, source_id: str) -> dict[str, Any]:
    reports = sorted((repo_root / "output" / "checkpointed" / baseline_run / source_id / "reports").glob("*.json"))
    if not reports:
        return {}
    try:
        payload = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("source_results", []) if isinstance(payload, dict) else []
    if not rows or not isinstance(rows[0], dict):
        return {}
    coverage = rows[0].get("coverage", {})
    return coverage if isinstance(coverage, dict) else {}


def existing_origin_evidence(repo_root: Path, source_id: str) -> list[str]:
    pattern_root = repo_root / "output" / "site-validation"
    if not pattern_root.exists():
        return []
    return sorted(str(path.relative_to(repo_root)) for path in pattern_root.glob(f"*/{source_id}/origin_recheck.json"))


def classify_a7_row(
    *,
    operational_status: str,
    raw_unique: int,
    reference_candidates: int,
    matched_raw: int,
    stop_reason: str | None,
    has_origin_evidence: bool,
    threshold: float = 0.80,
) -> tuple[str, list[str], float | None]:
    overlap = (matched_raw / raw_unique) if raw_unique > 0 else None
    flags: list[str] = []
    if raw_unique == 0:
        flags.append("NO_BASELINE_RAW")
    if raw_unique == 1000:
        flags.append("BASELINE_RESOURCE_CAP_1000")
    if reference_candidates > raw_unique:
        flags.append("REFERENCE_GT_RAW")
    if overlap is not None and overlap < threshold:
        flags.append("LOW_SITEWIDE_OVERLAP")
    if stop_reason and stop_reason not in {"QUEUE_EXHAUSTED", "API_SEEDS_EXHAUSTED", "CUSTOM_FORM_CONFIRMED"}:
        flags.append(f"BASELINE_STOP_{stop_reason}")

    if operational_status in SPECIALIZED_STATUSES:
        return "SPECIALIZED_VALIDATION", flags, overlap
    if has_origin_evidence:
        return "ORIGIN_EVIDENCE_AVAILABLE", flags, overlap
    if raw_unique <= 5:
        flags.append("LOW_BASELINE_VOLUME")
        return "ORIGIN_RECHECK_REQUIRED", flags, overlap
    if overlap is not None and overlap >= threshold and stop_reason == "QUEUE_EXHAUSTED":
        return "GENERIC_STRONG", flags, overlap
    return "ORIGIN_RECHECK_REQUIRED", flags, overlap


def build_a7_plan(
    *,
    repo_root: Path,
    batch_run: str,
    baseline_run: str,
    threshold: float = 0.80,
) -> dict[str, Any]:
    batch_path = repo_root / "output" / "site-validation" / batch_run / "batch_summary.json"
    if not batch_path.exists():
        raise FileNotFoundError(f"No existe batch summary: {batch_path}")
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    roster = AuditRoster(repo_root)
    rows: list[dict[str, Any]] = []

    for result in batch.get("results", []):
        if not isinstance(result, dict):
            continue
        source_id = str(result.get("representative_source_id") or result.get("physical_source_id") or "")
        if not source_id:
            continue
        source = roster.source(source_id)
        counts = result.get("comparison_counts") or {}
        raw_unique = int(counts.get("baseline_raw") or 0)
        reference_candidates = int(counts.get("reference_candidates") or 0)
        matched_raw = int(counts.get("reference_matched_raw") or 0)
        coverage = latest_baseline_coverage(repo_root, baseline_run, source_id)
        stop_reason = str(coverage.get("stop_reason") or "") or None
        evidence = existing_origin_evidence(repo_root, source_id)
        category, flags, overlap = classify_a7_row(
            operational_status=source.operational_status,
            raw_unique=raw_unique,
            reference_candidates=reference_candidates,
            matched_raw=matched_raw,
            stop_reason=stop_reason,
            has_origin_evidence=bool(evidence),
            threshold=threshold,
        )
        rows.append({
            "source_id": source_id,
            "logical_source_ids": result.get("logical_source_ids") or [source_id],
            "operational_status": source.operational_status,
            "entrypoint": source.entrypoint,
            "category": category,
            "risk_flags": flags,
            "sitewide_overlap_raw": round(overlap, 6) if overlap is not None else None,
            "reference_candidates": reference_candidates,
            "baseline_raw_unique": raw_unique,
            "reference_matched_raw": matched_raw,
            "baseline_stop_reason": stop_reason,
            "baseline_urls_pending": coverage.get("urls_pending"),
            "origin_evidence": evidence,
        })

    rows.sort(key=lambda row: row["source_id"])
    categories: dict[str, int] = {}
    for row in rows:
        categories[row["category"]] = categories.get(row["category"], 0) + 1
    return {
        "batch_run": batch_run,
        "baseline_run": baseline_run,
        "threshold": threshold,
        "physical_sources": len(rows),
        "category_counts": categories,
        "origin_recheck_targets": [row["source_id"] for row in rows if row["category"] == "ORIGIN_RECHECK_REQUIRED"],
        "specialized_targets": [row["source_id"] for row in rows if row["category"] == "SPECIALIZED_VALIDATION"],
        "rows": rows,
    }


def _recheck_one(
    *,
    repo_root: Path,
    baseline_run: str,
    run_id: str,
    source_id: str,
    timeout: float,
    delay_seconds: float,
    resume: bool,
) -> dict[str, Any]:
    out = repo_root / "output" / "site-validation" / run_id / source_id
    if resume and (out / "origin_recheck.json").exists():
        payload = json.loads((out / "origin_recheck.json").read_text(encoding="utf-8"))
        comparison = payload.get("comparison", {}).get("counts", {})
        stats = payload.get("stats", {})
        return {"source_id": source_id, "status": "SKIPPED_EXISTING", "stats": stats, "counts": comparison, "error": None}

    roster = AuditRoster(repo_root)
    source = roster.source(source_id)
    raw = baseline_raw(repo_root, baseline_run, source_id)
    legacy = baseline_legacy(repo_root, baseline_run, source_id)
    allowed_extensions = load_allowed_extensions(repo_root, baseline_run, source_id, source.config_source_id)
    rechecker = IndependentOriginRechecker(timeout=timeout, delay_seconds=delay_seconds)
    try:
        reference, stats, meta = rechecker.run(
            entrypoint=source.entrypoint,
            raw=raw,
            allowed_extensions=allowed_extensions,
        )
        comparison = compare_origin_recheck(
            reference=reference,
            raw=raw,
            legacy=legacy,
            successful_origins=meta["successful_origins"],
            base_url=source.entrypoint,
        )
        write_origin_recheck_output(
            output_dir=out,
            source_id=source_id,
            entrypoint=source.entrypoint,
            reference=reference,
            stats=stats,
            meta=meta,
            comparison=comparison,
        )
        return {"source_id": source_id, "status": "COMPLETED", "stats": asdict(stats), "counts": comparison["counts"], "error": None}
    except Exception as exc:
        out.mkdir(parents=True, exist_ok=True)
        payload = {"source_id": source_id, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        (out / "origin_batch_error.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**payload, "stats": {}, "counts": {}}


def run_a7_origin_batch(
    *,
    repo_root: Path,
    batch_run: str,
    baseline_run: str,
    run_id: str,
    workers: int = 4,
    timeout: float = 20.0,
    delay_seconds: float = 0.10,
    resume: bool = False,
    threshold: float = 0.80,
) -> dict[str, Any]:
    plan = build_a7_plan(repo_root=repo_root, batch_run=batch_run, baseline_run=baseline_run, threshold=threshold)
    targets = plan["origin_recheck_targets"]
    workers = max(1, min(int(workers), 6))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="origin-audit") as executor:
        pending = {
            executor.submit(
                _recheck_one,
                repo_root=repo_root,
                baseline_run=baseline_run,
                run_id=run_id,
                source_id=source_id,
                timeout=timeout,
                delay_seconds=delay_seconds,
                resume=resume,
            ): source_id
            for source_id in targets
        }
        for future in as_completed(pending):
            row = future.result()
            results.append(row)
            c = row.get("counts") or {}
            s = row.get("stats") or {}
            print(
                f"[{row['status']:<16}] {row['source_id']:<20} "
                f"origins={s.get('origin_pages_rechecked', '-')} "
                f"match={c.get('reference_matched_raw', '-')} "
                f"new={c.get('reference_only_raw', '-')} "
                f"not_seen={c.get('raw_recheckable_not_observed', '-')}"
            )

    results.sort(key=lambda row: row["source_id"])
    completed = sum(row["status"] == "COMPLETED" for row in results)
    skipped = sum(row["status"] == "SKIPPED_EXISTING" for row in results)
    errors = sum(row["status"] == "ERROR" for row in results)
    root = repo_root / "output" / "site-validation" / run_id
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "batch_run": batch_run,
        "baseline_run": baseline_run,
        "plan": plan,
        "targets": len(targets),
        "completed": completed,
        "skipped_existing": skipped,
        "errors": errors,
        "results": results,
    }
    (root / "a7_origin_batch_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "a7_triage.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# A7 Triage and Origin Recheck — {run_id}",
        "",
        f"- Physical sources classified: **{plan['physical_sources']}**",
        f"- Origin recheck targets: **{len(targets)}**",
        f"- Specialized targets: **{len(plan['specialized_targets'])}**",
        f"- Completed origin rechecks: **{completed}**",
        f"- Errors: **{errors}**",
        "",
        "## Categories",
        "",
    ]
    for key, value in sorted(plan["category_counts"].items()):
        lines.append(f"- {key}: **{value}**")
    lines.extend(["", "## Origin recheck results", "", "| Source | Status | Origins | Match | New | Not seen |", "|---|---|---:|---:|---:|---:|"])
    for row in results:
        s, c = row.get("stats") or {}, row.get("counts") or {}
        lines.append(
            f"| {row['source_id']} | {row['status']} | {s.get('origin_pages_rechecked', '-')} | "
            f"{c.get('reference_matched_raw', '-')} | {c.get('reference_only_raw', '-')} | "
            f"{c.get('raw_recheckable_not_observed', '-')} |"
        )
    (root / "A7_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload
