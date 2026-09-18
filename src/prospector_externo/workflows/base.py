"""Base para workflows HTTP del nuevo Discovery Engine."""

from __future__ import annotations

import os
import re
import calendar
from typing import List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse, urljoin

from bs4 import BeautifulSoup

from prospector_externo.domain.models import (
    ChangeStatus,
    DiscoveryType,
    ResourceCandidate,
    SourceConfig,
)
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.infrastructure.http_runtime import SourceHttpSession
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
                # Útil para download managers: ?path=archivo.pdf
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

    def _make_resource(
        self,
        *,
        config: SourceConfig,
        raw_url: str,
        current_url: str,
        title: str,
        discovery_type: DiscoveryType,
        anchor_text: Optional[str] = None,
    ) -> ResourceCandidate:
        normalized = UrlNormalizer.normalize(raw_url, base_url=current_url)
        ext = ResourceDetector.extension_for(normalized)
        parsed = urlparse(normalized)
        effective_title = title.strip() or os.path.basename(parsed.path) or normalized
        period = self._extract_period_from_text(f"{effective_title} {unquote(normalized)}")
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
            discovery_method=(
                "commented_html_link"
                if discovery_type == DiscoveryType.COMMENTED_HTML
                else "html_link"
            ),
            anchor_text=anchor_text or title.strip() or None,
        )

    def _extract_resources_and_links(
        self,
        html_content: str,
        current_url: str,
        config: SourceConfig,
        *,
        discovery_type: DiscoveryType,
        seen_resource_keys: Optional[Set[str]] = None,
    ) -> Tuple[List[ResourceCandidate], List[Tuple[str, str]]]:
        """Retorna recursos y enlaces navegables. No hace requests extra ni descarga archives."""

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
            if ResourceDetector.is_resource(normalized):
                resource = self._make_resource(
                    config=config,
                    raw_url=raw_href,
                    current_url=current_url,
                    title=title,
                    discovery_type=discovery_type,
                    anchor_text=title,
                )
                if seen_resource_keys is not None:
                    if resource.resource_key in seen_resource_keys:
                        continue
                    seen_resource_keys.add(resource.resource_key)
                resources.append(resource)
            else:
                next_urls.append((raw_href, title))

        return resources, next_urls
