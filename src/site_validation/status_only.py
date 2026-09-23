from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.site_validation.core import AuditRoster, IndependentReferenceProbe, ReferenceCandidate

DATA_EXTENSIONS = frozenset({
    ".csv", ".tsv", ".xls", ".xlsx", ".ods",
    ".json", ".xml", ".geojson", ".ndjson", ".jsonstat", ".topojson",
})
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".doc", ".docx"})
ARCHIVE_EXTENSIONS = frozenset({".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"})
DATA_HINTS = (
    "estadistic", "statistic", "dataset", "data-set", "open-data", "datos", "data",
    "indicador", "indicator", "boletin", "bulletin", "anuario", "yearbook",
    "serie", "series", "csv", "xlsx", "xls", "ods", "json", "xml",
)

def status_only_sources(roster: AuditRoster) -> list[str]:
    return sorted(source.source_id for source in roster.all_sources()
                  if source.next_phase != "OPERATIONAL_CONFIG")

def _semantic_hint(item: ReferenceCandidate) -> bool:
    value = f"{item.url} {item.title}".casefold()
    return any(token in value for token in DATA_HINTS)

def classify_status_only_result(
    *,
    prior_status: str,
    pages_visited: int,
    pages_failed: int,
    machine_readable: int,
    documents: int,
    archives: int,
    semantic_hits: int,
    stop_reason: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    if pages_visited == 0:
        reasons.append("NO_REACHABLE_HTML_PAGES")
        if pages_failed:
            reasons.append("REQUEST_FAILURES_OBSERVED")
        return "ACCESS_REVIEW", reasons

    if prior_status in {"ACCESS_RESTRICTED", "TLS_TECHNICAL_HOLD"}:
        reasons.append("PREVIOUS_ACCESS_BLOCKER_NOW_REACHABLE")
        if machine_readable > 0 or semantic_hits >= 3:
            reasons.append("PUBLIC_DATA_SIGNALS_OBSERVED")
            return "REVIEW_FOR_PROMOTION", reasons
        return "ACCESS_STATE_CHANGED", reasons

    if prior_status == "IDENTITY_UNRESOLVED":
        reasons.append("IDENTITY_STILL_REQUIRES_HUMAN_CONFIRMATION")
        if machine_readable > 0 or semantic_hits >= 3:
            reasons.append("PUBLIC_DATA_SIGNALS_OBSERVED")
        return "IDENTITY_REVIEW", reasons

    if prior_status in {"RETIRED_HISTORICAL_SOURCE", "ARCHIVE_ONLY"}:
        reasons.append("HISTORICAL_OR_ARCHIVE_CLASSIFICATION")
        if machine_readable > 0 or semantic_hits >= 5:
            reasons.append("CURRENT_PUBLIC_DATA_SIGNALS_OBSERVED")
            return "REVIEW_FOR_PROMOTION", reasons
        return "HISTORICAL_STATUS_SUPPORTED", reasons

    if machine_readable > 0:
        reasons.append("MACHINE_READABLE_PUBLIC_RESOURCES_FOUND")
        return "REVIEW_FOR_PROMOTION", reasons
    if semantic_hits >= 5:
        reasons.append("MULTIPLE_DATA_SEMANTIC_SIGNALS_FOUND")
        return "REVIEW_FOR_PROMOTION", reasons
    if documents > 0 or archives > 0:
        reasons.append("PUBLIC_DOCUMENTS_FOUND_BUT_NOT_ENOUGH_FOR_PROMOTION")
    else:
        reasons.append("NO_PUBLIC_RESOURCE_EVIDENCE_IN_BOUNDED_AUDIT")
    if stop_reason == "AUDIT_MAX_PAGES":
        reasons.append("AUDIT_BOUNDED_BY_MAX_PAGES")
    return "STATUS_ONLY_SUPPORTED", reasons

def _audit_one(
    *,
    repo_root: Path,
    run_id: str,
    source_id: str,
    max_pages: int,
    max_resources: int,
    timeout: float,
    delay_seconds: float,
    resume: bool,
) -> dict[str, Any]:
    out = repo_root / "output" / "site-validation" / run_id / source_id
    result_path = out / "status_only_validation.json"
    if resume and result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["execution_status"] = "SKIPPED_EXISTING"
        return payload

    roster = AuditRoster(repo_root)
    source = roster.source(source_id)
    probe = IndependentReferenceProbe(
        max_pages=max_pages,
        max_resources=max_resources,
        timeout=timeout,
        delay_seconds=delay_seconds,
    )

    try:
        reference, stats, meta = probe.run(source.entrypoint)
        machine = [item for item in reference if item.extension in DATA_EXTENSIONS]
        documents = [item for item in reference if item.extension in DOCUMENT_EXTENSIONS]
        archives = [item for item in reference if item.extension in ARCHIVE_EXTENSIONS]
        semantic = [item for item in reference if _semantic_hint(item)]
        classification, reasons = classify_status_only_result(
            prior_status=source.operational_status,
            pages_visited=stats.pages_visited,
            pages_failed=stats.pages_failed,
            machine_readable=len(machine),
            documents=len(documents),
            archives=len(archives),
            semantic_hits=len(semantic),
            stop_reason=stats.stop_reason,
        )
        payload = {
            "source_id": source.source_id,
            "logical_code": source.logical_code,
            "name": source.name,
            "entrypoint": source.entrypoint,
            "prior_status": source.operational_status,
            "next_phase": source.next_phase,
            "classification": classification,
            "reasons": reasons,
            "execution_status": "COMPLETED",
            "probe": {**asdict(stats), **meta},
            "counts": {
                "reference_candidates": len(reference),
                "machine_readable": len(machine),
                "documents": len(documents),
                "archives": len(archives),
                "semantic_data_hits": len(semantic),
            },
            "machine_readable_candidates": [asdict(item) for item in machine[:100]],
            "semantic_candidates": [asdict(item) for item in semantic[:100]],
            "interpretation": {
                "automatic_promotion": False,
                "note": (
                    "REVIEW_FOR_PROMOTION es una recomendacion de revision, no una promocion automatica. "
                    "PDFs/documentos por si solos no prueban que la fuente deba ser operacional."
                ),
            },
        }
    except Exception as exc:
        payload = {
            "source_id": source.source_id,
            "logical_code": source.logical_code,
            "name": source.name,
            "entrypoint": source.entrypoint,
            "prior_status": source.operational_status,
            "next_phase": source.next_phase,
            "classification": "ACCESS_REVIEW",
            "reasons": ["AUDIT_EXCEPTION"],
            "execution_status": "ERROR",
            "probe": {},
            "counts": {},
            "machine_readable_candidates": [],
            "semantic_candidates": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    out.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return payload

def run_status_only_batch(
    *,
    repo_root: Path,
    run_id: str,
    workers: int = 4,
    max_pages: int = 80,
    max_resources: int = 3000,
    timeout: float = 15.0,
    delay_seconds: float = 0.05,
    resume: bool = False,
) -> dict[str, Any]:
    roster = AuditRoster(repo_root)
    targets = status_only_sources(roster)
    workers = max(1, min(int(workers), 6))
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="status-audit") as executor:
        futures = {
            executor.submit(
                _audit_one,
                repo_root=repo_root,
                run_id=run_id,
                source_id=source_id,
                max_pages=max_pages,
                max_resources=max_resources,
                timeout=timeout,
                delay_seconds=delay_seconds,
                resume=resume,
            ): source_id
            for source_id in targets
        }
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            counts = row.get("counts") or {}
            print(
                f"[{row['classification']:<28}] {row['source_id']:<20} "
                f"pages={row.get('probe', {}).get('pages_visited', '-')} "
                f"ref={counts.get('reference_candidates', '-')} "
                f"data={counts.get('machine_readable', '-')} "
                f"docs={counts.get('documents', '-')}"
            )

    results.sort(key=lambda row: row["source_id"])
    class_counts: dict[str, int] = {}
    for row in results:
        class_counts[row["classification"]] = class_counts.get(row["classification"], 0) + 1

    root = repo_root / "output" / "site-validation" / run_id
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "targets": len(targets),
        "completed": sum(row.get("execution_status") == "COMPLETED" for row in results),
        "skipped_existing": sum(row.get("execution_status") == "SKIPPED_EXISTING" for row in results),
        "errors": sum(row.get("execution_status") == "ERROR" for row in results),
        "classification_counts": class_counts,
        "results": results,
    }
    (root / "a8_status_only_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        f"# A8 STATUS_ONLY Validation — {run_id}",
        "",
        f"- Targets: **{len(targets)}**",
        f"- Completed: **{payload['completed']}**",
        f"- Errors: **{payload['errors']}**",
        "",
        "| Source | Prior status | Classification | Pages | Ref | Data | Docs |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in results:
        probe = row.get("probe") or {}
        counts = row.get("counts") or {}
        lines.append(
            f"| {row['source_id']} | {row['prior_status']} | {row['classification']} | "
            f"{probe.get('pages_visited', '-')} | {counts.get('reference_candidates', '-')} | "
            f"{counts.get('machine_readable', '-')} | {counts.get('documents', '-')} |"
        )
    (root / "A8_STATUS_ONLY_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return payload
