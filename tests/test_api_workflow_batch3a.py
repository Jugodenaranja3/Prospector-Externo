import asyncio
import json
import httpx
import pytest
from prospector_externo.domain.models import ResourceType, SourceConfig
from prospector_externo.infrastructure.http_policy import RetryPolicy
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.workflows.api_workflow import ApiWorkflow
from prospector_externo.workflows.html_workflow import HtmlWorkflow

def _robots():
    return httpx.Response(200, text='User-agent: *\nAllow: /\n')

def test_api_workflow_json_endpoint_is_cataloged_without_extra_calls():

    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == '/robots.txt':
                return _robots()
            if request.url.path == '/api/data':
                return httpx.Response(200, json={'items': [{'id': 1}, {'id': 2}], 'next_page': 2}, headers={'Content-Type': 'application/json'})
            raise AssertionError(f'request inesperado: {request.url}')
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler), retry_policy=RetryPolicy(max_attempts=1, base_backoff_seconds=0, jitter_ratio=0))
        try:
            config = SourceConfig(source_id='api_json', name='API JSON', entrypoint='https://example.test/api/data', workflow='api', max_requests=10, rate_limit_seconds=0)
            workflow = ApiWorkflow(runtime.session_for(config))
            result = await workflow.run(config)
        finally:
            await runtime.aclose()
        assert result.success is True
        assert len(result.resources) == 1
        resource = result.resources[0]
        assert resource.resource_type == ResourceType.API
        assert resource.api is not None
        assert resource.api.format == 'json'
        assert resource.api.method == 'GET'
        assert resource.api.records_detected == 2
        assert resource.api.has_pagination is True
        assert resource.api.callable_by_policy is True
        assert result.coverage.api_endpoints == 1
        assert result.coverage.requests_total == 2
        assert calls == ['https://example.test/robots.txt', 'https://example.test/api/data']
    asyncio.run(scenario())

def test_api_workflow_geojson_metadata():

    async def scenario():

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return _robots()
            return httpx.Response(200, json={'type': 'FeatureCollection', 'features': [{'id': 1}]}, headers={'Content-Type': 'application/geo+json'})
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(source_id='geo', entrypoint='https://geo.test/api/features', workflow='api', rate_limit_seconds=0)
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
        api = result.resources[0].api
        assert api is not None
        assert api.format == 'geojson'
        assert api.is_geojson is True
        assert api.records_detected == 1
    asyncio.run(scenario())

def test_openapi_workflow_never_executes_discovered_operations():

    async def scenario():
        called_paths = []
        spec = {'openapi': '3.0.0', 'servers': [{'url': 'https://api.test/v1'}], 'paths': {'/stats': {'get': {'operationId': 'stats', 'responses': {'200': {'content': {'application/json': {}}}}}, 'post': {'operationId': 'mutate'}}, '/item/{id}': {'get': {'parameters': [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}], 'responses': {'200': {'content': {'application/json': {}}}}}}}}

        async def handler(request: httpx.Request) -> httpx.Response:
            called_paths.append(request.url.path)
            if request.url.path == '/robots.txt':
                return _robots()
            if request.url.path == '/openapi.json':
                return httpx.Response(200, json=spec, headers={'Content-Type': 'application/json'})
            raise AssertionError('BATCH 3A no debe ejecutar operaciones descubiertas')
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(source_id='oas', entrypoint='https://api.test/openapi.json', workflow='api', rate_limit_seconds=0)
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
        assert called_paths == ['/robots.txt', '/openapi.json']
        assert result.coverage.openapi_documents == 1
        assert result.coverage.api_non_get_operations_skipped == 1
        assert result.coverage.api_endpoints == 2
        assert all((r.resource_type == ResourceType.API for r in result.resources))
        stats = next((r for r in result.resources if r.api and r.api.operation_id == 'stats'))
        assert stats.api.callable_by_policy is True
        by_id = next((r for r in result.resources if '{id}' in r.url))
        assert by_id.api.callable_by_policy is False
        assert by_id.api.unresolved_required_params == ('id',)
    asyncio.run(scenario())

def test_api_workflow_html_docs_catalogs_refs_without_following_them():

    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == '/robots.txt':
                return _robots()
            if request.url.path == '/docs':
                return httpx.Response(200, text='<a href="/openapi.json">OpenAPI docs</a>', headers={'Content-Type': 'text/html', 'Link': '<https://example.test/api/v1/>; rel="service-desc"; type="application/json"'})
            raise AssertionError('las referencias no deben seguirse automáticamente')
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(source_id='docs', entrypoint='https://example.test/docs', workflow='api', rate_limit_seconds=0, probe_api_documentation=False)
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
        assert calls == ['/robots.txt', '/docs']
        urls = {r.url for r in result.resources}
        assert 'https://example.test/openapi.json' in urls
        assert 'https://example.test/api/v1/' in urls
        assert result.coverage.api_endpoints == 2
        assert result.coverage.api_documentation_found == 2
    asyncio.run(scenario())

