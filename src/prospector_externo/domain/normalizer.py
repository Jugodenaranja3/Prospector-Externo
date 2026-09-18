"""Normalización canónica e identidad estable de URLs."""

import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin
from typing import Optional


class UrlNormalizer:
    """Normaliza URLs sin destruir parámetros potencialmente semánticos de datasets."""

    TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "sessionid", "phpsessid", "jsessionid",
    }

    @classmethod
    def normalize(cls, raw_url: str, base_url: Optional[str] = None) -> str:
        if not raw_url:
            return ""

        url = raw_url.strip()
        if base_url:
            url = urljoin(base_url, url)

        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return url

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        if netloc.endswith(":80") and scheme == "http":
            netloc = netloc[:-3]
        elif netloc.endswith(":443") and scheme == "https":
            netloc = netloc[:-4]

        path = parsed.path or "/"

        filtered_query = []
        if parsed.query:
            for k, v in sorted(parse_qsl(parsed.query, keep_blank_values=True)):
                if k.lower() not in cls.TRACKING_PARAMS:
                    filtered_query.append((k, v))

        return urlunparse(
            (scheme, netloc, path, parsed.params, urlencode(filtered_query), "")
        )

    @classmethod
    def compute_url_hash(cls, normalized_url: str) -> str:
        return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()

    @classmethod
    def compute_resource_key(cls, source_id: str, normalized_url: str) -> str:
        """Identidad de catálogo: la misma URL puede pertenecer a dos source_id lógicos."""
        value = f"{source_id}|{normalized_url}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
