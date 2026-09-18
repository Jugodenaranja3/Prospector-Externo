import json

from prospector_externo.domain.api_discovery import (
    ApiDetector,
    ApiDocumentationDiscovery,
    ApiPagination,
    OpenApiDiscovery,
)


def test_api_pagination_follows_explicit_http_link_next_same_identity():
    decision = ApiPagination.next_page(
        body='{"items":[1]}',
        headers={'link': '<https://example.test/api/data?dataset=ipc&page=2>; rel="next"'},
        current_url='https://example.test/api/data?dataset=ipc&page=1',
    )
    assert decision.next_url == 'https://example.test/api/data?dataset=ipc&page=2'
    assert decision.strategy == 'http_link_next'


def test_api_pagination_rejects_next_that_changes_dataset_filter():
    decision = ApiPagination.next_page(
        body=json.dumps({'next': '/api/data?dataset=pib&page=2'}),
        headers={},
        current_url='https://example.test/api/data?dataset=ipc&page=1',
    )
    assert decision.next_url is None
    assert decision.reason == 'NEXT_IDENTITY_CHANGED'


def test_api_pagination_numeric_only_updates_existing_page_key():
    decision = ApiPagination.next_page(
        body=json.dumps({'page': 2, 'total_pages': 4, 'items': []}),
        headers={},
        current_url='https://example.test/api/data?dataset=ipc&page=2',
    )
    assert decision.next_url == 'https://example.test/api/data?dataset=ipc&page=3'
    assert decision.strategy == 'numeric_page'

    no_guess = ApiPagination.next_page(
        body=json.dumps({'page': 1, 'total_pages': 4, 'items': []}),
        headers={},
        current_url='https://example.test/api/data?dataset=ipc',
    )
    assert no_guess.next_url is None


def test_api_docs_extracts_explicit_swagger_ui_spec_without_guessing_paths():
    html = '''
    <html><body><script>
      const ui = SwaggerUIBundle({url: "/v3/openapi.json"});
    </script></body></html>
    '''
    refs = ApiDocumentationDiscovery.extract_openapi_references(
        html, base_url='https://example.test/docs'
    )
    assert [ref.url for ref in refs] == ['https://example.test/v3/openapi.json']
    assert refs[0].is_openapi is True


def test_api_detector_advanced_formats_and_record_counts():
    ndjson = '{"id":1}\n{"id":2}\n'
    assert ApiDetector.detect_format(ndjson, headers={'content-type': 'application/x-ndjson'}) == 'ndjson'
    assert ApiDetector.records_detected(ndjson, 'ndjson') == 2

    tsv = 'a\tb\n1\t2\n3\t4\n'
    assert ApiDetector.detect_format(tsv, headers={'content-type': 'text/tab-separated-values'}) == 'tsv'
    assert ApiDetector.records_detected(tsv, 'tsv') == 2

    jsonstat = json.dumps({
        'class': 'dataset', 'version': '2.0',
        'dimension': {'time': {}}, 'value': [1, 2, 3]
    })
    assert ApiDetector.detect_format(jsonstat, headers={'content-type': 'application/json'}) == 'jsonstat'
    assert ApiDetector.records_detected(jsonstat, 'jsonstat') == 3

    topojson = json.dumps({'type': 'Topology', 'objects': {'a': {}, 'b': {}}})
    assert ApiDetector.detect_format(topojson, headers={'content-type': 'application/json'}) == 'topojson'
    assert ApiDetector.records_detected(topojson, 'topojson') == 2


def test_openapi_discovery_exposes_spec_metadata_without_executing_external_docs():
    document = {
        'openapi': '3.1.0',
        'info': {'title': 'Estadísticas Públicas'},
        'externalDocs': {'url': '/manual'},
        'paths': {'/stats': {'get': {'responses': {'200': {'content': {'application/json': {}}}}}}},
    }
    result = OpenApiDiscovery.discover(
        json.dumps(document), document_url='https://example.test/openapi.json'
    )
    assert result.spec_version == '3.1.0'
    assert result.title == 'Estadísticas Públicas'
    assert result.external_docs_url == 'https://example.test/manual'
    assert [ref.url for ref in result.references] == ['https://example.test/stats']
