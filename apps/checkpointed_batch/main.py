from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.execution.checkpointed_batch import (
    initialize_or_resume,
    load_execution_map,
    load_plan,
    load_yaml,
    run_checkpointed_sources,
    validate_source_mapping,
    write_report,
)
from src.persistence.factory import create_store, load_config
from src.persistence.model import summary


def print_checkpoint_summary(checkpoint: dict) -> None:
    print()
    print("Checkpoint:")
    for key, count in summary(checkpoint).items():
        print(f"  {key:<20} {count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta crawler_batch por fuente con checkpoint/resume B9."
        )
    )
    parser.add_argument(
        "--plan",
        default="config/source_operational_plan.yaml",
    )
    parser.add_argument(
        "--sources-config",
        default="config/sources.yaml",
    )
    parser.add_argument(
        "--execution-map",
        default="config/source_execution_map.yaml",
    )
    parser.add_argument(
        "--checkpoint-config",
        default="config/checkpointing.yaml",
    )
    parser.add_argument(
        "--backend",
        choices=["json", "mongo"],
        default=None,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    validate_cmd = sub.add_parser("validate")
    validate_cmd.add_argument(
        "--report",
        default=".runtime/checkpointed_batch/mapping_report.json",
    )

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--run-id", default=None)
    run_cmd.add_argument("--resume", action="store_true")
    run_cmd.add_argument("--max-sources", type=int, default=None)
    run_cmd.add_argument("--fail-fast", action="store_true")
    run_cmd.add_argument(
        "--work-dir",
        default=".runtime/checkpointed_batch",
    )
    run_cmd.add_argument(
        "--output-dir",
        default="output/checkpointed",
    )
    run_cmd.add_argument(
        "--report",
        default=".runtime/checkpointed_batch/latest.json",
    )

    args = parser.parse_args()

    plan = load_plan(args.plan)
    source_config = load_yaml(args.sources_config)
    execution_map = load_execution_map(args.execution_map)

    if args.command == "validate":
        report = validate_source_mapping(
            plan,
            source_config,
            execution_map,
        )
        write_report(Path(args.report), report)

        print("=" * 78)
        print("B9B — SOURCE CONFIG MAPPING VALIDATION")
        print("=" * 78)
        print(
            f"Operacionales:  {report['operational_sources']}"
        )
        print(
            f"Mapeadas:       {report['matched_sources']}"
        )
        print(
            f"Sin match:      {report['unmatched_sources']}"
        )

        if report["failures"]:
            print()
            print("Fallos:")
            for item in report["failures"]:
                print(
                    f"  {item['source_id']}: {item['error']}"
                )

        print()
        print(f"JSON: {args.report}")
        return 0 if report["unmatched_sources"] == 0 else 2

    checkpoint_cfg = load_config(args.checkpoint_config)
    store = create_store(
        checkpoint_cfg,
        backend=args.backend,
    )

    checkpoint, created = initialize_or_resume(
        plan=plan,
        store=store,
        run_id=args.run_id,
        resume=args.resume,
    )

    print("=" * 78)
    print("B9B — CHECKPOINTED BATCH EXECUTION")
    print("=" * 78)
    print(f"Run ID:       {checkpoint['run_id']}")
    print(f"Nuevo run:    {'SÍ' if created else 'NO'}")
    print(f"Resume:       {'SÍ' if args.resume else 'NO'}")
    print(f"Max sources:  {args.max_sources or 'ALL'}")
    print()

    checkpoint, report = run_checkpointed_sources(
        plan=plan,
        source_config=source_config,
        checkpoint=checkpoint,
        store=store,
        work_dir=Path(args.work_dir) / checkpoint["run_id"],
        output_root=Path(args.output_dir) / checkpoint["run_id"],
        execution_map=execution_map,
        max_sources=args.max_sources,
        fail_fast=args.fail_fast,
        python_executable=sys.executable,
    )

    write_report(Path(args.report), report)

    print()
    print("=" * 78)
    print("B9B EXECUTION SUMMARY")
    print("=" * 78)
    print(f"Ejecutadas:    {len(report['executed_source_ids'])}")
    print(f"Exitosas:      {report['succeeded']}")
    print(f"Fallidas:      {report['failed']}")
    print(
        "Pendientes/retry: "
        f"{len(report['remaining_resume_candidates'])}"
    )
    print_checkpoint_summary(checkpoint)
    print()
    print(f"JSON: {args.report}")

    return 0 if report["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
