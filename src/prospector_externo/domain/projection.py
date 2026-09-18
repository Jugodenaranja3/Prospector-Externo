"""Proyección DATAX separada del catálogo bruto.

Este módulo no realiza HTTP, no modifica ResourceCandidate y no inventa conceptos
FILE/REPORT/DATA_BASE de Analize. Convierte evidencia ya descubierta en una vista
estable, agrupada por familia/periodo/representación para consumo downstream.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from prospector_externo.domain.grouping import (
    GroupingContract,
    GroupingContractResolver,
    GroupingUnmatchedPolicy,
)
from prospector_externo.domain.models import ApiMetadata, ResourceCandidate, ResourceType


class ProjectionPriority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ProjectionDecision(BaseModel):
    """Resultado auditable de clasificar un recurso bruto."""

    resource_key: str
    selected: bool
    priority: ProjectionPriority
    detected_format: str
    family_key: str
    period_label: Optional[str] = None
    reason_codes: Tuple[str, ...] = ()
    grouping_rule_id: Optional[str] = None


class ProjectedRepresentation(BaseModel):
    """Una representación publicada de una familia/periodo lógico."""

    resource_key: str
    source_id: str
    resource_type: ResourceType
    title: str
    description: str
    url: str
    raw_url: Optional[str] = None
    origin_url: Optional[str] = None
    format: str
    content_type: Optional[str] = None
    period_label: Optional[str] = None
    discovery_method: str
    priority: ProjectionPriority
    reason_codes: Tuple[str, ...] = ()
    content_hash: Optional[str] = None
    etag: Optional[str] = None
    last_modified_header: Optional[str] = None
    api: Optional[ApiMetadata] = None


class ProjectedPeriod(BaseModel):
    period_label: Optional[str] = None
    preferred_resource_key: str
    representations: List[ProjectedRepresentation] = Field(default_factory=list)


class ProjectedFamily(BaseModel):
    family_id: str
    family_key: str
    title: str
    latest_period: Optional[str] = None
    available_formats: List[str] = Field(default_factory=list)
    periods: List[ProjectedPeriod] = Field(default_factory=list)


class DataxProjection(BaseModel):
    """Vista determinista para downstream DATAX; no es el JSON legacy."""

    schema_version: str = "datax-projection-1.0"
    source_id: str
    source_name: str
    entrypoint: str
    run_id: str
    resources_hash: str
    total_raw_resources: int
    total_selected_resources: int
    total_families: int
    families: List[ProjectedFamily] = Field(default_factory=list)
    decisions: List[ProjectionDecision] = Field(default_factory=list)


class ProjectionPeriodNormalizer:
    """Normaliza periodos observables sin modificar el ResourceCandidate original."""

    MONTHS = {
        "enero": 1,
        "ene": 1,
        "january": 1,
        "jan": 1,
        "febrero": 2,
        "feb": 2,
        "february": 2,
        "marzo": 3,
        "mar": 3,
        "march": 3,
        "abril": 4,
        "abr": 4,
        "april": 4,
        "apr": 4,
        "mayo": 5,
        "may": 5,
        "junio": 6,
        "jun": 6,
        "june": 6,
        "julio": 7,
        "jul": 7,
        "july": 7,
        "agosto": 8,
        "ago": 8,
        "august": 8,
        "aug": 8,
        "septiembre": 9,
        "setiembre": 9,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "octubre": 10,
        "oct": 10,
        "october": 10,
        "noviembre": 11,
        "nov": 11,
        "november": 11,
        "diciembre": 12,
        "dic": 12,
        "december": 12,
        "dec": 12,
    }

    @staticmethod
    def _ascii(text: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFKD", text or "")
            if not unicodedata.combining(c)
        ).lower()

    @classmethod
    def extract_text(cls, raw_text: str) -> Optional[str]:
        text = cls._ascii(raw_text)

        # Fecha diaria ISO o numérica. Es necesaria para series semanales como BCB.
        match = re.search(
            r"(?<!\d)(19\d{2}|20\d{2})[-_/](0?[1-9]|1[0-2])[-_/](0?[1-9]|[12]\d|3[01])(?!\d)",
            text,
        )
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

        match = re.search(
            r"(?<!\d)(0?[1-9]|[12]\d|3[01])[-_/](0?[1-9]|1[0-2])[-_/](19\d{2}|20\d{2})(?!\d)",
            text,
        )
        if match:
            return f"{int(match.group(3)):04d}-{int(match.group(2)):02d}-{int(match.group(1)):02d}"

        # Día + nombre de mes + año, por ejemplo '11 de septiembre de 2026'.
        for name, month in sorted(cls.MONTHS.items(), key=lambda item: len(item[0]), reverse=True):
            day_match = re.search(
                rf"\b(0?[1-9]|[12]\d|3[01])(?:\s+de)?\s+{re.escape(name)}(?:\s+de)?\s+(19\d{{2}}|20\d{{2}})\b",
                text,
            )
            if day_match:
                return f"{int(day_match.group(2)):04d}-{month:02d}-{int(day_match.group(1)):02d}"

        # Quarter variants have priority over numeric month/year detection so
        # strings such as "T2 2026" are never mistaken for February 2026.
        quarter = re.search(
            r"\b(?:q|t)([1-4])\s*[-_/ ]?\s*(19\d{2}|20\d{2})\b|"
            r"\b(19\d{2}|20\d{2})\s*[-_/ ]?\s*(?:q|t)([1-4])\b|"
            r"\b([1-4])(?:er|do|ro|to)?\s+trimestre\s+(19\d{2}|20\d{2})\b",
            text,
        )
        if quarter:
            if quarter.group(1):
                q, y = quarter.group(1), quarter.group(2)
            elif quarter.group(3):
                y, q = quarter.group(3), quarter.group(4)
            else:
                q, y = quarter.group(5), quarter.group(6)
            return f"{int(y):04d}-Q{int(q)}"

        # YYYY-MM / YYYY_MM / YYYY/MM / YYYY MM
        match = re.search(r"(?<![a-z0-9])(19\d{2}|20\d{2})[-_/ ](0?[1-9]|1[0-2])(?![a-z0-9])", text)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"

        # MM-YYYY / MM_YYYY / MM/YYYY / MM YYYY
        match = re.search(r"(?<![a-z0-9])(0?[1-9]|1[0-2])[-_/ ](19\d{2}|20\d{2})(?![a-z0-9])", text)
        if match:
            return f"{int(match.group(2)):04d}-{int(match.group(1)):02d}"

        # Month name + year (or year + month name).
        for name, month in sorted(cls.MONTHS.items(), key=lambda item: len(item[0]), reverse=True):
            if not re.search(rf"\b{re.escape(name)}\b", text):
                continue
            year = re.search(r"\b(19\d{2}|20\d{2})\b", text)
            if year:
                return f"{int(year.group(1)):04d}-{month:02d}"

        year_only = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        return year_only.group(1) if year_only else None

    @classmethod
    def extract(cls, resource: ResourceCandidate) -> Optional[str]:
        if resource.period_label:
            return resource.period_label.strip() or None
        return cls.extract_text(
            " ".join(
                part
                for part in (
                    resource.title,
                    resource.anchor_text,
                    resource.context_text,
                    unquote(resource.url),
                )
                if part
            )
        )


class ResourceFamilyKeyBuilder:
    """Construye una familia estable eliminando solo ruido de representación/periodo."""

    GENERIC_LINK_TEXT = {
        "descargar", "download", "ver", "archivo", "documento", "file", "link",
        "aqui", "aquí", "pdf", "excel", "xlsx", "xls", "ods", "csv", "json", "xml", "get",
    }
    FORMAT_TOKENS = {
        "pdf", "doc", "docx", "xls", "xlsx", "ods", "csv", "tsv", "json", "xml",
        "geojson", "zip", "tar", "gz", "tgz", "rar", "7z", "ndjson", "jsonstat",
        "topojson", "estadistica", "estadisticas", "estadistico", "estadisticos",
    }
    MONTH_WORDS = set(ProjectionPeriodNormalizer.MONTHS)

    @staticmethod
    def _ascii(text: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFKD", text or "")
            if not unicodedata.combining(c)
        ).lower()

    @classmethod
    def _candidate_texts(cls, resource: ResourceCandidate) -> List[str]:
        values: List[str] = []
        title = (resource.title or "").strip()
        context = (resource.context_text or "").strip()
        anchor = (resource.anchor_text or "").strip()

        # Para APIs la identidad canónica tiene prioridad. Dos endpoints de una
        # misma fuente no deben colapsar solo porque compartan un título genérico.
        if resource.resource_type == ResourceType.API and resource.api is not None:
            values.append(resource.api.identity)

        if title and cls._ascii(title) not in cls.GENERIC_LINK_TEXT:
            values.append(title)
        if context:
            values.append(context)
        if anchor and cls._ascii(anchor) not in cls.GENERIC_LINK_TEXT:
            values.append(anchor)

        parsed = urlparse(resource.url)
        basename = os.path.basename(parsed.path.rstrip("/"))
        if basename:
            values.append(unquote(basename))
        elif parsed.path:
            values.append(unquote(parsed.path.strip("/")))
        return values

    @classmethod
    def _clean(cls, text: str) -> str:
        value = cls._ascii(unquote(text))
        value = re.sub(r"https?://[^/\s]+", " ", value)
        value = re.sub(r"\.(?:pdf|docx?|xlsx?|ods|csv|tsv|json|xml|geojson|zip|tar|gz|tgz|rar|7z)\b", " ", value)

        # Dates and periods.
        value = re.sub(r"(?<!\d)(?:19\d{2}|20\d{2})[-_/ ](?:0?[1-9]|1[0-2])(?!\d)", " ", value)
        value = re.sub(r"(?<!\d)(?:0?[1-9]|1[0-2])[-_/ ](?:19\d{2}|20\d{2})(?!\d)", " ", value)
        value = re.sub(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)", " ", value)
        value = re.sub(r"\b(?:q|t)[1-4]\b", " ", value)
        value = re.sub(r"\b[1-4](?:er|do|ro|to)?\s+trimestre\b", " ", value)
        value = re.sub(r"\b(?:0?[1-9]|[12]\d|3[01])[-_/](?:0?[1-9]|1[0-2])[-_/](?:19\d{2}|20\d{2})\b", " ", value)

        for month in sorted(cls.MONTH_WORDS, key=len, reverse=True):
            value = re.sub(rf"\b{re.escape(month)}\b", " ", value)

        tokens = re.findall(r"[a-z0-9]+", value)
        tokens = [
            token for token in tokens
            if token not in cls.GENERIC_LINK_TEXT
            and token not in cls.FORMAT_TOKENS
        ]
        return "-".join(tokens).strip("-")

    @classmethod
    def build(cls, resource: ResourceCandidate) -> str:
        candidates = cls._candidate_texts(resource)
        cleaned: List[str] = []
        for candidate in candidates:
            value = cls._clean(candidate)
            if value:
                cleaned.append(value)

        # Prefer a specific stable phrase, not a generic one-word label.
        for value in cleaned:
            if len(value) >= 4 and value not in {"reporte", "informe", "boletin", "datos", "data"}:
                return value

        if cleaned:
            return cleaned[0]

        # Last-resort key remains deterministic and source-local via family_id.
        path = urlparse(resource.url).path.strip("/") or "resource"
        fallback = cls._clean(path) or "resource"
        return fallback


class ResourceProjectionPolicy:
    """Clasifica recursos para downstream sin borrar evidencia del catálogo bruto."""

    STRUCTURED_FORMATS = {
        "csv", "tsv", "xls", "xlsx", "ods", "json", "xml", "geojson", "ndjson",
        "jsonstat", "topojson",
    }
    DOCUMENT_FORMATS = {"pdf", "doc", "docx"}
    ARCHIVE_FORMATS = {"zip", "tar", "gz", "tgz", "rar", "7z"}

    POSITIVE_TERMS = (
        "estadistic", "dataset", "datos", "serie", "indicador", "boletin estad",
        "reporte financiero", "informe financiero", "balance", "cartera", "mensual",
        "trimestral", "semestral", "anuario", "censo", "encuesta", "precios",
        "produccion", "comercio", "empleo", "presupuesto", "exportacion",
    )
    ADMIN_TERMS = (
        "convocatoria", "organigrama", "recursos humanos", "rrhh", "reglamento",
        "normativa", "resolucion", "formulario", "manual", "contratacion",
        "licitacion", "mision", "vision", "evento", "publicidad",
    )

    @staticmethod
    def detect_format(resource: ResourceCandidate) -> str:
        if resource.resource_type == ResourceType.API:
            if resource.api and resource.api.format:
                return resource.api.format.strip().lower().replace("-", "")
            return "api"

        ext = (resource.file_extension or "").strip().lower().lstrip(".")
        if ext:
            return ext

        content_type = (resource.content_type or "").lower()
        mapping = {
            "text/csv": "csv",
            "text/tab-separated-values": "tsv",
            "application/json": "json",
            "application/geo+json": "geojson",
            "application/xml": "xml",
            "text/xml": "xml",
            "application/pdf": "pdf",
            "spreadsheetml": "xlsx",
            "opendocument.spreadsheet": "ods",
        }
        for marker, value in mapping.items():
            if marker in content_type:
                return value
        return "unknown"

    @classmethod
    def _evidence_text(cls, resource: ResourceCandidate) -> str:
        text = " ".join(
            part for part in (
                resource.title,
                resource.anchor_text,
                resource.context_text,
                unquote(resource.url),
            ) if part
        )
        return ResourceFamilyKeyBuilder._ascii(text)

    @classmethod
    def classify(cls, resource: ResourceCandidate) -> Tuple[bool, ProjectionPriority, Tuple[str, ...]]:
        fmt = cls.detect_format(resource)
        text = cls._evidence_text(resource)
        has_positive = any(term in text for term in cls.POSITIVE_TERMS)
        has_admin = any(term in text for term in cls.ADMIN_TERMS)
        period = ProjectionPeriodNormalizer.extract(resource)

        if resource.resource_type == ResourceType.API:
            api = resource.api
            if api is None:
                return False, ProjectionPriority.LOW, ("api_without_metadata",)
            # El documento OpenAPI/Swagger es documentación, no dataset. Las
            # operaciones GET extraídas de ese documento sí son candidatas reales.
            if api.is_openapi and resource.discovery_method != "openapi_get_operation":
                return False, ProjectionPriority.LOW, ("api_documentation_not_dataset",)
            if api.method.upper() != "GET":
                return False, ProjectionPriority.LOW, ("api_non_get",)
            if api.auth_required or not api.callable_by_policy:
                return False, ProjectionPriority.LOW, ("api_not_publicly_callable",)
            return True, ProjectionPriority.HIGH, ("public_get_api",)

        if fmt in cls.STRUCTURED_FORMATS:
            return True, ProjectionPriority.HIGH, ("structured_data_format",)

        if fmt in cls.ARCHIVE_FORMATS:
            if has_positive or period:
                return True, ProjectionPriority.MEDIUM, ("archive_with_statistical_evidence",)
            return False, ProjectionPriority.LOW, ("archive_contents_unknown",)

        if fmt in cls.DOCUMENT_FORMATS:
            if has_admin and not has_positive:
                return False, ProjectionPriority.LOW, ("administrative_document",)
            if has_positive and period:
                return True, ProjectionPriority.MEDIUM, ("periodic_statistical_document",)
            if has_positive:
                return True, ProjectionPriority.MEDIUM, ("statistical_document",)
            if period:
                return True, ProjectionPriority.MEDIUM, ("periodic_document",)
            return False, ProjectionPriority.LOW, ("document_without_data_evidence",)

        return False, ProjectionPriority.LOW, ("unsupported_or_unknown_format",)


class RepresentationPreference:
    """Ordena representaciones sin descartar alternativas publicadas."""

    RANK = {
        "csv": 120,
        "tsv": 118,
        "xlsx": 115,
        "xls": 110,
        "ods": 108,
        "json": 106,
        "ndjson": 105,
        "geojson": 104,
        "jsonstat": 103,
        "topojson": 102,
        "xml": 100,
        "api": 98,
        "zip": 80,
        "tar": 78,
        "gz": 76,
        "tgz": 76,
        "pdf": 50,
        "docx": 40,
        "doc": 35,
        "unknown": 0,
    }

    @classmethod
    def rank(cls, representation: ProjectedRepresentation) -> Tuple[int, str]:
        fmt = representation.format
        if representation.resource_type == ResourceType.API:
            # API estructurada mantiene su formato para metadata, pero compite como API.
            score = max(cls.RANK.get(fmt, 0), cls.RANK["api"])
        else:
            score = cls.RANK.get(fmt, 0)
        return score, representation.resource_key

    @classmethod
    def preferred(cls, representations: Sequence[ProjectedRepresentation]) -> ProjectedRepresentation:
        return sorted(
            representations,
            key=lambda rep: (-cls.rank(rep)[0], rep.resource_key),
        )[0]


class ProjectionBuilder:
    """Construye una proyección determinista a partir de recursos ya descubiertos."""

    @staticmethod
    def family_id(source_id: str, family_key: str) -> str:
        digest = hashlib.sha256(f"{source_id}|{family_key}".encode("utf-8")).hexdigest()
        return digest[:24]

    @staticmethod
    def _family_title(family_key: str) -> str:
        return " ".join(part.capitalize() for part in family_key.split("-") if part)

    @staticmethod
    def _period_sort_key(period: Optional[str]) -> Tuple[int, int, int, str]:
        if not period:
            return (0, 0, 0, "")
        day = re.fullmatch(r"(19\d{2}|20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", period)
        if day:
            return (4, int(day.group(1)), int(day.group(2)) * 100 + int(day.group(3)), period)
        month = re.fullmatch(r"(19\d{2}|20\d{2})-(0[1-9]|1[0-2])", period)
        if month:
            return (3, int(month.group(1)), int(month.group(2)), period)
        quarter = re.fullmatch(r"(19\d{2}|20\d{2})-Q([1-4])", period)
        if quarter:
            return (2, int(quarter.group(1)), int(quarter.group(2)), period)
        year = re.fullmatch(r"(19\d{2}|20\d{2})", period)
        if year:
            return (1, int(year.group(1)), 0, period)
        return (0, 0, 0, period)

    @classmethod
    def _to_representation(
        cls,
        resource: ResourceCandidate,
        priority: ProjectionPriority,
        reasons: Tuple[str, ...],
        period: Optional[str],
    ) -> ProjectedRepresentation:
        fmt = ResourceProjectionPolicy.detect_format(resource)
        description = (
            (resource.context_text or "").strip()
            or (resource.anchor_text or "").strip()
            or (resource.title or "").strip()
            or resource.url
        )
        return ProjectedRepresentation(
            resource_key=resource.resource_key,
            source_id=resource.source_id,
            resource_type=resource.resource_type,
            title=resource.title or description,
            description=description,
            url=resource.url,
            raw_url=resource.raw_url,
            origin_url=resource.discovered_from_url,
            format=fmt,
            content_type=resource.content_type,
            period_label=period,
            discovery_method=resource.discovery_method,
            priority=priority,
            reason_codes=reasons,
            content_hash=resource.content_hash,
            etag=resource.etag,
            last_modified_header=resource.last_modified_header,
            api=resource.api.model_copy(deep=True) if resource.api is not None else None,
        )

    @classmethod
    def build(
        cls,
        *,
        source_id: str,
        source_name: str,
        entrypoint: str,
        run_id: str,
        resources_hash: str,
        resources: Iterable[ResourceCandidate],
        grouping_contract: Optional[GroupingContract] = None,
    ) -> DataxProjection:
        raw = list(resources)
        if grouping_contract is not None and grouping_contract.source_id != source_id:
            raise ValueError(
                f"Grouping contract de {grouping_contract.source_id!r} no corresponde a source_id={source_id!r}."
            )
        decisions: List[ProjectionDecision] = []
        grouped: dict[str, dict[Optional[str], dict[str, ProjectedRepresentation]]] = {}
        family_titles: dict[str, str] = {}

        for resource in raw:
            selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
            fmt = ResourceProjectionPolicy.detect_format(resource)
            period = ProjectionPeriodNormalizer.extract(resource)
            resolution = GroupingContractResolver.resolve(grouping_contract, resource)
            if resolution is not None:
                family_key = resolution.family_key
                family_titles.setdefault(family_key, resolution.family_title)
                grouping_rule_id = resolution.rule_id
            else:
                family_key = ResourceFamilyKeyBuilder.build(resource)
                grouping_rule_id = None
                if (
                    grouping_contract is not None
                    and grouping_contract.unmatched_policy == GroupingUnmatchedPolicy.EXCLUDE
                ):
                    selected = False
                    priority = ProjectionPriority.LOW
                    reasons = tuple(dict.fromkeys((*reasons, "grouping_contract_unmatched")))
            decisions.append(
                ProjectionDecision(
                    resource_key=resource.resource_key,
                    selected=selected,
                    priority=priority,
                    detected_format=fmt,
                    family_key=family_key,
                    period_label=period,
                    reason_codes=reasons,
                    grouping_rule_id=grouping_rule_id,
                )
            )
            if not selected:
                continue

            representation = cls._to_representation(resource, priority, reasons, period)
            by_period = grouped.setdefault(family_key, {})
            by_resource = by_period.setdefault(period, {})
            by_resource[representation.resource_key] = representation

        families: List[ProjectedFamily] = []
        for family_key in sorted(grouped):
            period_groups: List[ProjectedPeriod] = []
            available_formats = set()
            periods_map = grouped[family_key]
            ordered_periods = sorted(
                periods_map,
                key=cls._period_sort_key,
                reverse=True,
            )
            for period in ordered_periods:
                representations = list(periods_map[period].values())
                representations.sort(
                    key=lambda rep: (-RepresentationPreference.rank(rep)[0], rep.resource_key)
                )
                preferred = RepresentationPreference.preferred(representations)
                available_formats.update(rep.format for rep in representations)
                period_groups.append(
                    ProjectedPeriod(
                        period_label=period,
                        preferred_resource_key=preferred.resource_key,
                        representations=representations,
                    )
                )

            latest = next((item.period_label for item in period_groups if item.period_label), None)
            formats_sorted = sorted(
                available_formats,
                key=lambda fmt: (-RepresentationPreference.RANK.get(fmt, 0), fmt),
            )
            families.append(
                ProjectedFamily(
                    family_id=cls.family_id(source_id, family_key),
                    family_key=family_key,
                    title=family_titles.get(family_key, cls._family_title(family_key)),
                    latest_period=latest,
                    available_formats=formats_sorted,
                    periods=period_groups,
                )
            )

        decisions.sort(key=lambda item: item.resource_key)
        selected_count = sum(
            len(period.representations)
            for family in families
            for period in family.periods
        )
        return DataxProjection(
            source_id=source_id,
            source_name=source_name,
            entrypoint=entrypoint,
            run_id=run_id,
            resources_hash=resources_hash,
            total_raw_resources=len(raw),
            total_selected_resources=selected_count,
            total_families=len(families),
            families=families,
            decisions=decisions,
        )
