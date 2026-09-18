"""Descubrimiento bounded de sitemap.xml e índices de sitemap."""

from __future__ import annotations

import gzip
import io
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Set
from urllib.parse import urljoin, urlparse

from prospector_externo.domain.models import SourceConfig
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.infrastructure.http_runtime import SourceHttpSession


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    sitemap_url: str


@dataclass
class SitemapDiscoveryResult:
    entries: List[SitemapEntry] = field(default_factory=list)
    documents_checked: int = 0
    errors: int = 0
    documents: List[str] = field(default_factory=list)


class SitemapDiscovery:
    """Explora sitemap hints con límites de documentos, URLs y bytes."""

    def __init__(self, *, session: SourceHttpSession, config: SourceConfig) -> None:
        self.session = session
        self.config = config
        self._allowed_hosts = self._build_allowed_hosts(config)

    @staticmethod
    def _build_allowed_hosts(config: SourceConfig) -> Set[str]:
        hosts: Set[str] = set()
        for candidate in [config.entrypoint, *config.seeds]:
            host = (urlparse(candidate).hostname or "").lower().rstrip(".")
            if host:
                hosts.add(host)
        for host in config.allowed_hosts:
            clean = host.strip().lower().rstrip(".")
            if clean:
                hosts.add(clean)
        return hosts

    def _in_scope(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        return not self._allowed_hosts or host in self._allowed_hosts

    @staticmethod
    def _default_sitemap(entrypoint: str) -> str:
        parsed = urlparse(entrypoint)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}/sitemap.xml"

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def _maybe_decompress(self, body: bytes) -> bytes:
        if not body.startswith(b"\x1f\x8b"):
            return body
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as handle:
                decompressed = handle.read(self.config.max_sitemap_bytes + 1)
        except OSError as exc:
            raise ValueError("INVALID_GZIP_SITEMAP") from exc
        if len(decompressed) > self.config.max_sitemap_bytes:
            raise ValueError("SITEMAP_DECOMPRESSED_TOO_LARGE")
        return decompressed

    @staticmethod
    def _extract_locs(root: ET.Element) -> List[str]:
        values: List[str] = []
        for element in root.iter():
            if SitemapDiscovery._local_name(element.tag) != "loc":
                continue
            value = (element.text or "").strip()
            if value:
                values.append(value)
        return values

    async def discover(self, entrypoint: str) -> SitemapDiscoveryResult:
        result = SitemapDiscoveryResult()
        if not self.config.discover_sitemaps:
            return result
        if self.session.budget.remaining <= 0:
            return result

        decision, robot_hints = await self.session.robots_sitemaps(entrypoint)
        if not decision.allowed and decision.code not in {"ROBOTS_DISALLOWED"}:
            # Si robots es unreachable, tampoco corresponde probar sitemap a ciegas.
            result.errors += 1
            return result

        queue: Deque[str] = deque()
        scheduled: Set[str] = set()
        checked: Set[str] = set()

        candidates = [*robot_hints, self._default_sitemap(entrypoint)]
        for candidate in candidates:
            if len(scheduled) >= self.config.max_sitemap_documents:
                break
            normalized = UrlNormalizer.normalize(candidate, base_url=entrypoint)
            if normalized and self._in_scope(normalized) and normalized not in scheduled:
                scheduled.add(normalized)
                queue.append(normalized)

        seen_urls: Set[str] = set()

        while (
            queue
            and result.documents_checked < self.config.max_sitemap_documents
            and len(result.entries) < self.config.max_sitemap_urls
            and self.session.budget.remaining > 0
        ):
            sitemap_url = queue.popleft()
            if sitemap_url in checked:
                continue
            checked.add(sitemap_url)
            result.documents_checked += 1
            result.documents.append(sitemap_url)

            body, _status, error, _headers = await self.session.fetch_bytes_limited(
                sitemap_url,
                max_bytes=self.config.max_sitemap_bytes,
            )
            if error or body is None:
                result.errors += 1
                continue

            try:
                body = self._maybe_decompress(body)
                root = ET.fromstring(body)
            except (ET.ParseError, ValueError):
                result.errors += 1
                continue

            root_name = self._local_name(root.tag)
            locs = self._extract_locs(root)

            if root_name == "sitemapindex":
                for loc in locs:
                    normalized = UrlNormalizer.normalize(loc, base_url=sitemap_url)
                    if not normalized or not self._in_scope(normalized):
                        continue
                    if normalized in scheduled:
                        continue
                    if len(scheduled) >= self.config.max_sitemap_documents:
                        break
                    scheduled.add(normalized)
                    queue.append(normalized)
                continue

            if root_name != "urlset":
                result.errors += 1
                continue

            for loc in locs:
                if len(result.entries) >= self.config.max_sitemap_urls:
                    break
                normalized = UrlNormalizer.normalize(loc, base_url=sitemap_url)
                if not normalized or not self._in_scope(normalized):
                    continue
                if normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                result.entries.append(
                    SitemapEntry(url=normalized, sitemap_url=sitemap_url)
                )

        return result
