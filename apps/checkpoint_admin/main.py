from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from src.persistence.factory import create_store, load_config
from src.persistence.model import (
    FAILED,
    RUNNING,
    SUCCEEDED,
    assert_plan_compatible,
    new_checkpoint,
    recover_interrupted,
    resume_candidates,
    summary,
    transition,
)


def load_plan(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Plan operacional inválido")
    return value


def print_summary(checkpoint: dict) -> None:
    counts = summary(checkpoint)

    print("=" * 78)
    print("B9 — CHECKPOINT SUMMARY")
    print("=" * 78)
    print(f"Run ID:             {checkpoint['run_id']}")
    print(f"Fuentes lógicas:    {checkpoint['logical_sources']}")
    print(f"Operacionales:      {checkpoint['operational_sources']}")
    print(f"Status-only:         {checkpoint['status_only_sources']}")
    print()
    for key, count in counts.items():
        print(f"{key:<22} {count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Administración de checkpoints/resume del Prospector."
    )
    parser.add_argument(
        "--config",
        default="config/checkpointing.yaml",
    )
    parser.add_argument(
        "--backend",
        choices=["json", "mongo"],
        default=None,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init")
    init_cmd.add_argument(
        "--plan",
        default="config/source_operational_plan.yaml",
    )
    init_cmd.add_argument("--run-id", default=None)

    resume_cmd = sub.add_parser("resume")
    resume_cmd.add_argument(
        "--plan",
        default="config/source_operational_plan.yaml",
    )
    resume_cmd.add_argument("--run-id", default=None)
    resume_cmd.add_argument(
        "--pending-output",
        default=".runtime/checkpoints/resume_candidates.json",
    )

    summary_cmd = sub.add_parser("summary")
    summary_cmd.add_argument("--run-id", default=None)

    mark_cmd = sub.add_parser("mark")
    mark_cmd.add_argument("--run-id", default=None)
    mark_cmd.add_argument("--source-id", required=True)
    mark_cmd.add_argument(
        "--state",
        required=True,
        choices=[RUNNING, SUCCEEDED, FAILED],
    )
    mark_cmd.add_argument("--error", default=None)
    mark_cmd.add_argument("--resource-count", type=int, default=None)

    args = parser.parse_args()

    cfg = load_config(args.config)
    store = create_store(cfg, backend=args.backend)

    if args.command == "init":
        plan = load_plan(Path(args.plan))
        checkpoint = new_checkpoint(
            plan,
            run_id=args.run_id,
        )
        store.save(checkpoint)
        print_summary(checkpoint)
        print()
        print("Checkpoint creado.")
        return 0

    checkpoint = store.load(getattr(args, "run_id", None))

    if args.command == "summary":
        print_summary(checkpoint)
        return 0

    if args.command == "resume":
        plan = load_plan(Path(args.plan))
        assert_plan_compatible(checkpoint, plan)

        checkpoint, recovered = recover_interrupted(checkpoint)
        if recovered:
            store.save(checkpoint)

        pending = resume_candidates(
            checkpoint,
            include_failed=True,
        )

        output = Path(args.pending_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": checkpoint["run_id"],
                    "recovered_interrupted": recovered,
                    "candidate_count": len(pending),
                    "source_ids": pending,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        print_summary(checkpoint)
        print()
        print(f"RUNNING recuperados:  {len(recovered)}")
        print(f"Candidatas resume:    {len(pending)}")
        print(f"JSON:                 {output}")
        return 0

    if args.command == "mark":
        checkpoint = transition(
            checkpoint,
            args.source_id,
            args.state,
            error=args.error,
            resource_count=args.resource_count,
        )
        store.save(checkpoint)
        print_summary(checkpoint)
        return 0

    raise RuntimeError("Comando no soportado")


if __name__ == "__main__":
    raise SystemExit(main())
