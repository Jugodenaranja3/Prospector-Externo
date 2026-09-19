from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import yaml

from apps.browser_characterization.main import (
    discover_browser_runtime,
    launch_selected_browser,
    normalize_href,
)


DATA_EXTENSIONS = {
    ".csv", ".xlsx", ".xls", ".ods", ".json", ".xml", ".parquet",
}
DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
}
ARCHIVE_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".gz",
}
KEYWORDS = (
    "data", "datos", "estadistic", "statistics", "report", "reporte",
    "consulta", "consult", "download", "descarga", "export", "csv",
    "xlsx", "xls", "ods", "pdf", "dataset", "indicador", "indicator",
    "serie", "series", "buscar", "search", "filter", "filtrar",
)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def extension_from_url(url: str) -> str:
    try:
        suffix = Path(urlparse(url).path.lower()).suffix
    except Exception:
        return ""
    return suffix if len(suffix) <= 12 else ""


def resource_kind(url: str) -> str:
    ext = extension_from_url(url)
    if ext in DATA_EXTENSIONS:
        return "STRUCTURED_FILE"
    if ext in DOCUMENT_EXTENSIONS:
        return "DOCUMENT_FILE"
    if ext in ARCHIVE_EXTENSIONS:
        return "ARCHIVE_FILE"
    return "OTHER"


def host_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, entrypoint: str) -> bool:
    candidate = host_key(url)
    base = host_key(entrypoint)
    if not candidate or not base:
        return False
    return (
        candidate == base
        or candidate.endswith("." + base)
        or base.endswith("." + candidate)
    )


def looks_like_data_url(url: str) -> bool:
    lower = url.lower()
    if resource_kind(url) != "OTHER":
        return True
    return any(
        token in lower
        for token in (
            "/dataset", "/datasets", "/data/", "/statistics",
            "/estadistic", "/indicator", "/indicators", "/series",
            "/query", "/download", "/resource", "/resources",
            "/report", "/reports", "/export",
        )
    )


def json_data_shape(value: Any) -> tuple[bool, str]:
    if isinstance(value, list):
        if len(value) >= 2 and any(isinstance(item, (dict, list)) for item in value):
            return True, "LIST_RECORDS"
        return False, "LIST_SMALL_OR_SCALAR"

    if isinstance(value, dict):
        preferred = (
            "data", "results", "items", "records", "features",
            "observations", "series", "rows", "values",
        )
        for key in preferred:
            child = value.get(key)
            if isinstance(child, list) and child:
                return True, f"ARRAY_FIELD:{key}"
            if isinstance(child, dict) and len(child) >= 2:
                return True, f"OBJECT_FIELD:{key}"

        if len(value) >= 8:
            scalar_count = sum(
                not isinstance(child, (dict, list))
                for child in value.values()
            )
            if scalar_count >= 5:
                return True, "WIDE_OBJECT"

        return False, "OBJECT_METADATA_OR_SMALL"

    return False, "SCALAR"


