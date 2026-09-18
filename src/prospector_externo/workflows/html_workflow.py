"""Workflow HTML sobre DiscoveryFrontier y runtime HTTP async."""

import logging
from typing import List, Set

from prospector_externo.domain.discovery import DiscoveryFrontier, StopReason
from prospector_externo.domain.models import DiscoveredUrl, DiscoveryType, ResourceCandidate, SourceConfig
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.base import BaseWorkflow

logger = logging.getLogger("prospector.workflows.html")


class HtmlWorkflow(BaseWorkflow):
    async def run(self, config: SourceConfig) -> ExtractionResult:
        logger.info("Iniciando HtmlWorkflow async para [%s]", config.source_id)
        session = self._session()
        frontier = DiscoveryFrontier(config)
        frontier.seed(config.seeds or [config.entrypoint])

        coverage = CoverageStats()
        discovered_urls: List[DiscoveredUrl] = []
        resources: List[ResourceCandidate] = []
        seen_resource_keys: Set[str] = set()
        pagination = self._pagination_policy(config)
        consecutive_errors = 0
        successful_pages = 0

        await self._seed_from_sitemaps(
            config=config,
            frontier=frontier,
            resources=resources,
            seen_resource_keys=seen_resource_keys,
            coverage=coverage,
        )

        while True:
            if session.budget.remaining <= 0:
                frontier.stop_reason = StopReason.REQUEST_BUDGET
                break

            item = frontier.pop()
            if item is None:
                break

            html, status, error, response_headers = await session.fetch_document(
                item.normalized_url,
                conditional=False,
            )
            frontier.mark_visited(item)
            coverage.pages_visited += 1

            if error:
                coverage.urls_failed += 1
                consecutive_errors += 1
                if error == "TIMEOUT":
                    coverage.timeouts += 1
                elif error == "HTTP_403":
                    coverage.http_403 += 1
                elif error == "HTTP_429":
                    coverage.http_429 += 1
                elif error == "ROBOTS_DISALLOWED":
                    coverage.robots_disallowed += 1
                elif error == "REQUEST_BUDGET_EXCEEDED":
                    frontier.stop_reason = StopReason.REQUEST_BUDGET
                    break

                if consecutive_errors >= config.max_consecutive_errors:
                    frontier.stop_reason = StopReason.MAX_CONSECUTIVE_ERRORS
                    break
                continue

            successful_pages += 1
            consecutive_errors = 0
            discovered_urls.append(
                DiscoveredUrl(
                    normalized_url=item.normalized_url,
                    raw_url=item.raw_url,
                    source_id=config.source_id,
                    discovery_type=DiscoveryType.HTML,
                    parent_url=item.parent_url,
                    depth=item.depth,
                    http_status=status,
                )
            )

            page_resources, links = self._extract_resources_and_links(
                html or "",
                item.normalized_url,
                config,
                discovery_type=DiscoveryType.HTML,
                seen_resource_keys=seen_resource_keys,
            )
            current_api_count = sum(1 for r in resources if r.resource_type.value == "api")
            api_resources = self._extract_api_candidates(
                html_content=html or "",
                current_url=item.normalized_url,
                config=config,
                headers=response_headers,
                seen_resource_keys=seen_resource_keys,
                remaining_api_slots=config.max_api_endpoints - current_api_count,
            )

            accepted_resources = 0
            for resource in [*page_resources, *api_resources]:
                if frontier.register_resource(resource.url):
                    resources.append(resource)
                    accepted_resources += 1
                if frontier.stop_reason == StopReason.MAX_URLS:
                    break

            pagination.observe(
                item.normalized_url,
                new_resources=accepted_resources,
            )

            if frontier.stop_reason == StopReason.MAX_URLS:
                break

            if item.depth < config.max_depth:
                self._enqueue_links(
                    links=links,
                    current_url=item.normalized_url,
                    next_depth=item.depth + 1,
                    frontier=frontier,
                    pagination=pagination,
                )

        self._finish_coverage(
            coverage=coverage,
            frontier=frontier,
            pagination=pagination,
            resources=resources,
            session=session,
        )

        if successful_pages == 0 and coverage.urls_failed > 0:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                resources=resources,
                discovered_urls=discovered_urls,
                coverage=coverage,
                failure_code=coverage.stop_reason or "SOURCE_UNREACHABLE",
                error_message="No se pudo obtener ninguna página navegable de la fuente",
            )

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=resources,
            discovered_urls=discovered_urls,
            coverage=coverage,
        )
