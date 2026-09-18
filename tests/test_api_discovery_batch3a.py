import json

import pytest

from prospector_externo.domain.api_discovery import (
    ApiAccessPolicy,
    ApiDetector,
    ApiIdentity,
    OpenApiDiscovery,
)


def test_api_identity_strips_only_pagination_controls():
    a = ApiIdentity.value(
        "GET",
        "https://example.test/api/data?dataset=ipc&page=1&limit=50&region=bo",
    )
    b = ApiIdentity.value(
        "get",
        "https://example.test/api/data?limit=100&page=9&region=bo&dataset=ipc",
    )
    other = ApiIdentity.value(
        "GET",
        "https://example.test/api/data?dataset=pib&page=1&region=bo",
    )

    assert a == b
    assert "page=" not in a
    assert "limit=" not in a
    assert "dataset=ipc" in a
    assert "region=bo" in a
    assert a != other


@pytest.mark.parametrize(
    ("body", "headers", "expected"),
    [
        ('{"items": [1, 2]}', {"content-type": "application/json"}, "json"),
        (
            '{"type":"FeatureCollection","features":[]}',
            {"content-type": "application/geo+json"},
            "geojson",
        ),
        ("<?xml version='1.0'?><root/>", {"content-type": "text/xml"}, "xml"),
        ("a,b\n1,2\n", {"content-type": "text/csv"}, "csv"),
        (
            '{"openapi":"3.0.0","paths":{}}',
            {"content-type": "application/json"},
            "openapi",
        ),
        (
            "openapi: 3.0.0\npaths: {}\n",
            {"content-type": "application/yaml"},
            "openapi",
        ),
    ],
)
def test_api_detector_formats(body, headers, expected):
    assert ApiDetector.detect_format(body, headers=headers) == expected


def test_api_detector_records_and_pagination():
    body = json.dumps({"items": [{"id": 1}, {"id": 2}], "next_page": 2})
    assert ApiDetector.records_detected(body, "json") == 2
    assert ApiDetector.has_pagination(body, "https://example.test/api/data") is True
    assert ApiDetector.has_pagination("[]", "https://example.test/api/data?page=2") is True


def test_api_detector_extracts_wordpress_header_and_html_refs_without_duplicates():
    headers = {
        "link": (
            '<https://example.test/wp-json/>; rel="https://api.w.org/", '
            '<https://example.test/wp-json/wp/v2/pages/10>; rel="alternate"; '
            'type="application/json"'
        )
    }
    html = """
    <html><head>
      <link rel="https://api.w.org/" href="/wp-json/">
      <link rel="alternate" type="application/json" href="/wp-json/wp/v2/pages/10">
      <a href="/openapi.json">OpenAPI</a>
    </head></html>
    """
    refs = ApiDetector.extract_references(
        html,
        base_url="https://example.test/page",
        headers=headers,
    )
    urls = {ref.url for ref in refs}
    assert urls == {
        "https://example.test/wp-json/",
        "https://example.test/wp-json/wp/v2/pages/10",
        "https://example.test/openapi.json",
    }
    openapi = next(ref for ref in refs if ref.url.endswith("openapi.json"))
    assert openapi.is_openapi is True
    assert openapi.documentation_url == openapi.url


def test_get_only_policy_rejects_mutating_methods_auth_and_unresolved_params():
    assert ApiAccessPolicy.method_allowed("GET") is True
    assert ApiAccessPolicy.method_allowed("post") is False
    assert ApiAccessPolicy.operation_callable(
        method="GET", auth_required=False, unresolved_required_params=[]
    ) is True
    assert ApiAccessPolicy.operation_callable(
        method="GET", auth_required=True, unresolved_required_params=[]
    ) is False
    assert ApiAccessPolicy.operation_callable(
        method="GET", auth_required=False, unresolved_required_params=["id"]
    ) is False


