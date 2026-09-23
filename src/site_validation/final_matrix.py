
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.site_validation.core import AuditRoster, baseline_legacy, baseline_raw

A8_ADJUDICATIONS: dict[str, dict[str, str]] = {
    "fmi": {
        "classification": "REVIEW_FOR_PROMOTION",
        "note": "Adjudicacion A8: existe evidencia publica actual de portal de datos/descargas/API; revisar promocion operacional.",
    },
    "ibch": {
        "classification": "STATUS_ONLY_SUPPORTED_AFTER_ADJUDICATION",
        "note": "Adjudicacion A8: los hallazgos automaticos corresponden principalmente a publicaciones/documentos; no se confirma catalogo machine-readable suficiente.",
    },
    "otros_historicos": {
        "classification": "HISTORICAL_STATUS_SUPPORTED",
        "note": "Adjudicacion A8: la fuente conserva datos historicos accesibles, pero su condicion historica/retirada sigue siendo coherente.",
    },
    "mefp": {
        "classification": "ACCESS_REVIEW_WITH_EXTERNAL_EVIDENCE",
        "note": "Adjudicacion A8: el probe no recorrio el entrypoint, pero existe evidencia externa de disponibilidad actual; requiere revisar host/entrypoint sin inferir bug del crawler.",
    },
}

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

