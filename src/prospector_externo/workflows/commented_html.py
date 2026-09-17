"""
Plugin de workflow para portales que ocultan enlaces en comentarios HTML (CommentedHtmlWorkflow).
Especializado para portales como la Bolsa Boliviana de Valores (BBV).
"""

import os
import re
import logging
from typing import Set, List, Optional
from collections import deque
from bs4 import BeautifulSoup, Comment
from urllib.parse import urlparse, urljoin

from prospector_externo.workflows.base import BaseWorkflow
from prospector_externo.domain.models import SourceConfig, DiscoveredUrl, DiscoveryType, ResourceCandidate, ChangeStatus
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.observations import CoverageStats

logger = logging.getLogger("prospector.workflows.commented_html")


class CommentedHtmlWorkflow(BaseWorkflow):
    """Workflow especializado en extraer contenido del DOM y de bloques comentados HTML."""

    def __init__(self, max_depth: int = 2, max_pages: int = 30):
        super().__init__()
        self.max_depth = max_depth
        self.max_pages = max_pages

    def _extract_from_comments(
        self,
        html_content: str,
        current_url: str,
        config: SourceConfig,
        seen_resource_keys: Optional[Set[str]] = None
    ) -> List[ResourceCandidate]:
        """Busca y parsea fragmentos HTML dentro de comentarios <!-- ... -->."""
        soup = BeautifulSoup(html_content, "html.parser")
        comments = soup.find_all(string=lambda text: isinstance(text, Comment))
        allowed_exts = set(ext.lower() for ext in config.allowed_extensions)

        commented_resources: List[ResourceCandidate] = []

        for comment_text in comments:
            # Si el comentario contiene posibles enlaces o extensiones
            if any(ext in comment_text.lower() for ext in allowed_exts) or "href=" in comment_text:
                comment_soup = BeautifulSoup(comment_text, "html.parser")
                for a in comment_soup.find_all("a", href=True):
                    raw_href = a["href"].strip()
                    if not raw_href or raw_href.startswith(("#", "javascript:")):
                        continue
                    full_url = urljoin(current_url, raw_href)
                    normalized = UrlNormalizer.normalize(full_url)

                    # Omitir rutas excluidas por configuración
                    if any(kw.lower() in normalized.lower() for kw in config.excluded_path_keywords):
                        continue

                    parsed = urlparse(normalized)
                    file_ext = os.path.splitext(parsed.path)[1].lower()

                    if file_ext in allowed_exts or self.archive_extractor.is_archive(normalized):
                        resource_key = UrlNormalizer.compute_url_hash(normalized)
                        if seen_resource_keys is not None:
                            if resource_key in seen_resource_keys:
                                continue
                            seen_resource_keys.add(resource_key)

                        title = a.get_text(strip=True) or os.path.basename(parsed.path)
                        period = self._extract_period_from_text(f"{title} {parsed.path}")

                        headers, _ = self.http_client.fetch_headers(
                            normalized,
                            rate_limit_delay=config.rate_limit_seconds
                        )

                        res = ResourceCandidate(
                            resource_key=resource_key,
                            url=normalized,
                            source_id=config.source_id,
                            title=title,
                            file_extension=file_ext,
                            content_type=headers.get("content_type"),
                            content_length_bytes=headers.get("content_length_bytes"),
                            last_modified_header=headers.get("last_modified"),
                            etag=headers.get("etag"),
                            discovered_from_url=current_url,
                            period_label=period,
                            change_status=ChangeStatus.NEW
                        )
                        commented_resources.append(res)

                        if self.archive_extractor.is_archive(normalized):
                            commented_resources.extend(
                                self._process_archive(normalized, config.source_id, config)
                            )

        return commented_resources

    def run(self, config: SourceConfig) -> ExtractionResult:
        logger.info(f"Iniciando CommentedHtmlWorkflow para fuente: [{config.source_id}]")
        coverage = CoverageStats()
        discovered_urls: List[DiscoveredUrl] = []
        all_resources = []

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

            html_text, status_code, err = self.http_client.fetch_html(
                current_url,
                rate_limit_delay=config.rate_limit_seconds,
                ignore_robots_txt=config.ignore_robots_txt,
                robots_override_reason=config.robots_override_reason
            )

            if err:
                coverage.urls_failed += 1
                if len(visited) == 1:
                    return ExtractionResult(
                        source_id=config.source_id,
                        success=False,
                        failure_code=err,
                        error_message=f"Fallo en semilla inicial: {err}",
                        coverage=coverage
                    )
                continue

            discovered_urls.append(DiscoveredUrl(
                normalized_url=current_url,
                raw_url=current_url,
                source_id=config.source_id,
                discovery_type=DiscoveryType.COMMENTED_HTML,
                parent_url=parent_url,
                http_status=status_code
            ))

            # 1. Extraer del DOM normal
            dom_resources, next_urls = self._extract_resources_and_links(
                html_text,
                current_url,
                config,
                discovery_type=DiscoveryType.COMMENTED_HTML,
                seen_resource_keys=seen_resource_keys
            )
            # 2. Extraer de comentarios HTML
            comment_resources = self._extract_from_comments(
                html_text,
                current_url,
                config,
                seen_resource_keys=seen_resource_keys
            )

            # Agregar recursos descubiertos (ya deduplicados contra seen_resource_keys)
            for res in dom_resources + comment_resources:
                all_resources.append(res)
                coverage.resources_found += 1

            if depth < self.max_depth:
                for nxt in next_urls:
                    if nxt not in visited:
                        queue.append((nxt, depth + 1, current_url))

        coverage.urls_discovered = len(discovered_urls) + len(all_resources)
        logger.info(f"CommentedHtmlWorkflow finalizado para [{config.source_id}]: {len(all_resources)} recursos hallados.")

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=all_resources,
            discovered_urls=discovered_urls,
            coverage=coverage
        )
