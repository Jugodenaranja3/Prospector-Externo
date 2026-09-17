"""
Servicio de normalización canónica de URLs para el Prospector Externo.
Garantiza deduplicación determinista e identidad estable de recursos descubiertos.
"""

import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin
from typing import List, Optional


class UrlNormalizer:
    """Normaliza URLs para evitar duplicados y generar claves canónicas reproducibles."""

    # Parámetros de seguimiento que deben eliminarse
    TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "ref", "sessionid", "phpsessid", "jsessionid"
    }

    @classmethod
    def normalize(cls, raw_url: str, base_url: Optional[str] = None) -> str:
        """
        Normaliza una URL:
        - Resuelve URLs relativas contra la base
        - Remueve fragmentos (#...)
        - Convierte esquema y dominio a minúsculas
        - Remueve puertos por defecto (:80, :443)
        - Ordena parámetros de query y elimina parámetros de tracking
        - Normaliza rutas vacías a '/'
        """
        if not raw_url:
            return ""

        url = raw_url.strip()
        if base_url:
            url = urljoin(base_url, url)

        parsed = urlparse(url)

        # Solo procesar esquemas válidos
        if parsed.scheme.lower() not in ("http", "https"):
            return url

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Remover puertos estándar
        if netloc.endswith(":80") and scheme == "http":
            netloc = netloc[:-3]
        elif netloc.endswith(":443") and scheme == "https":
            netloc = netloc[:-4]

        # Normalizar path
        path = parsed.path
        if not path:
            path = "/"

        # Filtrar y ordenar query params
        filtered_query = []
        if parsed.query:
            for k, v in sorted(parse_qsl(parsed.query, keep_blank_values=True)):
                if k.lower() not in cls.TRACKING_PARAMS:
                    filtered_query.append((k, v))
        query_str = urlencode(filtered_query)

        # Omitir fragmentos completamente
        fragment = ""

        return urlunparse((scheme, netloc, path, parsed.params, query_str, fragment))

    @classmethod
    def compute_url_hash(cls, normalized_url: str) -> str:
        """Calcula el hash SHA-256 determinista de una URL normalizada."""
        return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
