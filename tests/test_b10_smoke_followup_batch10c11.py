from __future__ import annotations

from pathlib import Path

import yaml

from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow


def test_missing_playwright_browser_is_classified_explicitly():
    exc = RuntimeError(
        "BrowserType.launch: Executable doesn't exist at C:/ms-playwright/chromium/chrome.exe. "
        "Please run: playwright install chromium"
    )
    assert (
        JavascriptWorkflow._render_exception_code(exc)
        == "PLAYWRIGHT_BROWSER_MISSING"
    )


def test_other_render_exception_remains_playwright_error():
    exc = RuntimeError("browser crashed unexpectedly")
    assert (
        JavascriptWorkflow._render_exception_code(exc)
        == "PLAYWRIGHT_ERROR"
    )


def test_anapo_redirect_scope_includes_official_bare_domain():
    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load(
        (root / "config" / "sources.yaml").read_text(encoding="utf-8")
    )
    rows = {
        row["source_id"]: row
        for row in raw["sources"]
        if isinstance(row, dict)
    }
    anapo = rows["anapo"]

    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in anapo.get("allowed_hosts", [])
    }

    assert "anapobolivia.org" in hosts
