from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.site_validation.core import (
    AuditRoster,
    IndependentReferenceProbe,
    baseline_legacy,
    baseline_raw,
    compare_urls,
    write_audit_output,
)
from src.site_validation.batch import build_physical_targets, run_operational_batch
from src.site_validation.a7 import build_a7_plan, run_a7_origin_batch
from src.site_validation.specialized import run_specialized_batch
from src.site_validation.status_only import run_status_only_batch, status_only_sources
from src.site_validation.a9 import SPECIAL_CASES, run_a9_special_cases
from src.site_validation.final_matrix import write_final_matrix
from src.site_validation.final_report import write_final_report
from src.site_validation.origin_recheck import (
    IndependentOriginRechecker,
    compare_origin_recheck,
    load_allowed_extensions,
    write_origin_recheck_output,
)



def command_plan(repo_root: Path) -> int:
    roster = AuditRoster(repo_root)
    groups = roster.surface_groups()
    sources = roster.all_sources()
    operational = [source for source in sources if source.next_phase == "OPERATIONAL_CONFIG"]
    status_only = [source for source in sources if source.next_phase != "OPERATIONAL_CONFIG"]
    payload = {
        "logical_sources": len(sources),
        "unique_effective_surfaces": len(groups),
        "operational_logical_sources": len(operational),
        "status_only_logical_sources": len(status_only),
        "surface_groups": groups,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_run(
    repo_root: Path,
    *,
    source_id: str,
    baseline_run: str,
    run_id: str,
    max_pages: int,
    max_resources: int,
    timeout: float,
    delay_seconds: float,
) -> int:
    roster = AuditRoster(repo_root)
    source = roster.source(source_id)
    probe = IndependentReferenceProbe(
        max_pages=max_pages,
        max_resources=max_resources,
        timeout=timeout,
        delay_seconds=delay_seconds,
    )

    print("=" * 78)
    print(f"SITE VALIDATION — {source.logical_code} ({source.source_id})")
    print("=" * 78)
    print(f"Entrypoint:     {source.entrypoint}")
    print(f"Status previo:  {source.operational_status}")
    print(f"Baseline run:   {baseline_run}")
    print(f"Audit run:      {run_id}")
    print()

    reference, stats, meta = probe.run(source.entrypoint)
    raw = baseline_raw(repo_root, baseline_run, source.source_id)
    legacy = baseline_legacy(repo_root, baseline_run, source.source_id)
    comparison = compare_urls(reference, raw, legacy, base_url=source.entrypoint)

    out = repo_root / "output" / "site-validation" / run_id / source.source_id
    write_audit_output(
        output_dir=out,
        source=source,
        reference=reference,
        stats=stats,
        meta=meta,
        raw=raw,
        legacy=legacy,
        comparison=comparison,
    )

    counts = comparison["counts"]
    print(f"Reference candidates:      {counts['reference_candidates']}")
    print(f"B13 raw records:           {counts['baseline_raw_records']}")
    print(f"B13 raw unique URLs:       {counts['baseline_raw']}")
    print(f"B13 legacy records:        {counts['baseline_legacy_records']}")
    print(f"B13 legacy unique URLs:    {counts['baseline_legacy']}")
    print(f"Reference ∩ raw:           {counts['reference_matched_raw']}")
    print(f"Reference only (vs raw):   {counts['reference_only_raw']}")
    print(f"Raw only (vs reference):   {counts['raw_only_reference']}")
    print(f"Reference ∩ legacy:        {counts['reference_matched_legacy']}")
    print(f"Reference only (legacy):   {counts['reference_only_legacy']}")
    print()
    print(f"Output: {out}")
    print("Interpretation: PRELIMINARY — differences require A5 adjudication.")
    return 0


def command_recheck_origins(
    repo_root: Path,
    *,
    source_id: str,
    baseline_run: str,
    run_id: str,
    timeout: float,
    delay_seconds: float,
) -> int:
    roster = AuditRoster(repo_root)
    source = roster.source(source_id)
    raw = baseline_raw(repo_root, baseline_run, source.source_id)
    legacy = baseline_legacy(repo_root, baseline_run, source.source_id)
    allowed_extensions = load_allowed_extensions(
        repo_root, baseline_run, source.source_id, source.config_source_id
    )
    rechecker = IndependentOriginRechecker(timeout=timeout, delay_seconds=delay_seconds)

    print("=" * 78)
    print(f"ORIGIN RECHECK — {source.logical_code} ({source.source_id})")
    print("=" * 78)
    print(f"Entrypoint:     {source.entrypoint}")
    print(f"Baseline run:   {baseline_run}")
    print(f"Audit run:      {run_id}")
    print(f"Raw records:    {len(raw)}")
    print()

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
    out = repo_root / "output" / "site-validation" / run_id / source.source_id
    write_origin_recheck_output(
        output_dir=out,
        source_id=source.source_id,
        entrypoint=source.entrypoint,
        reference=reference,
        stats=stats,
        meta=meta,
        comparison=comparison,
    )

    c = comparison["counts"]
    print(f"Origin pages baseline:                {stats.origin_pages_total}")
    print(f"Origin pages rechecked:               {stats.origin_pages_rechecked}")
    print(f"Origin pages failed:                  {stats.origin_pages_failed}")
    print(f"Origin pages blocked robots:          {stats.origin_pages_blocked_robots}")
    print(f"Direct reference unique URLs:         {c['reference_direct_unique']}")
    print(f"Raw URLs on rechecked origins:        {c['raw_recheckable_unique']}")
    print(f"Direct reference ∩ raw:               {c['reference_matched_raw']}")
    print(f"Direct reference only vs raw:         {c['reference_only_raw']}")
    print(f"Raw from rechecked origins not seen:  {c['raw_recheckable_not_observed']}")
    print(f"Direct reference ∩ legacy:            {c['reference_matched_legacy']}")
    print()
    print(f"Output: {out}")
    print("Interpretation: ORIGIN-LEVEL — reference-only items are new-or-missed candidates.")
    return 0



def command_batch(
    repo_root: Path,
    *,
    baseline_run: str,
    run_id: str,
    workers: int,
    max_pages: int,
    max_resources: int,
    timeout: float,
    delay_seconds: float,
    resume: bool,
    dry_run: bool,
) -> int:
    roster = AuditRoster(repo_root)
    targets = build_physical_targets(roster, include_special=False)
    if dry_run:
        payload = {
            "physical_targets": len(targets),
            "logical_sources_represented": len({sid for target in targets for sid in target.logical_source_ids}),
            "excluded_for_a9": ["mhe", "sigma", "transtats"],
            "targets": [
                {
                    "physical_source_id": target.physical_source_id,
                    "representative_source_id": target.representative_source_id,
                    "logical_source_ids": list(target.logical_source_ids),
                    "entrypoint": target.entrypoint,
                }
                for target in targets
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    payload = run_operational_batch(
        repo_root=repo_root,
        baseline_run=baseline_run,
        run_id=run_id,
        workers=workers,
        max_pages=max_pages,
        max_resources=max_resources,
        timeout=timeout,
        delay_seconds=delay_seconds,
        resume=resume,
        include_special=False,
    )
    print()
    print("=" * 78)
    print("A6 OPERATIONAL BATCH SUMMARY")
    print("=" * 78)
    print(f"Physical targets:             {payload['physical_targets']}")
    print(f"Logical sources represented:  {payload['logical_sources_represented']}")
    print(f"Completed:                    {payload['completed']}")
    print(f"Skipped existing:             {payload['skipped_existing']}")
    print(f"Errors:                       {payload['errors']}")
    print(f"Output: {repo_root / 'output' / 'site-validation' / run_id}")
    return 0 if payload["errors"] == 0 else 2







def command_a11(
    repo_root: Path,
    *,
    matrix_run: str,
    output_run: str,
) -> int:
    payload = write_final_report(
        repo_root=repo_root,
        matrix_run=matrix_run,
        output_run=output_run,
    )
    summary = payload["summary"]
    comp = summary["comparable_origin_recheck"]
    print()
    print("=" * 78)
    print("A11 FINAL EFFECTIVENESS REPORT")
    print("=" * 78)
    print(f"Logical sources audited:      {summary['logical_sources_audited']} / 52")
    print(f"Unique physical sources:      {summary['unique_physical_sources']}")
    print(
        "Comparable origin overlap:   "
        f"{comp.get('matched_raw')}/{comp.get('raw_recheckable')} = "
        f"{comp.get('aggregate_overlap_ratio')}"
    )
    print(
        "Review for promotion:        "
        + (", ".join(summary["review_for_promotion_sources"]) or "none")
    )
    print(
        "Access review:               "
        + (", ".join(summary["access_review_sources"]) or "none")
    )
    print(f"Report: {payload['report_path']}")
    print(f"Summary: {payload['summary_path']}")
    return 0
def command_a10(
    repo_root: Path,
    *,
    output_run: str,
    baseline_run: str,
    batch_run: str,
    a7_run: str,
    specialized_run: str,
    a8_run: str,
    a9_run: str,
    bcb_origin_run: str,
) -> int:
    payload = write_final_matrix(
        repo_root=repo_root,
        output_run=output_run,
        baseline_run=baseline_run,
        batch_run=batch_run,
        a7_run=a7_run,
        specialized_run=specialized_run,
        a8_run=a8_run,
        a9_run=a9_run,
        bcb_origin_run=bcb_origin_run,
    )
    print()
    print("=" * 78)
    print("A10 FINAL MATRIX 52/52")
    print("=" * 78)
    print(f"Logical sources:          {payload['logical_sources']} / 52")
    print(f"Unique physical sources:  {payload['unique_physical_sources']}")
    print("Prior delivery:", payload["prior_delivery_counts"])
    print("Audit methods:", payload["audit_method_counts"])
    print("Audit classifications:", payload["audit_classification_counts"])
    comp = payload["comparable_origin_recheck"]
    print(
        "Comparable origin overlap: "
        f"{comp['matched_raw']}/{comp['raw_recheckable']} = "
        f"{comp['aggregate_overlap_ratio']}"
    )
    print(f"Output: {repo_root / 'output' / 'site-validation' / output_run}")
    return 0
def command_a9(
    repo_root: Path,
    *,
    baseline_run: str,
    run_id: str,
    timeout: float,
    dry_run: bool,
) -> int:
    if dry_run:
        print(json.dumps({"targets": len(SPECIAL_CASES), "source_ids": list(SPECIAL_CASES)},
                         ensure_ascii=False, indent=2))
        return 0
    payload = run_a9_special_cases(
        repo_root=repo_root,
        baseline_run=baseline_run,
        run_id=run_id,
        timeout=timeout,
    )
    print()
    print("=" * 78)
    print("A9 SPECIAL CASES SUMMARY")
    print("=" * 78)
    print(f"Targets: {payload['targets']}")
    for row in payload["results"]:
        print(f"[{row['classification']:<50}] {row['source_id']}")
        if row["source_id"] == "transtats":
            print(f"  contract_ok={row['contract_ok']} forms={row['forms_detected']} "
                  f"GET={row['current_get']['status_code'] or row['current_get']['error_type']}")
        else:
            print(f"  dns={row['dns']['ok']} "
                  f"home={row['homepage']['status_code'] or row['homepage']['error_type']} "
                  f"robots={row['robots']['status_code'] or row['robots']['error_type']}")
    print("Safety: verify_false=False, robots_bypass=False, POST=False")
    print(f"Output: {repo_root / 'output' / 'site-validation' / run_id}")
    return 0

def command_a8(
    repo_root: Path,
    *,
    run_id: str,
    workers: int,
    max_pages: int,
    max_resources: int,
    timeout: float,
    delay_seconds: float,
    resume: bool,
    dry_run: bool,
) -> int:
    roster = AuditRoster(repo_root)
    targets = status_only_sources(roster)
    if dry_run:
        print(json.dumps({"targets": len(targets), "source_ids": targets},
                         ensure_ascii=False, indent=2))
        return 0

    payload = run_status_only_batch(
        repo_root=repo_root,
        run_id=run_id,
        workers=workers,
        max_pages=max_pages,
        max_resources=max_resources,
        timeout=timeout,
        delay_seconds=delay_seconds,
        resume=resume,
    )
    print()
    print("=" * 78)
    print("A8 STATUS_ONLY VALIDATION SUMMARY")
    print("=" * 78)
    print(f"Targets:             {payload['targets']}")
    print(f"Completed:           {payload['completed']}")
    print(f"Skipped existing:    {payload['skipped_existing']}")
    print(f"Errors:              {payload['errors']}")
    for key, value in sorted(payload["classification_counts"].items()):
        print(f"{key:<28} {value}")
    print(f"Output: {repo_root / 'output' / 'site-validation' / run_id}")
    return 0 if payload["errors"] == 0 else 2

def command_a7_specialized(
    repo_root: Path,
    *,
    baseline_run: str,
    run_id: str,
    timeout: float,
    delay_seconds: float,
) -> int:
    payload = run_specialized_batch(
        repo_root=repo_root,
        baseline_run=baseline_run,
        run_id=run_id,
        timeout=timeout,
        delay_seconds=delay_seconds,
    )
    print()
    print("=" * 78)
    print("A7.2 SPECIALIZED VALIDATION SUMMARY")
    print("=" * 78)
    print(f"Targets:                              {payload['targets']}")
    print(f"Validated:                            {payload['validated']}")
    print(f"Validated temporal differences:       {payload['validated_with_temporal_differences']}")
    print(f"Partial reachability:                 {payload['partial_reachability']}")
    print(f"Review required:                      {payload['review_required']}")
    for row in payload["results"]:
        print(
            f"[{row['status']:<35}] {row['source_id']:<20} "
            f"workflow={row['effective_workflow']:<10} mode={row['validation_mode']}"
        )
    print(f"Output: {repo_root / 'output' / 'site-validation' / run_id}")
    return 0


def command_a7(
    repo_root: Path,
    *,
    batch_run: str,
    baseline_run: str,
    run_id: str,
    workers: int,
    timeout: float,
    delay_seconds: float,
    resume: bool,
    threshold: float,
    dry_run: bool,
) -> int:
    plan = build_a7_plan(
        repo_root=repo_root,
        batch_run=batch_run,
        baseline_run=baseline_run,
        threshold=threshold,
    )
    if dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    payload = run_a7_origin_batch(
        repo_root=repo_root,
        batch_run=batch_run,
        baseline_run=baseline_run,
        run_id=run_id,
        workers=workers,
        timeout=timeout,
        delay_seconds=delay_seconds,
        resume=resume,
        threshold=threshold,
    )
    print()
    print("=" * 78)
    print("A7 TRIAGE + ORIGIN RECHECK SUMMARY")
    print("=" * 78)
    print(f"Physical sources classified:  {payload['plan']['physical_sources']}")
    print(f"Origin recheck targets:        {payload['targets']}")
    print(f"Specialized targets:           {len(payload['plan']['specialized_targets'])}")
    print(f"Completed:                     {payload['completed']}")
    print(f"Skipped existing:              {payload['skipped_existing']}")
    print(f"Errors:                        {payload['errors']}")
    print(f"Output: {repo_root / 'output' / 'site-validation' / run_id}")
    return 0 if payload["errors"] == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditor independiente de cobertura por sitio para Prospector Externo."
    )
    parser.add_argument("--repo-root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("plan")

    run = sub.add_parser("run")
    run.add_argument("--source", required=True)
    run.add_argument("--baseline-run", default="b13-e2e")
    run.add_argument("--run-id", required=True)
    run.add_argument("--max-pages", type=int, default=150)
    run.add_argument("--max-resources", type=int, default=5000)
    run.add_argument("--timeout", type=float, default=15.0)
    run.add_argument("--delay-seconds", type=float, default=0.05)


    batch = sub.add_parser("batch")
    batch.add_argument("--baseline-run", default="b13-e2e")
    batch.add_argument("--run-id", required=True)
    batch.add_argument("--workers", type=int, default=4)
    batch.add_argument("--max-pages", type=int, default=120)
    batch.add_argument("--max-resources", type=int, default=5000)
    batch.add_argument("--timeout", type=float, default=15.0)
    batch.add_argument("--delay-seconds", type=float, default=0.05)
    batch.add_argument("--resume", action="store_true")
    batch.add_argument("--dry-run", action="store_true")

    a11 = sub.add_parser("a11-report")
    a11.add_argument("--matrix-run", default="audit-final-001")
    a11.add_argument("--output-run", default="audit-final-001")

    a10 = sub.add_parser("a10-matrix")
    a10.add_argument("--output-run", default="audit-final-001")
    a10.add_argument("--baseline-run", default="b13-e2e")
    a10.add_argument("--batch-run", default="audit-operational-001")
    a10.add_argument("--a7-run", default="audit-a7-001")
    a10.add_argument("--specialized-run", default="audit-a7-specialized-001")
    a10.add_argument("--a8-run", default="audit-a8-status-001")
    a10.add_argument("--a9-run", default="audit-a9-special-001")
    a10.add_argument("--bcb-origin-run", default="audit-bcb-origin-001")

    a9 = sub.add_parser("a9-special-cases")
    a9.add_argument("--baseline-run", default="b13-e2e")
    a9.add_argument("--run-id", required=True)
    a9.add_argument("--timeout", type=float, default=20.0)
    a9.add_argument("--dry-run", action="store_true")

    a8 = sub.add_parser("a8-status-only")
    a8.add_argument("--run-id", required=True)
    a8.add_argument("--workers", type=int, default=4)
    a8.add_argument("--max-pages", type=int, default=80)
    a8.add_argument("--max-resources", type=int, default=3000)
    a8.add_argument("--timeout", type=float, default=15.0)
    a8.add_argument("--delay-seconds", type=float, default=0.05)
    a8.add_argument("--resume", action="store_true")
    a8.add_argument("--dry-run", action="store_true")

    a72 = sub.add_parser("a7-specialized")
    a72.add_argument("--baseline-run", default="b13-e2e")
    a72.add_argument("--run-id", required=True)
    a72.add_argument("--timeout", type=float, default=20.0)
    a72.add_argument("--delay-seconds", type=float, default=0.10)

    a7 = sub.add_parser("a7")
    a7.add_argument("--batch-run", default="audit-operational-001")
    a7.add_argument("--baseline-run", default="b13-e2e")
    a7.add_argument("--run-id", required=True)
    a7.add_argument("--workers", type=int, default=4)
    a7.add_argument("--timeout", type=float, default=20.0)
    a7.add_argument("--delay-seconds", type=float, default=0.10)
    a7.add_argument("--threshold", type=float, default=0.80)
    a7.add_argument("--resume", action="store_true")
    a7.add_argument("--dry-run", action="store_true")

    origin = sub.add_parser("recheck-origins")
    origin.add_argument("--source", required=True)
    origin.add_argument("--baseline-run", default="b13-e2e")
    origin.add_argument("--run-id", required=True)
    origin.add_argument("--timeout", type=float, default=20.0)
    origin.add_argument("--delay-seconds", type=float, default=0.10)

    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()

    if args.command == "plan":
        return command_plan(repo_root)
    if args.command == "run":
        return command_run(
            repo_root,
            source_id=args.source,
            baseline_run=args.baseline_run,
            run_id=args.run_id,
            max_pages=args.max_pages,
            max_resources=args.max_resources,
            timeout=args.timeout,
            delay_seconds=args.delay_seconds,
        )
    if args.command == "batch":
        return command_batch(
            repo_root,
            baseline_run=args.baseline_run,
            run_id=args.run_id,
            workers=args.workers,
            max_pages=args.max_pages,
            max_resources=args.max_resources,
            timeout=args.timeout,
            delay_seconds=args.delay_seconds,
            resume=args.resume,
            dry_run=args.dry_run,
        )
    if args.command == "a11-report":
        return command_a11(
            repo_root,
            matrix_run=args.matrix_run,
            output_run=args.output_run,
        )
    if args.command == "a10-matrix":
        return command_a10(
            repo_root,
            output_run=args.output_run,
            baseline_run=args.baseline_run,
            batch_run=args.batch_run,
            a7_run=args.a7_run,
            specialized_run=args.specialized_run,
            a8_run=args.a8_run,
            a9_run=args.a9_run,
            bcb_origin_run=args.bcb_origin_run,
        )
    if args.command == "a9-special-cases":
        return command_a9(
            repo_root,
            baseline_run=args.baseline_run,
            run_id=args.run_id,
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
    if args.command == "a8-status-only":
        return command_a8(
            repo_root,
            run_id=args.run_id,
            workers=args.workers,
            max_pages=args.max_pages,
            max_resources=args.max_resources,
            timeout=args.timeout,
            delay_seconds=args.delay_seconds,
            resume=args.resume,
            dry_run=args.dry_run,
        )
    if args.command == "a7-specialized":
        return command_a7_specialized(
            repo_root,
            baseline_run=args.baseline_run,
            run_id=args.run_id,
            timeout=args.timeout,
            delay_seconds=args.delay_seconds,
        )
    if args.command == "a7":
        return command_a7(
            repo_root,
            batch_run=args.batch_run,
            baseline_run=args.baseline_run,
            run_id=args.run_id,
            workers=args.workers,
            timeout=args.timeout,
            delay_seconds=args.delay_seconds,
            resume=args.resume,
            threshold=args.threshold,
            dry_run=args.dry_run,
        )
    if args.command == "recheck-origins":
        return command_recheck_origins(
            repo_root,
            source_id=args.source,
            baseline_run=args.baseline_run,
            run_id=args.run_id,
            timeout=args.timeout,
            delay_seconds=args.delay_seconds,
        )
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