def _index_results(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("results", []) if isinstance(payload, dict) else []
    return {
        str(row.get("source_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("source_id")
    }

def _physical_id(roster: AuditRoster, source_id: str) -> str:
    source = roster.source(source_id)
    return source.config_source_id or source.source_id

def _prior_delivery_status(source_id: str, next_phase: str) -> str:
    if source_id in {"mhe", "sigma"}:
        return "EXTERNAL_BLOCKER"
    if source_id == "transtats":
        return "ACQUISITION_JOB_READY"
    if next_phase == "OPERATIONAL_CONFIG":
        return "DATAX_READY"
    return "STATUS_ONLY"

def _origin_payload(repo_root: Path, *, physical_id: str, a7_run: str, bcb_origin_run: str) -> dict[str, Any]:
    candidates = [
        repo_root / "output" / "site-validation" / a7_run / physical_id / "origin_recheck.json",
        repo_root / "output" / "site-validation" / bcb_origin_run / physical_id / "origin_recheck.json",
    ]
    for path in candidates:
        if path.exists():
            payload = _load_json(path)
            payload["_evidence_path"] = str(path.relative_to(repo_root))
            return payload
    return {}

def _sitewide_index(repo_root: Path, batch_run: str) -> dict[str, dict[str, Any]]:
    payload = _load_json(repo_root / "output" / "site-validation" / batch_run / "batch_summary.json")
    rows = payload.get("results", []) if isinstance(payload, dict) else []
    return {
        str(row.get("physical_source_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("physical_source_id")
    }

def _a7_plan_index(repo_root: Path, a7_run: str) -> dict[str, dict[str, Any]]:
    payload = _load_json(repo_root / "output" / "site-validation" / a7_run / "a7_triage.json")
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    return {
        str(row.get("source_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("source_id")
    }

def _specialized_index(repo_root: Path, run_id: str) -> dict[str, dict[str, Any]]:
    return _index_results(_load_json(
        repo_root / "output" / "site-validation" / run_id / "a7_specialized_summary.json"
    ))

def _a8_index(repo_root: Path, run_id: str) -> dict[str, dict[str, Any]]:
    return _index_results(_load_json(
        repo_root / "output" / "site-validation" / run_id / "a8_status_only_summary.json"
    ))

def _a9_index(repo_root: Path, run_id: str) -> dict[str, dict[str, Any]]:
    return _index_results(_load_json(
        repo_root / "output" / "site-validation" / run_id / "a9_special_cases_summary.json"
    ))

def _safe_ratio(num: int | None, den: int | None) -> float | None:
    if not den:
        return None
    return round((num or 0) / den, 6)

def build_final_matrix(
    *,
    repo_root: Path,
    baseline_run: str,
    batch_run: str,
    a7_run: str,
    specialized_run: str,
    a8_run: str,
    a9_run: str,
    bcb_origin_run: str,
) -> dict[str, Any]:
    roster = AuditRoster(repo_root)
    sitewide = _sitewide_index(repo_root, batch_run)
    a7_plan = _a7_plan_index(repo_root, a7_run)
    specialized = _specialized_index(repo_root, specialized_run)
    a8 = _a8_index(repo_root, a8_run)
    a9 = _a9_index(repo_root, a9_run)

    rows: list[dict[str, Any]] = []

    for source in roster.all_sources():
        physical_id = _physical_id(roster, source.source_id)
        prior_delivery = _prior_delivery_status(source.source_id, source.next_phase)
        legacy_records = len(baseline_legacy(repo_root, baseline_run, source.source_id))
        raw_records = len(baseline_raw(repo_root, baseline_run, source.source_id))

        row: dict[str, Any] = {
            "source_id": source.source_id,
            "logical_code": source.logical_code,
            "name": source.name,
            "entrypoint": source.entrypoint,
            "physical_source_id": physical_id,
            "prior_operational_status": source.operational_status,
            "prior_delivery_status": prior_delivery,
            "baseline_raw_records": raw_records,
            "baseline_legacy_records": legacy_records,
            "audit_method": None,
            "audit_classification": None,
            "coverage_ratio": None,
            "reference_new_candidates": None,
            "baseline_not_observed_now": None,
            "evidence_path": None,
            "note": "",
        }

        if source.source_id in a9:
            evidence = a9[source.source_id]
            row["audit_method"] = "A9_SPECIAL_CASE"
            row["audit_classification"] = evidence.get("classification")
            row["evidence_path"] = str(Path("output/site-validation") / a9_run / "a9_special_cases_summary.json")
            if source.source_id == "transtats":
                row["note"] = (
                    f"contract_ok={evidence.get('contract_ok')}; "
                    f"forms_detected={evidence.get('forms_detected')}; GET-only; no POST."
                )
            else:
                home = evidence.get("homepage") or {}
                robots = evidence.get("robots") or {}
                row["note"] = (
                    f"dns={bool((evidence.get('dns') or {}).get('ok'))}; "
                    f"home={home.get('status_code') or home.get('error_type')}; "
                    f"robots={robots.get('status_code') or robots.get('error_type')}."
                )

        elif source.next_phase != "OPERATIONAL_CONFIG":
            evidence = a8.get(source.source_id, {})
            automatic = str(evidence.get("classification") or "A8_EVIDENCE_MISSING")
            override = A8_ADJUDICATIONS.get(source.source_id)
            row["audit_method"] = "A8_STATUS_ONLY"
            row["audit_classification"] = override["classification"] if override else automatic
            row["evidence_path"] = str(
                Path("output/site-validation") / a8_run / source.source_id / "status_only_validation.json"
            )
            counts = evidence.get("counts") or {}
            probe = evidence.get("probe") or {}
            row["note"] = (
                f"automatic={automatic}; pages={probe.get('pages_visited', 0)}; "
                f"ref={counts.get('reference_candidates', 0)}; "
                f"machine_readable={counts.get('machine_readable', 0)}; "
                f"documents={counts.get('documents', 0)}."
                + (f" {override['note']}" if override else "")
            )

        elif physical_id in specialized:
            evidence = specialized[physical_id]
            row["audit_method"] = "A7_SPECIALIZED"
            row["audit_classification"] = evidence.get("status")
            row["evidence_path"] = str(
                Path("output/site-validation") / specialized_run / physical_id / "specialized_validation.json"
            )
            details = evidence.get("details") or {}
            counts = details.get("counts") or {}
            if counts:
                row["coverage_ratio"] = _safe_ratio(
                    counts.get("reference_matched_raw"),
                    counts.get("raw_recheckable_unique"),
                )
                row["reference_new_candidates"] = counts.get("reference_only_raw")
                row["baseline_not_observed_now"] = counts.get("raw_recheckable_not_observed")
            row["note"] = (
                f"effective_workflow={evidence.get('effective_workflow')}; "
                f"mode={evidence.get('validation_mode')}."
            )

        else:
            origin = _origin_payload(
                repo_root,
                physical_id=physical_id,
                a7_run=a7_run,
                bcb_origin_run=bcb_origin_run,
            )
            if origin:
                counts = ((origin.get("comparison") or {}).get("counts") or {})
                row["audit_method"] = "A7_ORIGIN_RECHECK"
                row["audit_classification"] = "ORIGIN_RECHECK_COMPLETED"
                row["coverage_ratio"] = _safe_ratio(
                    counts.get("reference_matched_raw"),
                    counts.get("raw_recheckable_unique"),
                )
                row["reference_new_candidates"] = counts.get("reference_only_raw")
                row["baseline_not_observed_now"] = counts.get("raw_recheckable_not_observed")
                row["evidence_path"] = origin.get("_evidence_path")
                stats = origin.get("stats") or {}
                row["note"] = (
                    f"origins_rechecked={stats.get('origin_pages_rechecked')}; "
                    f"origins_failed={stats.get('origin_pages_failed')}; "
                    f"raw_recheckable={counts.get('raw_recheckable_unique')}; "
                    f"matched={counts.get('reference_matched_raw')}."
                )
            else:
                triage = a7_plan.get(physical_id, {})
                batch = sitewide.get(physical_id, {})
                counts = batch.get("comparison_counts") or {}
                row["audit_method"] = "A6_SITEWIDE"
                row["audit_classification"] = triage.get("category") or "SITEWIDE_COMPLETED"
                row["coverage_ratio"] = _safe_ratio(
                    counts.get("reference_matched_raw"),
                    counts.get("baseline_raw"),
                )
                row["reference_new_candidates"] = counts.get("reference_only_raw")
                row["baseline_not_observed_now"] = counts.get("raw_only_reference")
                row["evidence_path"] = str(
                    Path("output/site-validation") / batch_run / physical_id / "summary.json"
                )
                row["note"] = (
                    f"sitewide_reference={counts.get('reference_candidates')}; "
                    f"raw_unique={counts.get('baseline_raw')}; "
                    f"matched={counts.get('reference_matched_raw')}; "
                    f"triage={triage.get('category')}."
                )

        rows.append(row)

    rows.sort(key=lambda item: item["source_id"])
    if len(rows) != 52:
        raise ValueError(f"La matriz final debe contener 52 filas; obtuvo {len(rows)}")

    missing = [
        row["source_id"]
        for row in rows
        if not row["audit_method"] or not row["audit_classification"]
    ]
    if missing:
        raise ValueError(f"Fuentes sin evidencia de auditoria: {missing}")

    unique_physical = sorted({row["physical_source_id"] for row in rows})
    class_counts: dict[str, int] = {}
    method_counts: dict[str, int] = {}
    for row in rows:
        class_counts[row["audit_classification"]] = class_counts.get(row["audit_classification"], 0) + 1
        method_counts[row["audit_method"]] = method_counts.get(row["audit_method"], 0) + 1

    comparable_physical: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["audit_method"] != "A7_ORIGIN_RECHECK":
            continue
        physical_id = row["physical_source_id"]
        if physical_id in comparable_physical:
            continue
        origin = _origin_payload(
            repo_root,
            physical_id=physical_id,
            a7_run=a7_run,
            bcb_origin_run=bcb_origin_run,
        )
        counts = ((origin.get("comparison") or {}).get("counts") or {})
        comparable_physical[physical_id] = {
            "matched": int(counts.get("reference_matched_raw") or 0),
            "raw_recheckable": int(counts.get("raw_recheckable_unique") or 0),
        }

    comparable_num = sum(row["matched"] for row in comparable_physical.values())
    comparable_den = sum(row["raw_recheckable"] for row in comparable_physical.values())

    return {
        "baseline_run": baseline_run,
        "runs": {
            "a6_batch": batch_run,
            "a7_origin": a7_run,
            "a7_specialized": specialized_run,
            "a8_status_only": a8_run,
            "a9_special_cases": a9_run,
            "bcb_origin": bcb_origin_run,
        },
        "logical_sources": len(rows),
        "unique_physical_sources": len(unique_physical),
        "prior_delivery_counts": {
            "DATAX_READY": sum(row["prior_delivery_status"] == "DATAX_READY" for row in rows),
            "ACQUISITION_JOB_READY": sum(row["prior_delivery_status"] == "ACQUISITION_JOB_READY" for row in rows),
            "EXTERNAL_BLOCKER": sum(row["prior_delivery_status"] == "EXTERNAL_BLOCKER" for row in rows),
            "STATUS_ONLY": sum(row["prior_delivery_status"] == "STATUS_ONLY" for row in rows),
        },
        "audit_method_counts": method_counts,
        "audit_classification_counts": class_counts,
        "comparable_origin_recheck": {
            "physical_sources": len(comparable_physical),
            "matched_raw": comparable_num,
            "raw_recheckable": comparable_den,
            "aggregate_overlap_ratio": _safe_ratio(comparable_num, comparable_den),
            "note": (
                "Este agregado solo incluye fuentes fisicas con origin-recheck comparable. "
                "No se mezcla con API/custom/JS, STATUS_ONLY ni blockers."
            ),
        },
        "rows": rows,
    }

def write_final_matrix(*, repo_root: Path, output_run: str, **kwargs: Any) -> dict[str, Any]:
    payload = build_final_matrix(repo_root=repo_root, **kwargs)
    root = repo_root / "output" / "site-validation" / output_run
    root.mkdir(parents=True, exist_ok=True)

    (root / "FINAL_MATRIX_52.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    fields = [
        "source_id", "logical_code", "name", "entrypoint", "physical_source_id",
        "prior_delivery_status", "prior_operational_status",
        "baseline_raw_records", "baseline_legacy_records",
        "audit_method", "audit_classification", "coverage_ratio",
        "reference_new_candidates", "baseline_not_observed_now",
        "evidence_path", "note",
    ]
    with (root / "FINAL_MATRIX_52.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({key: row.get(key) for key in fields})

    lines = [
        f"# Matriz Final 52/52 — {output_run}",
        "",
        f"- Fuentes logicas auditadas: **{payload['logical_sources']} / 52**",
        f"- Fuentes fisicas unicas representadas: **{payload['unique_physical_sources']}**",
        "",
        "## Estado previo de entrega",
        "",
    ]
    for key, value in payload["prior_delivery_counts"].items():
        lines.append(f"- {key}: **{value}**")
    comp = payload["comparable_origin_recheck"]
    lines += [
        "",
        "## Agregado comparable de origin-rechecks",
        "",
        f"- Fuentes fisicas comparables: **{comp['physical_sources']}**",
        f"- Matched raw: **{comp['matched_raw']}**",
        f"- Raw recheckable: **{comp['raw_recheckable']}**",
        f"- Overlap agregado: **{comp['aggregate_overlap_ratio']}**",
        "",
        "> Este porcentaje NO es una nota global del Prospector. Solo agrega fuentes con origin-recheck comparable.",
        "",
        "## Matriz",
        "",
        "| # | Source | Fisica | Previo | Metodo | Clasificacion auditada | Cobertura comparable |",
        "|---:|---|---|---|---|---|---:|",
    ]
    for idx, row in enumerate(payload["rows"], start=1):
        coverage = "-" if row["coverage_ratio"] is None else f"{row['coverage_ratio']:.4f}"
        lines.append(
            f"| {idx} | {row['source_id']} | {row['physical_source_id']} | "
            f"{row['prior_delivery_status']} | {row['audit_method']} | "
            f"{row['audit_classification']} | {coverage} |"
        )
    (root / "FINAL_MATRIX_52.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload
