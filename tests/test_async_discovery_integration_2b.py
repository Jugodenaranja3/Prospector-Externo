import asyncio
import tempfile
from pathlib import Path

import httpx
import yaml

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime


def test_end_to_end_sitemap_and_pagination_yield_smoke():
    async def scenario():
        requested_paths = []

        async def handler(request: httpx.Request) -> httpx.Response:
            path_with_query = request.url.path
            if request.url.query:
                path_with_query += "?" + request.url.query.decode()
            requested_paths.append(path_with_query)

            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text=(
                        "User-agent: *\n"
                        "Allow: /\n"
                        "Sitemap: https://example.test/sitemap.xml\n"
                    ),
                    request=request,
                )
            if request.url.path == "/sitemap.xml":
                return httpx.Response(
                    200,
                    text="""<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
                      <url><loc>https://example.test/deep</loc></url>
                      <url><loc>https://example.test/files/from-sitemap.xlsx</loc></url>
                    </urlset>""",
                    request=request,
                )
            if request.url.path == "/":
                return httpx.Response(
                    200,
                    text='<a href="/list?page=2">Siguiente</a>',
                    request=request,
                )
            if request.url.path == "/deep":
                return httpx.Response(
                    200,
                    text='<a href="/files/deep-report-2026.pdf">Reporte</a>',
                    request=request,
                )
            if request.url.path == "/list" and request.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    text='<a href="/list?page=3">Siguiente</a>',
                    request=request,
                )
            if request.url.path == "/list" and request.url.params.get("page") == "3":
                return httpx.Response(
                    200,
                    text='<a href="/list?page=4">Siguiente</a>',
                    request=request,
                )
            if request.url.path == "/list" and request.url.params.get("page") == "4":
                raise AssertionError("La página 4 debía ser cortada por PaginationYieldPolicy")
            raise AssertionError(f"Request inesperado: {request.url}")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = root / "sources.yaml"
            cfg.write_text(
                yaml.safe_dump(
                    {
                        "sources": [
                            {
                                "source_id": "smoke2b",
                                "name": "Smoke 2B",
                                "entrypoint": "https://example.test/",
                                "workflow": "html",
                                "rate_limit_seconds": 0,
                                "max_depth": 3,
                                "max_urls": 30,
                                "max_requests": 30,
                                "pagination_min_pages": 2,
                                "pagination_empty_streak": 2,
                                "pagination_window": 2,
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

            report = await orchestrator.run_batch_async(force=True)
            assert report.sources_processed == 1
            obs = report.source_results[0]
            assert obs.coverage.sitemap_documents == 1
            assert obs.coverage.sitemap_urls == 2
            assert obs.coverage.pagination_pages == 2
            assert obs.coverage.pagination_families_stopped == 1
            assert obs.coverage.urls_rejected >= 1
            assert obs.coverage.resources_found == 2
            assert not any("page=4" in item for item in requested_paths)

            snapshot = repo.get_latest_snapshot("smoke2b")
            assert snapshot is not None
            assert {resource.url for resource in snapshot.resources} == {
                "https://example.test/files/from-sitemap.xlsx",
                "https://example.test/files/deep-report-2026.pdf",
            }

    asyncio.run(scenario())
