from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import yaml

from src.persistence.model import (
    FAILED,
    RUNNING,
    SUCCEEDED,
    assert_plan_compatible,
    new_checkpoint,
    recover_interrupted,
    resume_candidates,
    transition,
)


IDENTITY_KEYS = (
    "source_id",
    "logical_code",
    "code",
    "id",
    "name",
    "nombre",
)
URL_KEYS = (
    "url",
    "base_url",
    "start_url",
    "entrypoint",
    "site_url",
    "source_url",
)


def load_yaml(path: str | Path) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_plan(path: str | Path) -> dict[str, Any]:
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise ValueError("Plan operacional inválido")
    rows = value.get("sources")
    if not isinstance(rows, list) or len(rows) != 52:
        raise ValueError("Plan operacional debe contener 52 fuentes")
    return value


def _container(config: Any) -> tuple[str, Any]:
    if isinstance(config, list):
        return "top_list", config

    if not isinstance(config, dict):
        raise ValueError("Config de fuentes debe ser mapping o list")

    sources = config.get("sources")
    if isinstance(sources, list):
        return "sources_list", sources
    if isinstance(sources, dict):
        return "sources_dict", sources

    raise ValueError(
        "No se encontró contenedor de fuentes compatible "
        "(list, sources:list o sources:mapping)"
    )


def iter_entries(config: Any) -> list[tuple[str | None, dict[str, Any]]]:
    kind, container = _container(config)

    if kind in {"top_list", "sources_list"}:
        result: list[tuple[str | None, dict[str, Any]]] = []
        for item in container:
            if isinstance(item, dict):
                result.append((None, item))
        return result

    result = []
    for key, item in container.items():
        if isinstance(item, dict):
            result.append((str(key), item))
    return result


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _identity_values(
    key: str | None,
    entry: dict[str, Any],
) -> set[str]:
    values = set()
    if key:
        values.add(_norm(key))
    for field in IDENTITY_KEYS:
        value = entry.get(field)
        if value is not None:
            values.add(_norm(value))
    return {value for value in values if value}


def _url_values(entry: dict[str, Any]) -> set[str]:
    values = set()
    for field in URL_KEYS:
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            values.add(value.strip().rstrip("/").casefold())
    return values


def match_source_entry(
    plan_source: dict[str, Any],
    config: Any,
) -> tuple[str | None, dict[str, Any]]:
    identity_targets = {
        _norm(plan_source.get("source_id")),
        _norm(plan_source.get("logical_code")),
        _norm(plan_source.get("name")),
    }
    identity_targets.discard("")

    entries = iter_entries(config)

    identity_matches: list[tuple[str | None, dict[str, Any]]] = []
    for key, entry in entries:
        if identity_targets & _identity_values(key, entry):
            identity_matches.append((key, entry))

    if len(identity_matches) == 1:
        return identity_matches[0]
    if len(identity_matches) > 1:
        # Prefer exact source_id first.
        source_id = _norm(plan_source.get("source_id"))
        exact = [
            item
            for item in identity_matches
            if source_id in _identity_values(item[0], item[1])
        ]
        if len(exact) == 1:
            return exact[0]
        raise ValueError(
            f"{plan_source.get('source_id')}: match ambiguo por identidad"
        )

    url_targets = set()
    for field in ("effective_entrypoint", "historical_entrypoint"):
        value = plan_source.get(field)
        if isinstance(value, str) and value.strip():
            url_targets.add(value.strip().rstrip("/").casefold())

    url_matches = [
        (key, entry)
        for key, entry in entries
        if url_targets & _url_values(entry)
    ]

    if len(url_matches) == 1:
        return url_matches[0]
    if len(url_matches) > 1:
        raise ValueError(
            f"{plan_source.get('source_id')}: match ambiguo por URL"
        )

    raise KeyError(
        f"{plan_source.get('source_id')}: no existe en config de fuentes"
    )


def build_single_source_config(
    config: Any,
    matched_key: str | None,
    matched_entry: dict[str, Any],
) -> Any:
    kind, _ = _container(config)
    entry = deepcopy(matched_entry)

    if kind == "top_list":
        return [entry]

    result = deepcopy(config)
    if kind == "sources_list":
        result["sources"] = [entry]
        return result

    key = matched_key
    if key is None:
        raise ValueError("sources:mapping requiere key")
    result["sources"] = {key: entry}
    return result


