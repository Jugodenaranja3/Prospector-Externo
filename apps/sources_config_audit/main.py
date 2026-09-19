from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


IDENTITY_KEYS = (
    "source_id",
    "logical_code",
    "code",
    "id",
    "name",
    "nombre",
    "slug",
    "key",
)
URL_KEYS = (
    "url",
    "base_url",
    "start_url",
    "entrypoint",
    "site_url",
    "source_url",
    "historical_entrypoint",
    "effective_entrypoint",
)


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def norm_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").casefold()


def source_container(value: Any) -> tuple[str, Any] | None:
    if isinstance(value, list):
        return "top_list", value
    if not isinstance(value, dict):
        return None
    sources = value.get("sources")
    if isinstance(sources, list):
        return "sources_list", sources
    if isinstance(sources, dict):
        return "sources_dict", sources
    return None


def iter_entries(value: Any) -> list[tuple[str | None, dict[str, Any]]]:
    container = source_container(value)
    if container is None:
        return []
    kind, rows = container

    result: list[tuple[str | None, dict[str, Any]]] = []
    if kind in {"top_list", "sources_list"}:
        for row in rows:
            if isinstance(row, dict):
                result.append((None, row))
        return result

    for key, row in rows.items():
        if isinstance(row, dict):
            result.append((str(key), row))
    return result


