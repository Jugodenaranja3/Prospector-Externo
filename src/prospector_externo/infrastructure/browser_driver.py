"""Playwright browser driver used by JavascriptWorkflow.

Policy:
- Prefer the Chromium revision managed by Playwright.
- If that revision is not installed, fall back to the locally installed
  Google Chrome channel.
- Never download or install a browser automatically at runtime.
- Keep browser/context/page lifecycle bounded to avoid memory growth.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger("prospector.infrastructure.browser")

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        sync_playwright,
    )

    HAS_PLAYWRIGHT = True
except ImportError:
    Browser = BrowserContext = Page = Playwright = object  # type: ignore[assignment,misc]
    sync_playwright = None  # type: ignore[assignment]
    HAS_PLAYWRIGHT = False


class PlaywrightDriver:
    """Bounded Playwright driver with a controlled system-Chrome fallback."""

    MAX_PAGES_BEFORE_RESTART = 15

    def __init__(self, headless: bool = True, timeout_ms: int = 25000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._pages_navigated = 0
        self._browser_source: Optional[str] = None

    @property
    def browser_source(self) -> Optional[str]:
        """Return the active browser source after launch."""
        return self._browser_source

    def is_available(self) -> bool:
        """Indicate whether the Playwright Python package is installed."""
        return HAS_PLAYWRIGHT

    @staticmethod
    def _launch_args() -> list[str]:
        return [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--js-flags=--max-old-space-size=512",
        ]

    @staticmethod
    def _is_missing_managed_browser(exc: Exception) -> bool:
        message = str(exc).casefold()
        return any(
            marker in message
            for marker in (
                "executable doesn't exist",
                "playwright install",
                "download new browsers",
            )
        )

    def _launch_browser(self) -> Browser:
        assert self._playwright is not None

        launch_kwargs = {
            "headless": self.headless,
            "args": self._launch_args(),
        }

        try:
            browser = self._playwright.chromium.launch(**launch_kwargs)
            self._browser_source = "playwright-chromium"
            return browser
        except Exception as managed_exc:
            if not self._is_missing_managed_browser(managed_exc):
                raise

            logger.warning(
                "Playwright Chromium no está instalado; intentando Chrome del sistema."
            )

            try:
                browser = self._playwright.chromium.launch(
                    channel="chrome",
                    **launch_kwargs,
                )
                self._browser_source = "system-chrome"
                logger.info(
                    "JavascriptWorkflow usará Chrome del sistema mediante Playwright."
                )
                return browser
            except Exception as chrome_exc:
                raise RuntimeError(
                    "No se pudo iniciar ni Chromium administrado por Playwright "
                    "ni Chrome del sistema. "
                    f"managed={managed_exc!s}; system_chrome={chrome_exc!s}"
                ) from chrome_exc

    def _ensure_browser(self) -> None:
        """Start or recycle the browser while preserving bounded memory use."""
        if not HAS_PLAYWRIGHT or sync_playwright is None:
            raise RuntimeError(
                "Playwright no está instalado en el entorno. "
                "Instala el paquete Python antes de ejecutar workflows JavaScript."
            )

        if (
            self._browser is not None
            and self._pages_navigated >= self.MAX_PAGES_BEFORE_RESTART
        ):
            logger.info(
                "Reciclando navegador Playwright para liberar memoria acumulada."
            )
            self.close()

        if self._browser is None:
            self._playwright = sync_playwright().start()
            try:
                self._browser = self._launch_browser()
            except Exception:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._browser_source = None
                raise

            self._pages_navigated = 0

    def fetch_dynamic_dom(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Load one URL in an ephemeral context and return rendered DOM."""
        self._ensure_browser()

        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        try:
            assert self._browser is not None

            context = self._browser.new_context(
                user_agent="DataX-ProspectorBot/1.0 (+http://datax.bo/bot)",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            page.route(
                "**/*",
                lambda route, request: (
                    route.abort()
                    if request.resource_type
                    in {"image", "media", "font", "stylesheet"}
                    else route.continue_()
                ),
            )

            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            content = page.content()
            self._pages_navigated += 1
            return content, None

        except Exception as exc:
            return None, f"PLAYWRIGHT_ERROR: {exc!s}"
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Close browser and Playwright runtime safely."""
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._pages_navigated = 0
        self._browser_source = None
