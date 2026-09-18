from __future__ import annotations

from apps.browser_runtime_audit.main import choose_strategy


def test_playwright_ready_with_existing_reference():
    packages = {"playwright": True, "selenium": False, "pyppeteer": False}
    playwright = {
        "driver_start": True,
        "browsers": {
            "chromium": {
                "executable_exists": True,
            }
        },
    }
    refs = [{"path": "src/browser.py", "terms": ["playwright", "browser"]}]
    assert choose_strategy(packages, playwright, refs) == "USE_EXISTING_PLAYWRIGHT"


def test_playwright_package_without_binary_is_explicit():
    packages = {"playwright": True, "selenium": False, "pyppeteer": False}
    playwright = {
        "driver_start": True,
        "browsers": {
            "chromium": {
                "executable_exists": False,
            }
        },
    }
    assert (
        choose_strategy(packages, playwright, [])
        == "PLAYWRIGHT_PACKAGE_NO_BROWSER_BINARY"
    )


def test_selenium_fallback_is_detected():
    packages = {"playwright": False, "selenium": True, "pyppeteer": False}
    playwright = {"driver_start": False, "browsers": {}}
    assert choose_strategy(packages, playwright, []) == "SELENIUM_PACKAGE_AVAILABLE"


def test_no_runtime_is_explicit():
    packages = {"playwright": False, "selenium": False, "pyppeteer": False}
    playwright = {"driver_start": False, "browsers": {}}
    assert choose_strategy(packages, playwright, []) == "NO_BROWSER_RUNTIME_DETECTED"
