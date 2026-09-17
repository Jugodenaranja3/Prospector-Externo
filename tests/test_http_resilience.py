
import asyncio
import httpx

from prospector_externo.infrastructure.async_http_client import AsyncResilientHttpClient
from prospector_externo.infrastructure.host_politeness import HostPolitenessController
from prospector_externo.infrastructure.http_policy import (
    ConditionalRequestCache,
    HttpTimeoutConfig,
    RequestBudget,
    RetryPolicy,
)

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

class GuardedAsyncStream(httpx.AsyncByteStream):
    def __init__(self):
        self.was_read=False
        self.was_closed=False
    async def __aiter__(self):
        self.was_read=True
        yield b"x"
    async def aclose(self):
        self.was_closed=True

def test_retry_after_is_global_via_governor():
    async def scenario():
        clock=FakeClock()
        governor=HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        calls=0
        async def handler(request):
            nonlocal calls
            calls+=1
            if calls==1:
                return httpx.Response(503,headers={"Retry-After":"2"},request=request)
            return httpx.Response(200,text="ok",request=request)
        client=AsyncResilientHttpClient(
            politeness_controller=governor,
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=3,base_backoff_seconds=0.5),
        )
        try:
            result=await client.fetch_html("https://example.test/a",check_robots=False)
        finally:
            await client.aclose()
        assert result==("ok",200,None)
        assert calls==2
        assert clock.sleeps==[2.0]
        assert governor.snapshot("example.test").requests_started==2
    asyncio.run(scenario())

def test_404_is_not_retried():
    async def scenario():
        calls=0
        async def handler(request):
            nonlocal calls
            calls+=1
            return httpx.Response(404,request=request)
        client=AsyncResilientHttpClient(
            politeness_controller=HostPolitenessController(default_min_interval_seconds=0),
            transport=httpx.MockTransport(handler),
        )
        try:
            result=await client.fetch_html("https://example.test/x",check_robots=False)
        finally:
            await client.aclose()
        assert result==(None,404,"HTTP_404")
        assert calls==1
    asyncio.run(scenario())

def test_budget_counts_actual_requests_including_retries():
    async def scenario():
        calls=0
        async def handler(request):
            nonlocal calls
            calls+=1
            return httpx.Response(503,request=request)
        budget=RequestBudget(2)
        client=AsyncResilientHttpClient(
            politeness_controller=HostPolitenessController(default_min_interval_seconds=0),
            transport=httpx.MockTransport(handler),
            request_budget=budget,
            retry_policy=RetryPolicy(max_attempts=3,base_backoff_seconds=0),
        )
        try:
            result=await client.fetch_html("https://example.test/x",check_robots=False)
        finally:
            await client.aclose()
        assert result==(None,None,"REQUEST_BUDGET_EXCEEDED")
        assert calls==2
        assert budget.used==2
        assert budget.remaining==0
    asyncio.run(scenario())

def test_conditional_headers_and_304():
    async def scenario():
        seen=[]
        async def handler(request):
            seen.append(dict(request.headers))
            if len(seen)==1:
                return httpx.Response(
                    200,
                    text="v1",
                    headers={
                        "ETag": '"abc"',
                        "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT",
                    },
                    request=request,
                )
            return httpx.Response(304,request=request)
        cache=ConditionalRequestCache()
        client=AsyncResilientHttpClient(
            politeness_controller=HostPolitenessController(default_min_interval_seconds=0),
            transport=httpx.MockTransport(handler),
            conditional_cache=cache,
        )
        try:
            first=await client.fetch_html("https://example.test/data",check_robots=False)
            second=await client.fetch_html("https://example.test/data",check_robots=False)
        finally:
            await client.aclose()
        assert first==("v1",200,None)
        assert second==(None,304,None)
        assert seen[1]["if-none-match"]=='"abc"'
        assert seen[1]["if-modified-since"]=="Wed, 21 Oct 2015 07:28:00 GMT"
    asyncio.run(scenario())

def test_head_405_fallback_still_streams_without_body():
    async def scenario():
        observed=[]
        stream=GuardedAsyncStream()
        async def handler(request):
            observed.append((request.method,request.headers.get("Range")))
            if request.method=="HEAD":
                return httpx.Response(405,request=request)
            return httpx.Response(206,headers={"ETag":'"v1"'},stream=stream,request=request)
        client=AsyncResilientHttpClient(
            politeness_controller=HostPolitenessController(default_min_interval_seconds=0),
            transport=httpx.MockTransport(handler),
        )
        try:
            headers,status,error=await client.fetch_headers("https://example.test/a.pdf",check_robots=False)
        finally:
            await client.aclose()
        assert observed==[("HEAD",None),("GET","bytes=0-0")]
        assert status==206 and error is None and headers["etag"]=='"v1"'
        assert stream.was_read is False
        assert stream.was_closed is True
    asyncio.run(scenario())

def test_timeout_config_rejects_nonpositive():
    try:
        HttpTimeoutConfig(connect=0)
    except ValueError:
        pass
    else:
        raise AssertionError("debió fallar")
