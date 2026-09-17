"""
Plugin de workflow para sitios HTML estáticos (HtmlWorkflow).
Explora semillas focalizadas y enlaces alcanzables en HTML sin requerir motor JavaScript.
"""

import logging
from typing import Set, List
from collections import deque

from prospector_externo.workflows.base import BaseWorkflow
from prospector_externo.domain.models import SourceConfig, DiscoveredUrl, DiscoveryType
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.observations import CoverageStats

logger = logging.getLogger("prospector.workflows.html")


class HtmlWorkflow(BaseWorkflow):
    """Workflow de exploración para portales predominantemente HTML."""

    def __init__(self, max_depth: int = 2, max_pages: int = 30):
        super().__init__()
        self.max_depth = max_depth
        self.max_pages = max_pages

    def run(self, config: SourceConfig) -> ExtractionResult:
        logger.info(f"Iniciando HtmlWorkflow para fuente: [{config.source_id}]")
        coverage = CoverageStats()
        discovered_urls: List[DiscoveredUrl] = []
        all_resources = []

        # Determinar semillas iniciales
        initial_seeds = config.seeds if config.seeds else [config.entrypoint]
        queue = deque([(seed, 0, None) for seed in initial_seeds])
        visited: Set[str] = set()
        seen_resource_keys: Set[str] = set()

        while queue and len(visited) < self.max_pages:
            current_url, depth, parent_url = queue.popleft()
            if current_url in visited:
                continue
            visited.add(current_url)
            coverage.pages_visited += 1

            # Obtener HTML
            html_text, status_code, err = self.http_client.fetch_html(
                current_url,
                rate_limit_delay=config.rate_limit_seconds,
                ignore_robots_txt=config.ignore_robots_txt,
                robots_override_reason=config.robots_override_reason
            )

            if err:
                coverage.urls_failed += 1
                logger.warning(f"Error accediendo a {current_url}: {err}")
                if len(visited) == 1:
                    # Falló la primera semilla
                    return ExtractionResult(
                        source_id=config.source_id,
                        success=False,
                        failure_code=err,
                        error_message=f"Fallo de conexión o robots.txt en semilla inicial: {err}",
                        coverage=coverage
                    )
                continue

            discovered_urls.append(DiscoveredUrl(
                normalized_url=current_url,
                raw_url=current_url,
                source_id=config.source_id,
                discovery_type=DiscoveryType.HTML,
                parent_url=parent_url,
                http_status=status_code
            ))

            # Extraer enlaces y recursos
            resources, next_urls = self._extract_resources_and_links(
                html_text,
                current_url,
                config,
                discovery_type=DiscoveryType.HTML,
                seen_resource_keys=seen_resource_keys
            )

            # Agregar recursos descubiertos (ya deduplicados contra seen_resource_keys)
            for res in resources:
                all_resources.append(res)
                coverage.resources_found += 1

            # Encolar páginas hijas si no superan la profundidad máxima
            if depth < self.max_depth:
                for nxt in next_urls:
                    if nxt not in visited:
                        queue.append((nxt, depth + 1, current_url))

        coverage.urls_discovered = len(discovered_urls) + len(all_resources)
        logger.info(f"HtmlWorkflow finalizado para [{config.source_id}]: {len(all_resources)} recursos hallados en {coverage.pages_visited} páginas.")

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=all_resources,
            discovered_urls=discovered_urls,
            coverage=coverage
        )
