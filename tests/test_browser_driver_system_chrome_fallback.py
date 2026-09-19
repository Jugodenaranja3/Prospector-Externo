from __future__ import annotations

import pytest

import prospector_externo.infrastructure.browser_driver as browser_driver


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, *, managed_error=None, chrome_error=None):
        self.managed_error = managed_error
        self.chrome_error = chrome_error
        self.calls = []
        self.browser = _FakeBrowser()

    def launch(self, **kwargs):
        self.calls.append(kwargs)

        if kwargs.get("channel") == "chrome":
            if self.chrome_error is not None:
                raise self.chrome_error
            return self.browser

        if self.managed_error is not None:
            raise self.managed_error
        return self.browser


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


class _Starter:
    def __init__(self, playwright):
        self.playwright = playwright

    def start(self):
        return self.playwright


def test_prefers_managed_playwright_chromium(monkeypatch):
    chromium = _FakeChromium()
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()
    driver._ensure_browser()

    assert driver.browser_source == "playwright-chromium"
    assert len(chromium.calls) == 1
    assert "channel" not in chromium.calls[0]

    driver.close()
    assert pw.stopped is True


def test_falls_back_to_system_chrome_when_managed_browser_is_missing(monkeypatch):
    chromium = _FakeChromium(
        managed_error=RuntimeError(
            "BrowserType.launch: Executable doesn't exist. "
            "Please run playwright install"
        )
    )
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()
    driver._ensure_browser()

    assert driver.browser_source == "system-chrome"
    assert len(chromium.calls) == 2
    assert "channel" not in chromium.calls[0]
    assert chromium.calls[1]["channel"] == "chrome"

    driver.close()


def test_does_not_mask_unrelated_managed_browser_error(monkeypatch):
    chromium = _FakeChromium(
        managed_error=RuntimeError("sandbox initialization failed")
    )
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()

    with pytest.raises(RuntimeError, match="sandbox initialization failed"):
        driver._ensure_browser()

    assert len(chromium.calls) == 1
    assert pw.stopped is True
    assert driver.browser_source is None


def test_reports_both_errors_when_system_chrome_also_fails(monkeypatch):
    chromium = _FakeChromium(
        managed_error=RuntimeError("Executable doesn't exist"),
        chrome_error=RuntimeError("Chrome channel not found"),
    )
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()

    with pytest.raises(RuntimeError) as exc_info:
        driver._ensure_browser()

    message = str(exc_info.value)
    assert "Chromium administrado por Playwright" in message
    assert "Chrome del sistema" in message
    assert "Chrome channel not found" in message
    assert pw.stopped is True