def identity_values(key: str | None, row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if key:
        values.append(key)
    for field in IDENTITY_KEYS:
        value = row.get(field)
        if value is not None and str(value).strip():
            values.append(str(value))
    return list(dict.fromkeys(values))


def url_values(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in URL_KEYS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return list(dict.fromkeys(values))


def row_summary(key: str | None, row: dict[str, Any]) -> dict[str, Any]:
    safe_scalar_fields = {}
    for field in (
        "workflow",
        "workflow_strategy",
        "type",
        "kind",
        "enabled",
        "active",
        "discover_apis",
        "name",
        "nombre",
        "source_id",
        "logical_code",
        "code",
        "id",
        "slug",
    ):
        value = row.get(field)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if field in row:
                safe_scalar_fields[field] = value

    return {
        "mapping_key": key,
        "identities": identity_values(key, row),
        "urls": url_values(row),
        "keys": sorted(str(k) for k in row.keys()),
        "safe_scalar_fields": safe_scalar_fields,
    }


def match_plan_source(
    plan_row: dict[str, Any],
    config_entries: list[tuple[str | None, dict[str, Any]]],
) -> list[dict[str, Any]]:
    identity_targets = {
        norm(plan_row.get("source_id")),
        norm(plan_row.get("logical_code")),
        norm(plan_row.get("name")),
    }
    identity_targets.discard("")

    url_targets = {
        norm_url(plan_row.get("effective_entrypoint")),
        norm_url(plan_row.get("historical_entrypoint")),
    }
    url_targets.discard("")

    matches = []
    for key, row in config_entries:
        ids = {norm(v) for v in identity_values(key, row)}
        urls = {norm_url(v) for v in url_values(row)}
        id_overlap = sorted(identity_targets & ids)
        url_overlap = sorted(url_targets & urls)
        if id_overlap or url_overlap:
            matches.append(
                {
                    "mapping_key": key,
                    "identity_overlap": id_overlap,
                    "url_overlap": url_overlap,
                    "identities": identity_values(key, row),
                    "urls": url_values(row),
                }
            )
    return matches


def summarize_yaml_registry(path: Path) -> dict[str, Any] | None:
    try:
        value = load_yaml(path)
    except Exception as exc:
        return {
            "path": str(path),
            "parse_error": f"{type(exc).__name__}: {exc}",
        }

    container = source_container(value)
    entries = iter_entries(value)

    if container is None and not entries:
        # Still recognize list/dict structures with source_id rows nested under common keys.
        candidate_counts = {}
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, list):
                    source_rows = [
                        row for row in child
                        if isinstance(row, dict) and (
                            "source_id" in row
                            or "logical_code" in row
                            or "effective_entrypoint" in row
                        )
                    ]
                    if source_rows:
                        candidate_counts[str(key)] = len(source_rows)
        if not candidate_counts:
            return None
        return {
            "path": str(path),
            "container": None,
            "entries": 0,
            "candidate_nested_source_lists": candidate_counts,
        }

    return {
        "path": str(path),
        "container": container[0] if container else None,
        "entries": len(entries),
        "sample": [
            row_summary(key, row)
            for key, row in entries[:5]
        ],
    }


def main() -> int:
    root = Path.cwd()
    sources_path = root / "config" / "sources.yaml"
    plan_path = root / "config" / "source_operational_plan.yaml"

    if not sources_path.exists():
        raise SystemExit("Falta config/sources.yaml")
    if not plan_path.exists():
        raise SystemExit("Falta config/source_operational_plan.yaml")

    sources_cfg = load_yaml(sources_path)
    plan = load_yaml(plan_path)

    if not isinstance(plan, dict):
        raise SystemExit("Plan operacional inválido")
    plan_rows = plan.get("sources")
    if not isinstance(plan_rows, list):
        raise SystemExit("Plan operacional sin sources")

    operational = [
        row for row in plan_rows
        if isinstance(row, dict)
        and row.get("next_phase") == "OPERATIONAL_CONFIG"
    ]

    config_entries = iter_entries(sources_cfg)

    root_shape = {
        "type": type(sources_cfg).__name__,
        "top_keys": (
            sorted(str(k) for k in sources_cfg.keys())
            if isinstance(sources_cfg, dict)
            else None
        ),
        "container": (
            source_container(sources_cfg)[0]
            if source_container(sources_cfg)
            else None
        ),
        "entry_count": len(config_entries),
    }

    matches = []
    unmatched = []
    ambiguous = []

    for row in operational:
        found = match_plan_source(row, config_entries)
        item = {
            "source_id": row.get("source_id"),
            "logical_code": row.get("logical_code"),
            "name": row.get("name"),
            "effective_entrypoint": row.get("effective_entrypoint"),
            "historical_entrypoint": row.get("historical_entrypoint"),
            "operational_status": row.get("operational_status"),
            "workflow_strategy": row.get("workflow_strategy"),
            "matches": found,
        }
        if len(found) == 1:
            matches.append(item)
        elif len(found) == 0:
            unmatched.append(item)
        else:
            ambiguous.append(item)

    registries = []
    for path in sorted((root / "config").glob("*.y*ml")):
        summary = summarize_yaml_registry(path)
        if summary is not None:
            registries.append(summary)

    consumers = []
    needles = (
        'get("sources")',
        "['sources']",
        '["sources"]',
        "sources.yaml",
        "yaml.safe_load",
    )
    for base in (root / "src", root / "apps"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            hit = [needle for needle in needles if needle in text]
            if hit:
                consumers.append(
                    {
                        "path": str(path.relative_to(root)),
                        "signals": hit,
                    }
                )

    payload = {
        "sources_yaml": root_shape,
        "sources_yaml_entries": [
            row_summary(key, row)
            for key, row in config_entries
        ],
        "plan_operational_count": len(operational),
        "matched_count": len(matches),
        "unmatched_count": len(unmatched),
        "ambiguous_count": len(ambiguous),
        "matched": matches,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "yaml_registries": registries,
        "config_consumers": consumers,
    }

    out_dir = root / ".runtime" / "sources_config_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "latest.json"
    out_json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("=" * 78)
    print("B9B.1 — SOURCES CONFIG AUDIT")
    print("=" * 78)
    print(f"config/sources.yaml root type:  {root_shape['type']}")
    print(f"container:                      {root_shape['container']}")
    print(f"physical entries:               {root_shape['entry_count']}")
    print(f"operational plan sources:       {len(operational)}")
    print(f"matched:                        {len(matches)}")
    print(f"unmatched:                      {len(unmatched)}")
    print(f"ambiguous:                      {len(ambiguous)}")
    print()

    print("Top-level keys:")
    for key in root_shape["top_keys"] or []:
        print(f"  - {key}")

    print()
    print("Physical entries in config/sources.yaml:")
    for key, row in config_entries:
        summary = row_summary(key, row)
        identities = ", ".join(summary["identities"]) or "-"
        urls = ", ".join(summary["urls"]) or "-"
        print(f"  key={key!r}")
        print(f"    identities: {identities}")
        print(f"    urls:       {urls}")
        print(f"    keys:       {', '.join(summary['keys'])}")

    print()
    print("Matched operational sources:")
    for item in matches:
        match = item["matches"][0]
        print(
            f"  {item['source_id']:<24} "
            f"{item.get('logical_code') or '-':<24} "
            f"-> key={match.get('mapping_key')!r} "
            f"ids={match.get('identity_overlap')} "
            f"urls={match.get('url_overlap')}"
        )

    print()
    print("Unmatched operational sources:")
    for item in unmatched:
        print(
            f"  {item['source_id']:<24} "
            f"{item.get('logical_code') or '-':<24} "
            f"{item.get('effective_entrypoint')}"
        )

    if ambiguous:
        print()
        print("Ambiguous:")
        for item in ambiguous:
            print(
                f"  {item['source_id']}: {len(item['matches'])} matches"
            )

    print()
    print("YAML registries/source-like configs:")
    for item in registries:
        print(
            f"  {item['path']}: "
            f"container={item.get('container')} "
            f"entries={item.get('entries')} "
            f"nested={item.get('candidate_nested_source_lists')}"
        )

    print()
    print("Potential config consumers:")
    for item in consumers:
        print(
            f"  {item['path']}: {', '.join(item['signals'])}"
        )

    print()
    print(f"JSON: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
