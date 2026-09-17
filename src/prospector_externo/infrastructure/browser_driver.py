"""
Driver de automatización con Playwright para JavascriptWorkflow.
Aplica control estricto de memoria RAM:
- Concurrencia limitada (máx. 1-2 páginas simultáneas).
- Bloqueo de imágenes, fuentes, estilos pesados y medios.
- Reciclaje periódico del proceso de Chromium para prevenir OOM.
"""

import logging
from typing import Optional, List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("prospector.infrastructure.browser")

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class PlaywrightDriver:
    """Driver protegido de Playwright con límites de memoria y reciclaje anti-OOM."""

    MAX_PAGES_BEFORE_RESTART = 15

    def __init__(self, headless: bool = True, timeout_ms: int = 25000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._pages_navigated: int = 0

    def is_available(self) -> bool:
        """Indica si Playwright está instalado en el entorno."""
        return HAS_PLAYWRIGHT

    def _ensure_browser(self) -> None:
        """Inicia o recicla el navegador si superó el límite de páginas para prevenir fugas de RAM."""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright no está instalado en el entorno. "
                "Ejecuta: pip install playwright && playwright install chromium"
            )

        if self._browser is not None and self._pages_navigated >= self.MAX_PAGES_BEFORE_RESTART:
            logger.info("Reciclando proceso Chromium para liberar memoria RAM acumulada (Anti-OOM)...")
            self.close()

        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--js-flags=--max-old-space-size=512"  # Límite de memoria V8 para Chromium
                ]
            )
            self._pages_navigated = 0

    def fetch_dynamic_dom(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Carga la URL en un contexto efímero bloqueando recursos no esenciales.
        Retorna: (dom_html, error_message).
        """
        self._ensure_browser()
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        try:
            # Crear un contexto aislado efímero
            context = self._browser.new_context(
                user_agent="DataX-ProspectorBot/1.0 (+http://datax.bo/bot)",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            # Optimización crítica de RAM: abortar descarga de imágenes, fuentes y multimedia
            page.route(
                "**/*",
                lambda route, request: (
                    route.abort()
                    if request.resource_type in ["image", "media", "font", "stylesheet"]
                    else route.continue_()
                )
            )

            page.goto(url, wait_until="domcontentloaded")
            # Esperar brevemente para hidratación JavaScript
            page.wait_for_timeout(1500)

            content = page.content()
            self._pages_navigated += 1
            return content, None

        except Exception as e:
            return None, f"PLAYWRIGHT_ERROR: {str(e)}"
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Cierra el navegador y el runtime de Playwright."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._pages_navigated = 0
