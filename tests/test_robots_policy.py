import asyncio

import httpx

from prospector_externo.infrastructure.async_http_client import AsyncResilientHttpClient
from prospector_externo.infrastructure.host_politeness import HostPolitenessController
from prospector_externo.infrastructure.robots_policy import RobotsFetchResult, RobotsPolicy


def test_robots_allowed_and_disallowed() -> None:
    async def scenario() -> None:
        async def fetcher(url: str) -> RobotsFetchResult:
            return RobotsFetchResult(
                200,
                "User-agent: *\nDisallow: /private/\nAllow: /\n",
                None,
            )

        policy = RobotsPolicy(fetcher=fetcher)
        allowed = await policy.check(
            "https://example.test/public/data",
            user_agent="DATAX-Prospector",
        )
        blocked = await policy.check(
            "https://example.test/private/report.pdf",
            user_agent="DATAX-Prospector",
        )
        assert allowed.allowed is True
        assert allowed.code == "ROBOTS_ALLOWED"
        assert blocked.allowed is False
        assert blocked.code == "ROBOTS_DISALLOWED"

    asyncio.run(scenario())


def test_robots_404_and_410_allow_access() -> None:
    async def scenario() -> None:
        for status in (404, 410):
            async def fetcher(url: str, status=status) -> RobotsFetchResult:
                return RobotsFetchResult(status, None, None)

            policy = RobotsPolicy(fetcher=fetcher)
            decision = await policy.check(
                "https://example.test/data",
                user_agent="DATAX-Prospector",
            )
            assert decision.allowed is True
            assert decision.code == "ROBOTS_UNAVAILABLE_ALLOW"

    asyncio.run(scenario())


def test_robots_5xx_network_and_429_block_temporarily() -> None:
    async def scenario() -> None:
        cases = [
            RobotsFetchResult(503, None, None),
            RobotsFetchResult(429, None, None),
            RobotsFetchResult(None, None, "CONNECTION_ERROR"),
        ]
        for result in cases:
            async def fetcher(url: str, result=result) -> RobotsFetchResult:
                return result

            policy = RobotsPolicy(fetcher=fetcher)
            decision = await policy.check(
                "https://example.test/data",
                user_agent="DATAX-Prospector",
            )
            assert decision.allowed is False
            assert decision.code == "ROBOTS_UNREACHABLE"

    asyncio.run(scenario())


def test_override_requires_reason_and_valid_override_skips_fetch() -> None:
    async def scenario() -> None:
        calls = 0

        async def fetcher(url: str) -> RobotsFetchResult:
            nonlocal calls
            calls += 1
            return RobotsFetchResult(200, "User-agent: *\nDisallow: /\n", None)

        policy = RobotsPolicy(fetcher=fetcher)
        rejected = await policy.check(
            "https://example.test/data",
            user_agent="DATAX-Prospector",
            ignore_robots_txt=True,
            robots_override_reason="",
        )
        assert rejected.allowed is False
        assert rejected.code == "ROBOTS_OVERRIDE_REASON_REQUIRED"

        allowed = await policy.check(
            "https://example.test/data",
            user_agent="DATAX-Prospector",
            ignore_robots_txt=True,
            robots_override_reason="Autorización formal DATAX",
        )
        assert allowed.allowed is True
        assert allowed.code == "ROBOTS_OVERRIDE"
        assert allowed.override_reason == "Autorización formal DATAX"
        assert calls == 0

    asyncio.run(scenario())


def test_robots_cache_is_per_origin() -> None:
    async def scenario() -> None:
        calls = 0

        async def fetcher(url: str) -> RobotsFetchResult:
            nonlocal calls
            calls += 1
            return RobotsFetchResult(200, "User-agent: *\nAllow: /\n", None)

        policy = RobotsPolicy(fetcher=fetcher)
        first = await policy.check(
            "https://example.test/a",
            user_agent="DATAX-Prospector",
        )
        second = await policy.check(
            "https://example.test/b",
            user_agent="DATAX-Prospector",
        )
        assert first.allowed is True
        assert second.allowed is True
        assert second.from_cache is True
        assert calls == 1

    asyncio.run(scenario())


def test_async_client_checks_and_caches_robots_before_resources() -> None:
    async def scenario() -> None:
        observed = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed.append((request.method, request.url.path))
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nAllow: /\n",
                    request=request,
                )
            return httpx.Response(200, text="ok", request=request)

        governor = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
        )
        client = AsyncResilientHttpClient(
            transport=httpx.MockTransport(handler),
            politeness_controller=governor,
        )

        try:
            first = await client.fetch_html("https://example.test/public/a")
            second = await client.fetch_html("https://example.test/public/b")
        finally:
            await client.aclose()

        assert first == ("ok", 200, None)
        assert second == ("ok", 200, None)
        assert observed == [
            ("GET", "/robots.txt"),
            ("GET", "/public/a"),
            ("GET", "/public/b"),
        ]

    asyncio.run(scenario())


def test_async_client_never_fetches_disallowed_resource() -> None:
    async def scenario() -> None:
        observed = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed.append((request.method, request.url.path))
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nDisallow: /private/\n",
                    request=request,
                )
            raise AssertionError("El recurso bloqueado no debe solicitarse")

        governor = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
        )
        client = AsyncResilientHttpClient(
            transport=httpx.MockTransport(handler),
            politeness_controller=governor,
        )

        try:
            html, status, error = await client.fetch_html(
                "https://example.test/private/report"
            )
        finally:
            await client.aclose()

        assert html is None
        assert status is None
        assert error == "ROBOTS_DISALLOWED"
        assert observed == [("GET", "/robots.txt")]

    asyncio.run(scenario())
