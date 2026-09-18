"""ApiWorkflow básico async. API Discovery de producción se completa en BATCH 3."""

import json
from typing import List, Set

from prospector_externo.domain.models import (
    ChangeStatus,
    DiscoveredUrl,
    DiscoveryType,
    ResourceCandidate,
    SourceConfig,
)
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.base import BaseWorkflow, ResourceDetector


class ApiWorkflow(BaseWorkflow):
    async def run(self, config: SourceConfig) -> ExtractionResult:
        session = self._session()
        endpoint = config.entrypoint
        text, status, error = await session.fetch_html(endpoint, conditional=False)
        coverage = CoverageStats(
            pages_visited=1,
            requests_total=session.requests_used,
        )

        if error or not text:
            if error == "TIMEOUT":
                coverage.timeouts += 1
            if error == "HTTP_429":
                coverage.http_429 += 1
            if error == "ROBOTS_DISALLOWED":
                coverage.robots_disallowed += 1
            coverage.urls_failed += 1
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code=error or "API_EMPTY",
                coverage=coverage,
            )

        discovered = [
            DiscoveredUrl(
                normalized_url=UrlNormalizer.normalize(endpoint),
                raw_url=endpoint,
                source_id=config.source_id,
                discovery_type=DiscoveryType.API,
                http_status=status,
            )
        ]
        resources: List[ResourceCandidate] = []
        seen: Set[str] = set()

        try:
            data = json.loads(text)
            items = data if isinstance(data, list) else data.get("items", data.get("data", []))
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    raw_url = item.get("url") or item.get("download_url") or item.get("link")
                    if not raw_url:
                        continue
                    normalized = UrlNormalizer.normalize(raw_url, base_url=endpoint)
                    key = UrlNormalizer.compute_resource_key(config.source_id, normalized)
                    if key in seen:
                        continue
                    seen.add(key)
                    resources.append(
                        ResourceCandidate(
                            resource_key=key,
                            url=normalized,
                            raw_url=raw_url,
                            source_id=config.source_id,
                            title=item.get("title") or item.get("name") or "Recurso API",
                            file_extension=item.get("extension") or ResourceDetector.extension_for(normalized),
                            discovered_from_url=endpoint,
                            discovery_method="api_response",
                            change_status=ChangeStatus.NEW,
                        )
                    )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="INVALID_RESPONSE",
                error_message=str(exc),
                discovered_urls=discovered,
                coverage=coverage,
            )

        coverage.resources_found = len(resources)
        coverage.urls_discovered = len(discovered) + len(resources)
        coverage.requests_total = session.requests_used
        coverage.stop_reason = "QUEUE_EXHAUSTED"
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=resources,
            discovered_urls=discovered,
            coverage=coverage,
        )