def test_html_workflow_discovers_api_link_headers_without_fetching_api():

    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == '/robots.txt':
                return _robots()
            if request.url.path == '/stats':
                return httpx.Response(200, text='<html><a href="/files/report.pdf">Reporte</a></html>', headers={'Content-Type': 'text/html', 'Link': '<https://example.test/wp-json/>; rel="https://api.w.org/", <https://example.test/wp-json/wp/v2/pages/10>; rel="alternate"; type="application/json"'})
            raise AssertionError(f'no debe fetch API ni binarios: {request.url}')
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(source_id='html_api', entrypoint='https://example.test/stats', seeds=['https://example.test/stats'], workflow='html', discover_sitemaps=False, max_depth=0, max_urls=20, rate_limit_seconds=0)
            result = await HtmlWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
        assert calls == ['/robots.txt', '/stats']
        api_resources = [r for r in result.resources if r.resource_type == ResourceType.API]
        files = [r for r in result.resources if r.resource_type == ResourceType.FILE]
        assert len(api_resources) == 2
        assert len(files) == 1
        assert files[0].url.endswith('/files/report.pdf')
        assert result.coverage.api_endpoints == 2
    asyncio.run(scenario())

def test_api_workflow_rejects_malformed_declared_json():

    async def scenario():

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return _robots()
            return httpx.Response(200, text='{not-json', headers={'Content-Type': 'application/json'})
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(source_id='bad_json', entrypoint='https://example.test/api/data', workflow='api', rate_limit_seconds=0)
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
        assert result.success is False
        assert result.failure_code == 'INVALID_RESPONSE'
        assert result.coverage.urls_failed == 1
    asyncio.run(scenario())

def test_api_workflow_catalogs_oversized_endpoint_without_downloading_it_fully():

    async def scenario():
        payload = b'x' * 100

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return _robots()
            return httpx.Response(200, content=payload, headers={'Content-Type': 'application/json', 'Content-Length': '100'})
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(source_id='bounded', entrypoint='https://example.test/api/data', workflow='api', rate_limit_seconds=0, max_api_response_bytes=10)
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
        assert result.success is True
        assert len(result.resources) == 1
        assert result.resources[0].resource_type == ResourceType.API
        assert result.resources[0].api is not None
        assert result.resources[0].api.format == 'json'
        assert result.resources[0].discovery_method == 'api_seed_bounded'
    asyncio.run(scenario())

def test_local_json_compact_and_tree_preserve_api_metadata(tmp_path):
    import json as _json
    from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
    from prospector_externo.domain.models import ApiMetadata, ResourceCandidate, Snapshot
    repo = LocalJsonRepositoryAdapter(tmp_path)
    candidate = ResourceCandidate(resource_key='api-key', url='https://example.test/api/data', source_id='api_src', title='API pública', resource_type=ResourceType.API, api=ApiMetadata(identity='GET https://example.test/api/data', format='json', method='GET', callable_by_policy=True))
    repo.save_snapshot(Snapshot(source_id='api_src', run_id='run_api', resources_hash='hash', total_resources=1, resources=[candidate]))
    compact = _json.loads((tmp_path / 'api_src' / 'mapa_api_src_compact.json').read_text(encoding='utf-8'))
    tree = _json.loads((tmp_path / 'api_src' / 'mapa_api_src_tree.json').read_text(encoding='utf-8'))
    assert compact['resources'][0]['resource_type'] == 'api'
    assert compact['resources'][0]['api']['format'] == 'json'
    assert tree['children'][0]['resource_type'] == 'api'
    assert tree['children'][0]['api']['method'] == 'GET'

def test_html_api_discovery_respects_max_api_endpoints():

    async def scenario():

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return _robots()
            if request.url.path == '/page':
                return httpx.Response(200, text='\n                <a href="/api/one">One</a>\n                <a href="/api/two">Two</a>\n                <a href="/api/three">Three</a>\n                ', headers={'Content-Type': 'text/html'})
            raise AssertionError(f'request inesperado: {request.url}')
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(source_id='cap', entrypoint='https://example.test/page', workflow='html', discover_sitemaps=False, max_depth=0, max_api_endpoints=1, rate_limit_seconds=0)
            result = await HtmlWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
        api_resources = [r for r in result.resources if r.resource_type == ResourceType.API]
        assert len(api_resources) == 1
        assert result.coverage.api_endpoints == 1
    asyncio.run(scenario())
