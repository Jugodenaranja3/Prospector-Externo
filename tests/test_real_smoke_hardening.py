import asyncio
import json
import tempfile
from pathlib import Path

import httpx
import yaml

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.domain.models import DiscoveryType, SourceConfig
from prospector_externo.infrastructure.async_http_client import AsyncResilientHttpClient
from prospector_externo.infrastructure.host_politeness import HostPolitenessController
from prospector_externo.infrastructure.http_policy import RequestBudget, RetryPolicy
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime, SourceHttpSession
from prospector_externo.infrastructure.sitemap_discovery import SitemapDiscovery
from prospector_externo.workflows.html_workflow import HtmlWorkflow


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


def test_manual_redirects_consume_budget_and_politeness_per_hop():
    async def scenario():
        calls = []
        clock = FakeClock()

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/start":
                return httpx.Response(301, headers={"Location": "/final"}, request=request)
            if request.url.path == "/final":
                return httpx.Response(200, text="ok", request=request)
            raise AssertionError(request.url)

        governor = HostPolitenessController(
            default_min_interval_seconds=0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        budget = RequestBudget(5)
        client = AsyncResilientHttpClient(
            politeness_controller=governor,
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        try:
            result = await client.fetch_html(
                "https://example.test/start",
                check_robots=False,
                rate_limit_delay=1.5,
                request_budget=budget,
                allowed_redirect_hosts={"example.test"},
            )
        finally:
            await client.aclose()

        assert result == ("ok", 200, None)
        assert calls == [
            "https://example.test/start",
            "https://example.test/final",
        ]
        assert budget.used == 2
        assert governor.snapshot("example.test").requests_started == 2
        assert clock.sleeps == [1.5]

    asyncio.run(scenario())


def test_robots_redirect_and_page_share_source_rate_limit_and_budget():
    async def scenario():
        calls = []
        clock = FakeClock()

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(301, headers={"Location": "/robots-real.txt"}, request=request)
            if request.url.path == "/robots-real.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            if request.url.path == "/page":
                return httpx.Response(200, text="<html>ok</html>", request=request)
            raise AssertionError(request.url)

        governor = HostPolitenessController(
            default_min_interval_seconds=0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        client = AsyncResilientHttpClient(
            politeness_controller=governor,
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        session = SourceHttpSession(
            config=SourceConfig(
                source_id="src",
                entrypoint="https://example.test/",
                rate_limit_seconds=1.5,
                max_requests=10,
            ),
            client=client,
        )
        try:
            result = await session.fetch_html("https://example.test/page")
        finally:
            await client.aclose()

        assert result == ("<html>ok</html>", 200, None)
        assert calls == ["/robots.txt", "/robots-real.txt", "/page"]
        assert session.requests_used == 3
        assert governor.snapshot("example.test").requests_started == 3
        assert clock.sleeps == [1.5, 1.5]

    asyncio.run(scenario())


def test_redirect_outside_source_scope_is_not_fetched():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/start":
                return httpx.Response(
                    302,
                    headers={"Location": "https://outside.test/final"},
                    request=request,
                )
            raise AssertionError("El destino externo no debía solicitarse")

        budget = RequestBudget(5)
        client = AsyncResilientHttpClient(
            politeness_controller=HostPolitenessController(default_min_interval_seconds=0),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        try:
            body, status, error = await client.fetch_html(
                "https://example.test/start",
                check_robots=False,
                rate_limit_delay=0,
                request_budget=budget,
                allowed_redirect_hosts={"example.test"},
            )
        finally:
            await client.aclose()

        assert body is None
        assert status == 302
        assert error == "REDIRECT_OUT_OF_SCOPE"
        assert calls == ["https://example.test/start"]
        assert budget.used == 1

    asyncio.run(scenario())


def test_redirect_target_is_checked_against_robots_before_fetch():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.url.host, request.url.path))
            if request.url.host == "example.test" and request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            if request.url.host == "example.test" and request.url.path == "/start":
                return httpx.Response(
                    302,
                    headers={"Location": "https://alias.test/final"},
                    request=request,
                )
            if request.url.host == "alias.test" and request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nDisallow: /final\n",
                    request=request,
                )
            if request.url.host == "alias.test" and request.url.path == "/final":
                raise AssertionError("robots debía impedir el fetch final")
            raise AssertionError(request.url)

        budget = RequestBudget(10)
        client = AsyncResilientHttpClient(
            politeness_controller=HostPolitenessController(default_min_interval_seconds=0),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        try:
            body, status, error = await client.fetch_html(
                "https://example.test/start",
                rate_limit_delay=0,
                request_budget=budget,
                allowed_redirect_hosts={"example.test", "alias.test"},
            )
        finally:
            await client.aclose()

        assert body is None
        assert status is None
        assert error == "ROBOTS_DISALLOWED"
        assert calls == [
            ("example.test", "/robots.txt"),
            ("example.test", "/start"),
            ("alias.test", "/robots.txt"),
        ]
        assert budget.used == 3

    asyncio.run(scenario())


def test_sitemap_index_can_use_second_document_slot():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text=(
                        "User-agent: *\nAllow: /\n"
                        "Sitemap: https://example.test/sitemap.xml\n"
                    ),
                    request=request,
                )
            if request.url.path == "/sitemap.xml":
                return httpx.Response(
                    200,
                    text="""<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
                    <sitemap><loc>https://example.test/child.xml</loc></sitemap>
                    </sitemapindex>""",
                    request=request,
                )
            if request.url.path == "/child.xml":
                return httpx.Response(
                    200,
                    text="""<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
                    <url><loc>https://example.test/files/data.csv</loc></url>
                    </urlset>""",
                    request=request,
                )
            raise AssertionError(request.url)

        config = SourceConfig(
            source_id="sm",
            entrypoint="https://example.test/",
            rate_limit_seconds=0,
            max_requests=10,
            max_sitemap_documents=2,
            max_sitemap_urls=10,
        )
        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        try:
            result = await SitemapDiscovery(
                session=runtime.session_for(config),
                config=config,
            ).discover(config.entrypoint)
        finally:
            await runtime.aclose()

        assert result.documents_checked == 2
        assert [entry.url for entry in result.entries] == [
            "https://example.test/files/data.csv"
        ]
        assert calls == ["/robots.txt", "/sitemap.xml", "/child.xml"]

    asyncio.run(scenario())


def test_resource_context_recovers_period_when_anchor_is_empty():
    workflow = HtmlWorkflow()
    config = SourceConfig(
        source_id="ctx",
        entrypoint="https://example.test/",
        rate_limit_seconds=0,
    )
    html = """
    <div class='publication-card'>
      <span>Enero</span><span>2026</span>
      <a href='/files/download.pdf'><i class='download-icon'></i></a>
    </div>
    """

    resources, _links = workflow._extract_resources_and_links(
        html,
        "https://example.test/reports",
        config,
        discovery_type=DiscoveryType.HTML,
    )

    assert len(resources) == 1
    resource = resources[0]
    assert resource.anchor_text is None
    assert resource.context_text is not None
    assert "Enero" in resource.context_text
    assert "2026" in resource.context_text
    assert resource.period_label == "2026-01"


def test_standard_map_uses_real_source_metadata_after_first_run():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            if request.url.path == "/sitemap.xml":
                return httpx.Response(404, request=request)
            if request.url.path == "/reports":
                return httpx.Response(
                    200,
                    text='<a href="/files/a-2026.pdf">Reporte A</a>',
                    request=request,
                )
            raise AssertionError(request.url)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = root / "sources.yaml"
            cfg.write_text(
                yaml.safe_dump(
                    {
                        "sources": [
                            {
                                "source_id": "meta",
                                "name": "Fuente Humana",
                                "entrypoint": "https://example.test/reports",
                                "workflow": "html",
                                "rate_limit_seconds": 0,
                                "max_requests": 10,
                                "max_urls": 10,
                                "update_category": "MONTHLY",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            repo = LocalJsonRepositoryAdapter(root / "output")
            orchestrator = RunOrchestrator(
                catalog_repo=repo,
                report_repo=repo,
                config_path=cfg,
                http_runtime_factory=lambda: AsyncHttpRuntime(
                    transport=httpx.MockTransport(handler),
                    default_min_interval_seconds=0,
                ),
            )
            await orchestrator.run_batch_async(force=True)

            map_path = root / "output" / "meta" / "mapa_meta.json"
            payload = json.loads(map_path.read_text(encoding="utf-8"))
            assert payload["source"] == {
                "id": "meta",
                "name": "Fuente Humana",
                "entrypoint": "https://example.test/reports",
            }

    asyncio.run(scenario())
