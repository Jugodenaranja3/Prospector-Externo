"""Utilidades de descubrimiento de APIs públicas de solo lectura.

BATCH 3A introduce detección, identidad estable, parsing OpenAPI/Swagger y
política GET-only. Ninguna utilidad de este módulo ejecuta operaciones mutables.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import yaml
from bs4 import BeautifulSoup

from prospector_externo.domain.normalizer import UrlNormalizer


PAGINATION_QUERY_KEYS = {
    "page",
    "page_number",
    "pagenumber",
    "page_size",
    "pagesize",
    "per_page",
    "perpage",
    "offset",
    "limit",
    "cursor",
    "continuation",
    "continuation_token",
    "skip",
}

API_PATH_HINTS = (
    "/api/",
    "/api?",
    "/rest/",
    "/wp-json/",
    "/odata/",
    "/openapi",
    "/swagger",
    "/api-docs",
    "/geoserver/",
    "/arcgis/rest/",
    "/featureserver",
    "/mapserver",
    "/graphql",
)

OPENAPI_PATH_HINTS = (
    "openapi.json",
    "openapi.yaml",
    "openapi.yml",
    "swagger.json",
    "swagger.yaml",
    "swagger.yml",
    "/api-docs",
    "/v3/api-docs",
)


@dataclass(frozen=True)
class ApiReference:
    """Referencia API observable sin necesidad de ejecutarla."""

    url: str
    method: str = "GET"
    detection_method: str = "api_reference"
    api_format: Optional[str] = None
    documentation_url: Optional[str] = None
    is_openapi: bool = False
    is_geojson: bool = False
    has_pagination: bool = False
    records_detected: Optional[int] = None
    operation_id: Optional[str] = None
    auth_required: bool = False
    unresolved_required_params: Tuple[str, ...] = ()
    title: Optional[str] = None


@dataclass
class OpenApiDiscoveryResult:
    references: List[ApiReference] = field(default_factory=list)
    non_get_operations_skipped: int = 0
    auth_required_operations: int = 0
    unresolved_operations: int = 0
    parse_error: Optional[str] = None


class ApiAccessPolicy:
    """Política de ejecución del Prospector: únicamente lectura pública GET."""

    SAFE_METHOD = "GET"

    @classmethod
    def method_allowed(cls, method: str) -> bool:
        return (method or "").strip().upper() == cls.SAFE_METHOD

    @classmethod
    def operation_callable(
        cls,
        *,
        method: str,
        auth_required: bool,
        unresolved_required_params: Sequence[str],
    ) -> bool:
        return (
            cls.method_allowed(method)
            and not auth_required
            and not unresolved_required_params
        )


class ApiIdentity:
    """Construye identidad estable de endpoints sin colapsar filtros de negocio.

    Solo neutraliza parámetros inequívocamente asociados a paginación. El resto
    de la query se conserva porque puede seleccionar datasets distintos.
    """

    @staticmethod
    def canonical_url(url: str, *, base_url: Optional[str] = None) -> str:
        normalized = UrlNormalizer.normalize(url, base_url=base_url)
        parsed = urlparse(normalized)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in PAGINATION_QUERY_KEYS
        ]
        query.sort(key=lambda item: (item[0], item[1]))
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query, doseq=True),
                "",
            )
        )

    @classmethod
    def value(cls, method: str, url: str, *, base_url: Optional[str] = None) -> str:
        return f"{(method or 'GET').upper()} {cls.canonical_url(url, base_url=base_url)}"


class ApiDetector:
    """Detección conservadora de formatos y referencias API."""

    @staticmethod
    def _content_type(headers: Optional[Mapping[str, str]]) -> str:
        if not headers:
            return ""
        return (headers.get("content-type") or headers.get("Content-Type") or "").lower()

    @classmethod
    def declared_format(
        cls,
        *,
        headers: Optional[Mapping[str, str]] = None,
        url: Optional[str] = None,
    ) -> Optional[str]:
        content_type = cls._content_type(headers)
        if "geo+json" in content_type or "geojson" in content_type:
            return "geojson"
        if "json" in content_type:
            if url and cls.is_openapi_url(url):
                return "openapi"
            return "json"
        if "xml" in content_type:
            return "xml"
        if "text/csv" in content_type or "application/csv" in content_type:
            return "csv"
        if "yaml" in content_type and url and cls.is_openapi_url(url):
            return "openapi"
        return None

    @classmethod
    def detect_format(
        cls,
        body: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Optional[str]:
        content_type = cls._content_type(headers)
        stripped = (body or "").lstrip()

        if "geo+json" in content_type or "geojson" in content_type:
            return "geojson"

        parsed_json: Any = None
        json_ok = False
        if "json" in content_type or stripped.startswith(("{", "[")):
            try:
                parsed_json = json.loads(body)
                json_ok = True
            except (TypeError, ValueError, json.JSONDecodeError):
                json_ok = False

        if json_ok:
            if isinstance(parsed_json, dict):
                if "openapi" in parsed_json or "swagger" in parsed_json:
                    return "openapi"
                if (
                    parsed_json.get("type") == "FeatureCollection"
                    or isinstance(parsed_json.get("features"), list)
                ):
                    return "geojson"
            return "json"

        lowered = stripped[:200].lower()
        if (
            "application/xml" in content_type
            or "text/xml" in content_type
            or "+xml" in content_type
            or stripped.startswith("<?xml")
            or lowered.startswith(("<wfs:", "<ows:", "<rss", "<feed"))
        ):
            return "xml"

        if (
            "text/csv" in content_type
            or "application/csv" in content_type
        ):
            return "csv"

        # OpenAPI YAML debe tener una señal fuerte; no tratamos YAML arbitrario como API.
        if (
            "yaml" in content_type
            or stripped.startswith("openapi:")
            or stripped.startswith("swagger:")
        ):
            try:
                data = yaml.safe_load(body)
            except yaml.YAMLError:
                data = None
            if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
                return "openapi"

        return None

    @staticmethod
    def records_detected(body: str, api_format: Optional[str]) -> Optional[int]:
        if api_format not in {"json", "geojson", "openapi"}:
            return None
        try:
            data = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

        if isinstance(data, list):
            return len(data)
        if not isinstance(data, dict):
            return None
        for key in ("features", "items", "data", "results", "records"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        return None

    @staticmethod
    def has_pagination(body: str, url: str) -> bool:
        parsed = urlparse(url)
        if any(key.lower() in PAGINATION_QUERY_KEYS for key, _ in parse_qsl(parsed.query)):
            return True
        try:
            data = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        pagination_keys = {
            "next",
            "next_page",
            "nextpage",
            "page",
            "pages",
            "total_pages",
            "totalpages",
            "cursor",
            "offset",
            "limit",
            "links",
            "pagination",
        }
        return bool({str(k).lower() for k in data.keys()} & pagination_keys)

    @staticmethod
    def is_openapi_url(url: str) -> bool:
        low = url.lower()
        return any(hint in low for hint in OPENAPI_PATH_HINTS)

    @staticmethod
    def is_api_url(url: str) -> bool:
        low = url.lower()
        parsed = urlparse(low)
        path = parsed.path or "/"
        combined = f"{path}?{parsed.query}"
        if ApiDetector.is_openapi_url(low):
            return True
        if any(hint in combined for hint in API_PATH_HINTS):
            return True
        query = {key.lower(): value.lower() for key, value in parse_qsl(parsed.query)}
        service = query.get("service", "")
        if service in {"wfs", "wms", "wcs", "wmts"}:
            return True
        if path.rstrip("/").endswith(("/ows", "/wfs", "/wms", "/wcs", "/wmts")):
            return True
        return False

    @staticmethod
    def unresolved_url_params(url: str) -> Tuple[str, ...]:
        parsed = urlparse(url)
        if parsed.path.lower().rstrip("/").endswith("/graphql"):
            keys = {key.lower() for key, _ in parse_qsl(parsed.query)}
            if "query" not in keys:
                return ("query",)
        return ()

    @staticmethod
    def _parse_link_header(value: str, base_url: str) -> List[ApiReference]:
        refs: List[ApiReference] = []
        if not value:
            return refs

        # Suficiente para Link headers HTTP comunes. No se ejecuta lo descubierto.
        for match in re.finditer(r"<([^>]+)>\s*((?:;\s*[^,<]+)*)", value):
            raw_url = match.group(1).strip()
            params = match.group(2).lower()
            absolute = UrlNormalizer.normalize(raw_url, base_url=base_url)
            strong_json = "application/json" in params or "+json" in params
            service_desc = "service-desc" in params
            api_rel = "api.w.org" in params or service_desc
            openapi = ApiDetector.is_openapi_url(absolute) or "openapi" in params
            if not (strong_json or api_rel or openapi or ApiDetector.is_api_url(absolute)):
                continue
            refs.append(
                ApiReference(
                    url=absolute,
                    detection_method="http_link_header",
                    api_format="openapi" if openapi else ("json" if strong_json or api_rel else None),
                    documentation_url=absolute if (openapi or service_desc) else None,
                    is_openapi=openapi,
                    unresolved_required_params=ApiDetector.unresolved_url_params(absolute),
                )
            )
        return refs

    @classmethod
    def extract_references(
        cls,
        html: str,
        *,
        base_url: str,
        headers: Optional[Mapping[str, str]] = None,
    ) -> List[ApiReference]:
        candidates: List[ApiReference] = []

        if headers:
            link_value = headers.get("link") or headers.get("Link") or ""
            candidates.extend(cls._parse_link_header(link_value, base_url))

        soup = BeautifulSoup(html or "", "html.parser")

        for tag in soup.find_all(["link", "a"], href=True):
            raw = str(tag.get("href", "")).strip()
            if not raw:
                continue
            absolute = UrlNormalizer.normalize(raw, base_url=base_url)
            rel = " ".join(tag.get("rel", [])).lower() if tag.name == "link" else ""
            mime = str(tag.get("type", "")).lower()
            text = tag.get_text(" ", strip=True) if tag.name == "a" else ""
            openapi = cls.is_openapi_url(absolute) or "swagger" in text.lower() or "openapi" in text.lower()
            strong_json = "json" in mime or "api.w.org" in rel
            if not (openapi or strong_json or cls.is_api_url(absolute)):
                continue
            candidates.append(
                ApiReference(
                    url=absolute,
                    detection_method="html_api_reference",
                    api_format="openapi" if openapi else ("json" if strong_json else None),
                    documentation_url=absolute if openapi else None,
                    is_openapi=openapi,
                    unresolved_required_params=cls.unresolved_url_params(absolute),
                    title=text or None,
                )
            )

        # Atributos explícitos usados por dashboards/portales sin ejecutar JS.
        for tag in soup.find_all(True):
            for attr in ("data-api-url", "data-endpoint", "data-url"):
                raw = tag.get(attr)
                if not isinstance(raw, str) or not raw.strip():
                    continue
                absolute = UrlNormalizer.normalize(raw.strip(), base_url=base_url)
                if cls.is_api_url(absolute):
                    candidates.append(
                        ApiReference(
                            url=absolute,
                            detection_method="html_data_attribute",
                            unresolved_required_params=cls.unresolved_url_params(absolute),
                        )
                    )

        deduped: Dict[str, ApiReference] = {}
        for ref in candidates:
            if not ref.url.startswith(("http://", "https://")):
                continue
            identity = ApiIdentity.value(ref.method, ref.url)
            if identity not in deduped:
                deduped[identity] = ref
            else:
                old = deduped[identity]
                # Preferir la referencia con más metadata.
                if ref.is_openapi or (ref.api_format and not old.api_format):
                    deduped[identity] = ref
        return list(deduped.values())


class OpenApiDiscovery:
    """Parser local y bounded de OpenAPI 3 / Swagger 2.

    No resuelve `$ref` remotos ni ejecuta operaciones. Solo construye referencias
    GET y registra por qué una operación no sería ejecutable automáticamente.
    """

    HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}

    @staticmethod
    def parse_document(body: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            try:
                data = yaml.safe_load(body)
            except yaml.YAMLError:
                return None
        if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
            return data
        return None

    @staticmethod
    def _server_base(
        document: Mapping[str, Any], document_url: str
    ) -> Tuple[str, bool]:
        servers = document.get("servers")
        if isinstance(servers, list):
            for item in servers:
                if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                    continue
                raw = item["url"].strip()
                if not raw:
                    continue
                unresolved_server = False
                variables = item.get("variables") if isinstance(item.get("variables"), dict) else {}
                for name in re.findall(r"\{([^{}]+)\}", raw):
                    definition = variables.get(name) if isinstance(variables, dict) else None
                    default = definition.get("default") if isinstance(definition, dict) else None
                    if default is None:
                        unresolved_server = True
                        continue
                    raw = raw.replace("{" + name + "}", str(default))
                if "{" in raw or "}" in raw:
                    unresolved_server = True
                if not unresolved_server:
                    return urljoin(document_url, raw.rstrip("/") + "/"), False
                # Conservamos una base provisional solo para inventario, nunca callable.
                parsed_doc = urlparse(document_url)
                return f"{parsed_doc.scheme}://{parsed_doc.netloc}/", True

        host = document.get("host")
        if isinstance(host, str) and host.strip():
            schemes = document.get("schemes")
            scheme = "https"
            if isinstance(schemes, list) and schemes and isinstance(schemes[0], str):
                scheme = schemes[0]
            base_path = document.get("basePath") if isinstance(document.get("basePath"), str) else "/"
            return f"{scheme}://{host.strip()}{base_path.rstrip('/')}/", False

        parsed = urlparse(document_url)
        base_path = document.get("basePath") if isinstance(document.get("basePath"), str) else "/"
        return f"{parsed.scheme}://{parsed.netloc}{base_path.rstrip('/')}/", False

    @staticmethod
    def _required_parameters(
        path: str, path_item: Mapping[str, Any], operation: Mapping[str, Any]
    ) -> Tuple[str, ...]:
        params: List[Any] = []
        for source in (path_item.get("parameters"), operation.get("parameters")):
            if isinstance(source, list):
                params.extend(source)

        # Los placeholders del path son siempre irresueltos hasta que otra política
        # suministre explícitamente un valor seguro. BATCH 3A nunca adivina IDs.
        unresolved: List[str] = re.findall(r"\{([^{}]+)\}", path)
        for param in params:
            if not isinstance(param, dict):
                continue
            if "$ref" in param:
                unresolved.append(str(param.get("$ref") or "parameter_ref"))
                continue
            if not param.get("required"):
                continue
            unresolved.append(str(param.get("name") or "required_param"))

        request_body = operation.get("requestBody")
        if isinstance(request_body, dict) and request_body.get("required"):
            unresolved.append("requestBody")

        return tuple(sorted(set(unresolved)))

    @staticmethod
    def _auth_required(document: Mapping[str, Any], operation: Mapping[str, Any]) -> bool:
        security = operation.get("security", document.get("security"))
        if security is None:
            return False
        if security == []:
            return False
        if isinstance(security, list) and any(item == {} for item in security):
            # En OpenAPI una security requirement vacía permite acceso anónimo.
            return False
        return bool(security)

    @staticmethod
    def _format_from_operation(document: Mapping[str, Any], operation: Mapping[str, Any]) -> Optional[str]:
        responses = operation.get("responses")
        if isinstance(responses, dict):
            for response in responses.values():
                if not isinstance(response, dict):
                    continue
                content = response.get("content")
                if isinstance(content, dict):
                    for mime in content.keys():
                        low = str(mime).lower()
                        if "geo+json" in low:
                            return "geojson"
                        if "json" in low:
                            return "json"
                        if "xml" in low:
                            return "xml"
                        if "csv" in low:
                            return "csv"
        produces = operation.get("produces", document.get("produces"))
        if isinstance(produces, list):
            for mime in produces:
                low = str(mime).lower()
                if "geo+json" in low:
                    return "geojson"
                if "json" in low:
                    return "json"
                if "xml" in low:
                    return "xml"
                if "csv" in low:
                    return "csv"
        return None

    @classmethod
    def discover(cls, body: str, *, document_url: str) -> OpenApiDiscoveryResult:
        document = cls.parse_document(body)
        if document is None:
            return OpenApiDiscoveryResult(parse_error="INVALID_OPENAPI_DOCUMENT")

        result = OpenApiDiscoveryResult()
        base, unresolved_server = cls._server_base(document, document_url)
        paths = document.get("paths")
        if not isinstance(paths, dict):
            return result

        for path, raw_path_item in paths.items():
            if not isinstance(path, str) or not isinstance(raw_path_item, dict):
                continue
            for method, raw_operation in raw_path_item.items():
                method_lower = str(method).lower()
                if method_lower not in cls.HTTP_METHODS or not isinstance(raw_operation, dict):
                    continue
                if method_lower != "get":
                    result.non_get_operations_skipped += 1
                    continue

                endpoint_url = urljoin(base, path.lstrip("/"))
                unresolved = list(cls._required_parameters(path, raw_path_item, raw_operation))
                if unresolved_server:
                    unresolved.append("server")
                unresolved = tuple(sorted(set(unresolved)))
                auth_required = cls._auth_required(document, raw_operation)
                if auth_required:
                    result.auth_required_operations += 1
                if unresolved:
                    result.unresolved_operations += 1

                params = []
                for source in (raw_path_item.get("parameters"), raw_operation.get("parameters")):
                    if isinstance(source, list):
                        params.extend(source)
                param_names = {
                    str(p.get("name", "")).lower()
                    for p in params
                    if isinstance(p, dict)
                }
                has_pagination = bool(param_names & PAGINATION_QUERY_KEYS)
                api_format = cls._format_from_operation(document, raw_operation)

                result.references.append(
                    ApiReference(
                        url=endpoint_url,
                        method="GET",
                        detection_method="openapi_get_operation",
                        api_format=api_format,
                        documentation_url=document_url,
                        is_openapi=True,
                        is_geojson=api_format == "geojson",
                        has_pagination=has_pagination,
                        operation_id=(
                            str(raw_operation.get("operationId"))
                            if raw_operation.get("operationId") is not None
                            else None
                        ),
                        auth_required=auth_required,
                        unresolved_required_params=unresolved,
                        title=(
                            str(raw_operation.get("summary") or raw_operation.get("description") or path)
                        ),
                    )
                )

        return result
