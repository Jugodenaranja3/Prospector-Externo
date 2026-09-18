"""Base para workflows HTTP del nuevo Discovery Engine."""

from __future__ import annotations

import os
import re
from typing import List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from prospector_externo.domain.discovery import DiscoveryFrontier, StopReason
from prospector_externo.domain.discovery_intelligence import PaginationYieldPolicy
from prospector_externo.domain.api_discovery import ApiAccessPolicy, ApiDetector, ApiIdentity, ApiReference
from prospector_externo.domain.models import (
    ApiMetadata,
    ChangeStatus,
    DiscoveryType,
    ResourceCandidate,
    ResourceType,
    SourceConfig,
)
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.infrastructure.http_runtime import SourceHttpSession
from prospector_externo.infrastructure.sitemap_discovery import SitemapDiscovery
from prospector_externo.kernel.workflow_port import SourceWorkflow


class ResourceDetector:
    """Clasificación barata por URL. No realiza HEAD masivo."""

    RESOURCE_EXTENSIONS = {
        ".csv", ".tsv", ".xls", ".xlsx", ".ods",
        ".json", ".xml", ".geojson",
        ".pdf", ".doc", ".docx",
        ".zip", ".tar", ".gz", ".tgz", ".rar", ".7z",
    }

    @classmethod
    def extension_for(cls, url: str) -> str:
        parsed = urlparse(url)
        ext = os.path.splitext(parsed.path)[1].lower()
        if ext in cls.RESOURCE_EXTENSIONS:
            return ext

        decoded = unquote(url).lower()
        for candidate in sorted(cls.RESOURCE_EXTENSIONS, key=len, reverse=True):
            if candidate in decoded:
                return candidate
        return ""

    @classmethod
    def is_resource(cls, url: str) -> bool:
        return bool(cls.extension_for(url))


