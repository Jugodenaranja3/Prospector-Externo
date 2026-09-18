"""Políticas de cobertura para paginación y spider traps.

Estas políticas son deliberadamente conservadoras: evitan patrones claramente
explosivos sin convertir heurísticas de relevancia en gates destructivos.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


@dataclass
class PaginationFamilyState:
    pages_visited: int = 0
    new_resources: int = 0
    consecutive_empty: int = 0
    recent_yield: Deque[int] = field(default_factory=deque)
    stopped: bool = False


class PaginationYieldPolicy:
    """Corta familias paginadas improductivas y mantiene las productivas."""

    PAGINATION_KEYS = {
        "page", "p", "pg", "pageno", "page_no", "page_num", "pagina",
        "offset", "start", "from", "skip",
    }
    PATH_PATTERN = re.compile(r"/(?:page|pagina|p)/(\d+)(?:/|$)", re.IGNORECASE)

    def __init__(
        self,
        *,
        min_pages_before_cutoff: int = 2,
        max_consecutive_empty: int = 2,
        recent_window: int = 3,
    ) -> None:
        self.min_pages_before_cutoff = max(1, min_pages_before_cutoff)
        self.max_consecutive_empty = max(1, max_consecutive_empty)
        self.recent_window = max(1, recent_window)
        self._states: Dict[str, PaginationFamilyState] = {}

    @classmethod
    def family_for(cls, url: str) -> Optional[str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None

        query = parse_qsl(parsed.query, keep_blank_values=True)
        has_pagination_query = any(k.lower() in cls.PAGINATION_KEYS for k, _ in query)
        path_match = cls.PATH_PATTERN.search(parsed.path or "")
        if not has_pagination_query and not path_match:
            return None

        normalized_query = []
        for key, value in sorted(query):
            normalized_query.append(
                (key, "*" if key.lower() in cls.PAGINATION_KEYS else value)
            )

        path = parsed.path or "/"
        if path_match:
            path = cls.PATH_PATTERN.sub(
                lambda m: m.group(0).replace(m.group(1), "*"),
                path,
            )

        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                path,
                "",
                urlencode(normalized_query),
                "",
            )
        )

    @classmethod
    def is_pagination_url(cls, url: str) -> bool:
        return cls.family_for(url) is not None

    def observe(self, url: str, *, new_resources: int) -> None:
        family = self.family_for(url)
        if family is None:
            return

        state = self._states.setdefault(family, PaginationFamilyState())
        if state.stopped:
            return

        state.pages_visited += 1
        state.new_resources += max(0, new_resources)
        if new_resources > 0:
            state.consecutive_empty = 0
        else:
            state.consecutive_empty += 1

        state.recent_yield.append(max(0, new_resources))
        while len(state.recent_yield) > self.recent_window:
            state.recent_yield.popleft()

        if (
            state.pages_visited >= self.min_pages_before_cutoff
            and state.consecutive_empty >= self.max_consecutive_empty
            and sum(state.recent_yield) == 0
        ):
            state.stopped = True

    def should_enqueue(self, url: str) -> bool:
        family = self.family_for(url)
        if family is None:
            return True
        state = self._states.get(family)
        return state is None or not state.stopped

    @property
    def pages_observed(self) -> int:
        return sum(state.pages_visited for state in self._states.values())

    @property
    def families_stopped(self) -> int:
        return sum(1 for state in self._states.values() if state.stopped)


class SpiderTrapDetector:
    """Detecta firmas acotadas de URLs claramente explosivas.

    No intenta decidir relevancia. Solo rechaza patrones técnicos que pueden
    producir espacios casi infinitos: URLs enormes, query con demasiadas claves,
    segmentos repetidos y calendarios diarios/mensuales con demasiadas variantes.
    """

    DATE_PATH_RE = re.compile(
        r"(?P<prefix>/)(?P<year>(?:19|20)\d{2})/(?P<month>0?[1-9]|1[0-2])"
        r"(?:/(?P<day>0?[1-9]|[12]\d|3[01]))?(?=/|$)"
    )
    YEAR_QUERY_KEYS = {"year", "anio", "ano", "gestion"}
    MONTH_QUERY_KEYS = {"month", "mes"}
    DAY_QUERY_KEYS = {"day", "dia"}

    def __init__(
        self,
        *,
        max_calendar_variants: int = 36,
        max_url_length: int = 2048,
        max_query_keys: int = 12,
    ) -> None:
        self.max_calendar_variants = max(1, max_calendar_variants)
        self.max_url_length = max(256, max_url_length)
        self.max_query_keys = max(1, max_query_keys)
        self._calendar_variants: Dict[str, Set[str]] = defaultdict(set)

    @staticmethod
    def _repeated_segment_trap(path: str) -> bool:
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 4:
            return False
        run = 1
        for prev, current in zip(segments, segments[1:]):
            if current.lower() == prev.lower():
                run += 1
                if run >= 4:
                    return True
            else:
                run = 1
        return False

    @classmethod
    def _calendar_family_and_value(cls, url: str) -> Optional[Tuple[str, str]]:
        parsed = urlparse(url)
        path_match = cls.DATE_PATH_RE.search(parsed.path or "")
        if path_match:
            date_value = "/".join(
                part
                for part in (
                    path_match.group("year"),
                    path_match.group("month"),
                    path_match.group("day"),
                )
                if part
            )
            templated_path = cls.DATE_PATH_RE.sub("/YYYY/MM/DD", parsed.path, count=1)
            family = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{templated_path}"
            return family, date_value

        query = parse_qsl(parsed.query, keep_blank_values=True)
        lower = {key.lower(): value for key, value in query}
        if not any(key in lower for key in cls.YEAR_QUERY_KEYS):
            return None
        if not any(key in lower for key in cls.MONTH_QUERY_KEYS):
            return None

        date_parts = []
        templated_query = []
        for key, value in sorted(query):
            low = key.lower()
            if low in cls.YEAR_QUERY_KEYS:
                date_parts.append(value)
                templated_query.append((key, "YYYY"))
            elif low in cls.MONTH_QUERY_KEYS:
                date_parts.append(value)
                templated_query.append((key, "MM"))
            elif low in cls.DAY_QUERY_KEYS:
                date_parts.append(value)
                templated_query.append((key, "DD"))
            else:
                templated_query.append((key, value))

        family = urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path or "/",
                "",
                urlencode(templated_query),
                "",
            )
        )
        return family, "|".join(date_parts)

    def inspect(self, url: str) -> Optional[str]:
        if len(url) > self.max_url_length:
            return "URL_TOO_LONG"

        parsed = urlparse(url)
        if len(parse_qsl(parsed.query, keep_blank_values=True)) > self.max_query_keys:
            return "TOO_MANY_QUERY_KEYS"

        if self._repeated_segment_trap(parsed.path or "/"):
            return "REPEATED_PATH_SEGMENTS"

        calendar = self._calendar_family_and_value(url)
        if calendar is not None:
            family, value = calendar
            variants = self._calendar_variants[family]
            if value not in variants and len(variants) >= self.max_calendar_variants:
                return "CALENDAR_VARIANT_LIMIT"
            variants.add(value)

        return None
