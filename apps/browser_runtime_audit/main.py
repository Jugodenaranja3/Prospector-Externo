from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


BROWSER_PACKAGES = ("playwright", "selenium", "pyppeteer")
SEARCH_TERMS = (
    "playwright",
    "selenium",
    "pyppeteer",
    "chromium",
    "browser",
    "route.abort",
    "asset blocking",
    "block_assets",
)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def package_status() -> dict[str, bool]:
    return {
        package: importlib.util.find_spec(package) is not None
        for package in BROWSER_PACKAGES
    }


def common_browser_paths() -> dict[str, list[str]]:
    candidates = {
        "chrome": [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ],
        "edge": [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        ],
        "firefox": [
            os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
        ],
    }
    found: dict[str, list[str]] = {}
    for name, paths in candidates.items():
        hits = []
        for value in paths:
            if "%" in value:
                continue
            path = Path(value)
            if path.exists():
                hits.append(str(path))
        if hits:
            found[name] = hits
    return found


def path_browsers() -> dict[str, str]:
    commands = (
        "chrome",
        "chrome.exe",
        "chromium",
        "chromium.exe",
        "msedge",
        "msedge.exe",
        "firefox",
        "firefox.exe",
    )
    found: dict[str, str] = {}
    for command in commands:
        value = shutil.which(command)
        if value:
            found[command] = value
    return found


def playwright_runtime_status() -> dict[str, Any]:
    if importlib.util.find_spec("playwright") is None:
        return {
            "available": False,
            "driver_start": False,
            "browsers": {},
            "error": None,
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "available": True,
            "driver_start": False,
            "browsers": {},
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        with sync_playwright() as pw:
            browsers: dict[str, dict[str, Any]] = {}
            for name in ("chromium", "firefox", "webkit"):
                browser_type = getattr(pw, name)
                executable = browser_type.executable_path
                browsers[name] = {
                    "executable_path": executable,
                    "executable_exists": Path(executable).exists() if executable else False,
                }
            return {
                "available": True,
                "driver_start": True,
                "browsers": browsers,
                "error": None,
            }
    except Exception as exc:
        return {
            "available": True,
            "driver_start": False,
            "browsers": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def scan_browser_references(root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    allowed_suffixes = {".py", ".toml", ".yaml", ".yml", ".json", ".md"}

    for base in ("src", "apps", "config", "tests", "pyproject.toml"):
        path = root / base
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = [
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in allowed_suffixes
            ]
        else:
            continue

        for candidate in candidates:
            if ".venv" in candidate.parts or ".runtime" in candidate.parts:
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lower = text.lower()
            matched = [term for term in SEARCH_TERMS if term in lower]
            if matched:
                results.append(
                    {
                        "path": str(candidate.relative_to(root)),
                        "terms": matched,
                    }
                )

    return results


def choose_strategy(
    packages: dict[str, bool],
    playwright: dict[str, Any],
    references: list[dict[str, Any]],
) -> str:
    reference_terms = {
        term
        for row in references
        for term in row.get("terms", [])
    }

    if packages.get("playwright") and playwright.get("driver_start"):
        executable_ready = any(
            value.get("executable_exists")
            for value in playwright.get("browsers", {}).values()
            if isinstance(value, dict)
        )
        if executable_ready:
            if "playwright" in reference_terms:
                return "USE_EXISTING_PLAYWRIGHT"
            return "PLAYWRIGHT_RUNTIME_READY"
        return "PLAYWRIGHT_PACKAGE_NO_BROWSER_BINARY"

    if packages.get("selenium"):
        return "SELENIUM_PACKAGE_AVAILABLE"

    if packages.get("pyppeteer"):
        return "PYPPETEER_PACKAGE_AVAILABLE"

    return "NO_BROWSER_RUNTIME_DETECTED"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditoría offline del runtime browser antes de B7."
    )
    parser.add_argument(
        "--candidates",
        default="config/browser_candidates.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/browser_runtime_audit",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
    )
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    candidates = load_yaml(Path(args.candidates))
    rows = candidates.get("sources")
    if not isinstance(rows, list):
        raise ValueError("browser_candidates.yaml sin sources")

    packages = package_status()
    playwright = playwright_runtime_status()
    references = scan_browser_references(root)
    path_found = path_browsers()
    common_found = common_browser_paths()
    strategy = choose_strategy(packages, playwright, references)

    term_counts = Counter(
        term
        for row in references
        for term in row.get("terms", [])
    )

    payload = {
        "schema_version": "browser-runtime-audit-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_sources": len(rows),
        "candidate_source_ids": [row["source_id"] for row in rows],
        "packages": packages,
        "playwright": playwright,
        "browser_commands_on_path": path_found,
        "common_browser_paths": common_found,
        "code_reference_files": len(references),
        "code_reference_term_counts": dict(sorted(term_counts.items())),
        "code_references": references,
        "recommended_strategy": strategy,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "# B7A — Browser runtime audit",
        "",
        f"- Candidatas: **{len(rows)}**",
        f"- Estrategia recomendada: **{strategy}**",
        "",
        "## Paquetes",
        "",
        "| Paquete | Disponible |",
        "|---|---|",
    ]
    for name, available in packages.items():
        lines.append(f"| {name} | {'sí' if available else 'no'} |")

    lines.extend([
        "",
        "## Playwright",
        "",
        f"- Driver inicia: **{'sí' if playwright.get('driver_start') else 'no'}**",
    ])
    for name, info in playwright.get("browsers", {}).items():
        lines.append(
            f"- {name}: `{info.get('executable_path')}` "
            f"(existe={'sí' if info.get('executable_exists') else 'no'})"
        )

    lines.extend([
        "",
        "## Referencias de código",
        "",
        f"- Archivos con referencias browser: **{len(references)}**",
    ])
    for row in references[:40]:
        lines.append(f"- `{row['path']}` → {', '.join(row['terms'])}")

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B7A — BROWSER RUNTIME AUDIT")
    print("=" * 78)
    print(f"Candidatas browser:       {len(rows)}")
    print(f"Playwright package:       {'SÍ' if packages.get('playwright') else 'NO'}")
    print(f"Selenium package:         {'SÍ' if packages.get('selenium') else 'NO'}")
    print(f"Pyppeteer package:        {'SÍ' if packages.get('pyppeteer') else 'NO'}")
    print(f"Playwright driver inicia: {'SÍ' if playwright.get('driver_start') else 'NO'}")
    print(f"Referencias en código:    {len(references)}")
    print(f"Estrategia:               {strategy}")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
