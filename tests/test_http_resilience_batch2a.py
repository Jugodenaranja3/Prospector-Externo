import asyncio
import httpx

from prospector_externo.infrastructure.async_http_client import AsyncResilientHttpClient
from prospector_externo.infrastructure.host_politeness import HostPolitenessController
from prospector_externo.infrastructure.http_policy import RetryPolicy


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
    def monotonic(self):
        return self.now
    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


def test_stricter_host_interval_cannot_be_relaxed_by_second_source():
    async def scenario():
        clock = FakeClock()
        governor = HostPolitenessController(
            default_min_interval_seconds=0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        async with governor.slot("example.test", min_interval_seconds=1.0):
            pass
        async with governor.slot("example.test", min_interval_seconds=0.1):
            pass
        assert clock.sleeps == [1.0]
        assert governor.snapshot("example.test").min_interval_seconds == 1.0
    asyncio.run(scenario())


def test_http_500_is_retryable():
    async def scenario():
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(500, request=request)
            return httpx.Response(200, text="ok", request=request)
        client = AsyncResilientHttpClient(
            politeness_controller=HostPolitenessController(default_min_interval_seconds=0),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=2, base_backoff_seconds=0),
        )
        try:
            result = await client.fetch_html("https://example.test/a", check_robots=False)
        finally:
            await client.aclose()
        assert result == ("ok", 200, None)
        assert calls == 2
    asyncio.run(scenario())


def test_source_session_html_navigation_is_not_conditional_by_default():
    async def scenario():
        headers_seen = []
        async def handler(request):
            headers_seen.append(dict(request.headers))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            return httpx.Response(200, text="ok", headers={"ETag": '"v1"'}, request=request)

        from prospector_externo.domain.models import SourceConfig
        from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        session = runtime.session_for(SourceConfig(
            source_id="src", entrypoint="https://example.test", rate_limit_seconds=0
        ))
        try:
            await session.fetch_html("https://example.test/a")
            await session.fetch_html("https://example.test/a")
        finally:
            await runtime.aclose()
        resource_headers = [h for h in headers_seen if h.get("host") == "example.test" and "text/plain" not in h.get("accept", "")]
        assert all("if-none-match" not in h for h in resource_headers)
    asyncio.run(scenario())
