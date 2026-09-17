import asyncio

import httpx

from prospector_externo.infrastructure.async_http_client import (
    AsyncResilientHttpClient,
)
from prospector_externo.infrastructure.host_politeness import (
    HostPolitenessController,
)


def test_fetch_html_success() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url == httpx.URL("https://example.test/page")

            return httpx.Response(
                status_code=200,
                text="<html><body>ok</body></html>",
                headers={"Content-Type": "text/html"},
                request=request,
            )

        transport = httpx.MockTransport(handler)

        governor = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
        )

        client = AsyncResilientHttpClient(
            transport=transport,
            politeness_controller=governor,
        )

        try:
            html, status_code, error = await client.fetch_html(
                "https://example.test/page",
                check_robots=False,
            )
        finally:
            await client.aclose()

        assert html == "<html><body>ok</body></html>"
        assert status_code == 200
        assert error is None

        snapshot = governor.snapshot("example.test")
        assert snapshot.requests_started == 1

    asyncio.run(scenario())


def test_fetch_html_http_error_is_classified() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=404,
                text="Not Found",
                request=request,
            )

        transport = httpx.MockTransport(handler)

        governor = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
        )

        client = AsyncResilientHttpClient(
            transport=transport,
            politeness_controller=governor,
        )

        try:
            html, status_code, error = await client.fetch_html(
                "https://example.test/missing",
                check_robots=False,
            )
        finally:
            await client.aclose()

        assert html is None
        assert status_code == 404
        assert error == "HTTP_404"

    asyncio.run(scenario())


def test_fetch_html_timeout_is_classified() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                "simulated timeout",
                request=request,
            )

        transport = httpx.MockTransport(handler)

        governor = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
        )

        client = AsyncResilientHttpClient(
            transport=transport,
            politeness_controller=governor,
        )

        try:
            html, status_code, error = await client.fetch_html(
                "https://example.test/slow",
                check_robots=False,
            )
        finally:
            await client.aclose()

        assert html is None
        assert status_code is None
        assert error == "TIMEOUT"

    asyncio.run(scenario())


def test_two_clients_share_same_host_governor() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                text="ok",
                request=request,
            )

        transport = httpx.MockTransport(handler)

        governor = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
        )

        client_a = AsyncResilientHttpClient(
            transport=transport,
            politeness_controller=governor,
        )
        client_b = AsyncResilientHttpClient(
            transport=transport,
            politeness_controller=governor,
        )

        try:
            await client_a.fetch_html(
                "https://example.test/a",
                check_robots=False,
            )

            await client_b.fetch_html(
                "https://example.test/b",
                check_robots=False,
            )
        finally:
            await client_a.aclose()
            await client_b.aclose()

        snapshot = governor.snapshot("example.test")
        assert snapshot.requests_started == 2

    asyncio.run(scenario())