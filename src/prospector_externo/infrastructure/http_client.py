"""
Cliente HTTP resiliente con respeto a robots.txt, override auditable,
rate limiting por dominio, reintentos exponenciales y fallback HEAD -> GET.
"""

import time
import logging
import threading
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from typing import Dict, Optional, Tuple, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("prospector.infrastructure.http")


class ResilientHttpClient:
    """Cliente HTTP con resiliencia, control de robots.txt y rate-limiting."""

    DEFAULT_USER_AGENT = "DataX-ProspectorBot/1.0 (+http://datax.bo/bot; bot@datax.bo)"

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 15,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        default_rate_limit_seconds: float = 1.0
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.default_rate_limit = default_rate_limit_seconds

        # Configurar sesión Requests con pool concurrente y reintentos para 5xx y errores de red
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET"]
        )
        adapter = HTTPAdapter(pool_connections=30, pool_maxsize=30, max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Cache de robots.txt por dominio y cerrojos de concurrencia
        self._robots_cache: Dict[str, Optional[RobotFileParser]] = {}
        self._last_request_time: Dict[str, float] = {}
        self._rate_limit_lock = threading.Lock()

    def _apply_rate_limit(self, domain: str, delay: float) -> None:
        """Garantiza una pausa mínima entre solicitudes dirigidas al mismo dominio de forma thread-safe."""
        if delay <= 0:
            return

        now = time.time()
        sleep_time = 0.0
        with self._rate_limit_lock:
            if domain in self._last_request_time:
                elapsed = now - self._last_request_time[domain]
                if elapsed < delay:
                    sleep_time = delay - elapsed
            self._last_request_time[domain] = now + sleep_time

        if sleep_time > 0:
            time.sleep(sleep_time)

    def _get_robots_parser(self, domain: str, scheme: str) -> Optional[RobotFileParser]:
        """Descarga e inicializa el parser de robots.txt para un dominio."""
        if domain in self._robots_cache:
            return self._robots_cache[domain]

        robots_url = f"{scheme}://{domain}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            resp = self.session.get(robots_url, timeout=5, headers={"User-Agent": self.user_agent})
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
                self._robots_cache[domain] = parser
                return parser
        except Exception:
            pass

        self._robots_cache[domain] = None
        return None

    def is_allowed_by_robots(
        self,
        url: str,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifica si la URL está permitida por robots.txt.
        Retorna (permitido, motivo_si_aplica).
        """
        if ignore_robots_txt:
            msg = f"Robots.txt ignorado por override: {robots_override_reason or 'Sin justificación'}"
            return True, msg

        parsed = urlparse(url)
        domain = parsed.netloc
        scheme = parsed.scheme or "https"

        parser = self._get_robots_parser(domain, scheme)
        if parser is None:
            return True, None

        allowed = parser.can_fetch(self.user_agent, url)
        if not allowed:
            return False, f"Bloqueado por robots.txt en {domain}"
        return True, None

    def fetch_html(
        self,
        url: str,
        rate_limit_delay: Optional[float] = None,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """
        Obtiene el código HTML de una URL.
        Retorna: (html_text, status_code, error_message).
        """
        allowed, reason = self.is_allowed_by_robots(url, ignore_robots_txt, robots_override_reason)
        if not allowed:
            return None, None, reason

        parsed = urlparse(url)
        delay = rate_limit_delay if rate_limit_delay is not None else self.default_rate_limit
        self._apply_rate_limit(parsed.netloc, delay)

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.text, resp.status_code, None
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            return None, code, f"HTTP_{code}"
        except requests.exceptions.Timeout:
            return None, None, "TIMEOUT"
        except requests.exceptions.ConnectionError:
            return None, None, "CONNECTION_ERROR"
        except Exception as e:
            return None, None, f"REQUEST_ERROR: {str(e)}"

    def fetch_headers(
        self,
        url: str,
        rate_limit_delay: Optional[float] = 0.05
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Obtiene cabeceras de recurso con HEAD, aplicando fallback a GET con streaming
        cuando el servidor responda 405 (Method Not Allowed) o 501 (Not Implemented).
        """
        parsed = urlparse(url)
        delay = rate_limit_delay if rate_limit_delay is not None else 0.05
        self._apply_rate_limit(parsed.netloc, delay)

        headers_result: Dict[str, Any] = {}
        try:
            resp = self.session.head(url, timeout=self.timeout, allow_redirects=True)
            if resp.status_code in (405, 501):
                # Fallback a GET truncado
                with self.session.get(url, timeout=self.timeout, stream=True) as stream_resp:
                    return self._extract_headers_dict(stream_resp), None
            resp.raise_for_status()
            return self._extract_headers_dict(resp), None
        except Exception as e:
            # Intentar fallback GET si HEAD falló
            try:
                with self.session.get(url, timeout=self.timeout, stream=True) as stream_resp:
                    return self._extract_headers_dict(stream_resp), None
            except Exception as e2:
                return {}, str(e2)

    def fetch_bytes(
        self,
        url: str,
        max_bytes: int = 50 * 1024 * 1024,
        rate_limit_delay: Optional[float] = None
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Descarga un archivo en memoria (máximo max_bytes, default 50MB) para inspección de comprimidos.
        Retorna: (bytes_data, error_msg).
        """
        parsed = urlparse(url)
        delay = rate_limit_delay if rate_limit_delay is not None else self.default_rate_limit
        self._apply_rate_limit(parsed.netloc, delay)

        try:
            with self.session.get(url, timeout=self.timeout, stream=True) as resp:
                resp.raise_for_status()
                content_len = resp.headers.get("content-length")
                if content_len and int(content_len) > max_bytes:
                    return None, f"Excede el límite máximo de memoria ({max_bytes} bytes)"

                data = bytearray()
                for chunk in resp.iter_content(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        return None, f"Descarga excedió el límite máximo de memoria ({max_bytes} bytes)"
                return bytes(data), None
        except Exception as e:
            return None, str(e)

    def _extract_headers_dict(self, resp: requests.Response) -> Dict[str, Any]:
        """Extrae cabeceras relevantes de metadatos."""
        content_len = resp.headers.get("Content-Length")
        return {
            "content_type": resp.headers.get("Content-Type"),
            "content_length_bytes": int(content_len) if content_len and content_len.isdigit() else None,
            "last_modified": resp.headers.get("Last-Modified"),
            "etag": resp.headers.get("ETag")
        }