def probe_http_json(
    client: httpx.Client,
    urls: list[str],
    entrypoint: str,
    max_urls: int = 8,
    max_bytes: int = 500_000,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in urls:
        if not isinstance(url, str) or not url or url in seen:
            continue
        seen.add(url)

        if len(results) >= max_urls:
            break

        if not same_site(url, entrypoint):
            results.append(
                {
                    "url": url,
                    "skipped": True,
                    "skip_reason": "cross_site",
                }
            )
            continue

        try:
            with client.stream("GET", url) as response:
                body = b""
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    remaining = max_bytes - len(body)
                    if remaining <= 0:
                        break
                    body += chunk[:remaining]
                    if len(body) >= max_bytes:
                        break

                content_type = response.headers.get("content-type", "")
                item: dict[str, Any] = {
                    "url": url,
                    "skipped": False,
                    "status_code": response.status_code,
                    "final_url": str(response.url),
                    "content_type": content_type,
                    "sample_bytes": len(body),
                    "json_data": False,
                    "json_shape": None,
                    "resource_kind": resource_kind(str(response.url)),
                    "error": None,
                }

                if (
                    "json" in content_type.lower()
                    or extension_from_url(str(response.url)) == ".json"
                ):
                    try:
                        parsed = json.loads(body.decode("utf-8", errors="replace"))
                        is_data, shape = json_data_shape(parsed)
                        item["json_data"] = is_data
                        item["json_shape"] = shape
                    except Exception as exc:
                        item["json_shape"] = f"JSON_PARSE_ERROR:{type(exc).__name__}"

                results.append(item)

        except Exception as exc:
            results.append(
                {
                    "url": url,
                    "skipped": False,
                    "status_code": None,
                    "final_url": None,
                    "content_type": None,
                    "sample_bytes": 0,
                    "json_data": False,
                    "json_shape": None,
                    "resource_kind": "OTHER",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return results


def candidate_api_urls(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    api_evidence = row.get("api_evidence") or {}

    for resource in api_evidence.get("sample_resources") or []:
        if not isinstance(resource, dict):
            continue
        url = resource.get("url")
        if isinstance(url, str) and url:
            urls.append(url)

    browser = row.get("browser_evidence") or {}
    for key in (
        "data_endpoint_urls",
        "same_origin_json_urls",
        "xhr_fetch_urls",
    ):
        for url in browser.get(key) or []:
            if isinstance(url, str) and url:
                urls.append(url)

    return list(dict.fromkeys(urls))


def safe_get_form_urls(page: Any, base_url: str, limit: int = 4) -> tuple[list[str], int]:
    forms = page.eval_on_selector_all(
        "form",
        """forms => forms.map(form => ({
            method: (form.getAttribute('method') || 'get').toLowerCase(),
            action: form.getAttribute('action') || location.href,
            fields: Array.from(form.elements).map(el => {
                const tag = (el.tagName || '').toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                const name = el.getAttribute('name') || '';
                let value = el.value || '';
                if (tag === 'select') {
                    const option = Array.from(el.options || [])
                        .find(opt => opt.value && !opt.disabled);
                    if (option) value = option.value;
                }
                return {tag, type, name, value, disabled: !!el.disabled};
            })
        }))"""
    )

    urls: list[str] = []
    blocked_non_get = 0

    for form in forms:
        if not isinstance(form, dict):
            continue

        method = str(form.get("method") or "get").lower()
        action = normalize_href(base_url, str(form.get("action") or base_url))
        if not action:
            continue

        if method != "get":
            blocked_non_get += 1
            continue

        if not same_site(action, base_url):
            continue

        params: list[tuple[str, str]] = []
        for field in form.get("fields") or []:
            if not isinstance(field, dict) or field.get("disabled"):
                continue
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            field_type = str(field.get("type") or "").lower()
            if field_type in {"password", "file", "submit", "button", "reset"}:
                continue
            value = str(field.get("value") or "").strip()
            if not value:
                continue
            params.append((name, value))

        parsed = urlparse(action)
        existing = parse_qsl(parsed.query, keep_blank_values=True)
        query = urlencode(existing + params, doseq=True)
        candidate = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                query,
                "",
            )
        )
        if candidate not in urls:
            urls.append(candidate)
        if len(urls) >= limit:
            break

    return urls, blocked_non_get


def browser_custom_probe(
    browser: Any,
    source_id: str,
    entrypoint: str,
    output_dir: Path,
    *,
    timeout_ms: int = 18_000,
    max_pages: int = 7,
) -> dict[str, Any]:
    source_dir = output_dir / "sources" / source_id
    source_dir.mkdir(parents=True, exist_ok=True)

    context = browser.new_context(
        java_script_enabled=True,
        ignore_https_errors=False,
        accept_downloads=False,
        service_workers="block",
    )
    page = context.new_page()

    network_events: list[dict[str, Any]] = []
    blocked_non_idempotent = 0

    def route_handler(route: Any) -> None:
        nonlocal blocked_non_idempotent
        request = route.request

        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            blocked_non_idempotent += 1
            route.abort()
            return

        if request.resource_type in {"image", "media", "font"}:
            route.abort()
            return

        route.continue_()

    def response_handler(response: Any) -> None:
        try:
            request = response.request
            if request.resource_type not in {"xhr", "fetch"}:
                return
            network_events.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "content_type": response.headers.get("content-type"),
                    "method": request.method,
                }
            )
        except Exception:
            return

    page.route("**/*", route_handler)
    page.on("response", response_handler)

    pages_visited: list[str] = []
    discovered_files: set[str] = set()
    candidate_links: list[str] = []
    blocked_non_get_forms = 0
    navigation_errors: list[str] = []

    queue = [entrypoint]
    seen_pages: set[str] = set()

    try:
        while queue and len(pages_visited) < max_pages:
            url = queue.pop(0)
            if url in seen_pages:
                continue
            seen_pages.add(url)

            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                pages_visited.append(page.url)
                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=min(timeout_ms, 5_000),
                    )
                except Exception:
                    pass
                page.wait_for_timeout(1200)

                hrefs = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(el => el.getAttribute('href')).filter(Boolean)",
                )

                for href in hrefs:
                    absolute = normalize_href(page.url, href)
                    if not absolute:
                        continue
                    kind = resource_kind(absolute)
                    if kind != "OTHER":
                        discovered_files.add(absolute)
                        continue
                    if (
                        same_site(absolute, entrypoint)
                        and any(keyword in absolute.lower() for keyword in KEYWORDS)
                        and absolute not in candidate_links
                    ):
                        candidate_links.append(absolute)

                form_urls, blocked_forms = safe_get_form_urls(
                    page,
                    page.url,
                )
                blocked_non_get_forms += blocked_forms

                for candidate in form_urls:
                    if candidate not in queue and candidate not in seen_pages:
                        queue.append(candidate)

                for candidate in candidate_links:
                    if (
                        candidate not in queue
                        and candidate not in seen_pages
                        and len(queue) < max_pages * 2
                    ):
                        queue.append(candidate)

            except Exception as exc:
                navigation_errors.append(
                    f"{url} -> {type(exc).__name__}: {exc}"
                )

    finally:
        context.close()

    same_site_network = [
        event
        for event in network_events
        if same_site(str(event.get("url") or ""), entrypoint)
    ]

    data_network = [
        event
        for event in same_site_network
        if looks_like_data_url(str(event.get("url") or ""))
        or "json" in str(event.get("content_type") or "").lower()
    ]

    result = {
        "pages_visited": pages_visited,
        "candidate_links": candidate_links[:100],
        "discovered_files": sorted(discovered_files)[:100],
        "structured_files": sorted(
            url for url in discovered_files
            if resource_kind(url) == "STRUCTURED_FILE"
        )[:100],
        "network_events": same_site_network[:200],
        "data_network_events": data_network[:100],
        "blocked_non_idempotent_requests": blocked_non_idempotent,
        "blocked_non_get_forms": blocked_non_get_forms,
        "navigation_errors": navigation_errors[:50],
    }

    (source_dir / "browser_probe.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return result


def classify_probe(
    strategy: str,
    *,
    http_results: list[dict[str, Any]] | None = None,
    browser_result: dict[str, Any] | None = None,
    identity_status: str | None = None,
) -> tuple[str, str]:
    if identity_status == "UNRESOLVED_NO_SAFE_SUCCESSOR":
        return "CUSTOM_IDENTITY_UNRESOLVED", "B10_STATUS"

    http_results = http_results or []
    browser_result = browser_result or {}

    if any(
        row.get("json_data") is True
        or row.get("resource_kind") == "STRUCTURED_FILE"
        for row in http_results
        if isinstance(row, dict) and not row.get("skipped")
    ):
        return "CUSTOM_DATA_ENDPOINT_CONFIRMED", "PROMOTE_CUSTOM_WORKFLOW"

    if browser_result.get("structured_files"):
        return "CUSTOM_STRUCTURED_FILES_CONFIRMED", "PROMOTE_CUSTOM_WORKFLOW"

    if browser_result.get("discovered_files"):
        return "CUSTOM_FILES_CONFIRMED", "PROMOTE_CUSTOM_WORKFLOW"

    if browser_result.get("data_network_events"):
        return "CUSTOM_DATA_NETWORK_CONFIRMED", "PROMOTE_CUSTOM_WORKFLOW"

    if (
        browser_result.get("blocked_non_get_forms", 0) > 0
        or browser_result.get("blocked_non_idempotent_requests", 0) > 0
    ):
        return "CUSTOM_REQUIRES_FORM_POLICY", "B8_POLICY_REVIEW"

    if http_results or browser_result:
        return "CUSTOM_NO_DATA_EVIDENCE", "B10_STATUS"

    return "CUSTOM_EXECUTION_ERROR", "B10_STATUS"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B8B: probes custom por estrategia, sin adaptadores por fuente."
    )
    parser.add_argument(
        "--strategies",
        default="config/custom_strategy_candidates.yaml",
    )
    parser.add_argument(
        "--identity-resolution",
        default="config/custom_identity_resolution.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/custom_probe",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    strategy_config = load_yaml(Path(args.strategies))
    identity_config = load_yaml(Path(args.identity_resolution))

    rows = strategy_config.get("sources")
    if not isinstance(rows, list):
        raise ValueError("custom_strategy_candidates.yaml sin sources")

    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit debe ser > 0")
        rows = rows[: args.limit]

    identity_index = {
        row["source_id"]: row
        for row in identity_config.get("sources", [])
        if isinstance(row, dict) and isinstance(row.get("source_id"), str)
    }

    needs_browser = any(
        row.get("recommended_strategy")
        in {
            "SAFE_BROWSER_INTERACTION",
            "DATA_LINK_TRAVERSAL",
            "CUSTOM_SITE_REVIEW",
        }
        or (
            row.get("recommended_strategy") == "IDENTITY_RESEARCH"
            and identity_index.get(row.get("source_id"), {}).get("candidate_entrypoint")
        )
        for row in rows
    )

    runtime = discover_browser_runtime() if needs_browser else None
    if needs_browser and not runtime.get("launchable"):
        raise SystemExit(
            "B8B necesita browser local, pero Playwright no encontró Chrome/Edge."
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(
        connect=8.0,
        read=12.0,
        write=12.0,
        pool=8.0,
    )
    headers = {
        "User-Agent": "DATAX-Prospector-Externo/1.0 B8-custom-probe",
        "Accept": "application/json,text/html,application/xhtml+xml,*/*;q=0.5",
    }

    results: list[dict[str, Any]] = []

    print("=" * 78)
    print("B8B — CUSTOM STRATEGY LIVE PROBES")
    print("=" * 78)
    print(f"Fuentes objetivo: {len(rows)}")
    print("Adaptadores por fuente: NO")
    print("Métodos HTTP custom: GET only")
    print("Browser non-idempotent requests: BLOCKED")
    print("Captcha/WAF bypass: NO")
    print()

    browser_context_manager = None
    pw = None
    browser = None

    try:
        if needs_browser:
            from playwright.sync_api import sync_playwright
            browser_context_manager = sync_playwright()
            pw = browser_context_manager.start()
            browser = launch_selected_browser(pw, runtime, headed=False)

        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
            verify=True,
        ) as client:
            for index, row in enumerate(rows, 1):
                source_id = row["source_id"]
                logical_code = row.get("logical_code") or source_id
                strategy = row.get("recommended_strategy")
                entrypoint = (
                    row.get("effective_entrypoint")
                    or row.get("historical_entrypoint")
                )
                identity = identity_index.get(source_id)

                http_results: list[dict[str, Any]] = []
                browser_result: dict[str, Any] | None = None
                identity_status = (
                    identity.get("status")
                    if isinstance(identity, dict)
                    else None
                )

                print(
                    f"[{index:02d}/{len(rows):02d}] "
                    f"{logical_code:<22} strategy={strategy}"
                )

                if strategy == "IDENTITY_RESEARCH":
                    if identity is None:
                        status, route = (
                            "CUSTOM_IDENTITY_UNRESOLVED",
                            "B10_STATUS",
                        )
                    elif identity_status == "UNRESOLVED_NO_SAFE_SUCCESSOR":
                        status, route = classify_probe(
                            strategy,
                            identity_status=identity_status,
                        )
                    else:
                        entrypoint = identity.get("candidate_entrypoint") or entrypoint
                        browser_result = browser_custom_probe(
                            browser,
                            source_id,
                            entrypoint,
                            output_dir,
                        )
                        status, route = classify_probe(
                            strategy,
                            browser_result=browser_result,
                            identity_status=identity_status,
                        )

                elif strategy == "API_SEMANTIC_PROBE":
                    urls = candidate_api_urls(row)
                    http_results = probe_http_json(
                        client,
                        urls,
                        entrypoint,
                    )
                    status, route = classify_probe(
                        strategy,
                        http_results=http_results,
                    )

                elif strategy == "NETWORK_ENDPOINT_PROBE":
                    browser_evidence = row.get("browser_evidence") or {}
                    urls = [
                        url
                        for url in browser_evidence.get("xhr_fetch_urls") or []
                        if isinstance(url, str)
                    ]
                    http_results = probe_http_json(
                        client,
                        urls,
                        entrypoint,
                    )
                    status, route = classify_probe(
                        strategy,
                        http_results=http_results,
                    )

                else:
                    browser_result = browser_custom_probe(
                        browser,
                        source_id,
                        entrypoint,
                        output_dir,
                    )
                    status, route = classify_probe(
                        strategy,
                        browser_result=browser_result,
                    )

                result = {
                    "source_id": source_id,
                    "logical_code": logical_code,
                    "strategy": strategy,
                    "entrypoint": entrypoint,
                    "identity_status": identity_status,
                    "status": status,
                    "recommended_route": route,
                    "http_probe_count": len(http_results),
                    "http_results": http_results,
                    "browser_result": browser_result,
                }
                results.append(result)

                source_dir = output_dir / "sources" / source_id
                source_dir.mkdir(parents=True, exist_ok=True)
                (source_dir / "result.json").write_text(
                    json.dumps(result, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                print(
                    f"    => {status:<34} "
                    f"http={len(http_results):<2} "
                    f"files={len((browser_result or {}).get('discovered_files', [])):<3} "
                    f"data_net={len((browser_result or {}).get('data_network_events', [])):<3} "
                    f"next={route}"
                )

    finally:
        if browser is not None:
            browser.close()
        if pw is not None:
            pw.stop()

    status_counts = Counter(row["status"] for row in results)
    route_counts = Counter(row["recommended_route"] for row in results)

    payload = {
        "schema_version": "custom-probe-report-1.0",
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
            "strategy",
            "entrypoint",
            "identity_status",
            "status",
            "recommended_route",
            "http_probe_count",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B8B — Custom Strategy Live Probes",
        "",
        f"- Fuentes: **{len(results)}**",
        "",
        "## Estados",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(status_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Fuentes",
            "",
            "| Fuente | Estrategia | Estado | Ruta |",
            "|---|---|---|---|",
        ]
    )
    for row in results:
        lines.append(
            f"| {row['logical_code']} | {row['strategy']} | "
            f"{row['status']} | {row['recommended_route']} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("B8B COMPLETADO")
    print("=" * 78)
    for key, count in sorted(status_counts.items()):
        print(f"{key:<38} {count}")
    print()
    print("Rutas:")
    for key, count in sorted(route_counts.items()):
        print(f"  {key:<36} {count}")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"CSV:  {output_dir / 'latest.csv'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
