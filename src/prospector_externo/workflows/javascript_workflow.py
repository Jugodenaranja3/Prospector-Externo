"""
Plugin de workflow para sitios con renderizado JavaScript dinámico (JavascriptWorkflow).
Utiliza PlaywrightDriver con aislamiento estricto de memoria RAM y bloqueo de assets no esenciales.
"""

import logging
from typing import Set, List
from collections import deque

from prospector_externo.workflows.base import BaseWorkflow
from prospector_externo.domain.models import SourceConfig, DiscoveredUrl, DiscoveryType
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.infrastructure.browser_driver import PlaywrightDriver

logger = logging.getLogger("prospector.workflows.javascript")


class JavascriptWorkflow(BaseWorkflow):
    """Workflow para páginas dinámicas con renderizado JS y SPAs."""

    def __init__(self, max_depth: int = 1, max_pages: int = 15):
        super().__init__()
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.browser_driver = PlaywrightDriver()

    def run(self, config: SourceConfig) -> ExtractionResult:
        logger.info(f"Iniciando JavascriptWorkflow (Playwright) para fuente: [{config.source_id}]")
        coverage = CoverageStats()
        discovered_urls: List[DiscoveredUrl] = []
        all_resources = []

        if not self.browser_driver.is_available():
            msg = "Playwright no está instalado. Instálalo con: pip install playwright && playwright install chromium"
            logger.error(msg)
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="PLAYWRIGHT_UNAVAILABLE",
                error_message=msg,
                coverage=coverage
            )

        initial_seeds = config.seeds if config.seeds else [config.entrypoint]
        queue = deque([(seed, 0, None) for seed in initial_seeds])
        visited: Set[str] = set()
        seen_resource_keys: Set[str] = set()

        try:
            while queue and len(visited) < self.max_pages:
                current_url, depth, parent_url = queue.popleft()
                if current_url in visited:
                    continue
                visited.add(current_url)
                coverage.pages_visited += 1

                # 1. Verificar robots.txt antes de abrir navegador
                allowed, reason = self.http_client.is_allowed_by_robots(
                    current_url,
                    config.ignore_robots_txt,
                    config.robots_override_reason
                )
                if not allowed:
                    coverage.urls_rejected += 1
                    logger.warning(f"URL omitida por robots.txt: {current_url} ({reason})")
                    continue

                # 2. Renderizar mediante Playwright con límite de memoria
                dom_html, err = self.browser_driver.fetch_dynamic_dom(current_url)
                if err or not dom_html:
                    coverage.urls_failed += 1
                    logger.warning(f"Error renderizando {current_url}: {err}")
                    if len(visited) == 1:
                        return ExtractionResult(
                            source_id=config.source_id,
                            success=False,
                            failure_code="PLAYWRIGHT_ERROR",
                            error_message=f"Error al renderizar semilla dinámica: {err}",
                            coverage=coverage
                        )
                    continue

                discovered_urls.append(DiscoveredUrl(
                    normalized_url=current_url,
                    raw_url=current_url,
                    source_id=config.source_id,
                    discovery_type=DiscoveryType.JAVASCRIPT,
                    parent_url=parent_url,
                    http_status=200
                ))

                # 3. Extraer recursos del DOM renderizado
                resources, next_urls = self._extract_resources_and_links(
                    dom_html,
                    current_url,
                    config,
                    discovery_type=DiscoveryType.JAVASCRIPT
                )

                for res in resources:
                    if res.resource_key not in seen_resource_keys:
                        seen_resource_keys.add(res.resource_key)
                        all_resources.append(res)
                        coverage.resources_found += 1

                if depth < self.max_depth:
                    for nxt in next_urls:
                        if nxt not in visited:
                            queue.append((nxt, depth + 1, current_url))

        finally:
            self.browser_driver.close()

        coverage.urls_discovered = len(discovered_urls) + len(all_resources)
        logger.info(f"JavascriptWorkflow finalizado para [{config.source_id}]: {len(all_resources)} recursos hallados.")

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=all_resources,
            discovered_urls=discovered_urls,
            coverage=coverage
        )