def test_openapi_discovery_catalogs_get_only_and_flags_risk():
    document = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://api.example.test/v1"}],
        "paths": {
            "/public": {
                "get": {
                    "operationId": "listPublic",
                    "summary": "Lista pública",
                    "responses": {
                        "200": {"content": {"application/json": {}}}
                    },
                },
                "post": {"operationId": "createPublic"},
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"content": {"application/geo+json": {}}}},
                }
            },
            "/private": {
                "get": {
                    "security": [{"apiKey": []}],
                    "responses": {"200": {"content": {"application/json": {}}}},
                }
            },
        },
    }
    result = OpenApiDiscovery.discover(
        json.dumps(document), document_url="https://docs.example.test/openapi.json"
    )

    assert result.parse_error is None
    assert result.non_get_operations_skipped == 1
    assert len(result.references) == 3

    public = next(r for r in result.references if r.operation_id == "listPublic")
    assert public.url == "https://api.example.test/v1/public"
    assert public.api_format == "json"
    assert public.auth_required is False
    assert public.unresolved_required_params == ()

    item = next(r for r in result.references if r.operation_id == "getItem")
    assert item.api_format == "geojson"
    assert item.is_geojson is True
    assert item.unresolved_required_params == ("id",)

    private = next(r for r in result.references if r.url.endswith("/private"))
    assert private.auth_required is True
    assert result.auth_required_operations == 1
    assert result.unresolved_operations == 1


def test_swagger2_base_path_is_resolved():
    document = {
        "swagger": "2.0",
        "host": "api.example.test",
        "basePath": "/v2",
        "schemes": ["https"],
        "paths": {
            "/stats": {
                "get": {"produces": ["application/json"], "responses": {"200": {}}}
            }
        },
    }
    result = OpenApiDiscovery.discover(
        json.dumps(document), document_url="https://docs.example.test/swagger.json"
    )
    assert [r.url for r in result.references] == ["https://api.example.test/v2/stats"]


def test_openapi_security_empty_object_allows_anonymous_access():
    document = {
        "openapi": "3.0.0",
        "security": [{"apiKey": []}],
        "paths": {
            "/optional": {
                "get": {
                    "security": [{}],
                    "responses": {"200": {"content": {"application/json": {}}}},
                }
            }
        },
    }
    result = OpenApiDiscovery.discover(
        json.dumps(document), document_url="https://example.test/openapi.json"
    )
    assert len(result.references) == 1
    assert result.references[0].auth_required is False


def test_openapi_path_placeholder_is_unresolved_even_if_parameter_is_omitted():
    document = {
        "openapi": "3.0.0",
        "paths": {
            "/series/{series_id}": {
                "get": {"responses": {"200": {"content": {"application/json": {}}}}}
            }
        },
    }
    result = OpenApiDiscovery.discover(
        json.dumps(document), document_url="https://example.test/openapi.json"
    )
    assert result.references[0].unresolved_required_params == ("series_id",)
    assert result.unresolved_operations == 1


def test_openapi_required_request_body_is_never_callable_by_get_only_policy():
    document = {
        "openapi": "3.0.0",
        "paths": {
            "/search": {
                "get": {
                    "requestBody": {"required": True, "content": {"application/json": {}}},
                    "responses": {"200": {"content": {"application/json": {}}}},
                }
            }
        },
    }
    result = OpenApiDiscovery.discover(
        json.dumps(document), document_url="https://example.test/openapi.json"
    )
    assert result.references[0].unresolved_required_params == ("requestBody",)


def test_openapi_server_variables_use_declared_defaults():
    document = {
        "openapi": "3.0.0",
        "servers": [
            {
                "url": "https://{env}.example.test/{version}",
                "variables": {
                    "env": {"default": "api"},
                    "version": {"default": "v1"},
                },
            }
        ],
        "paths": {"/stats": {"get": {"responses": {"200": {}}}}},
    }
    result = OpenApiDiscovery.discover(
        json.dumps(document), document_url="https://docs.example.test/openapi.json"
    )
    assert result.references[0].url == "https://api.example.test/v1/stats"
    assert result.references[0].unresolved_required_params == ()


def test_api_detector_recognizes_ogc_and_arcgis_but_not_generic_versioned_pages():
    assert ApiDetector.is_api_url("https://geo.test/ows?service=WFS&request=GetCapabilities") is True
    assert ApiDetector.is_api_url("https://geo.test/geoserver/public/wms") is True
    assert ApiDetector.is_api_url("https://maps.test/arcgis/rest/services/Layer/FeatureServer/0") is True
    assert ApiDetector.is_api_url("https://site.test/v2/noticias/") is False


def test_graphql_reference_is_cataloged_but_not_callable_without_query():
    refs = ApiDetector.extract_references(
        '<a href="/graphql">GraphQL</a>',
        base_url="https://example.test/docs",
    )
    assert len(refs) == 1
    assert refs[0].unresolved_required_params == ("query",)
    assert ApiAccessPolicy.operation_callable(
        method=refs[0].method,
        auth_required=refs[0].auth_required,
        unresolved_required_params=refs[0].unresolved_required_params,
    ) is False
