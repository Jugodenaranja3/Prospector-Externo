from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


KEYWORDS = (
    "data", "datos", "estadistic", "statistics", "report", "reporte",
    "consulta", "consult", "download", "descarga", "export", "csv",
    "xlsx", "xls", "ods", "pdf", "dataset", "indicador", "indicator",
    "serie", "series", "buscar", "search", "filtrar", "filter",
)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def load_json_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def index_rows(rows: Any, key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(key)
        if isinstance(value, str) and value:
            result[value] = row
    return result


def classify_candidate(row: dict[str, Any]) -> str:
    b6_status = row.get("b6_status")
    b7_status = row.get("b7_status")

    if b6_status == "IDENTITY_REVIEW_REQUIRED":
        return "IDENTITY_RESEARCH_REQUIRED"
    if b6_status == "API_SEMANTIC_REVIEW":
        return "API_SEMANTIC_REVIEW"
    if b7_status == "JAVASCRIPT_NETWORK_REVIEW":
        return "BROWSER_NETWORK_SEMANTIC_REVIEW"
    if b7_status == "B8_CUSTOM_CANDIDATE":
        return "BROWSER_INTERACTION_REVIEW"
    return "CUSTOM_GENERAL_REVIEW"


def html_signals(html_path: Path) -> dict[str, Any]:
    if not html_path.exists():
        return {
            "html_available": False,
            "forms": 0,
            "selects": 0,
            "buttons": 0,
            "keyword_hits": {},
        }

    text = html_path.read_text(
        encoding="utf-8",
        errors="replace",
    )[:1_500_000]
    lower = text.lower()

    keyword_hits = {
        keyword: lower.count(keyword)
        for keyword in KEYWORDS
        if keyword in lower
    }

    return {
        "html_available": True,
        "forms": len(re.findall(r"<form\b", lower)),
        "selects": len(re.findall(r"<select\b", lower)),
        "buttons": len(re.findall(r"<button\b", lower)),
        "keyword_hits": dict(sorted(keyword_hits.items())),
    }


def browser_artifact_summary(
    source_id: str,
    browser_root: Path,
    browser_resolution: dict[str, Any] | None,
) -> dict[str, Any]:
    source_dir = browser_root / "sources" / source_id

    links_path = source_dir / "rendered_links.json"
    network_path = source_dir / "network_events.json"
    html_path = source_dir / "rendered.html"

    links: list[str] = []
    if links_path.exists():
        value = json.loads(links_path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            links = [item for item in value if isinstance(item, str)]

    events: list[dict[str, Any]] = []
    if network_path.exists():
        value = json.loads(network_path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            events = [item for item in value if isinstance(item, dict)]

    keyword_links = [
        url
        for url in links
        if any(keyword in url.lower() for keyword in KEYWORDS)
    ]

    xhr_fetch_urls = [
        str(event.get("url"))
        for event in events
        if isinstance(event.get("url"), str)
    ]

    resolution = browser_resolution or {}

    return {
        "browser_artifacts_available": source_dir.exists(),
        "rendered_links": len(links),
        "keyword_links": keyword_links[:50],
        "xhr_fetch_events": len(events),
        "xhr_fetch_urls": xhr_fetch_urls[:50],
        "direct_download_urls": resolution.get("direct_download_urls") or [],
        "network_category_counts": resolution.get("network_category_counts") or {},
        "data_endpoint_urls": resolution.get("data_endpoint_urls") or [],
        "same_origin_json_urls": resolution.get("same_origin_json_urls") or [],
        "html_signals": html_signals(html_path),
    }


def api_evidence_summary(
    source_id: str,
    evidence_audit: dict[str, Any],
) -> dict[str, Any]:
    ready_index = index_rows(
        evidence_audit.get("ready_results"),
        "source_id",
    )
    row = ready_index.get(source_id, {})
    return {
        "api_quality": row.get("api_quality"),
        "api_resource_count": int(row.get("api_resource_count") or 0),
        "resource_count": int(row.get("resource_count") or 0),
        "api_category_counts": row.get("api_category_counts") or {},
        "sample_resources": row.get("sample_resources") or [],
    }


def choose_strategy(
    candidate_class: str,
    browser_summary: dict[str, Any],
    api_summary: dict[str, Any],
) -> str:
    if candidate_class == "IDENTITY_RESEARCH_REQUIRED":
        return "IDENTITY_RESEARCH"

    if candidate_class == "API_SEMANTIC_REVIEW":
        return "API_SEMANTIC_PROBE"

    if candidate_class == "BROWSER_NETWORK_SEMANTIC_REVIEW":
        return "NETWORK_ENDPOINT_PROBE"

    if candidate_class == "BROWSER_INTERACTION_REVIEW":
        html = browser_summary.get("html_signals") or {}
        if (
            int(html.get("forms") or 0) > 0
            or int(html.get("selects") or 0) > 0
            or int(html.get("buttons") or 0) > 0
        ):
            return "SAFE_BROWSER_INTERACTION"
        if browser_summary.get("keyword_links"):
            return "DATA_LINK_TRAVERSAL"
        return "CUSTOM_SITE_REVIEW"

    return "CUSTOM_SITE_REVIEW"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita offline los candidatos B8 y asigna una estrategia custom "
            "sin crear adaptadores por fuente."
        )
    )
    parser.add_argument(
        "--custom-candidates",
        default="config/custom_candidates.yaml",
    )
    parser.add_argument(
        "--browser-resolution",
        default="config/browser_resolution.yaml",
    )
    parser.add_argument(
        "--browser-artifacts",
        default=".runtime/browser_characterization",
    )
    parser.add_argument(
        "--source-evidence-audit",
        default=".runtime/source_evidence_audit/latest.json",
    )
    parser.add_argument(
        "--output-yaml",
        default="config/custom_strategy_candidates.yaml",
    )
    parser.add_argument(
        "--report-dir",
        default=".runtime/custom_candidate_audit",
    )
    args = parser.parse_args()

    custom = load_yaml(Path(args.custom_candidates))
    browser = load_yaml(Path(args.browser_resolution))
    evidence = load_json_optional(Path(args.source_evidence_audit))

    rows = custom.get("sources")
    if not isinstance(rows, list):
        raise ValueError("custom_candidates.yaml sin sources")

    expected_count = custom.get("candidate_count")
    if isinstance(expected_count, int) and expected_count != len(rows):
        raise ValueError(
            f"candidate_count={expected_count}, sources={len(rows)}"
        )

    browser_index = index_rows(browser.get("sources"), "source_id")
    browser_root = Path(args.browser_artifacts)

    audited: list[dict[str, Any]] = []

    for row in rows:
        source_id = row["source_id"]
        candidate_class = classify_candidate(row)

        browser_summary = browser_artifact_summary(
            source_id,
            browser_root,
            browser_index.get(source_id),
        )
        api_summary = api_evidence_summary(source_id, evidence)

        strategy = choose_strategy(
            candidate_class,
            browser_summary,
            api_summary,
        )

        audited.append(
            {
                "source_id": source_id,
                "logical_code": row.get("logical_code"),
                "name": row.get("name"),
                "historical_entrypoint": row.get("historical_entrypoint"),
                "effective_entrypoint": row.get("effective_entrypoint"),
                "lifecycle": row.get("lifecycle"),
                "b6_status": row.get("b6_status"),
                "b7_status": row.get("b7_status"),
                "custom_reason": row.get("custom_reason"),
                "candidate_class": candidate_class,
                "recommended_strategy": strategy,
                "browser_evidence": browser_summary,
                "api_evidence": api_summary,
            }
        )

    class_counts = Counter(row["candidate_class"] for row in audited)
    strategy_counts = Counter(
        row["recommended_strategy"] for row in audited
    )

    payload = {
        "schema_version": "custom-strategy-candidates-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(audited),
        "summary_by_class": dict(sorted(class_counts.items())),
        "summary_by_strategy": dict(sorted(strategy_counts.items())),
        "sources": audited,
    }

    output_yaml = Path(args.output_yaml)
    report_dir = Path(args.report_dir)
    output_yaml.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    output_yaml.write_text(
        yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )

    (report_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fields = [
        "logical_code",
        "source_id",
        "candidate_class",
        "recommended_strategy",
        "effective_entrypoint",
        "b6_status",
        "b7_status",
    ]
    with (report_dir / "latest.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in audited:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B8A — Custom Candidate Audit",
        "",
        f"- Candidatas: **{len(audited)}**",
        "",
        "## Clases",
        "",
        "| Clase | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(class_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Estrategias recomendadas",
            "",
            "| Estrategia | Cantidad |",
            "|---|---:|",
        ]
    )
    for key, count in sorted(strategy_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Fuentes",
            "",
            "| Fuente | Clase | Estrategia | Forms | Selects | Buttons | XHR/Fetch |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in audited:
        html = row["browser_evidence"]["html_signals"]
        lines.append(
            f"| {row['logical_code']} | "
            f"{row['candidate_class']} | "
            f"{row['recommended_strategy']} | "
            f"{html.get('forms', 0)} | "
            f"{html.get('selects', 0)} | "
            f"{html.get('buttons', 0)} | "
            f"{row['browser_evidence'].get('xhr_fetch_events', 0)} |"
        )

    (report_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B8A — CUSTOM CANDIDATE AUDIT")
    print("=" * 78)
    print(f"Candidatas auditadas: {len(audited)}")
    print()
    print("Clases:")
    for key, count in sorted(class_counts.items()):
        print(f"  {key:<38} {count}")
    print()
    print("Estrategias:")
    for key, count in sorted(strategy_counts.items()):
        print(f"  {key:<38} {count}")
    print()
    print("Fuentes:")
    for row in audited:
        print(
            f"  {row['logical_code']:<24} "
            f"{row['candidate_class']:<34} "
            f"{row['recommended_strategy']}"
        )
    print()
    print(f"YAML: {output_yaml}")
    print(f"JSON: {report_dir / 'latest.json'}")
    print(f"CSV:  {report_dir / 'latest.csv'}")
    print(f"MD:   {report_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