class BaseWorkflow(SourceWorkflow):
    MONTHS_ES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }

    def __init__(self, http_session: Optional[SourceHttpSession] = None) -> None:
        self.http_session = http_session

    def bind_http_session(self, session: SourceHttpSession) -> None:
        self.http_session = session

    def _session(self) -> SourceHttpSession:
        if self.http_session is None:
            raise RuntimeError("Workflow sin SourceHttpSession; debe ejecutarse mediante SourceDispatcher")
        return self.http_session

    def _extract_period_from_text(self, text: str) -> Optional[str]:
        low = text.lower()
        # Series semanales: conservar el día cuando la publicación lo expresa.
        for name, num in self.MONTHS_ES.items():
            daily = re.search(
                rf"\b(0?[1-9]|[12]\d|3[01])(?:\s+de)?\s+{re.escape(name)}(?:\s+de)?\s+(20\d{{2}})\b",
                low,
            )
            if daily:
                return f"{int(daily.group(2)):04d}-{num:02d}-{int(daily.group(1)):02d}"
        for name, num in self.MONTHS_ES.items():
            if name in low:
                year_m = re.search(r"\b(20\d{2})\b", low)
                if year_m:
                    return f"{int(year_m.group(1)):04d}-{num:02d}"
        m = re.search(r"[_\-](?P<m>0[1-9]|1[0-2])[_\-](?P<y>20\d{2})\b", low)
        if m:
            return f"{int(m.group('y')):04d}-{int(m.group('m')):02d}"
        year_only = re.search(r"\b(20\d{2})\b", low)
        return year_only.group(1) if year_only else None

    @staticmethod
    def _discovery_method(discovery_type: DiscoveryType) -> str:
        if discovery_type == DiscoveryType.COMMENTED_HTML:
            return "commented_html_link"
        if discovery_type == DiscoveryType.SITEMAP:
            return "sitemap"
        if discovery_type == DiscoveryType.API:
            return "api_response"
        return "html_link"

    def _make_resource(
        self,
        *,
        config: SourceConfig,
        raw_url: str,
        current_url: str,
        title: str,
        discovery_type: DiscoveryType,
        anchor_text: Optional[str] = None,
        context_text: Optional[str] = None,
    ) -> ResourceCandidate:
        normalized = UrlNormalizer.normalize(raw_url, base_url=current_url)
        ext = ResourceDetector.extension_for(normalized)
        parsed = urlparse(normalized)
        effective_title = title.strip() or os.path.basename(parsed.path) or normalized
        period = self._extract_period_from_text(
            f"{effective_title} {context_text or ''} {unquote(normalized)}"
        )
        return ResourceCandidate(
            resource_key=UrlNormalizer.compute_resource_key(config.source_id, normalized),
            url=normalized,
            raw_url=raw_url,
            source_id=config.source_id,
            title=effective_title,
            file_extension=ext,
            discovered_from_url=current_url,
            period_label=period,
            change_status=ChangeStatus.NEW,
            discovery_method=self._discovery_method(discovery_type),
            anchor_text=anchor_text or title.strip() or None,
            context_text=context_text,
        )

    def _make_api_resource(
        self,
        *,
        config: SourceConfig,
        reference: ApiReference,
        current_url: str,
    ) -> ResourceCandidate:
        normalized = UrlNormalizer.normalize(reference.url, base_url=current_url)
        identity = ApiIdentity.value(reference.method, normalized)
        api_metadata = ApiMetadata(
            identity=identity,
            format=reference.api_format,
            method=reference.method.upper(),
            documentation_url=reference.documentation_url,
            is_openapi=reference.is_openapi,
            is_geojson=reference.is_geojson,
            has_pagination=reference.has_pagination,
            records_detected=reference.records_detected,
            operation_id=reference.operation_id,
            auth_required=reference.auth_required,
            unresolved_required_params=reference.unresolved_required_params,
            callable_by_policy=ApiAccessPolicy.operation_callable(
                method=reference.method,
                auth_required=reference.auth_required,
                unresolved_required_params=reference.unresolved_required_params,
            ),
        )
        title = (reference.title or "").strip() or f"API {reference.method.upper()} {urlparse(normalized).path or '/'}"
        return ResourceCandidate(
            resource_key=UrlNormalizer.compute_resource_key(
                config.source_id, f"api|{identity}"
            ),
            url=normalized,
            raw_url=reference.url,
            source_id=config.source_id,
            title=title,
            file_extension=ResourceDetector.extension_for(normalized),
            discovered_from_url=current_url,
            change_status=ChangeStatus.NEW,
            discovery_method=reference.detection_method,
            anchor_text=reference.title,
            resource_type=ResourceType.API,
            api=api_metadata,
        )

    def _extract_api_candidates(
        self,
        *,
        html_content: str,
        current_url: str,
        config: SourceConfig,
        headers: Optional[dict] = None,
        seen_resource_keys: Optional[Set[str]] = None,
        remaining_api_slots: Optional[int] = None,
    ) -> List[ResourceCandidate]:
        if not config.discover_apis or config.max_api_endpoints <= 0:
            return []
        resources: List[ResourceCandidate] = []
        limit = config.max_api_endpoints if remaining_api_slots is None else max(0, remaining_api_slots)
        for ref in ApiDetector.extract_references(
            html_content, base_url=current_url, headers=headers
        ):
            if len(resources) >= limit:
                break
            resource = self._make_api_resource(
                config=config, reference=ref, current_url=current_url
            )
            if seen_resource_keys is not None:
                if resource.resource_key in seen_resource_keys:
                    continue
                seen_resource_keys.add(resource.resource_key)
            resources.append(resource)
        return resources

    @classmethod
    def _context_for_anchor(cls, tag) -> Optional[str]:
        """Recupera contexto humano cercano y el encabezado de periodo de la sección.

        Muchos portales (BCB incluido) publican un enlace genérico como "Ver archivo
        Excel" y ponen el periodo en un ``h2/h3`` anterior. Se conserva esa evidencia
        sin copiar bloques completos de la página.
        """
        anchor = tag.get_text(" ", strip=True)
        parts: List[str] = []

        parent = tag.parent
        for _ in range(5):
            if parent is None or getattr(parent, "name", None) in {"body", "html"}:
                break
            text = parent.get_text(" ", strip=True)
            if text and text != anchor and 3 <= len(text) <= 280:
                parts.append(text)
                break
            parent = getattr(parent, "parent", None)

        month_pattern = "|".join(sorted(cls.MONTHS_ES, key=len, reverse=True))
        period_heading_re = re.compile(
            rf"(?:{month_pattern}).*\b20\d{{2}}\b|\b20\d{{2}}\b.*(?:{month_pattern})",
            flags=re.IGNORECASE,
        )
        headings: List[str] = []
        for heading in tag.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6"], limit=8):
            text = heading.get_text(" ", strip=True)
            if text and len(text) <= 180 and period_heading_re.search(text):
                headings.append(text)
                if len(headings) >= 2:
                    break
        parts.extend(headings)

        # Si no hubo encabezado temporal, buscar únicamente marcadores fuertes de
        # series semanales o del reporte estadístico; evitamos confundir la fecha de
        # publicación con el periodo de datos de un boletín mensual.
        if not headings:
            strong_period_re = re.compile(
                rf"(?:\bAL\s+\d{{1,2}}\s+DE\s+(?:{month_pattern})\s+DE\s+20\d{{2}}\b|"
                rf"OPERACIONES\s+DEL\s+SISTEMA\s+DE\s+PAGOS\s+NACIONAL.*(?:{month_pattern}).*20\d{{2}})",
                flags=re.IGNORECASE,
            )
            for node in tag.find_all_previous(string=True, limit=80):
                text = re.sub(r"\s+", " ", str(node)).strip()
                if 4 <= len(text) <= 180 and strong_period_re.search(text):
                    parts.append(text)
                    break

        unique: List[str] = []
        for part in parts:
            normalized = re.sub(r"\s+", " ", part).strip()
            if normalized and normalized not in unique:
                unique.append(normalized)
        return " | ".join(unique) if unique else None

    def _extract_resources_and_links(
        self,
        html_content: str,
        current_url: str,
        config: SourceConfig,
        *,
        discovery_type: DiscoveryType,
        seen_resource_keys: Optional[Set[str]] = None,
    ) -> Tuple[List[ResourceCandidate], List[Tuple[str, str]]]:
        """Retorna recursos y enlaces navegables sin requests extra."""

        soup = BeautifulSoup(html_content, "html.parser")
        resources: List[ResourceCandidate] = []
        next_urls: List[Tuple[str, str]] = []

        for tag in soup.find_all("a", href=True):
            raw_href = tag["href"].strip()
            if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            normalized = UrlNormalizer.normalize(raw_href, base_url=current_url)
            parsed = urlparse(normalized)
            if parsed.scheme not in {"http", "https"}:
                continue

            title = tag.get_text(" ", strip=True)
            context_text = self._context_for_anchor(tag)
            # Las referencias API fuertes se catalogan como API y no se navegan
            # como páginas HTML. Un .json estático sin señal API sigue siendo recurso.
            if config.discover_apis and ApiDetector.is_api_url(normalized):
                continue
            if ResourceDetector.is_resource(normalized):
                resource = self._make_resource(
                    config=config,
                    raw_url=raw_href,
                    current_url=current_url,
                    title=title,
                    discovery_type=discovery_type,
                    anchor_text=title,
                    context_text=context_text,
                )
                if seen_resource_keys is not None:
                    if resource.resource_key in seen_resource_keys:
                        continue
                    seen_resource_keys.add(resource.resource_key)
                resources.append(resource)
            else:
                next_urls.append((raw_href, title))

        return resources, next_urls

    @staticmethod
    def _pagination_policy(config: SourceConfig) -> PaginationYieldPolicy:
        return PaginationYieldPolicy(
            min_pages_before_cutoff=config.pagination_min_pages,
            max_consecutive_empty=config.pagination_empty_streak,
            recent_window=config.pagination_window,
        )

    async def _seed_from_sitemaps(
        self,
        *,
        config: SourceConfig,
        frontier: DiscoveryFrontier,
        resources: List[ResourceCandidate],
        seen_resource_keys: Set[str],
        coverage: CoverageStats,
    ) -> None:
        result = await SitemapDiscovery(
            session=self._session(),
            config=config,
        ).discover(config.entrypoint)

        coverage.sitemap_documents += result.documents_checked
        coverage.sitemap_urls += len(result.entries)
        coverage.sitemap_errors += result.errors

        for entry in result.entries:
            if frontier.stop_reason == StopReason.MAX_URLS:
                break
            if ResourceDetector.is_resource(entry.url):
                resource = self._make_resource(
                    config=config,
                    raw_url=entry.url,
                    current_url=entry.sitemap_url,
                    title=os.path.basename(urlparse(entry.url).path),
                    discovery_type=DiscoveryType.SITEMAP,
                )
                if resource.resource_key in seen_resource_keys:
                    continue
                seen_resource_keys.add(resource.resource_key)
                if frontier.register_resource(resource.url):
                    resources.append(resource)
            else:
                frontier.enqueue(
                    entry.url,
                    depth=0,
                    parent_url=entry.sitemap_url,
                )

    def _enqueue_links(
        self,
        *,
        links: List[Tuple[str, str]],
        current_url: str,
        next_depth: int,
        frontier: DiscoveryFrontier,
        pagination: PaginationYieldPolicy,
    ) -> None:
        for raw_href, _title in links:
            normalized = UrlNormalizer.normalize(raw_href, base_url=current_url)
            if PaginationYieldPolicy.is_pagination_url(normalized):
                if not pagination.should_enqueue(normalized):
                    frontier.reject("PAGINATION_LOW_YIELD")
                    continue
            frontier.enqueue(
                raw_href,
                base_url=current_url,
                depth=next_depth,
                parent_url=current_url,
            )

    @staticmethod
    def _finish_coverage(
        *,
        coverage: CoverageStats,
        frontier: DiscoveryFrontier,
        pagination: PaginationYieldPolicy,
        resources: List[ResourceCandidate],
        session: SourceHttpSession,
    ) -> None:
        coverage.resources_found = len(resources)
        coverage.urls_discovered = frontier.discovered_count
        coverage.urls_rejected = frontier.rejected_count
        coverage.urls_pending = frontier.pending_count
        coverage.requests_total = session.requests_used
        coverage.spider_traps_blocked = frontier.spider_traps_blocked
        coverage.query_variants_blocked = frontier.query_variants_blocked
        coverage.pagination_pages = pagination.pages_observed
        coverage.pagination_families_stopped = pagination.families_stopped
        api_resources = [r for r in resources if r.resource_type == ResourceType.API]
        coverage.api_endpoints = len(api_resources)
        coverage.api_documentation_found = len({
            r.api.documentation_url
            for r in api_resources
            if r.api is not None and r.api.documentation_url
        })
        coverage.stop_reason = (
            frontier.stop_reason.value if frontier.stop_reason is not None else None
        )