def operational_sources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in plan.get("sources", [])
        if isinstance(row, dict)
        and row.get("next_phase") == "OPERATIONAL_CONFIG"
    ]


def validate_source_mapping(
    plan: dict[str, Any],
    source_config: Any,
) -> dict[str, Any]:
    rows = operational_sources(plan)
    matched: list[str] = []
    failures: list[dict[str, str]] = []

    for row in rows:
        source_id = str(row.get("source_id"))
        try:
            match_source_entry(row, source_config)
            matched.append(source_id)
        except Exception as exc:
            failures.append(
                {
                    "source_id": source_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return {
        "operational_sources": len(rows),
        "matched_sources": len(matched),
        "unmatched_sources": len(failures),
        "matched_source_ids": matched,
        "failures": failures,
    }


def default_executor(
    *,
    python_executable: str,
    single_config_path: Path,
    output_dir: Path,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        python_executable,
        "-m",
        "apps.crawler_batch.main",
        "--config",
        str(single_config_path),
        "--output-dir",
        str(output_dir),
    ]
    print("> " + subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command)
    return int(completed.returncode)


def run_checkpointed_sources(
    *,
    plan: dict[str, Any],
    source_config: Any,
    checkpoint: dict[str, Any],
    store: Any,
    work_dir: Path,
    output_root: Path,
    max_sources: int | None = None,
    fail_fast: bool = False,
    executor: Callable[..., int] = default_executor,
    python_executable: str = sys.executable,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assert_plan_compatible(checkpoint, plan)

    checkpoint, recovered = recover_interrupted(checkpoint)
    if recovered:
        store.save(checkpoint)

    plan_index = {
        row["source_id"]: row
        for row in operational_sources(plan)
    }

    candidates = [
        source_id
        for source_id in resume_candidates(
            checkpoint,
            include_failed=True,
        )
        if source_id in plan_index
    ]

    if max_sources is not None:
        if max_sources <= 0:
            raise ValueError("max_sources debe ser > 0")
        candidates = candidates[:max_sources]

    work_dir.mkdir(parents=True, exist_ok=True)
    configs_dir = work_dir / "source_configs"
    configs_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    failed = 0
    executed: list[str] = []

    for index, source_id in enumerate(candidates, 1):
        source = plan_index[source_id]
        matched_key, matched_entry = match_source_entry(
            source,
            source_config,
        )
        single_config = build_single_source_config(
            source_config,
            matched_key,
            matched_entry,
        )

        config_path = configs_dir / f"{source_id}.yaml"
        config_path.write_text(
            yaml.safe_dump(
                single_config,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            ),
            encoding="utf-8",
        )

        source_output = output_root / source_id

        print(
            f"[{index:02d}/{len(candidates):02d}] "
            f"{source.get('logical_code') or source_id} "
            f"({source_id})",
            flush=True,
        )

        checkpoint = transition(
            checkpoint,
            source_id,
            RUNNING,
            metadata={
                "single_config": str(config_path),
                "output_dir": str(source_output),
            },
        )
        store.save(checkpoint)

        exit_code = executor(
            python_executable=python_executable,
            single_config_path=config_path,
            output_dir=source_output,
        )

        executed.append(source_id)

        if exit_code == 0:
            checkpoint = transition(
                checkpoint,
                source_id,
                SUCCEEDED,
                metadata={"exit_code": 0},
            )
            succeeded += 1
        else:
            checkpoint = transition(
                checkpoint,
                source_id,
                FAILED,
                error=f"crawler_batch_exit_code_{exit_code}",
                metadata={"exit_code": exit_code},
            )
            failed += 1

        store.save(checkpoint)

        if exit_code != 0 and fail_fast:
            break

    report = {
        "run_id": checkpoint["run_id"],
        "recovered_interrupted": recovered,
        "candidate_count": len(candidates),
        "executed_source_ids": executed,
        "succeeded": succeeded,
        "failed": failed,
        "remaining_resume_candidates": resume_candidates(
            checkpoint,
            include_failed=True,
        ),
    }

    return checkpoint, report


def initialize_or_resume(
    *,
    plan: dict[str, Any],
    store: Any,
    run_id: str | None,
    resume: bool,
) -> tuple[dict[str, Any], bool]:
    if resume:
        checkpoint = store.load(run_id)
        assert_plan_compatible(checkpoint, plan)
        return checkpoint, False

    checkpoint = new_checkpoint(
        plan,
        run_id=run_id,
    )
    store.save(checkpoint)
    return checkpoint, True


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
