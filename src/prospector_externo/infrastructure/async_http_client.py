"""
Cliente HTTP asíncrono base del Prospector Externo.

Responsabilidades actuales:

- usar httpx.AsyncClient;
- aplicar cortesía compartida por host mediante HostPolitenessController;
- centralizar las solicitudes HTTP en una única primitiva _request();
- clasificar errores HTTP y de transporte;
- soportar GET textual;
- soportar HEAD para metadatos;
- aplicar fallback HEAD -> GET mínimo ante 405/501;
- evitar consumir el body durante el fallback de metadatos;
- cerrar explícitamente conexiones y recursos.

Todavía NO implementa:

- robots.txt real;
- Retry-After;
- retries/backoff;
- ETag / Last-Modified condicional;
- budgets globales;
- Range genérico para inspección de archivos.

Estas capacidades se incorporarán incrementalmente.
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

    Todas las operaciones HTTP pasan por `_request()` para compartir:

    - validación;
    - cortesía por host;
    - transporte HTTP;
    - clasificación de errores.

    El HostPolitenessController puede compartirse entre diferentes
    instancias y workflows durante una misma corrida.
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
        stream: bool = False,
    ) -> HttpRequestResult:
        """
        Primitiva única para solicitudes HTTP.

        Parámetro ``stream``:

        - False:
          respuesta convencional administrada por httpx.

        - True:
          devuelve la respuesta sin consumir su body. El caller
          debe ejecutar ``await response.aclose()``.

        Retorna:

            (response, status_code, error_code)
        """

        if self._closed:
            raise RuntimeError(
                "AsyncResilientHttpClient ya fue cerrado"
            )

        # Mientras RobotsPolicy no esté implementada, fallamos de forma
        # segura cuando se solicita explícitamente su comprobación.
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
                if stream:
                    request = self._client.build_request(
                        normalized_method,
                        url,
                        headers=headers,
                    )

                    response = await self._client.send(
                        request,
                        stream=True,
                    )
                else:
                    response = await self._client.request(
                        normalized_method,
                        url,
                        headers=headers,
                    )

            status_code = response.status_code

            if response.is_error:
                if stream:
                    await response.aclose()

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
    ) -> Tuple[
        Optional[str],
        Optional[int],
        Optional[str],
    ]:
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
        Obtiene metadatos HTTP.

        Flujo:

        1. intenta HEAD;
        2. si HEAD responde 405 o 501:
           - ejecuta GET;
           - solicita Range: bytes=0-0;
           - usa streaming;
           - NO consume el body;
           - cierra inmediatamente la respuesta;
        3. otros errores HEAD no activan fallback.

        Retorna:

            (headers, status_code, error_code)
        """

        response, status_code, error = await self._request(
            "HEAD",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
        )

        # HEAD exitoso.
        if response is not None:
            return (
                self._normalize_headers(response.headers),
                status_code,
                None,
            )

        # Solo 405/501 justifican fallback.
        if status_code not in {405, 501}:
            return (
                None,
                status_code,
                error,
            )

        fallback_response, fallback_status, fallback_error = (
            await self._request(
                "GET",
                url,
                rate_limit_delay=rate_limit_delay,
                check_robots=check_robots,
                headers={
                    "Range": "bytes=0-0",
                },
                stream=True,
            )
        )

        if fallback_response is None:
            return (
                None,
                fallback_status,
                fallback_error,
            )

        try:
            normalized_headers = self._normalize_headers(
                fallback_response.headers
            )

            return (
                normalized_headers,
                fallback_status,
                None,
            )

        finally:
            # Fundamental:
            # no consumimos el body del recurso.
            await fallback_response.aclose()

    @staticmethod
    def _normalize_headers(
        headers: httpx.Headers,
    ) -> Dict[str, str]:
        """
        Convierte cabeceras HTTP a un diccionario con claves lowercase.
        """

        return {
            key.lower(): value
            for key, value in headers.items()
        }

    async def aclose(self) -> None:
        """Cierra conexiones mantenidas por httpx."""

        if self._closed:
            return

        await self._client.aclose()
        self._closed = True

    async def __aenter__(
        self,
    ) -> "AsyncResilientHttpClient":
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        await self.aclose()