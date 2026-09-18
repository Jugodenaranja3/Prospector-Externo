"""ApiWorkflow BATCH 3B: APIs públicas GET-only con paginación/docs acotadas."""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from prospector_externo.domain.api_fingerprint import ApiContentFingerprint
from prospector_externo.domain.api_discovery import (
    ApiDetector,
    ApiDocumentationDiscovery,
    ApiPagination,
    ApiReference,
    OpenApiDiscovery,
    OpenApiDiscoveryResult,
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
    "application/geo+json, application/topo+json, application/json, application/*+json, "
    "application/x-ndjson, application/ndjson, application/xml, text/xml, "
    "text/csv, text/tab-separated-values, application/yaml, text/yaml, "
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

    @staticmethod
    def _json_data(body: str) -> Any:
        try:
            return json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _count_api_resources(resources: List[ResourceCandidate]) -> int:
        return sum(1 for resource in resources if resource.resource_type == ResourceType.API)

    @staticmethod
    def _record_fetch_error(coverage: CoverageStats, error: Optional[str]) -> None:
        coverage.urls_failed += 1
        if error == "TIMEOUT":
            coverage.timeouts += 1
        elif error == "HTTP_403":
            coverage.http_403 += 1
        elif error == "HTTP_429":
            coverage.http_429 += 1
        elif error == "ROBOTS_DISALLOWED":
            coverage.robots_disallowed += 1

    def _add_resource(
        self,
        *,
        resources: List[ResourceCandidate],
        seen_resource_keys: Set[str],
        resource: ResourceCandidate,
        by_key: Dict[str, ResourceCandidate],
    ) -> ResourceCandidate:
        existing = by_key.get(resource.resource_key)
        if existing is not None:
            if existing.api and resource.api:
                existing.api.has_pagination = existing.api.has_pagination or resource.api.has_pagination
                existing.api.is_openapi = existing.api.is_openapi or resource.api.is_openapi
                existing.api.is_geojson = existing.api.is_geojson or resource.api.is_geojson
                existing.api.documentation_url = existing.api.documentation_url or resource.api.documentation_url
                existing.api.format = existing.api.format or resource.api.format
                existing.api.operation_id = existing.api.operation_id or resource.api.operation_id
                existing.api.auth_required = existing.api.auth_required or resource.api.auth_required
                if not existing.api.unresolved_required_params:
                    existing.api.unresolved_required_params = resource.api.unresolved_required_params
                existing.api.callable_by_policy = (
                    existing.api.callable_by_policy and resource.api.callable_by_policy
                )
            return existing
        seen_resource_keys.add(resource.resource_key)
        by_key[resource.resource_key] = resource
        resources.append(resource)
        return resource

    def _extract_file_resources_from_json(
        self,
        *,
        data: Any,
        config: SourceConfig,
        endpoint: str,
        seen_resource_keys: Set[str],
    ) -> List[ResourceCandidate]:
        """Detecta URLs de archivo publicadas en respuestas JSON/GeoJSON."""

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

    def _catalog_openapi_result(
        self,
        *,
        result: OpenApiDiscoveryResult,
        document_url: str,
        status: Optional[int],
        content_type: Optional[str],
        config: SourceConfig,
        resources: List[ResourceCandidate],
        seen_resource_keys: Set[str],
        by_key: Dict[str, ResourceCandidate],
        coverage: CoverageStats,
    ) -> None:
        coverage.api_non_get_operations_skipped += result.non_get_operations_skipped
        coverage.api_auth_required += result.auth_required_operations
        coverage.api_unresolved_operations += result.unresolved_operations

        remaining = max(0, config.max_api_endpoints - self._count_api_resources(resources))
        for ref in result.references[:remaining]:
            resource = self._make_api_resource(
                config=config,
                reference=ref,
                current_url=document_url,
            )
            resource.http_status = status
            resource.content_type = content_type
            stored = self._add_resource(
                resources=resources,
                seen_resource_keys=seen_resource_keys,
                resource=resource,
                by_key=by_key,
            )
            if stored.api:
                stored.api.documentation_probed = True
                stored.api.spec_version = result.spec_version

    async def _follow_pagination(
        self,
        *,
        config: SourceConfig,
        first_url: str,
        first_body: str,
        first_headers: dict,
        first_format: str,
        api_resource: ResourceCandidate,
        resources: List[ResourceCandidate],
        seen_resource_keys: Set[str],
        by_key: Dict[str, ResourceCandidate],
        discovered: List[DiscoveredUrl],
        coverage: CoverageStats,
    ) -> None:
        if not api_resource.api:
            return

        first_records = ApiDetector.records_detected(first_body, first_format) or 0
        first_page_hash = ApiContentFingerprint.page_hash(first_body, first_format)
        page_hashes = [first_page_hash]
        api_resource.content_hash = ApiContentFingerprint.aggregate_hash(page_hashes, first_format)
        api_resource.api.pages_sampled = 1
        api_resource.api.records_sampled = first_records
        coverage.api_pages_visited += 1
        coverage.api_records_sampled += first_records

        if not config.follow_api_pagination or first_format not in {"json", "geojson"}:
            return
        if not api_resource.api.has_pagination:
            return

        session = self._session()
        current_url = first_url
        current_body = first_body
        current_headers = first_headers
        seen_page_urls = {UrlNormalizer.normalize(first_url)}
        seen_payload_hashes = {first_page_hash}
        pages_sampled = 1
        records_sampled = first_records
        stagnant_pages = 0
        strategy: Optional[str] = None

        while pages_sampled < config.max_api_pages:
            if session.budget.remaining <= 0:
                coverage.api_pagination_stopped += 1
                break
            if records_sampled >= config.max_api_records_sampled:
                coverage.api_pagination_stopped += 1
                break

            decision = ApiPagination.next_page(
                body=current_body,
                headers=current_headers,
                current_url=current_url,
            )
            if not decision.next_url:
                break
            strategy = strategy or decision.strategy
            next_url = UrlNormalizer.normalize(decision.next_url)
            if next_url in seen_page_urls:
                coverage.api_pagination_stopped += 1
                break
            seen_page_urls.add(next_url)

            payload, status, error, headers = await session.fetch_bytes_limited(
                next_url,
                max_bytes=config.max_api_response_bytes,
                accept=API_ACCEPT,
            )
            coverage.pages_visited += 1
            coverage.requests_total = session.requests_used
            if error or payload is None:
                self._record_fetch_error(coverage, error)
                coverage.api_pagination_stopped += 1
                break

            body = self._decode_body(payload, headers)
            api_format = ApiDetector.detect_format(body, headers=headers)
            if api_format != first_format:
                coverage.api_pagination_stopped += 1
                break

            payload_hash = ApiContentFingerprint.page_hash(body, api_format)
            if payload_hash in seen_payload_hashes:
                coverage.api_pagination_stopped += 1
                break
            seen_payload_hashes.add(payload_hash)
            page_hashes.append(payload_hash)

            page_records = ApiDetector.records_detected(body, api_format) or 0
            pages_sampled += 1
            records_sampled += page_records
            coverage.api_pages_visited += 1
            coverage.api_records_sampled += page_records
            stagnant_pages = stagnant_pages + 1 if page_records == 0 else 0

            discovered.append(
                DiscoveredUrl(
                    normalized_url=next_url,
                    raw_url=next_url,
                    source_id=config.source_id,
                    discovery_type=DiscoveryType.API,
                    parent_url=current_url,
                    http_status=status,
                )
            )

            data = self._json_data(body)
            if data is not None:
                for file_resource in self._extract_file_resources_from_json(
                    data=data,
                    config=config,
                    endpoint=next_url,
                    seen_resource_keys=seen_resource_keys,
                ):
                    by_key[file_resource.resource_key] = file_resource
                    resources.append(file_resource)

            if stagnant_pages >= config.max_api_stagnant_pages:
                coverage.api_pagination_stopped += 1
                current_url, current_body, current_headers = next_url, body, headers
                break

            current_url, current_body, current_headers = next_url, body, headers

        api_resource.api.pages_sampled = pages_sampled
        api_resource.api.records_sampled = records_sampled
        api_resource.api.pagination_strategy = strategy
        api_resource.api.records_detected = records_sampled
        api_resource.content_hash = ApiContentFingerprint.aggregate_hash(page_hashes, first_format)

    async def _probe_documentation(
        self,
        *,
        config: SourceConfig,
        queue: Deque[Tuple[str, str, int]],
        docs_found: Set[str],
        resources: List[ResourceCandidate],
        seen_resource_keys: Set[str],
        by_key: Dict[str, ResourceCandidate],
        discovered: List[DiscoveredUrl],
        coverage: CoverageStats,
    ) -> None:
        if not config.probe_api_documentation or config.max_api_documents <= 0:
            return

        session = self._session()
        seen_docs: Set[str] = set()
        while queue and coverage.api_documents_probed < config.max_api_documents:
            url, parent_url, depth = queue.popleft()
            normalized = UrlNormalizer.normalize(url, base_url=parent_url)
            if normalized in seen_docs:
                continue
            seen_docs.add(normalized)
            if depth > config.max_api_document_depth:
                continue
            if session.budget.remaining <= 0:
                break

            payload, status, error, headers = await session.fetch_bytes_limited(
                normalized,
                max_bytes=config.max_api_document_bytes,
                accept=API_ACCEPT,
            )
            coverage.api_documents_probed += 1
            coverage.pages_visited += 1
            coverage.requests_total = session.requests_used

            if error or payload is None:
                coverage.api_document_errors += 1
                if error not in {"RESPONSE_TOO_LARGE", None}:
                    self._record_fetch_error(coverage, error)
                continue

            body = self._decode_body(payload, headers)
            discovered.append(
                DiscoveredUrl(
                    normalized_url=normalized,
                    raw_url=url,
                    source_id=config.source_id,
                    discovery_type=DiscoveryType.API,
                    parent_url=parent_url,
                    depth=depth,
                    http_status=status,
                )
            )
            docs_found.add(normalized)

            api_format = ApiDetector.detect_format(body, headers=headers)
            if api_format == "openapi":
                coverage.openapi_documents += 1
                result = OpenApiDiscovery.discover(body, document_url=normalized)
                spec_reference = ApiReference(
                    url=normalized,
                    method="GET",
                    detection_method="openapi_document_probed",
                    api_format="openapi",
                    documentation_url=parent_url,
                    is_openapi=True,
                )
                spec_resource = self._make_api_resource(
                    config=config, reference=spec_reference, current_url=parent_url
                )
                spec_resource.http_status = status
                spec_resource.content_type = headers.get("content-type")
                stored_spec = self._add_resource(
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    resource=spec_resource,
                    by_key=by_key,
                )
                if stored_spec.api:
                    stored_spec.api.documentation_probed = True
                    stored_spec.api.spec_version = result.spec_version
                self._catalog_openapi_result(
                    result=result,
                    document_url=normalized,
                    status=status,
                    content_type=headers.get("content-type"),
                    config=config,
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    by_key=by_key,
                    coverage=coverage,
                )
                continue

            if depth >= config.max_api_document_depth:
                continue
            for ref in ApiDocumentationDiscovery.extract_openapi_references(
                body,
                base_url=normalized,
                headers=headers,
            ):
                docs_found.add(ref.url)
                if ref.url not in seen_docs:
                    queue.append((ref.url, normalized, depth + 1))

    async def run(self, config: SourceConfig) -> ExtractionResult:
        session = self._session()
        coverage = CoverageStats()
        discovered: List[DiscoveredUrl] = []
        resources: List[ResourceCandidate] = []
        seen_resource_keys: Set[str] = set()
        by_key: Dict[str, ResourceCandidate] = {}
        successful_documents = 0
        last_error: Optional[str] = None
        docs_found: Set[str] = set()
        docs_queue: Deque[Tuple[str, str, int]] = deque()

        if not config.discover_apis:
            return ExtractionResult(
                source_id=config.source_id,
                success=True,
                coverage=coverage,
                metadata={"api_discovery_disabled": True},
            )

        for endpoint in self._seed_urls(config):
            if self._count_api_resources(resources) >= config.max_api_endpoints:
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
                self._add_resource(
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    resource=resource,
                    by_key=by_key,
                )
                successful_documents += 1
                continue

            if error or payload is None:
                self._record_fetch_error(coverage, error)
                last_error = error or "API_EMPTY"
                continue

            body = self._decode_body(payload, headers)
            successful_documents += 1
            normalized_endpoint = UrlNormalizer.normalize(endpoint)
            discovered.append(
                DiscoveredUrl(
                    normalized_url=normalized_endpoint,
                    raw_url=endpoint,
                    source_id=config.source_id,
                    discovery_type=DiscoveryType.API,
                    http_status=status,
                )
            )

            api_format = ApiDetector.detect_format(body, headers=headers)
            declared_format = ApiDetector.declared_format(headers=headers, url=endpoint)
            content_type = headers.get("content-type")

            if api_format is None and declared_format in {
                "json", "geojson", "openapi", "topojson", "ndjson"
            }:
                self._record_fetch_error(coverage, "INVALID_RESPONSE")
                last_error = "INVALID_RESPONSE"
                successful_documents -= 1
                continue

            if api_format == "openapi":
                docs_found.add(endpoint)
                coverage.openapi_documents += 1
                result = OpenApiDiscovery.discover(body, document_url=endpoint)
                self._catalog_openapi_result(
                    result=result,
                    document_url=endpoint,
                    status=status,
                    content_type=content_type,
                    config=config,
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    by_key=by_key,
                    coverage=coverage,
                )
                continue

            if api_format in {
                "json", "geojson", "xml", "csv", "tsv", "ndjson", "jsonstat", "topojson"
            }:
                page_records = ApiDetector.records_detected(body, api_format) or 0
                reference = ApiReference(
                    url=endpoint,
                    method="GET",
                    detection_method="api_response",
                    api_format=api_format,
                    is_geojson=api_format == "geojson",
                    has_pagination=ApiDetector.has_pagination(body, endpoint),
                    records_detected=page_records,
                    title=config.name or f"API {config.source_id}",
                )
                resource = self._make_api_resource(
                    config=config,
                    reference=reference,
                    current_url=endpoint,
                )
                resource.http_status = status
                resource.content_type = content_type
                resource.etag = headers.get("etag")
                resource.last_modified_header = headers.get("last-modified")
                try:
                    resource.content_length_bytes = int(headers.get("content-length", ""))
                except (TypeError, ValueError):
                    resource.content_length_bytes = None
                resource = self._add_resource(
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    resource=resource,
                    by_key=by_key,
                )

                if api_format in {"json", "geojson"}:
                    data = self._json_data(body)
                    if data is not None:
                        for file_resource in self._extract_file_resources_from_json(
                            data=data,
                            config=config,
                            endpoint=endpoint,
                            seen_resource_keys=seen_resource_keys,
                        ):
                            by_key[file_resource.resource_key] = file_resource
                            resources.append(file_resource)

                await self._follow_pagination(
                    config=config,
                    first_url=endpoint,
                    first_body=body,
                    first_headers=headers,
                    first_format=api_format,
                    api_resource=resource,
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    by_key=by_key,
                    discovered=discovered,
                    coverage=coverage,
                )
                continue

            # HTML de documentación/landing API: se catalogan referencias y 3B
            # solo sondea specs OpenAPI explícitamente publicadas.
            remaining = max(0, config.max_api_endpoints - self._count_api_resources(resources))
            html_api_resources = self._extract_api_candidates(
                html_content=body,
                current_url=endpoint,
                config=config,
                headers=headers,
                seen_resource_keys=None,
                remaining_api_slots=remaining,
            )
            for candidate in html_api_resources:
                stored = self._add_resource(
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    resource=candidate,
                    by_key=by_key,
                )
                if stored.api and stored.api.documentation_url:
                    docs_found.add(stored.api.documentation_url)

            for ref in ApiDocumentationDiscovery.extract_openapi_references(
                body,
                base_url=endpoint,
                headers=headers,
            ):
                docs_found.add(ref.url)
                spec_resource = self._make_api_resource(
                    config=config,
                    reference=ref,
                    current_url=endpoint,
                )
                self._add_resource(
                    resources=resources,
                    seen_resource_keys=seen_resource_keys,
                    resource=spec_resource,
                    by_key=by_key,
                )
                docs_queue.append((ref.url, endpoint, 0))

        await self._probe_documentation(
            config=config,
            queue=docs_queue,
            docs_found=docs_found,
            resources=resources,
            seen_resource_keys=seen_resource_keys,
            by_key=by_key,
            discovered=discovered,
            coverage=coverage,
        )

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
                "api_pagination": "EXPLICIT_ONLY",
                "api_docs_probing": "EXPLICIT_OPENAPI_ONLY",
            },
        )
