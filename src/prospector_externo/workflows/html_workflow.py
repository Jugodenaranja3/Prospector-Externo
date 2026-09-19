"""Workflow HTML sobre DiscoveryFrontier y runtime HTTP async."""

import logging
from typing import List, Set

from bs4.exceptions import ParserRejectedMarkup

from prospector_externo.domain.discovery import DiscoveryFrontier, StopReason
from prospector_externo.domain.models import DiscoveredUrl, DiscoveryType, ResourceCandidate, SourceConfig
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.base import BaseWorkflow, ResourceDetector

logger = logging.getLogger("prospector.workflows.html")


class HtmlWorkflow(BaseWorkflow):
    _CONTENT_TYPE_EXTENSIONS = {
        "application/pdf": ".pdf",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "text/csv": ".csv",
        "application/zip": ".zip",
        "application/x-zip-compressed": ".zip",
        "application/json": ".json",
        "application/xml": ".xml",
        "text/xml": ".xml",
    }

    @classmethod
    def _content_type(cls, headers) -> str:
        if not headers:
            return ""
        raw = headers.get("content-type") or headers.get("Content-Type") or ""
        return str(raw).split(";", 1)[0].strip().lower()

    @classmethod
    def _is_markup_content_type(cls, headers) -> bool:
        content_type = cls._content_type(headers)
        if not content_type:
            # Algunos servidores públicos omiten Content-Type. En ese caso
            # se conserva el comportamiento histórico y se intenta parsear.
            return True
        return (
            content_type.startswith("text/")
            or content_type in {
                "application/xhtml+xml",
                "application/xml",
            }
        )

    def _catalog_direct_response_resource(
        self,
        *,
        config: SourceConfig,
        item,
        status,
        response_headers,
        frontier: DiscoveryFrontier,
        resources: List[ResourceCandidate],
        seen_resource_keys: Set[str],
    ) -> bool:
        content_type = self._content_type(response_headers)
        extension = self._CONTENT_TYPE_EXTENSIONS.get(content_type, "")

        if not extension and not ResourceDetector.is_resource(item.normalized_url):
            return False

        resource = self._make_resource(
            config=config,
            raw_url=item.raw_url,
            current_url=item.normalized_url,
            title=config.name or item.normalized_url,
            discovery_type=DiscoveryType.HTML,
        )
        if extension and not resource.file_extension:
            resource.file_extension = extension
        resource.content_type = content_type or None
        resource.http_status = status

        if resource.resource_key in seen_resource_keys:
            return True

        seen_resource_keys.add(resource.resource_key)
        if frontier.register_resource(resource.url):
            resources.append(resource)
        return True

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
        last_error = None

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
                last_error = error
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

            if not self._is_markup_content_type(response_headers):
                if self._catalog_direct_response_resource(
                    config=config,
                    item=item,
                    status=status,
                    response_headers=response_headers,
                    frontier=frontier,
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                ):
                    consecutive_errors = 0
                    continue

                last_error = "NON_MARKUP_RESPONSE"
                coverage.urls_failed += 1
                consecutive_errors += 1
                logger.warning(
                    "Respuesta no-markup omitida para [%s]: %s content_type=%s",
                    config.source_id,
                    item.normalized_url,
                    self._content_type(response_headers) or "unknown",
                )
                if consecutive_errors >= config.max_consecutive_errors:
                    frontier.stop_reason = StopReason.MAX_CONSECUTIVE_ERRORS
                    break
                continue

            try:
                page_resources, links = self._extract_resources_and_links(
                    html or "",
                    item.normalized_url,
                    config,
                    discovery_type=DiscoveryType.HTML,
                    seen_resource_keys=seen_resource_keys,
                )
            except ParserRejectedMarkup as exc:
                last_error = "PARSER_REJECTED_MARKUP"
                coverage.urls_failed += 1
                consecutive_errors += 1
                logger.warning(
                    "Markup rechazado para [%s] en %s: %s",
                    config.source_id,
                    item.normalized_url,
                    ascii(exc),
                )
                if consecutive_errors >= config.max_consecutive_errors:
                    frontier.stop_reason = StopReason.MAX_CONSECUTIVE_ERRORS
                    break
                continue

            successful_pages += 1
            consecutive_errors = 0

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

            pagination.observe(
                item.normalized_url,
                new_resources=accepted_resources,
            )

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

        if successful_pages == 0 and not resources and coverage.urls_failed > 0:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                resources=resources,
                discovered_urls=discovered_urls,
                coverage=coverage,
                failure_code=last_error or coverage.stop_reason or "SOURCE_UNREACHABLE",
                error_message=(
                    "No se pudo obtener ninguna página navegable ni recurso directo "
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
