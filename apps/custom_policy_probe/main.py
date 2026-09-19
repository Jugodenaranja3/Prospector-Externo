from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from apps.browser_characterization.main import (
    discover_browser_runtime,
    launch_selected_browser,
)


DATA_EXTENSIONS = {
    ".csv", ".xlsx", ".xls", ".ods", ".json", ".xml",
    ".parquet", ".zip", ".pdf",
}
DATA_WORDS = (
    "data", "datos", "estadistic", "statistics", "export",
    "download", "descarga", "bolet", "report", "reporte",
    "csv", "xlsx", "pdf", "indicador", "indicator",
)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def ext(url: str) -> str:
    return Path(urlparse(url).path.lower()).suffix


def same_site(url: str, base: str) -> bool:
    a = (urlparse(url).hostname or "").lower().removeprefix("www.")
    b = (urlparse(base).hostname or "").lower().removeprefix("www.")
    return bool(a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)))


def extract_links(html: str, base: str) -> list[str]:
    hrefs = re.findall(
        r"href\s*=\s*[\"']([^\"'#]+)[\"']",
        html,
        flags=re.I,
    )
    out: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


def probe_cadexco(
    client: httpx.Client,
    row: dict[str, Any],
) -> dict[str, Any]:
    seed_results: list[dict[str, Any]] = []
    discovered: set[str] = set()

    for seed in row.get("seeds") or []:
        try:
            response = client.get(seed)
            links = extract_links(response.text[:1_500_000], str(response.url))

            resource_links = [
                link
                for link in links
                if ext(link) in DATA_EXTENSIONS
                or any(word in link.lower() for word in DATA_WORDS)
            ]
            discovered.update(resource_links)

            seed_results.append(
                {
                    "seed": seed,
                    "status_code": response.status_code,
                    "final_url": str(response.url),
                    "content_type": response.headers.get("content-type"),
                    "links": len(links),
                    "resource_like_links": len(resource_links),
                    "error": None,
                }
            )
        except Exception as exc:
            seed_results.append(
                {
                    "seed": seed,
                    "status_code": None,
                    "final_url": None,
                    "content_type": None,
                    "links": 0,
                    "resource_like_links": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    good_seeds = sum(
        1
        for item in seed_results
        if isinstance(item.get("status_code"), int)
        and 200 <= item["status_code"] < 400
    )

    if good_seeds and discovered:
        status = "RESOLVED_CURATED_HTTP_SEEDS"
        route = "PROMOTE_HTML_CURATED_SEEDS"
    elif good_seeds:
        status = "CURATED_SEEDS_REACHABLE_NO_RESOURCES"
        route = "B10_STATUS"
    else:
        status = "CURATED_SEEDS_UNREACHABLE"
        route = "B10_STATUS"

    return {
        "source_id": row["source_id"],
        "logical_code": row["logical_code"],
        "decision": row["decision"],
        "status": status,
        "recommended_route": route,
        "seed_results": seed_results,
        "discovered_urls": sorted(discovered)[:200],
    }


def inspect_transtats_forms(
    browser: Any,
    row: dict[str, Any],
    *,
    timeout_ms: int = 20_000,
) -> dict[str, Any]:
    entrypoint = row["entrypoint"]
    context = browser.new_context(
        java_script_enabled=True,
        ignore_https_errors=False,
        accept_downloads=False,
        service_workers="block",
    )
    page = context.new_page()

    blocked_non_idempotent = 0

    def route_handler(route: Any) -> None:
        nonlocal blocked_non_idempotent
        req = route.request
        if req.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            blocked_non_idempotent += 1
            route.abort()
            return
        if req.resource_type in {"image", "media", "font"}:
            route.abort()
            return
        route.continue_()

    page.route("**/*", route_handler)

    navigation_error = None
    forms: list[dict[str, Any]] = []
    final_url = None

    try:
        page.goto(
            entrypoint,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        final_url = page.url
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass
        page.wait_for_timeout(1_000)

        forms = page.eval_on_selector_all(
            "form",
            r"""forms => forms.map((form, index) => ({
                index,
                method: (form.getAttribute('method') || 'get').toLowerCase(),
                action: form.action || location.href,
                id: form.id || null,
                name: form.getAttribute('name'),
                text: (form.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 1000),
                fields: Array.from(form.elements).map(el => ({
                    tag: (el.tagName || '').toLowerCase(),
                    type: (el.getAttribute('type') || '').toLowerCase(),
                    name: el.getAttribute('name'),
                    id: el.id || null,
                    value: String(el.value || '').slice(0, 200),
                    options: (el.tagName || '').toLowerCase() === 'select'
                        ? Array.from(el.options || []).slice(0, 8).map(o => ({
                            value: o.value,
                            text: (o.textContent || '').trim().slice(0, 120)
                        }))
                        : []
                })).slice(0, 100)
            }))"""
        )
    except Exception as exc:
        navigation_error = f"{type(exc).__name__}: {exc}"
        final_url = page.url or entrypoint
    finally:
        context.close()

    method_counts = Counter(
        str(form.get("method") or "get").lower()
        for form in forms
        if isinstance(form, dict)
    )

    query_like = 0
    mutation_like = 0
    review_forms: list[dict[str, Any]] = []

    read_tokens = (
        "select", "query", "search", "filter", "download",
        "data", "table", "year", "month", "airport", "carrier",
        "origin", "destination", "field", "submit", "statistics",
    )
    mutation_tokens = (
        "login", "password", "register", "contact", "message",
        "subscribe", "payment", "purchase", "delete", "update",
        "save", "upload",
    )

    for form in forms:
        if not isinstance(form, dict):
            continue
        blob = " ".join(
            [
                str(form.get("action") or ""),
                str(form.get("id") or ""),
                str(form.get("name") or ""),
                str(form.get("text") or ""),
                " ".join(
                    str(field.get("name") or "")
                    for field in form.get("fields") or []
                    if isinstance(field, dict)
                ),
            ]
        ).lower()

        reads = sorted(token for token in read_tokens if token in blob)
        mutations = sorted(token for token in mutation_tokens if token in blob)

        if reads:
            query_like += 1
        if mutations:
            mutation_like += 1

        review_forms.append(
            {
                **form,
                "read_only_signal_tokens": reads,
                "mutation_signal_tokens": mutations,
            }
        )

    post_query_candidates = [
        form
        for form in review_forms
        if form.get("method") == "post"
        and form.get("read_only_signal_tokens")
        and not form.get("mutation_signal_tokens")
        and same_site(str(form.get("action") or ""), entrypoint)
    ]

    if post_query_candidates:
        status = "READ_ONLY_POST_QUERY_CANDIDATES"
        route = "B8_ALLOWLIST_READ_ONLY_POST"
    elif forms:
        status = "FORM_METADATA_CAPTURED_NO_SAFE_POST"
        route = "B10_STATUS"
    else:
        status = "NO_FORMS_CAPTURED"
        route = "B10_STATUS"

    return {
        "source_id": row["source_id"],
        "logical_code": row["logical_code"],
        "decision": row["decision"],
        "status": status,
        "recommended_route": route,
        "entrypoint": entrypoint,
        "final_url": final_url,
        "navigation_error": navigation_error,
        "form_count": len(forms),
        "method_counts": dict(sorted(method_counts.items())),
        "query_like_forms": query_like,
        "mutation_like_forms": mutation_like,
        "post_query_candidate_count": len(post_query_candidates),
        "post_query_candidates": post_query_candidates[:20],
        "forms": review_forms[:50],
        "blocked_non_idempotent_requests": blocked_non_idempotent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B8C: resuelve policy review sin enviar formularios no idempotentes."
    )
    parser.add_argument(
        "--decisions",
        default="config/custom_policy_decisions.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/custom_policy_probe",
    )
    args = parser.parse_args()

    config = load_yaml(Path(args.decisions))
    rows = config.get("sources")
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError("custom_policy_decisions.yaml debe contener 3 fuentes")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime = discover_browser_runtime()
    if not runtime.get("launchable"):
        raise SystemExit("No hay Chrome/Edge local usable por Playwright.")

    from playwright.sync_api import sync_playwright

    timeout = httpx.Timeout(
        connect=8.0,
        read=12.0,
        write=12.0,
        pool=8.0,
    )

    results: list[dict[str, Any]] = []

    print("=" * 78)
    print("B8C — CUSTOM FORM POLICY RESOLUTION")
    print("=" * 78)
    print("Fuentes objetivo: 3")
    print("POST enviado: NO")
    print("Login/auth: NO")
    print("Captcha/WAF bypass: NO")
    print()

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        verify=True,
        headers={"User-Agent": "DATAX-Prospector-Externo/1.0 B8C-policy-probe"},
    ) as client:
        with sync_playwright() as pw:
            browser = launch_selected_browser(pw, runtime, headed=False)
            try:
                for row in rows:
                    source_id = row["source_id"]
                    print(
                        f"{row['logical_code']:<20} "
                        f"decision={row['decision']}"
                    )

                    if source_id == "atc":
                        result = {
                            "source_id": source_id,
                            "logical_code": row["logical_code"],
                            "decision": row["decision"],
                            "status": "RESOLVED_NO_PUBLIC_DATA_SCOPE",
                            "recommended_route": "B10_STATUS",
                            "rationale": row.get("rationale"),
                        }

                    elif source_id == "cadexco":
                        result = probe_cadexco(client, row)

                    elif source_id == "transtats":
                        result = inspect_transtats_forms(browser, row)

                    else:
                        raise ValueError(f"Fuente B8C inesperada: {source_id}")

                    results.append(result)

                    source_dir = output_dir / "sources" / source_id
                    source_dir.mkdir(parents=True, exist_ok=True)
                    (source_dir / "result.json").write_text(
                        json.dumps(result, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

                    print(
                        f"    => {result['status']:<38} "
                        f"next={result['recommended_route']}"
                    )
            finally:
                browser.close()

    status_counts = Counter(row["status"] for row in results)
    route_counts = Counter(row["recommended_route"] for row in results)

    payload = {
        "schema_version": "custom-policy-probe-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": len(results),
        "summary_by_status": dict(sorted(status_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "results": results,
    }

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
            "logical_code",
            "source_id",
            "decision",
            "status",
            "recommended_route",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B8C — Custom Form Policy Resolution",
        "",
        "| Fuente | Estado | Ruta |",
        "|---|---|---|",
    ]
    for row in results:
        lines.append(
            f"| {row['logical_code']} | {row['status']} | "
            f"{row['recommended_route']} |"
        )
    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("B8C COMPLETADO")
    print("=" * 78)
    for key, count in sorted(status_counts.items()):
        print(f"{key:<42} {count}")
    print()
    print("Rutas:")
    for key, count in sorted(route_counts.items()):
        print(f"  {key:<38} {count}")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"CSV:  {output_dir / 'latest.csv'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
