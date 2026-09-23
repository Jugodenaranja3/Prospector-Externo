from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
import yaml

from src.site_validation.core import AuditRoster, baseline_legacy, baseline_raw
from src.site_validation.origin_recheck import (
    IndependentOriginRechecker,
    compare_origin_recheck,
    load_allowed_extensions,
    write_origin_recheck_output,
)

SPECIALIZED_TARGETS = (
    "data_gov",
    "fifa",
    "sicoes",
    "statistics_denmark",
    "undata",
    "vipfe",
)


def _source_configs(repo_root: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load((repo_root / "config" / "sources.yaml").read_text(encoding="utf-8")) or {}
    rows = payload.get("sources", []) if isinstance(payload, dict) else []
    return {
        str(row["source_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("source_id")
    }


def _absolute_raw_url(item: dict[str, Any], fallback: str) -> str:
    raw = str(item.get("raw_url") or item.get("url") or "").strip()
    origin = str(item.get("discovered_from_url") or fallback or "").strip()
    if not raw:
        return ""
    return urljoin(origin or fallback, raw)


def _unique_urls(raw: list[dict[str, Any]], fallback: str) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in raw:
        url = _absolute_raw_url(item, fallback)
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(url)
    return output


def _probe_endpoint(url: str, timeout: float) -> dict[str, Any]:
    headers = {
        "User-Agent": "DATAX-Site-Validation/1.0 (+independent-audit)",
        "Accept": "application/json,text/plain,*/*;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        content_type = (response.headers.get("content-type") or "").lower()
        sample = response.content[:1024 * 1024]
        json_valid = False
        json_shape = None
        json_count = None
        if "json" in content_type or sample.lstrip()[:1] in (b"{", b"["):
            try:
                payload = response.json()
                json_valid = True
                if isinstance(payload, list):
                    json_shape = "list"
                    json_count = len(payload)
                elif isinstance(payload, dict):
                    json_shape = "object"
                    json_count = len(payload)
            except Exception:
                pass
        return {
            "requested_url": url,
            "final_url": str(response.url),
            "status_code": response.status_code,
            "ok": 200 <= response.status_code < 400,
            "content_type": content_type,
            "content_length": response.headers.get("content-length"),
            "json_valid": json_valid,
            "json_shape": json_shape,
            "json_count": json_count,
            "error": None,
        }
    except Exception as exc:
        return {
            "requested_url": url,
            "final_url": None,
            "status_code": None,
            "ok": False,
            "content_type": None,
            "content_length": None,
            "json_valid": False,
            "json_shape": None,
            "json_count": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def validate_specialized_source(
    *,
    repo_root: Path,
    baseline_run: str,
    run_id: str,
    source_id: str,
    timeout: float = 20.0,
    delay_seconds: float = 0.10,
) -> dict[str, Any]:
    roster = AuditRoster(repo_root)
    source = roster.source(source_id)
    configs = _source_configs(repo_root)
    config = configs.get(source_id, {})
    workflow = str(config.get("workflow") or "").lower()

    raw = baseline_raw(repo_root, baseline_run, source_id)
    legacy = baseline_legacy(repo_root, baseline_run, source_id)
    effective_entrypoint = str(config.get("entrypoint") or source.entrypoint)
    raw_urls = _unique_urls(raw, effective_entrypoint)

    out = repo_root / "output" / "site-validation" / run_id / source_id
    out.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "source_id": source_id,
        "plan_operational_status": source.operational_status,
        "effective_workflow": workflow,
        "entrypoint": effective_entrypoint,
        "baseline_raw_records": len(raw),
        "baseline_raw_unique_urls": len(raw_urls),
        "baseline_legacy_records": len(legacy),
        "validation_mode": None,
        "status": None,
        "details": {},
    }

    if workflow in {"html", "javascript"}:
        allowed_extensions = load_allowed_extensions(
            repo_root, baseline_run, source_id, source.config_source_id
        )
        rechecker = IndependentOriginRechecker(
            timeout=timeout,
            delay_seconds=delay_seconds,
        )
        reference, stats, meta = rechecker.run(
            entrypoint=effective_entrypoint,
            raw=raw,
            allowed_extensions=allowed_extensions,
        )
        comparison = compare_origin_recheck(
            reference=reference,
            raw=raw,
            legacy=legacy,
            successful_origins=meta["successful_origins"],
            base_url=effective_entrypoint,
        )
        write_origin_recheck_output(
            output_dir=out,
            source_id=source_id,
            entrypoint=effective_entrypoint,
            reference=reference,
            stats=stats,
            meta=meta,
            comparison=comparison,
        )
        counts = comparison["counts"]
        result["validation_mode"] = "ORIGIN_RECHECK"
        result["details"] = {
            "stats": asdict(stats),
            "counts": counts,
        }
        if stats.origin_pages_rechecked == 0:
            result["status"] = "REVIEW_REQUIRED"
        elif counts.get("raw_recheckable_not_observed", 0) == 0:
            result["status"] = "VALIDATED"
        else:
            result["status"] = "VALIDATED_WITH_TEMPORAL_DIFFERENCES"

    elif workflow in {"api", "custom"}:
        probes = [_probe_endpoint(url, timeout) for url in raw_urls]
        ok_count = sum(bool(row.get("ok")) for row in probes)
        json_count = sum(bool(row.get("json_valid")) for row in probes)
        result["validation_mode"] = "ENDPOINT_RECHECK"
        result["details"] = {
            "endpoint_probes": probes,
            "reachable": ok_count,
            "json_valid": json_count,
        }
        if not probes:
            result["status"] = "REVIEW_REQUIRED"
        elif ok_count == len(probes):
            result["status"] = "VALIDATED"
        elif ok_count > 0:
            result["status"] = "PARTIAL_REACHABILITY"
        else:
            result["status"] = "REVIEW_REQUIRED"

    else:
        result["validation_mode"] = "UNSUPPORTED_WORKFLOW"
        result["status"] = "REVIEW_REQUIRED"
        result["details"] = {"workflow": workflow}

    (out / "specialized_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def run_specialized_batch(
    *,
    repo_root: Path,
    baseline_run: str,
    run_id: str,
    timeout: float = 20.0,
    delay_seconds: float = 0.10,
) -> dict[str, Any]:
    results = [
        validate_specialized_source(
            repo_root=repo_root,
            baseline_run=baseline_run,
            run_id=run_id,
            source_id=source_id,
            timeout=timeout,
            delay_seconds=delay_seconds,
        )
        for source_id in SPECIALIZED_TARGETS
    ]

    root = repo_root / "output" / "site-validation" / run_id
    summary = {
        "run_id": run_id,
        "baseline_run": baseline_run,
        "targets": len(results),
        "validated": sum(row["status"] == "VALIDATED" for row in results),
        "validated_with_temporal_differences": sum(
            row["status"] == "VALIDATED_WITH_TEMPORAL_DIFFERENCES" for row in results
        ),
        "partial_reachability": sum(row["status"] == "PARTIAL_REACHABILITY" for row in results),
        "review_required": sum(row["status"] == "REVIEW_REQUIRED" for row in results),
        "results": results,
    }
    (root / "a7_specialized_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        f"# A7.2 Specialized Validation — {run_id}",
        "",
        "| Source | Plan status | Effective workflow | Mode | Status | Raw | Legacy |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for row in results:
        lines.append(
            f"| {row['source_id']} | {row['plan_operational_status']} | "
            f"{row['effective_workflow']} | {row['validation_mode']} | {row['status']} | "
            f"{row['baseline_raw_unique_urls']} | {row['baseline_legacy_records']} |"
        )
    (root / "A7_SPECIALIZED_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return summary
