import asyncio
import tempfile
from pathlib import Path

import httpx
import yaml

import prospector_externo.workflows  # noqa: F401
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime


def test_end_to_end_async_html_discovery_without_mass_head():
    async def scenario():
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path, request.url.query.decode()))
            assert request.method == "GET", "BATCH 2A no debe hacer HEAD masivo para enlaces evidentes"

            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            if request.url.path == "/sitemap.xml":
                return httpx.Response(404, request=request)
            if request.url.path == "/":
                return httpx.Response(
                    200,
                    text="""<html><body>
                    <a href="/stats">Estadísticas</a>
                    <a href="/files/reporte-mayo-2026.xlsx?utm_source=test">Excel mayo 2026</a>
                    <a href="https://outside.test/other">Exterior</a>
                    </body></html>""",
                    request=request,
                )
            if request.url.path == "/stats":
                return httpx.Response(
                    200,
                    text="""<html><body>
                    <a href="/files/reporte-mayo-2026.xlsx">Duplicado</a>
                    <a href="/Descargar?path=boletin-junio-2026.pdf">Boletín Junio 2026</a>
                    </body></html>""",
                    request=request,
                )
            raise AssertionError(f"Request inesperado: {request.url}")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = root / "sources.yaml"
            cfg.write_text(
                yaml.safe_dump({
                    "sources": [{
                        "source_id": "smoke",
                        "name": "Smoke",
                        "entrypoint": "https://example.test/",
                        "workflow": "html",
                        "rate_limit_seconds": 0,
                        "max_depth": 2,
                        "max_urls": 20,
                        "max_requests": 20,
                    }]
                }),
                encoding="utf-8",
            )
            repo = LocalJsonRepositoryAdapter(root / "output")
            transport = httpx.MockTransport(handler)
            orchestrator = RunOrchestrator(
                catalog_repo=repo,
                report_repo=repo,
                config_path=cfg,
                http_runtime_factory=lambda: AsyncHttpRuntime(
                    transport=transport,
                    default_min_interval_seconds=0,
                ),
            )

            report = await orchestrator.run_batch_async(force=True)
            assert report.sources_processed == 1
            obs = report.source_results[0]
            assert obs.coverage.pages_visited == 2
            assert obs.coverage.resources_found == 2
            assert obs.coverage.requests_total == 4  # robots + sitemap probe + 2 HTML
            assert obs.coverage.sitemap_documents == 1
            assert obs.coverage.sitemap_errors == 1
            assert obs.coverage.stop_reason == "QUEUE_EXHAUSTED"

            snapshot = repo.get_latest_snapshot("smoke")
            assert snapshot is not None
            assert snapshot.total_resources == 2
            urls = {r.url for r in snapshot.resources}
            assert "https://example.test/files/reporte-mayo-2026.xlsx" in urls
            assert "https://example.test/Descargar?path=boletin-junio-2026.pdf" in urls
            assert all(method == "GET" for method, _, _ in requests)

    asyncio.run(scenario())


def test_two_logical_sources_share_host_politeness_but_have_independent_budgets():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            return httpx.Response(200, text="<html></html>", request=request)

        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        from prospector_externo.domain.models import SourceConfig

        a = runtime.session_for(SourceConfig(
            source_id="aps", entrypoint="https://example.test/a", rate_limit_seconds=0, max_requests=3
        ))
        b = runtime.session_for(SourceConfig(
            source_id="aps_soat", entrypoint="https://example.test/b", rate_limit_seconds=0, max_requests=3
        ))
        try:
            await a.fetch_html("https://example.test/a")
            await b.fetch_html("https://example.test/b")
        finally:
            await runtime.aclose()

        # robots se consulta una vez y queda cacheado por origin; cada source conserva su budget.
        assert calls == [("GET", "/robots.txt"), ("GET", "/a"), ("GET", "/b")]
        assert a.requests_used == 2
        assert b.requests_used == 1
        assert runtime.politeness.snapshot("example.test").requests_started == 3

    asyncio.run(scenario())
