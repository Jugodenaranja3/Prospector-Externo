"""ApiWorkflow BATCH 3A: detección/identidad/OpenAPI con política GET-only."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Set

from prospector_externo.domain.api_discovery import (
    ApiDetector,
    ApiReference,
    OpenApiDiscovery,
)
from prospector_externo.domain.models import (
    DiscoveredUrl,
    DiscoveryType,
    ResourceCandidate,
    ResourceType,
    SourceConfig,
)
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.base import BaseWorkflow, ResourceDetector


API_ACCEPT = (
    "application/geo+json, application/json, application/*+json, "
    "application/xml, text/xml, text/csv, application/yaml, text/yaml, "
    "text/html;q=0.8, */*;q=0.1"
)


class ApiWorkflow(BaseWorkflow):
    @staticmethod
    def _seed_urls(config: SourceConfig) -> List[str]:
        ordered: List[str] = []
        seen: Set[str] = set()
        for raw in [config.entrypoint, *config.seeds]:
            normalized = UrlNormalizer.normalize(raw, base_url=config.entrypoint)
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    @staticmethod
    def _decode_body(data: bytes, headers: dict) -> str:
        content_type = headers.get("content-type", "")
        match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.I)
        charset = match.group(1) if match else "utf-8"
        try:
            return data.decode(charset, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")

    def _extract_file_resources_from_json(
        self,
        *,
        data: Any,
        config: SourceConfig,
        endpoint: str,
        seen_resource_keys: Set[str],
    ) -> List[ResourceCandidate]:
        """Conserva la capacidad histórica de detectar URLs de archivo en respuestas JSON."""

        containers: List[Any] = []
        if isinstance(data, list):
            containers = data
        elif isinstance(data, dict):
            for key in ("items", "data", "results", "records", "features"):
                value = data.get(key)
                if isinstance(value, list):
                    containers = value
                    break

        resources: List[ResourceCandidate] = []
        for item in containers:
            if not isinstance(item, dict):
                continue
            raw_url = item.get("url") or item.get("download_url") or item.get("link")
            if not isinstance(raw_url, str) or not raw_url.strip():
                continue
            normalized = UrlNormalizer.normalize(raw_url, base_url=endpoint)
            if not ResourceDetector.is_resource(normalized):
                continue
            resource = self._make_resource(
                config=config,
                raw_url=raw_url,
                current_url=endpoint,
                title=str(item.get("title") or item.get("name") or "Recurso API"),
                discovery_type=DiscoveryType.API,
            )
            if resource.resource_key in seen_resource_keys:
                continue
            seen_resource_keys.add(resource.resource_key)
            resources.append(resource)
        return resources

    async def run(self, config: SourceConfig) -> ExtractionResult:
        session = self._session()
        coverage = CoverageStats()
        discovered: List[DiscoveredUrl] = []
        resources: List[ResourceCandidate] = []
        seen_resource_keys: Set[str] = set()
        successful_documents = 0
        last_error: Optional[str] = None
        docs_found: Set[str] = set()

        if not config.discover_apis:
            return ExtractionResult(
                source_id=config.source_id,
                success=True,
                coverage=coverage,
                metadata={"api_discovery_disabled": True},
            )

        for endpoint in self._seed_urls(config):
            if len([r for r in resources if r.resource_type == ResourceType.API]) >= config.max_api_endpoints:
                coverage.stop_reason = "MAX_API_ENDPOINTS"
                break
            if session.budget.remaining <= 0:
                coverage.stop_reason = "REQUEST_BUDGET_EXCEEDED"
                break

            payload, status, error, headers = await session.fetch_bytes_limited(
                endpoint,
                max_bytes=config.max_api_response_bytes,
                accept=API_ACCEPT,
            )
            coverage.pages_visited += 1
            coverage.requests_total = session.requests_used

            if error == "RESPONSE_TOO_LARGE":
                declared = ApiDetector.declared_format(headers=headers, url=endpoint)
                reference = ApiReference(
                    url=endpoint,
                    method="GET",
                    detection_method="api_seed_bounded",
                    api_format=declared,
                    is_openapi=declared == "openapi",
                    is_geojson=declared == "geojson",
                    title=config.name or f"API {config.source_id}",
                )
                resource = self._make_api_resource(
                    config=config, reference=reference, current_url=endpoint
                )
                resource.http_status = status
                resource.content_type = headers.get("content-type")
                if resource.resource_key not in seen_resource_keys:
                    seen_resource_keys.add(resource.resource_key)
                    resources.append(resource)
                successful_documents += 1
                continue

            if error or payload is None:
                coverage.urls_failed += 1
                last_error = error or "API_EMPTY"
                if error == "TIMEOUT":
                    coverage.timeouts += 1
                elif error == "HTTP_403":
                    coverage.http_403 += 1
                elif error == "HTTP_429":
                    coverage.http_429 += 1
                elif error == "ROBOTS_DISALLOWED":
                    coverage.robots_disallowed += 1
                continue

            body = self._decode_body(payload, headers)
            successful_documents += 1
            discovered.append(
                DiscoveredUrl(
                    normalized_url=UrlNormalizer.normalize(endpoint),
                    raw_url=endpoint,
                    source_id=config.source_id,
                    discovery_type=DiscoveryType.API,
                    http_status=status,
                )
            )

            api_format = ApiDetector.detect_format(body, headers=headers)
            declared_format = ApiDetector.declared_format(headers=headers, url=endpoint)
            content_type = headers.get("content-type")

            if api_format is None and declared_format in {"json", "geojson", "openapi"}:
                coverage.urls_failed += 1
                last_error = "INVALID_RESPONSE"
                successful_documents -= 1
                continue

            if api_format == "openapi":
                docs_found.add(endpoint)
                coverage.openapi_documents += 1
                result = OpenApiDiscovery.discover(body, document_url=endpoint)
                coverage.api_non_get_operations_skipped += result.non_get_operations_skipped
                coverage.api_auth_required += result.auth_required_operations
                coverage.api_unresolved_operations += result.unresolved_operations

                remaining = max(0, config.max_api_endpoints - len([
                    r for r in resources if r.resource_type == ResourceType.API
                ]))
                for ref in result.references[:remaining]:
                    resource = self._make_api_resource(
                        config=config,
                        reference=ref,
                        current_url=endpoint,
                    )
                    resource.http_status = status
                    resource.content_type = content_type
                    if resource.resource_key in seen_resource_keys:
                        continue
                    seen_resource_keys.add(resource.resource_key)
                    resources.append(resource)
                continue

            if api_format in {"json", "geojson", "xml", "csv"}:
                reference = ApiReference(
                    url=endpoint,
                    method="GET",
                    detection_method="api_response",
                    api_format=api_format,
                    is_geojson=api_format == "geojson",
                    has_pagination=ApiDetector.has_pagination(body, endpoint),
                    records_detected=ApiDetector.records_detected(body, api_format),
                    title=config.name or f"API {config.source_id}",
                )
                resource = self._make_api_resource(
                    config=config,
                    reference=reference,
                    current_url=endpoint,
                )
                resource.http_status = status
                resource.content_type = content_type
                if resource.resource_key not in seen_resource_keys:
                    seen_resource_keys.add(resource.resource_key)
                    resources.append(resource)

                if api_format in {"json", "geojson"}:
                    try:
                        data = json.loads(body)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        data = None
                    if data is not None:
                        resources.extend(
                            self._extract_file_resources_from_json(
                                data=data,
                                config=config,
                                endpoint=endpoint,
                                seen_resource_keys=seen_resource_keys,
                            )
                        )
                continue

            # HTML de documentación/landing API: catalogar referencias observables,
            # pero no seguirlas automáticamente en 3A.
            html_api_resources = self._extract_api_candidates(
                html_content=body,
                current_url=endpoint,
                config=config,
                headers=headers,
                seen_resource_keys=seen_resource_keys,
                remaining_api_slots=max(0, config.max_api_endpoints - len([
                    r for r in resources if r.resource_type == ResourceType.API
                ])),
            )
            resources.extend(html_api_resources)
            for resource in html_api_resources:
                if resource.api and resource.api.documentation_url:
                    docs_found.add(resource.api.documentation_url)

        api_resources = [r for r in resources if r.resource_type == ResourceType.API]
        coverage.resources_found = len(resources)
        coverage.urls_discovered = len(discovered) + len(resources)
        coverage.api_endpoints = len(api_resources)
        coverage.api_documentation_found = len(docs_found)
        coverage.requests_total = session.requests_used
        if coverage.stop_reason is None:
            coverage.stop_reason = "API_SEEDS_EXHAUSTED"

        if successful_documents == 0 and coverage.urls_failed > 0:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                resources=resources,
                discovered_urls=discovered,
                coverage=coverage,
                failure_code=last_error or "API_UNREACHABLE",
            )

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=resources,
            discovered_urls=discovered,
            coverage=coverage,
            metadata={
                "api_documents": sorted(docs_found),
                "api_policy": "GET_ONLY",
            },
        )
