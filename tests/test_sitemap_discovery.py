import asyncio
import gzip

import httpx

from prospector_externo.domain.models import SourceConfig
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.infrastructure.sitemap_discovery import SitemapDiscovery


def test_sitemap_from_robots_index_and_secondary_document():
    async def scenario():
        observed = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text=(
                        "User-agent: *\n"
                        "Allow: /\n"
                        "Sitemap: https://example.test/sitemap-index.xml\n"
                    ),
                    request=request,
                )
            if request.url.path == "/sitemap-index.xml":
                return httpx.Response(
                    200,
                    text="""<?xml version='1.0'?>
                    <sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
                      <sitemap><loc>https://example.test/sitemap-data.xml</loc></sitemap>
                      <sitemap><loc>https://outside.test/ignored.xml</loc></sitemap>
                    </sitemapindex>""",
                    request=request,
                )
            if request.url.path == "/sitemap.xml":
                return httpx.Response(404, request=request)
            if request.url.path == "/sitemap-data.xml":
                return httpx.Response(
                    200,
                    text="""<?xml version='1.0'?>
                    <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
                      <url><loc>https://example.test/deep/stats</loc></url>
                      <url><loc>https://example.test/files/data-2026.xlsx</loc></url>
                      <url><loc>https://outside.test/nope</loc></url>
                    </urlset>""",
                    request=request,
                )
            raise AssertionError(request.url)

        config = SourceConfig(
            source_id="sm",
            entrypoint="https://example.test/",
            rate_limit_seconds=0,
            max_requests=20,
            max_sitemap_documents=5,
            max_sitemap_urls=20,
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

        assert {entry.url for entry in result.entries} == {
            "https://example.test/deep/stats",
            "https://example.test/files/data-2026.xlsx",
        }
        assert result.documents_checked == 3
        assert result.errors == 1  # /sitemap.xml 404
        assert "/robots.txt" in observed
        assert "/sitemap-index.xml" in observed
        assert "/sitemap-data.xml" in observed

    asyncio.run(scenario())


def test_sitemap_body_is_bounded_by_bytes():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404, request=request)
            if request.url.path == "/sitemap.xml":
                body = b"<urlset>" + (b"x" * 500) + b"</urlset>"
                return httpx.Response(200, content=body, request=request)
            raise AssertionError(request.url)

        config = SourceConfig(
            source_id="sm",
            entrypoint="https://example.test/",
            rate_limit_seconds=0,
            max_requests=10,
            max_sitemap_bytes=100,
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

        assert result.entries == []
        assert result.documents_checked == 1
        assert result.errors == 1

    asyncio.run(scenario())


def test_gzipped_sitemap_is_bounded_and_parsed():
    async def scenario():
        sitemap_xml = b"""<?xml version='1.0'?>
        <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
          <url><loc>https://example.test/files/a.csv</loc></url>
        </urlset>"""
        compressed = gzip.compress(sitemap_xml)

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nAllow: /\nSitemap: https://example.test/site.xml.gz\n",
                    request=request,
                )
            if request.url.path == "/site.xml.gz":
                # Sin Content-Encoding para probar el fallback por magic bytes.
                return httpx.Response(200, content=compressed, request=request)
            if request.url.path == "/sitemap.xml":
                return httpx.Response(404, request=request)
            raise AssertionError(request.url)

        config = SourceConfig(
            source_id="sm",
            entrypoint="https://example.test/",
            rate_limit_seconds=0,
            max_requests=10,
            max_sitemap_bytes=1000,
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

        assert [entry.url for entry in result.entries] == [
            "https://example.test/files/a.csv"
        ]

    asyncio.run(scenario())
