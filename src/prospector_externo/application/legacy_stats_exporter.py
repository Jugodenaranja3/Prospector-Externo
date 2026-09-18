"""Exportador de compatibilidad para el JSON histórico ``ESTADISTICAS``.

La compatibilidad legacy vive aquí, después de ``DataxProjection``. El catálogo
bruto y el modelo de proyección no conocen claves ``Descargar.csv`` ni árboles
específicos de fuentes antiguas.

El exportador es puro salvo ``write_atomic``: no hace HTTP, no descarga archivos
y no inventa FILE/REPORT/DATA_BASE de Analize.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from prospector_externo.domain.projection import (
    DataxProjection,
    ProjectedFamily,
    ProjectedRepresentation,
    RepresentationPreference,
)


LEGACY_ROOT_KEY = "ESTADISTICAS"
LEGACY_SLOT_BASENAME = "Descargar"
LEGACY_SLOT_EXTENSION = ".csv"
LEGACY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "descripcion",
    "url_descarga",
    "fecha_actualizacion",
    "tipo_archivo",
    "url_origen",
    "metodo_deteccion",
)


class LegacyExportPlan(BaseModel):
    """Routing externo al dominio para adaptar fuentes a árboles históricos.

    ``family_routes`` usa ``family_key`` de la proyección como clave. Cada segmento
    acepta: ``{family}``, ``{family_key}``, ``{year}``, ``{period}``,
    ``{source_id}`` y ``{format}``.

    Una fuente puede reproducir un árbol histórico sin añadir campos legacy a
    ``ResourceCandidate``/``DataxProjection``. Familias sin regla usan un layout
    seguro y determinista bajo ``files/<familia>/<año>``.
    """

    root_key: str = LEGACY_ROOT_KEY
    family_routes: Dict[str, Tuple[str, ...]] = Field(default_factory=dict)
    default_route: Tuple[str, ...] = ("files", "{family}", "{year}")
    no_period_label: str = "SIN_PERIODO"


class LegacyExportResult(BaseModel):
    document: Dict[str, Any]
    record_count: int


class LegacyStatsContractValidator:
    """Valida el contrato observado en los JSON históricos de DATAX."""

    _slot = re.compile(r"^Descargar(?:_[2-9]\d*)?\.csv$")

    @classmethod
    def validate(cls, document: Mapping[str, Any], *, root_key: str = LEGACY_ROOT_KEY) -> int:
        if set(document.keys()) != {root_key}:
            raise ValueError(f"El documento legacy debe contener solo la raíz {root_key!r}.")
        root = document[root_key]
        if not isinstance(root, Mapping):
            raise ValueError("La raíz ESTADISTICAS debe ser un objeto JSON.")

        count = 0

        def walk(node: Any, path: Tuple[str, ...]) -> None:
            nonlocal count
            if not isinstance(node, Mapping):
                raise ValueError(f"Nodo legacy no-objeto en {'/'.join(path) or root_key}.")
            for key, value in node.items():
                if cls._slot.fullmatch(str(key)):
                    if not isinstance(value, Mapping):
                        raise ValueError(f"Registro {key!r} debe ser objeto JSON.")
                    if set(value.keys()) != set(LEGACY_REQUIRED_FIELDS):
                        raise ValueError(
                            f"Registro {key!r} no cumple campos legacy exactos: {sorted(value.keys())}"
                        )
                    for field in LEGACY_REQUIRED_FIELDS:
                        if not isinstance(value[field], str):
                            raise ValueError(f"Campo {field!r} en {key!r} debe ser string.")
                    count += 1
                else:
                    walk(value, path + (str(key),))

        walk(root, (root_key,))
        return count


class LegacyStatsJsonExporter:
    """Convierte ``DataxProjection`` al contrato histórico ``ESTADISTICAS``."""

    GENERIC_TITLES = {
        "descargar", "download", "ver", "archivo", "documento", "file", "link",
        "aqui", "aquí",
    }

    @staticmethod
    def _ascii(value: str) -> str:
        return "".join(
            char
            for char in unicodedata.normalize("NFKD", value or "")
            if not unicodedata.combining(char)
        ).lower().strip()

    @classmethod
    def _legacy_segment(cls, value: str) -> str:
        ascii_value = "".join(
            char
            for char in unicodedata.normalize("NFKD", value or "")
            if not unicodedata.combining(char)
        )
        tokens = re.findall(r"[A-Za-z0-9]+", ascii_value)
        return "_".join(tokens) or "SIN_NOMBRE"

    @staticmethod
    def _year(period: Optional[str], fallback: str) -> str:
        if period:
            match = re.match(r"^(19\d{2}|20\d{2})", period)
            if match:
                return match.group(1)
        return fallback

    @staticmethod
    def _format_name(representation: ProjectedRepresentation) -> str:
        value = (representation.format or "unknown").strip().upper()
        aliases = {
            "JSONSTAT": "JSON-STAT",
            "NDJSON": "NDJSON",
            "GEOJSON": "GEOJSON",
            "TOPOJSON": "TOPOJSON",
            "UNKNOWN": "DESCONOCIDO",
        }
        return aliases.get(value, value)

    @classmethod
    def _description(cls, representation: ProjectedRepresentation) -> str:
        title = (representation.title or "").strip()
        description = (representation.description or "").strip()
        if title and cls._ascii(title) not in cls.GENERIC_TITLES:
            return title
        if description:
            return description
        path = unquote(urlparse(representation.raw_url or representation.url).path)
        filename = os.path.basename(path.rstrip("/"))
        return filename or representation.url

    @staticmethod
    def _download_url(representation: ProjectedRepresentation) -> str:
        # El legacy consumía la URL publicada real. ``raw_url`` preserva queries
        # observadas que una normalización podría haber reordenado/limpiado.
        return (representation.raw_url or representation.url).strip()

    @classmethod
    def _record(
        cls,
        projection: DataxProjection,
        representation: ProjectedRepresentation,
    ) -> Dict[str, str]:
        return {
            "descripcion": cls._description(representation),
            "url_descarga": cls._download_url(representation),
            "fecha_actualizacion": (representation.period_label or "").strip(),
            "tipo_archivo": cls._format_name(representation),
            "url_origen": (representation.origin_url or projection.entrypoint or "").strip(),
            "metodo_deteccion": (representation.discovery_method or "").strip(),
        }

    @classmethod
    def _route(
        cls,
        projection: DataxProjection,
        family: ProjectedFamily,
        representation: ProjectedRepresentation,
        plan: LegacyExportPlan,
    ) -> Tuple[str, ...]:
        template: Sequence[str] = plan.family_routes.get(family.family_key, plan.default_route)
        period = representation.period_label or plan.no_period_label
        values = {
            "family": cls._legacy_segment(family.title or family.family_key),
            "family_key": cls._legacy_segment(family.family_key),
            "year": cls._year(representation.period_label, plan.no_period_label),
            "period": cls._legacy_segment(period),
            "source_id": cls._legacy_segment(projection.source_id),
            "format": cls._legacy_segment(cls._format_name(representation)),
        }
        route: list[str] = []
        for segment in template:
            rendered = str(segment)
            for token, replacement in values.items():
                rendered = rendered.replace("{" + token + "}", replacement)
            rendered = rendered.strip()
            if not rendered:
                raise ValueError(f"Ruta legacy produjo un segmento vacío para {family.family_key!r}.")
            route.append(rendered)
        if not route:
            raise ValueError("Una ruta legacy no puede quedar vacía.")
        return tuple(route)

    @staticmethod
    def _period_sort(period: Optional[str]) -> Tuple[int, int, int, str]:
        if not period:
            return (0, 0, 0, "")
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
    def _representation_sort_key(cls, rep: ProjectedRepresentation) -> Tuple[Any, ...]:
        period = cls._period_sort(rep.period_label)
        rank = RepresentationPreference.rank(rep)[0]
        # Se ordena newest-first y, dentro del mismo periodo, por preferencia de
        # representación. URL/key hacen el desempate reproducible.
        return (-period[0], -period[1], -period[2], -rank, rep.url, rep.resource_key)

    @staticmethod
    def _slot_name(index: int) -> str:
        if index == 1:
            return f"{LEGACY_SLOT_BASENAME}{LEGACY_SLOT_EXTENSION}"
        return f"{LEGACY_SLOT_BASENAME}_{index}{LEGACY_SLOT_EXTENSION}"

    @classmethod
    def build(
        cls,
        projection: DataxProjection,
        plan: Optional[LegacyExportPlan] = None,
    ) -> LegacyExportResult:
        plan = plan or LegacyExportPlan()
        buckets: Dict[Tuple[str, ...], list[ProjectedRepresentation]] = {}

        # DataxProjection ya contiene solo recursos seleccionados. Conservamos
        # todas las representaciones publicadas, no solo la preferida.
        seen: set[str] = set()
        for family in projection.families:
            for period in family.periods:
                for representation in period.representations:
                    if representation.resource_key in seen:
                        continue
                    seen.add(representation.resource_key)
                    route = cls._route(projection, family, representation, plan)
                    buckets.setdefault(route, []).append(representation)

        root: Dict[str, Any] = {}
        for route in sorted(buckets):
            node = root
            for segment in route:
                child = node.setdefault(segment, {})
                if not isinstance(child, dict):
                    raise ValueError(f"Colisión de ruta legacy en {route!r}.")
                node = child
            ordered = sorted(buckets[route], key=cls._representation_sort_key)
            for index, representation in enumerate(ordered, start=1):
                node[cls._slot_name(index)] = cls._record(projection, representation)

        document = {plan.root_key: root}
        count = LegacyStatsContractValidator.validate(document, root_key=plan.root_key)
        return LegacyExportResult(document=document, record_count=count)

    @classmethod
    def write_atomic(
        cls,
        path: str | Path,
        projection: DataxProjection,
        plan: Optional[LegacyExportPlan] = None,
    ) -> LegacyExportResult:
        result = cls.build(projection, plan)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(result.document, handle, ensure_ascii=False, indent=4)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
            temp_name = None
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
        return result
