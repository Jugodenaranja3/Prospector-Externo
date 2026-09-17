"""
Workflow base con cola de descubrimiento (DiscoveryQueue), extracción de enlaces,
resolución canónica, extracción de metadatos e inspección de comprimidos en memoria.
"""

import os
import re
import calendar
import logging
from typing import List, Set, Dict, Any, Optional, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

from prospector_externo.kernel.workflow_port import SourceWorkflow
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.models import SourceConfig, ResourceCandidate, DiscoveredUrl, DiscoveryType, ChangeStatus
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.infrastructure.http_client import ResilientHttpClient
from prospector_externo.infrastructure.archive_extractor import ArchiveExtractor
from prospector_externo.infrastructure.concurrency import AsyncWorkerPool

logger = logging.getLogger("prospector.workflows.base")


class BaseWorkflow(SourceWorkflow):
    """Clase base reutilizable para workflows de descubrimiento."""

    MONTHS_ES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
    }

    def __init__(
        self,
        http_client: Optional[ResilientHttpClient] = None,
        worker_pool: Optional[AsyncWorkerPool] = None
    ):
        self.http_client = http_client or ResilientHttpClient()
        self.archive_extractor = ArchiveExtractor()
        self.worker_pool = worker_pool or AsyncWorkerPool(max_http_workers=8, max_browser_workers=2)

    def _extract_period_from_text(self, text: str) -> Optional[str]:
        """Extrae etiqueta de período (ej. '2024-05') analizando meses en español y años."""
        low = text.lower()
        for name, num in self.MONTHS_ES.items():
            if name in low:
                year_m = re.search(r"\b(20\d{2})\b", low)
                if year_m:
                    year = int(year_m.group(1))
                    return f"{year:04d}-{num:02d}"
        # Regex _MM_YYYY o _YYYY_MM
        m = re.search(r"[_\-](?P<m>0[1-9]|1[0-2])[_\-](?P<y>20\d{2})\b", low)
        if m:
            return f"{int(m.group('y')):04d}-{int(m.group('m')):02d}"
        return None

    def _process_archive(
        self,
        archive_url: str,
        source_id: str,
        config: SourceConfig
    ) -> List[ResourceCandidate]:
        """Descarga transitoriamente en memoria un archivo .zip/.tar y extrae sus recursos internos."""
        logger.info(f"Inspeccionando archivo comprimido en memoria: {archive_url}")
        archive_bytes, err = self.http_client.fetch_bytes(
            archive_url,
            rate_limit_delay=config.rate_limit_seconds
        )
        if not archive_bytes:
            logger.warning(f"No se pudo descargar comprimido {archive_url}: {err}")
            return []

        entries = self.archive_extractor.inspect_archive_bytes(archive_bytes, filename_hint=archive_url)
        extracted_resources: List[ResourceCandidate] = []

        allowed_exts = set(ext.lower() for ext in config.allowed_extensions)

        for entry in entries:
            ext = entry.extension
            # Si el archivo interno coincide con las extensiones de interés
            if ext in allowed_exts:
                norm_archive_url = UrlNormalizer.normalize(archive_url)
                internal_key = f"{norm_archive_url}#{entry.filename}"
                resource_key = UrlNormalizer.compute_url_hash(internal_key)

                period = self._extract_period_from_text(entry.filename)
                title = os.path.basename(entry.filename)

                extracted_resources.append(ResourceCandidate(
                    resource_key=resource_key,
                    url=archive_url,
                    source_id=source_id,
                    title=title,
                    file_extension=ext,
                    content_length_bytes=entry.size_bytes,
                    discovered_from_url=archive_url,
                    extracted_from_archive=archive_url,
                    period_label=period,
                    content_hash=None,
                    change_status=ChangeStatus.NEW
                ))

        logger.info(f"Se extrajeron {len(extracted_resources)} recursos del comprimido {archive_url}")
        return extracted_resources

    def _extract_resources_and_links(
        self,
        html_content: str,
        current_url: str,
        config: SourceConfig,
        discovery_type: DiscoveryType = DiscoveryType.HTML,
        seen_resource_keys: Optional[Set[str]] = None
    ) -> Tuple[List[ResourceCandidate], List[str]]:
        """
        Parsea HTML buscando enlaces `<a href="...">`.
        Separa entre URLs de recursos descargables y enlaces a otras páginas dentro de dominio.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        allowed_exts = set(ext.lower() for ext in config.allowed_extensions)
        domain = urlparse(config.entrypoint).netloc.lower()

        resources: List[ResourceCandidate] = []
        next_page_urls: List[str] = []
        pending_candidates: List[Dict[str, Any]] = []

        for a_tag in soup.find_all("a", href=True):
            raw_href = a_tag["href"].strip()
            if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            full_url = urljoin(current_url, raw_href)
            normalized = UrlNormalizer.normalize(full_url)

            # Omitir rutas excluidas por configuración (ej. /historia/, /mision-y-vision/, /contacto/)
            if any(kw.lower() in normalized.lower() for kw in config.excluded_path_keywords):
                continue

            parsed = urlparse(normalized)

            # Verificar si el enlace apunta a un archivo descargable
            file_ext = os.path.splitext(parsed.path)[1].lower()

            if file_ext in allowed_exts or self.archive_extractor.is_archive(normalized):
                resource_key = UrlNormalizer.compute_url_hash(normalized)
                if seen_resource_keys is not None:
                    if resource_key in seen_resource_keys:
                        continue
                    seen_resource_keys.add(resource_key)

                title = a_tag.get_text(strip=True) or os.path.basename(parsed.path)
                period = self._extract_period_from_text(f"{title} {parsed.path}")

                pending_candidates.append({
                    "resource_key": resource_key,
                    "url": normalized,
                    "title": title,
                    "file_ext": file_ext,
                    "period": period,
                    "is_archive": self.archive_extractor.is_archive(normalized)
                })

            elif parsed.netloc.lower() == domain:
                # Es una subpágina dentro del dominio
                next_page_urls.append(normalized)

        # Resolver cabeceras de candidatos en paralelo con AsyncWorkerPool
        if pending_candidates:
            def _fetch_meta(item: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
                headers, _ = self.http_client.fetch_headers(item["url"], rate_limit_delay=0.05)
                return item, headers

            resolved = self.worker_pool.run_parallel(_fetch_meta, pending_candidates, max_workers=8)
            for item, headers in resolved:
                res = ResourceCandidate(
                    resource_key=item["resource_key"],
                    url=item["url"],
                    source_id=config.source_id,
                    title=item["title"],
                    file_extension=item["file_ext"],
                    content_type=headers.get("content_type"),
                    content_length_bytes=headers.get("content_length_bytes"),
                    last_modified_header=headers.get("last_modified"),
                    etag=headers.get("etag"),
                    discovered_from_url=current_url,
                    period_label=item["period"],
                    change_status=ChangeStatus.NEW
                )
                resources.append(res)

                # Si es un contenedor comprimido (.zip/.tar), inspeccionar su contenido interno en RAM
                if item["is_archive"]:
                    internal_res = self._process_archive(item["url"], config.source_id, config)
                    for in_item in internal_res:
                        if seen_resource_keys is not None:
                            if in_item.resource_key in seen_resource_keys:
                                continue
                            seen_resource_keys.add(in_item.resource_key)
                        resources.append(in_item)

        return resources, next_page_urls
