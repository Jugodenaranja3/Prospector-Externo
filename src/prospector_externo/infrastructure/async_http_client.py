"""
Cliente HTTP asíncrono base del Prospector Externo.

Esta primera versión introduce únicamente:

- httpx.AsyncClient;
- integración con HostPolitenessController compartido;
- clasificación básica de errores;
- cierre explícito del cliente.

Todavía NO implementa:

- robots.txt;
- Retry-After;
- retries/backoff;
- HEAD -> GET;
- ETag / Last-Modified;
- Range requests;
- budgets.

Esas capacidades se incorporarán de forma incremental y con pruebas.
"""

from __future__ import annotations

from typing import Optional, Tuple

import httpx

from prospector_externo.infrastructure.host_politeness import (
    HostPolitenessController,
)


class AsyncResilientHttpClient:
    """
    Cliente HTTP asíncrono compartible por los workflows.

    La cortesía por host no vive dentro de cada instancia del cliente:
    se delega a un HostPolitenessController que puede ser compartido
    por toda una corrida.
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

        En esta etapa robots.txt todavía no está implementado en el
        cliente asíncrono. Si ``check_robots`` permanece habilitado,
        el método falla de forma segura en lugar de realizar una
        solicitud que pueda omitir accidentalmente esa política.
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

            host = parsed_url.host
            if not host:
                return None, None, "INVALID_URL"

        except Exception:
            return None, None, "INVALID_URL"

        try:
            async with self._politeness.slot(
                host,
                min_interval_seconds=rate_limit_delay,
            ):
                response = await self._client.get(url)

            status_code = response.status_code

            if response.is_error:
                return (
                    None,
                    status_code,
                    f"HTTP_{status_code}",
                )

            return (
                response.text,
                status_code,
                None,
            )

        except httpx.TimeoutException:
            return None, None, "TIMEOUT"

        except httpx.ConnectError:
            return None, None, "CONNECTION_ERROR"

        except httpx.RequestError:
            return None, None, "REQUEST_ERROR"

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