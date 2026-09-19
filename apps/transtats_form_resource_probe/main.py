from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def same_site(url: str, base: str) -> bool:
    a = (urlparse(url).hostname or "").lower().removeprefix("www.")
    b = (urlparse(base).hostname or "").lower().removeprefix("www.")
    return bool(a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)))


def page_signals(html: str, required: list[str]) -> dict[str, Any]:
    lower = html.lower()
    signals = {
        signal: signal.lower() in lower
        for signal in required
    }

    input_count = len(re.findall(r"<input\b", html, flags=re.I))
    select_count = len(re.findall(r"<select\b", html, flags=re.I))
    form_count = len(re.findall(r"<form\b", html, flags=re.I))

    field_tokens = (
        "field name",
        "filter year",
        "filter period",
        "filter geography",
        "download",
        "select all fields",
    )
    field_token_hits = sum(
        1 for token in field_tokens if token in lower
    )

    return {
        "required_signals": signals,
        "required_signals_ok": all(signals.values()),
        "input_count": input_count,
        "select_count": select_count,
        "form_count": form_count,
        "field_token_hits": field_token_hits,
    }


def classify_probe(
    *,
    status_code: int | None,
    final_url: str | None,
    entrypoint: str,
    signals: dict[str, Any],
    minimum_inputs: int,
) -> tuple[str, str]:
    if (
        isinstance(status_code, int)
        and 200 <= status_code < 400
        and isinstance(final_url, str)
        and same_site(final_url, entrypoint)
        and signals.get("required_signals_ok")
        and int(signals.get("input_count") or 0) >= minimum_inputs
        and int(signals.get("field_token_hits") or 0) >= 3
    ):
        return (
            "TRANSTATS_FORM_RESOURCE_CONFIRMED",
            "PROMOTE_CUSTOM_FORM_RESOURCE",
        )

    if isinstance(status_code, int) and 200 <= status_code < 400:
        return (
            "TRANSTATS_PAGE_REACHABLE_SEMANTICS_INCOMPLETE",
            "B10_STATUS",
        )

    return (
        "TRANSTATS_FORM_RESOURCE_UNREACHABLE",
        "B10_STATUS",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Valida por GET que una página DL_SelectFields de TranStats "
            "representa un recurso de adquisición sin enviar POST."
        )
    )
    parser.add_argument(
        "--policy",
        default="config/transtats_custom_resource_policy.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/transtats_form_resource_probe",
    )
    args = parser.parse_args()

    policy = load_yaml(Path(args.policy))
    entrypoint = str(policy["entrypoint"])
    seeds = policy.get("evidence_seed_urls")

    if not isinstance(seeds, list) or not seeds:
        raise ValueError("Policy sin evidence_seed_urls")

    required = policy.get("required_page_signals")
    if not isinstance(required, list):
        raise ValueError("Policy sin required_page_signals")

    minimum_inputs = int(policy.get("minimum_field_inputs") or 1)

    timeout = httpx.Timeout(
        connect=8.0,
        read=15.0,
        write=15.0,
        pool=8.0,
    )

    results: list[dict[str, Any]] = []

    print("=" * 78)
    print("B8D — TRANSTATS FORM RESOURCE PROBE")
    print("=" * 78)
    print(f"Seeds: {len(seeds)}")
    print("Método: GET only")
    print("POST enviado: NO")
    print("Descarga CSV: NO")
    print()

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        verify=True,
        headers={
            "User-Agent": "DATAX-Prospector-Externo/1.0 B8D-transtats-form-probe",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
        },
    ) as client:
        for index, seed in enumerate(seeds, 1):
            print(f"[{index:02d}/{len(seeds):02d}] {seed}")

            try:
                response = client.get(seed)
                body = response.text[:2_000_000]
                signals = page_signals(body, required)
                status, route = classify_probe(
                    status_code=response.status_code,
                    final_url=str(response.url),
                    entrypoint=entrypoint,
                    signals=signals,
                    minimum_inputs=minimum_inputs,
                )

                result = {
                    "seed_url": seed,
                    "status_code": response.status_code,
                    "final_url": str(response.url),
                    "content_type": response.headers.get("content-type"),
                    "html_chars": len(body),
                    "signals": signals,
                    "status": status,
                    "recommended_route": route,
                    "error": None,
                }
            except Exception as exc:
                result = {
                    "seed_url": seed,
                    "status_code": None,
                    "final_url": None,
                    "content_type": None,
                    "html_chars": 0,
                    "signals": {},
                    "status": "TRANSTATS_FORM_RESOURCE_UNREACHABLE",
                    "recommended_route": "B10_STATUS",
                    "error": f"{type(exc).__name__}: {exc}",
                }

            results.append(result)
            print(
                f"    => {result['status']:<44} "
                f"inputs={result.get('signals', {}).get('input_count', 0):<3} "
                f"selects={result.get('signals', {}).get('select_count', 0):<3} "
                f"next={result['recommended_route']}"
            )

    status_counts = Counter(row["status"] for row in results)
    route_counts = Counter(row["recommended_route"] for row in results)

    confirmed = any(
        row["status"] == "TRANSTATS_FORM_RESOURCE_CONFIRMED"
        for row in results
    )

    payload = {
        "schema_version": "transtats-form-resource-probe-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_id": policy.get("source_id"),
        "logical_code": policy.get("logical_code"),
        "workflow_strategy": policy.get("workflow_strategy"),
        "resource_model": policy.get("resource_model"),
        "submission_policy": policy.get("submission_policy"),
        "confirmed": confirmed,
        "summary_by_status": dict(sorted(status_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "results": results,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_dir / "latest.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as fh:
        fields = [
            "seed_url",
            "status_code",
            "final_url",
            "status",
            "recommended_route",
            "error",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B8D — TranStats Form Resource Probe",
        "",
        f"- Confirmed: **{confirmed}**",
        f"- Resource model: **{policy.get('resource_model')}**",
        f"- Submission policy: **{policy.get('submission_policy')}**",
        "",
        "| Seed | Estado | Ruta |",
        "|---|---|---|",
    ]
    for row in results:
        lines.append(
            f"| {row['seed_url']} | {row['status']} | "
            f"{row['recommended_route']} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("B8D COMPLETADO")
    print("=" * 78)
    print(f"Confirmed: {confirmed}")
    for key, count in sorted(status_counts.items()):
        print(f"{key:<46} {count}")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"CSV:  {output_dir / 'latest.csv'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0 if confirmed else 2


if __name__ == "__main__":
    raise SystemExit(main())
