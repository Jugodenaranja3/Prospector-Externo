"""JavascriptWorkflow operacional sobre Playwright y políticas HTTP compartidas."""

import asyncio
import logging
from typing import List, Set

from prospector_externo.domain.discovery import DiscoveryFrontier, StopReason
from prospector_externo.domain.models import (
    DiscoveredUrl,
    DiscoveryType,
    ResourceCandidate,
    SourceConfig,
)
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.infrastructure.browser_driver import PlaywrightDriver
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.base import BaseWorkflow

logger = logging.getLogger("prospector.workflows.javascript")


class JavascriptWorkflow(BaseWorkflow):
    """Workflow para páginas dinámicas con renderizado JS y SPAs.

    El navegador solo se abre después de un preflight realizado por el runtime
    HTTP compartido, de modo que robots, redirects, budgets y politeness siguen
    siendo política central del Prospector.
    """

    def __init__(self) -> None:
        super().__init__()
        self.browser_driver = PlaywrightDriver()

    @staticmethod
    def _record_error(coverage: CoverageStats, error: str | None) -> None:
        coverage.urls_failed += 1
        if error == "TIMEOUT":
            coverage.timeouts += 1
        elif error == "HTTP_403":
            coverage.http_403 += 1
        elif error == "HTTP_429":
            coverage.http_429 += 1
        elif error == "ROBOTS_DISALLOWED":
            coverage.robots_disallowed += 1

    @staticmethod
    def _render_exception_code(exc: Exception) -> str:
        """Clasifica fallos del browser sin ocultarlos como UNHANDLED."""
        message = str(exc).casefold()
        if (
            "executable doesn't exist" in message
            or "playwright install" in message
            or "download new browsers" in message
        ):
            return "PLAYWRIGHT_BROWSER_MISSING"
        return "PLAYWRIGHT_ERROR"

    async def run(self, config: SourceConfig) -> ExtractionResult:
        logger.info(
            "Iniciando JavascriptWorkflow (Playwright) para fuente: [%s]",
            config.source_id,
        )

        coverage = CoverageStats()
        discovered_urls: List[DiscoveredUrl] = []
        resources: List[ResourceCandidate] = []
        seen_resource_keys: Set[str] = set()
        session = self._session()
        frontier = DiscoveryFrontier(config)
        frontier.seed(config.seeds or [config.entrypoint])

        if not self.browser_driver.is_available():
            msg = (
                "Playwright no está disponible. Instala la dependencia y "
                "Chromium antes de ejecutar fuentes javascript."
            )
            logger.error(msg)
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="PLAYWRIGHT_UNAVAILABLE",
                error_message=msg,
                coverage=coverage,
            )

        successful_pages = 0
        last_error = None
        page_limit_reached = False

        try:
            while successful_pages + coverage.urls_failed < config.max_browser_pages:
                if session.budget.remaining <= 0:
                    frontier.stop_reason = StopReason.REQUEST_BUDGET
                    last_error = "REQUEST_BUDGET_EXCEEDED"
                    break

                item = frontier.pop()
                if item is None:
                    break

                # Preflight read-only. Además de comprobar reachability, aplica
                # robots/redirect/politeness antes de abrir el navegador.
                _, status, error, _ = await session.fetch_document(
                    item.normalized_url,
                    conditional=False,
                    accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                )
                frontier.mark_visited(item)
                coverage.pages_visited += 1
                coverage.requests_total = session.requests_used

                if error:
                    last_error = error
                    self._record_error(coverage, error)
                    logger.warning(
                        "Preflight JS rechazado para [%s] %s: %s",
                        config.source_id,
                        item.normalized_url,
                        error,
                    )
                    if error == "REQUEST_BUDGET_EXCEEDED":
                        frontier.stop_reason = StopReason.REQUEST_BUDGET
                        break
                    continue

                try:
                    dom_html, render_error = await asyncio.to_thread(
                        self.browser_driver.fetch_dynamic_dom,
                        item.normalized_url,
                    )
                except Exception as exc:
                    last_error = self._render_exception_code(exc)
                    self._record_error(coverage, last_error)
                    logger.error(
                        "Playwright no pudo renderizar [%s] %s: %s",
                        config.source_id,
                        item.normalized_url,
                        ascii(str(exc)),
                    )
                    continue
                if render_error or not dom_html:
                    last_error = render_error or "EMPTY_DYNAMIC_DOM"
                    self._record_error(coverage, "PLAYWRIGHT_ERROR")
                    logger.warning(
                        "Error renderizando [%s] %s: %s",
                        config.source_id,
                        item.normalized_url,
                        ascii(render_error),
                    )
                    continue

                successful_pages += 1
                discovered_urls.append(
                    DiscoveredUrl(
                        normalized_url=item.normalized_url,
                        raw_url=item.raw_url,
                        source_id=config.source_id,
                        discovery_type=DiscoveryType.JAVASCRIPT,
                        parent_url=item.parent_url,
                        depth=item.depth,
                        http_status=status or 200,
                    )
                )

                page_resources, next_urls = self._extract_resources_and_links(
                    dom_html,
                    item.normalized_url,
                    config,
                    discovery_type=DiscoveryType.JAVASCRIPT,
                    seen_resource_keys=seen_resource_keys,
                )

                for resource in page_resources:
                    if frontier.register_resource(resource.url):
                        resources.append(resource)

                if item.depth < config.max_depth:
                    for next_url, _anchor_text in next_urls:
                        frontier.enqueue(
                            next_url,
                            base_url=item.normalized_url,
                            depth=item.depth + 1,
                            parent_url=item.normalized_url,
                        )

            if (
                successful_pages + coverage.urls_failed
                >= config.max_browser_pages
                and frontier.pending_count > 0
            ):
                page_limit_reached = True

        finally:
            self.browser_driver.close()

        coverage.resources_found = len(resources)
        coverage.urls_discovered = frontier.discovered_count
        coverage.urls_rejected = frontier.rejected_count
        coverage.urls_pending = frontier.pending_count
        coverage.spider_traps_blocked = frontier.spider_traps_blocked
        coverage.query_variants_blocked = frontier.query_variants_blocked
        coverage.requests_total = session.requests_used

        if page_limit_reached:
            coverage.stop_reason = "BROWSER_PAGE_LIMIT"
        elif frontier.stop_reason is not None:
            coverage.stop_reason = frontier.stop_reason

        logger.info(
            "JavascriptWorkflow finalizado para [%s]: %d recursos hallados.",
            config.source_id,
            len(resources),
        )

        if successful_pages == 0 and coverage.urls_failed > 0:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                resources=resources,
                discovered_urls=discovered_urls,
                coverage=coverage,
                failure_code=last_error or "PLAYWRIGHT_ERROR",
                error_message=(
                    "No se pudo renderizar ninguna página dinámica "
                    f"de la fuente; último error={last_error or 'unknown'}"
                ),
            )

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=resources,
            discovered_urls=discovered_urls,
            coverage=coverage,
        )
