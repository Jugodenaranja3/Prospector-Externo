from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml


DOWNLOAD_EXTENSIONS = {
    ".pdf", ".csv", ".xlsx", ".xls", ".ods", ".json", ".xml",
    ".zip", ".parquet",
}
HEAVY_RESOURCE_TYPES = {"image", "media", "font"}
DATA_CONTENT_HINTS = (
    "application/json",
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument",
    "application/xml",
    "text/xml",
)
DATA_URL_HINTS = (
    "/api/", "/dataset", "/datasets", "/data/", "/statistics",
    "/estadistic", "/indicator", "/series", "/query", "/download",
    ".csv", ".xlsx", ".xls", ".ods", ".json", ".xml", ".parquet",
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


def is_download_url(url: str) -> bool:
    return extension_from_url(url) in DOWNLOAD_EXTENSIONS


def is_data_network_event(url: str, content_type: str | None) -> bool:
    lower_url = (url or "").lower()
    lower_ct = (content_type or "").lower()
    if any(hint in lower_ct for hint in DATA_CONTENT_HINTS):
        return True
    if any(hint in lower_url for hint in DATA_URL_HINTS):
        return True
    return False


def classify_result(
    *,
    navigation_ok: bool,
    direct_downloads: int,
    data_network_events: int,
    xhr_fetch_events: int,
) -> tuple[str, str]:
    if direct_downloads > 0:
        return "BROWSER_FILE_DISCOVERY", "B7_OPERATIONAL_BROWSER"
    if data_network_events > 0:
        return "BROWSER_DATA_NETWORK_EVIDENCE", "B7_NETWORK_REVIEW"
    if xhr_fetch_events > 0:
        return "BROWSER_GENERIC_NETWORK_EVIDENCE", "B7_NETWORK_REVIEW"
    if navigation_ok:
        return "BROWSER_NO_DATA_EVIDENCE", "B8_CUSTOM_REVIEW"
    return "BROWSER_EXECUTION_ERROR", "B8_CUSTOM_REVIEW"


def normalize_href(base_url: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href:
        return None
    lower = href.lower()
    if lower.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    try:
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    return absolute


def common_system_browser_paths() -> list[tuple[str, str]]:
    raw = [
        ("chrome", os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")),
        ("chrome", os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe")),
        ("chrome", os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")),
        ("msedge", os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe")),
        ("msedge", os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")),
    ]
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for name, value in raw:
        if "%" in value:
            continue
        path = str(Path(value))
        if path in seen:
            continue
        seen.add(path)
        if Path(path).exists():
            result.append((name, path))
    return result


def launch_specs(pw: Any) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    bundled = pw.chromium.executable_path
    if bundled and Path(bundled).exists():
        specs.append(
            {
                "kind": "bundled",
                "name": "playwright-chromium",
                "kwargs": {"executable_path": bundled},
                "executable_path": bundled,
            }
        )

    # Playwright puede usar los canales instalados localmente sin descargar
    # el Chromium empaquetado por Playwright.
    specs.extend(
        [
            {
                "kind": "channel",
                "name": "chrome",
                "kwargs": {"channel": "chrome"},
                "executable_path": None,
            },
            {
                "kind": "channel",
                "name": "msedge",
                "kwargs": {"channel": "msedge"},
                "executable_path": None,
            },
        ]
    )

    for name, path in common_system_browser_paths():
        specs.append(
            {
                "kind": "system_path",
                "name": name,
                "kwargs": {"executable_path": path},
                "executable_path": path,
            }
        )

    return specs


def discover_browser_runtime() -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "playwright_import": False,
            "launchable": False,
            "selected": None,
            "attempts": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    attempts: list[dict[str, Any]] = []
    try:
        with sync_playwright() as pw:
            for spec in launch_specs(pw):
                browser = None
                try:
                    browser = pw.chromium.launch(
                        headless=True,
                        **spec["kwargs"],
                    )
                    attempts.append(
                        {
                            "kind": spec["kind"],
                            "name": spec["name"],
                            "executable_path": spec.get("executable_path"),
                            "ok": True,
                            "error": None,
                        }
                    )
                    return {
                        "playwright_import": True,
                        "launchable": True,
                        "selected": {
                            "kind": spec["kind"],
                            "name": spec["name"],
                            "executable_path": spec.get("executable_path"),
                            "kwargs": spec["kwargs"],
                        },
                        "attempts": attempts,
                        "error": None,
                    }
                except Exception as exc:
                    attempts.append(
                        {
                            "kind": spec["kind"],
                            "name": spec["name"],
                            "executable_path": spec.get("executable_path"),
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                finally:
                    if browser is not None:
                        browser.close()

            return {
                "playwright_import": True,
                "launchable": False,
                "selected": None,
                "attempts": attempts,
                "error": "No launchable Chromium/Chrome/Edge runtime found.",
            }
    except Exception as exc:
        return {
            "playwright_import": True,
            "launchable": False,
            "selected": None,
            "attempts": attempts,
            "error": f"{type(exc).__name__}: {exc}",
        }


def launch_selected_browser(pw: Any, runtime: dict[str, Any], headed: bool) -> Any:
    selected = runtime.get("selected")
    if not isinstance(selected, dict):
        raise RuntimeError("No hay runtime browser seleccionado")

    kwargs = dict(selected.get("kwargs") or {})
    kwargs["headless"] = not headed
    return pw.chromium.launch(**kwargs)


def browser_characterize_source(
    browser: Any,
    source: dict[str, Any],
    *,
    timeout_ms: int,
    settle_ms: int,
    max_html_chars: int,
    max_network_events: int,
    output_dir: Path,
) -> dict[str, Any]:
    entrypoint = source.get("effective_entrypoint") or source.get("historical_entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise ValueError(f"{source.get('source_id')}: sin entrypoint")

    source_dir = output_dir / "sources" / source["source_id"]
    source_dir.mkdir(parents=True, exist_ok=True)

    context = browser.new_context(
        java_script_enabled=True,
        ignore_https_errors=False,
        accept_downloads=False,
        service_workers="block",
    )
    page = context.new_page()

    blocked = Counter()
    network_events: list[dict[str, Any]] = []
    network_seen: set[tuple[str, str]] = set()

    def route_handler(route: Any) -> None:
        resource_type = route.request.resource_type
        if resource_type in HEAVY_RESOURCE_TYPES:
            blocked[resource_type] += 1
            route.abort()
            return
        route.continue_()

    def response_handler(response: Any) -> None:
        if len(network_events) >= max_network_events:
            return
        try:
            request = response.request
            resource_type = request.resource_type
            if resource_type not in {"xhr", "fetch"}:
                return

            url = response.url
            key = (resource_type, url)
            if key in network_seen:
                return
            network_seen.add(key)

            content_type = response.headers.get("content-type")
            network_events.append(
                {
                    "resource_type": resource_type,
                    "url": url,
                    "status": response.status,
                    "content_type": content_type,
                    "data_signal": is_data_network_event(url, content_type),
                }
            )
        except Exception:
            return

    page.route("**/*", route_handler)
    page.on("response", response_handler)

    navigation_ok = False
    navigation_error = None
    final_url = None
    title = None
    rendered_links: list[str] = []
    direct_downloads: list[str] = []
    html_chars = 0
    started = time.monotonic()

    try:
        page.goto(
            entrypoint,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        navigation_ok = True
        final_url = page.url

        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 7000))
        except Exception:
            pass

        if settle_ms > 0:
            page.wait_for_timeout(settle_ms)

        try:
            page.evaluate(
                "() => window.scrollTo(0, Math.min(document.body.scrollHeight, 2500))"
            )
            page.wait_for_timeout(min(settle_ms, 1500))
        except Exception:
            pass

        title = page.title()

        raw_hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => el.getAttribute('href')).filter(Boolean)",
        )

        seen_links: set[str] = set()
        for href in raw_hrefs:
            normalized = normalize_href(page.url, href)
            if not normalized or normalized in seen_links:
                continue
            seen_links.add(normalized)
            rendered_links.append(normalized)

        direct_downloads = sorted(
            url for url in rendered_links if is_download_url(url)
        )

        try:
            html = page.content()
            html_chars = len(html)
            (source_dir / "rendered.html").write_text(
                html[:max_html_chars],
                encoding="utf-8",
            )
        except Exception:
            pass

    except Exception as exc:
        navigation_error = f"{type(exc).__name__}: {exc}"
        final_url = page.url if page.url else entrypoint
    finally:
        context.close()

    data_network = [row for row in network_events if row["data_signal"]]
    status, route = classify_result(
        navigation_ok=navigation_ok,
        direct_downloads=len(direct_downloads),
        data_network_events=len(data_network),
        xhr_fetch_events=len(network_events),
    )

    (source_dir / "rendered_links.json").write_text(
        json.dumps(rendered_links, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (source_dir / "network_events.json").write_text(
        json.dumps(network_events, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    result = {
        "source_id": source["source_id"],
        "logical_code": source["logical_code"],
        "entrypoint": entrypoint,
        "final_url": final_url,
        "navigation_ok": navigation_ok,
        "navigation_error": navigation_error,
        "title": title,
        "rendered_links": len(rendered_links),
        "direct_downloads": len(direct_downloads),
        "direct_download_urls": direct_downloads[:100],
        "xhr_fetch_events": len(network_events),
        "data_network_events": len(data_network),
        "data_network_urls": [row["url"] for row in data_network[:100]],
        "blocked_assets": dict(sorted(blocked.items())),
        "rendered_html_chars": html_chars,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "status": status,
        "recommended_route": route,
    }

    (source_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B7 browser characterization para candidatas explícitas de B6."
    )
    parser.add_argument("--candidates", default="config/browser_candidates.yaml")
    parser.add_argument("--output-dir", default=".runtime/browser_characterization")
    parser.add_argument("--timeout-ms", type=int, default=20000)
    parser.add_argument("--settle-ms", type=int, default=2500)
    parser.add_argument("--max-html-chars", type=int, default=1500000)
    parser.add_argument("--max-network-events", type=int, default=500)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--runtime-check", action="store_true")
    args = parser.parse_args()

    runtime = discover_browser_runtime()

    if args.runtime_check:
        print(json.dumps(runtime, indent=2, ensure_ascii=False))
        return 0 if runtime.get("launchable") else 2

    if not runtime.get("launchable"):
        print(json.dumps(runtime, indent=2, ensure_ascii=False))
        raise SystemExit(
            "No hay browser compatible listo. Puede usarse Chrome/Edge del sistema "
            "o instalar Chromium de Playwright más adelante."
        )

    config = load_yaml(Path(args.candidates))
    rows = config.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("browser_candidates.yaml sin sources")

    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit debe ser > 0")
        rows = rows[: args.limit]

    from playwright.sync_api import sync_playwright

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []

    print("=" * 78)
    print("B7B — BROWSER CHARACTERIZATION")
    print("=" * 78)
    print(f"Fuentes objetivo: {len(rows)}")
    print(f"Headless: {'NO' if args.headed else 'SÍ'}")
    print(f"Runtime: {runtime['selected']['kind']} / {runtime['selected']['name']}")
    print("JS: SÍ")
    print("Assets pesados bloqueados: image/media/font")
    print("Anti-bot bypass: NO")
    print("Captcha bypass: NO")
    print()

    with sync_playwright() as pw:
        browser = launch_selected_browser(pw, runtime, args.headed)
        try:
            for index, source in enumerate(rows, 1):
                print(
                    f"[{index:02d}/{len(rows):02d}] "
                    f"{source['logical_code']:<20} "
                    f"{source.get('effective_entrypoint') or source.get('historical_entrypoint')}"
                )
                try:
                    result = browser_characterize_source(
                        browser,
                        source,
                        timeout_ms=args.timeout_ms,
                        settle_ms=args.settle_ms,
                        max_html_chars=args.max_html_chars,
                        max_network_events=args.max_network_events,
                        output_dir=output_dir,
                    )
                except Exception as exc:
                    result = {
                        "source_id": source["source_id"],
                        "logical_code": source["logical_code"],
                        "entrypoint": source.get("effective_entrypoint")
                        or source.get("historical_entrypoint"),
                        "final_url": None,
                        "navigation_ok": False,
                        "navigation_error": f"{type(exc).__name__}: {exc}",
                        "title": None,
                        "rendered_links": 0,
                        "direct_downloads": 0,
                        "direct_download_urls": [],
                        "xhr_fetch_events": 0,
                        "data_network_events": 0,
                        "data_network_urls": [],
                        "blocked_assets": {},
                        "rendered_html_chars": 0,
                        "elapsed_seconds": 0,
                        "status": "BROWSER_EXECUTION_ERROR",
                        "recommended_route": "B8_CUSTOM_REVIEW",
                    }
                results.append(result)
                print(
                    f"    => {result['status']:<30} "
                    f"links={result['rendered_links']:<4} "
                    f"files={result['direct_downloads']:<3} "
                    f"xhr/fetch={result['xhr_fetch_events']:<3} "
                    f"data_net={result['data_network_events']:<3}"
                )
        finally:
            browser.close()

    status_counts = Counter(row["status"] for row in results)
    route_counts = Counter(row["recommended_route"] for row in results)

    payload = {
        "schema_version": "browser-characterization-report-1.1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": len(results),
        "summary_by_status": dict(sorted(status_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "runtime": runtime,
        "results": results,
    }

    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = [
            "logical_code", "source_id", "entrypoint", "final_url",
            "status", "recommended_route", "rendered_links",
            "direct_downloads", "xhr_fetch_events", "data_network_events",
            "elapsed_seconds",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B7B — Browser Characterization",
        "",
        f"- Fuentes: **{len(results)}**",
        f"- Runtime: **{runtime['selected']['kind']} / {runtime['selected']['name']}**",
        "",
        "## Estados",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(status_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Fuentes",
        "",
        "| Fuente | Estado | Links | Files | XHR/Fetch | Data net | Ruta |",
        "|---|---|---:|---:|---:|---:|---|",
    ])
    for row in results:
        lines.append(
            f"| {row['logical_code']} | {row['status']} | "
            f"{row['rendered_links']} | {row['direct_downloads']} | "
            f"{row['xhr_fetch_events']} | {row['data_network_events']} | "
            f"{row['recommended_route']} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("B7B COMPLETADO")
    print("=" * 78)
    for key, count in sorted(status_counts.items()):
        print(f"{key:<36} {count}")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"CSV:  {output_dir / 'latest.csv'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
