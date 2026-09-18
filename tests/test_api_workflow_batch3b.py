import asyncio
import json

import httpx

from prospector_externo.domain.models import ResourceType, SourceConfig
from prospector_externo.infrastructure.http_policy import RetryPolicy
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.workflows.api_workflow import ApiWorkflow


def _robots():
    return httpx.Response(200, text='User-agent: *\nAllow: /\n')


def test_api_workflow_follows_bounded_explicit_pagination_and_aggregates_records():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == '/robots.txt':
                return _robots()
            page = request.url.params.get('page')
            if request.url.path == '/api/data' and page == '1':
                return httpx.Response(200, json={
                    'items': [
                        {'id': 1, 'url': '/files/a.pdf', 'title': 'A'},
                        {'id': 2},
                    ],
                    'next': '/api/data?dataset=ipc&page=2',
                }, headers={'Content-Type': 'application/json'})
            if request.url.path == '/api/data' and page == '2':
                return httpx.Response(200, json={
                    'items': [
                        {'id': 3, 'url': '/files/b.xlsx', 'title': 'B'},
                        {'id': 4},
                    ],
                    'next': '/api/data?dataset=ipc&page=3',
                }, headers={'Content-Type': 'application/json'})
            if request.url.path == '/api/data' and page == '3':
                return httpx.Response(200, json={'items': [{'id': 5}]}, headers={'Content-Type': 'application/json'})
            raise AssertionError(f'request inesperado: {request.url}')

        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1, base_backoff_seconds=0, jitter_ratio=0),
        )
        try:
            config = SourceConfig(
                source_id='paged',
                entrypoint='https://example.test/api/data?dataset=ipc&page=1',
                workflow='api',
                rate_limit_seconds=0,
                max_requests=10,
                max_api_pages=3,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api_resources = [r for r in result.resources if r.resource_type == ResourceType.API]
        files = [r for r in result.resources if r.resource_type == ResourceType.FILE]
        assert result.success is True
        assert len(api_resources) == 1
        assert {r.url for r in files} == {
            'https://example.test/files/a.pdf',
            'https://example.test/files/b.xlsx',
        }
        api = api_resources[0].api
        assert api is not None
        assert api.pages_sampled == 3
        assert api.records_sampled == 5
        assert api.records_detected == 5
        assert api.pagination_strategy == 'json_next'
        assert result.coverage.api_pages_visited == 3
        assert result.coverage.api_records_sampled == 5
        assert result.coverage.requests_total == 4  # robots + 3 páginas
        assert len(calls) == 4

    asyncio.run(scenario())


def test_api_workflow_stops_duplicate_pagination_payload_without_looping():
    async def scenario():
        calls = []
        duplicate = {'items': [{'id': 1}], 'next': '/api/data?page=2'}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == '/robots.txt':
                return _robots()
            return httpx.Response(200, json=duplicate, headers={'Content-Type': 'application/json'})

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='dup',
                entrypoint='https://example.test/api/data?page=1',
                workflow='api',
                rate_limit_seconds=0,
                max_api_pages=20,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api = next(r for r in result.resources if r.resource_type == ResourceType.API).api
        assert api is not None
        assert api.pages_sampled == 1
        assert result.coverage.api_pagination_stopped == 1
        assert len(calls) == 3  # robots + page1 + page2, duplicate detectado

    asyncio.run(scenario())


def test_api_workflow_probes_only_explicit_openapi_document_and_never_executes_operations():
    async def scenario():
        calls = []
        spec = {
            'openapi': '3.0.3',
            'info': {'title': 'Public API'},
            'paths': {
                '/stats': {
                    'get': {'operationId': 'stats', 'responses': {'200': {'content': {'application/json': {}}}}},
                    'post': {'operationId': 'mutate'},
                }
            },
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == '/robots.txt':
                return _robots()
            if request.url.path == '/docs':
                return httpx.Response(
                    200,
                    text='<script>SwaggerUIBundle({url: "/openapi.json"})</script>',
                    headers={'Content-Type': 'text/html'},
                )
            if request.url.path == '/openapi.json':
                return httpx.Response(200, json=spec, headers={'Content-Type': 'application/json'})
            if request.url.path == '/stats':
                raise AssertionError('una operación OpenAPI descubierta no debe ejecutarse')
            raise AssertionError(f'inesperado: {request.url}')

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='docs_probe',
                entrypoint='https://example.test/docs',
                workflow='api',
                rate_limit_seconds=0,
                max_api_documents=2,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        assert calls == ['/robots.txt', '/docs', '/openapi.json']
        assert result.coverage.api_documents_probed == 1
        assert result.coverage.openapi_documents == 1
        assert result.coverage.api_non_get_operations_skipped == 1
        stats = next(r for r in result.resources if r.api and r.api.operation_id == 'stats')
        assert stats.api is not None
        assert stats.api.spec_version == '3.0.3'
        spec_resource = next(r for r in result.resources if r.url.endswith('/openapi.json'))
        assert spec_resource.api is not None
        assert spec_resource.api.documentation_probed is True
        assert spec_resource.api.spec_version == '3.0.3'

    asyncio.run(scenario())


def test_api_workflow_respects_max_api_pages_even_when_next_exists():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == '/robots.txt':
                return _robots()
            page = int(request.url.params.get('page', '1'))
            return httpx.Response(
                200,
                json={'items': [{'id': page}], 'next': f'/api/data?page={page + 1}'},
                headers={'Content-Type': 'application/json'},
            )

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='cap_pages',
                entrypoint='https://example.test/api/data?page=1',
                workflow='api',
                rate_limit_seconds=0,
                max_api_pages=2,
                max_requests=20,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api = next(r for r in result.resources if r.resource_type == ResourceType.API).api
        assert api is not None
        assert api.pages_sampled == 2
        assert result.coverage.api_pages_visited == 2
        assert len(calls) == 3  # robots + dos páginas

    asyncio.run(scenario())


def test_api_workflow_catalogs_advanced_ndjson_without_pagination_guessing():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return _robots()
            return httpx.Response(
                200,
                text='{"id":1}\n{"id":2}\n',
                headers={'Content-Type': 'application/x-ndjson'},
            )

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='ndjson',
                entrypoint='https://example.test/api/feed',
                workflow='api',
                rate_limit_seconds=0,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api = next(r for r in result.resources if r.resource_type == ResourceType.API).api
        assert api is not None
        assert api.format == 'ndjson'
        assert api.records_detected == 2
        assert api.pages_sampled == 1
        assert api.records_sampled == 2

    asyncio.run(scenario())
