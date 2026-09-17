"""
Cliente HTTP asíncrono base del Prospector Externo.

Responsabilidades actuales:

- usar httpx.AsyncClient;
- aplicar cortesía compartida por host mediante HostPolitenessController;
- centralizar todas las solicitudes HTTP en una única primitiva _request();
- clasificar errores HTTP y de transporte;
- soportar GET textual y HEAD de metadatos;
- cerrar explícitamente conexiones y recursos.

Todavía NO implementa:

- robots.txt real;
- Retry-After;
- retries/backoff;
- fallback HEAD -> GET mínimo;
- ETag / Last-Modified condicional;
- Range requests;
- presupuestos globales de recorrido.

Esas capacidades se incorporarán incrementalmente sobre _request().
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import httpx

from prospector_externo.infrastructure.host_politeness import (
    HostPolitenessController,
)


HttpRequestResult = Tuple[
    Optional[httpx.Response],
    Optional[int],
    Optional[str],
]


class AsyncResilientHttpClient:
    """
    Cliente HTTP asíncrono del Prospector Externo.

    Las instancias pueden compartir un mismo HostPolitenessController,
    permitiendo que múltiples workflows respeten una política común
    de concurrencia y frecuencia por host.

    Todas las operaciones HTTP deben atravesar `_request()` para evitar
    políticas divergentes entre GET, HEAD y futuras variantes como Range.
    """

    DEFAULT_USER_AGENT = "DATAX-Prospector/1.0"

    def __init__(
        self,
        *,
        politeness_controller: HostPolitenessController,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout debe ser mayor que cero")

        self._politeness = politeness_controller

        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
            },
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=True,
        )

        self._closed = False

    async def _request(
        self,
        method: str,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        headers: Optional[Dict[str, str]] = None,
    ) -> HttpRequestResult:
        """
        Primitiva única para realizar solicitudes HTTP.

        Toda operación HTTP pública del cliente debe terminar pasando por
        este método.

        En esta etapa aplica:

        - validación básica de URL;
        - política fail-safe mientras robots.txt no esté implementado;
        - cortesía compartida por host;
        - ejecución mediante httpx.AsyncClient;
        - clasificación centralizada de errores.

        Retorna:

            (response, status_code, error_code)

        Cuando existe un error controlado, ``response`` será None.
        """

        if self._closed:
            raise RuntimeError(
                "AsyncResilientHttpClient ya fue cerrado"
            )

        if check_robots:
            return (
                None,
                None,
                "ROBOTS_POLICY_NOT_IMPLEMENTED",
            )

        try:
            parsed_url = httpx.URL(url)
        except Exception:
            return None, None, "INVALID_URL"

        host = parsed_url.host

        if not host:
            return None, None, "INVALID_URL"

        normalized_method = method.strip().upper()

        if not normalized_method:
            return None, None, "INVALID_HTTP_METHOD"

        try:
            async with self._politeness.slot(
                host,
                min_interval_seconds=rate_limit_delay,
            ):
                response = await self._client.request(
                    normalized_method,
                    url,
                    headers=headers,
                )

            status_code = response.status_code

            if response.is_error:
                return (
                    None,
                    status_code,
                    f"HTTP_{status_code}",
                )

            return (
                response,
                status_code,
                None,
            )

        except httpx.TimeoutException:
            return None, None, "TIMEOUT"

        except httpx.ConnectError:
            return None, None, "CONNECTION_ERROR"

        except httpx.RequestError:
            return None, None, "REQUEST_ERROR"

    async def fetch_html(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """
        Obtiene contenido textual mediante GET.

        Retorna:

            (html_text, status_code, error_code)
        """

        response, status_code, error = await self._request(
            "GET",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
        )

        if response is None:
            return (
                None,
                status_code,
                error,
            )

        return (
            response.text,
            status_code,
            None,
        )

    async def fetch_headers(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
    ) -> Tuple[
        Optional[Dict[str, str]],
        Optional[int],
        Optional[str],
    ]:
        """
        Obtiene metadatos mediante HEAD.

        Todavía no implementa fallback HEAD -> GET ante 405/501.
        Ese comportamiento se incorporará posteriormente sobre la misma
        primitiva `_request()`.

        Retorna:

            (headers, status_code, error_code)
        """

        response, status_code, error = await self._request(
            "HEAD",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
        )

        if response is None:
            return (
                None,
                status_code,
                error,
            )

        normalized_headers = {
            key.lower(): value
            for key, value in response.headers.items()
        }

        return (
            normalized_headers,
            status_code,
            None,
        )

    async def aclose(self) -> None:
        """Cierra conexiones y recursos mantenidos por httpx."""

        if self._closed:
            return

        await self._client.aclose()
        self._closed = True

    async def __aenter__(self) -> "AsyncResilientHttpClient":
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        await self.aclose()