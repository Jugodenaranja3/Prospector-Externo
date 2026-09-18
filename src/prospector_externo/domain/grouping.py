"""Contratos declarativos de agrupamiento por fuente.

El grouping contract vive entre el catálogo bruto y la proyección DATAX. Permite
normalizar familias/series cuando el portal publica nombres, códigos o rutas que
no pueden inferirse de forma confiable con una heurística global.

No realiza HTTP, no modifica ``ResourceCandidate`` y no introduce conceptos
FILE/REPORT/DATA_BASE de Analize.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from prospector_externo.domain.models import ResourceCandidate


class GroupingMatch(BaseModel):
    """Predicados regex. Todos los campos declarados deben coincidir."""

    url_regex: Optional[str] = None
    origin_url_regex: Optional[str] = None
    title_regex: Optional[str] = None
    anchor_regex: Optional[str] = None
    context_regex: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_matcher(self):
        if not any(
            (
                self.url_regex,
                self.origin_url_regex,
                self.title_regex,
                self.anchor_regex,
                self.context_regex,
            )
        ):
            raise ValueError("GroupingMatch requiere al menos un regex.")
        return self


class GroupingRule(BaseModel):
    """Regla de familia lógica con templates alimentados por grupos nombrados."""

    rule_id: str
    match: GroupingMatch
    family_key_template: str
    family_title_template: Optional[str] = None


class GroupingContract(BaseModel):
    """Contrato por ``source_id``. La primera regla que coincide gana."""

    schema_version: str = "grouping-contract-1.0"
    source_id: str
    rules: List[GroupingRule] = Field(default_factory=list)


class GroupingResolution(BaseModel):
    family_key: str
    family_title: str
    rule_id: str


class GroupingContractResolver:
    """Resuelve reglas sin efectos laterales y de forma determinista."""

    FIELD_MAP = {
        "url_regex": "url",
        "origin_url_regex": "discovered_from_url",
        "title_regex": "title",
        "anchor_regex": "anchor_text",
        "context_regex": "context_text",
    }

    @staticmethod
    def _ascii(value: str) -> str:
        return "".join(
            char
            for char in unicodedata.normalize("NFKD", value or "")
            if not unicodedata.combining(char)
        )

    @classmethod
    def slugify(cls, value: str) -> str:
        value = cls._ascii(value).lower()
        tokens = re.findall(r"[a-z0-9]+", value)
        return "-".join(tokens).strip("-") or "resource"

    @staticmethod
    def _render(template: str, captures: Dict[str, str]) -> str:
        class SafeDict(dict):
            def __missing__(self, key):
                raise ValueError(f"Capture requerida no encontrada: {key}")

        return template.format_map(SafeDict(captures))

    @classmethod
    def _match_rule(
        cls,
        resource: ResourceCandidate,
        rule: GroupingRule,
    ) -> Optional[Dict[str, str]]:
        captures: Dict[str, str] = {}
        for matcher_name, resource_field in cls.FIELD_MAP.items():
            pattern = getattr(rule.match, matcher_name)
            if not pattern:
                continue
            value = getattr(resource, resource_field, None) or ""
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if match is None:
                return None
            for name, captured in match.groupdict().items():
                if captured is None:
                    continue
                previous = captures.get(name)
                if previous is not None and previous != captured:
                    return None
                captures[name] = captured.strip()
        return captures

    @classmethod
    def resolve(
        cls,
        contract: Optional[GroupingContract],
        resource: ResourceCandidate,
    ) -> Optional[GroupingResolution]:
        if contract is None:
            return None
        if contract.source_id != resource.source_id:
            raise ValueError(
                f"Grouping contract de {contract.source_id!r} no aplica a "
                f"resource.source_id={resource.source_id!r}."
            )

        for rule in contract.rules:
            captures = cls._match_rule(resource, rule)
            if captures is None:
                continue
            key_rendered = cls._render(rule.family_key_template, captures)
            family_key = cls.slugify(key_rendered)
            if rule.family_title_template:
                title = cls._render(rule.family_title_template, captures)
                family_title = re.sub(r"\s+", " ", title).strip()
            else:
                family_title = " ".join(
                    part.capitalize() for part in family_key.split("-") if part
                )
            return GroupingResolution(
                family_key=family_key,
                family_title=family_title,
                rule_id=rule.rule_id,
            )
        return None
