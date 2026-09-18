"""Frontera de discovery acotada, determinista y consciente de source_id."""

from __future__ import annotations

import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, Iterable, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlparse

from prospector_externo.domain.discovery_intelligence import SpiderTrapDetector
from prospector_externo.domain.models import SourceConfig
from prospector_externo.domain.normalizer import UrlNormalizer


class StopReason(str, Enum):
    QUEUE_EXHAUSTED = "QUEUE_EXHAUSTED"
    MAX_URLS = "MAX_URLS"
    MAX_RUNTIME = "MAX_RUNTIME"
    REQUEST_BUDGET = "REQUEST_BUDGET_REACHED"
    MAX_CONSECUTIVE_ERRORS = "MAX_CONSECUTIVE_ERRORS"


@dataclass(frozen=True)
class DiscoveryItem:
    normalized_url: str
    raw_url: str
    depth: int
    parent_url: Optional[str] = None


class DiscoveryFrontier:
    """Cola BFS con canonicalización, scope, dedupe y límites por fuente."""

    def __init__(self, config: SourceConfig, monotonic=time.monotonic) -> None:
        self.config = config
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._queue: Deque[DiscoveryItem] = deque()
        self._seen: Set[str] = set()
        self._visited: Set[str] = set()
        self._rejected = 0
        self._rejection_reasons: Counter[str] = Counter()
        self._query_variants: Dict[Tuple[str, str, Tuple[str, ...]], Set[str]] = defaultdict(set)
        self._spider_detector = SpiderTrapDetector(
            max_calendar_variants=config.max_calendar_variants,
            max_url_length=config.max_url_length,
            max_query_keys=config.max_query_keys,
        )
        self.stop_reason: Optional[StopReason] = None

        hosts = set()
        for candidate in [config.entrypoint, *config.seeds]:
            host = (urlparse(candidate).hostname or "").lower().rstrip(".")
            if host:
                hosts.add(host)
        for host in config.allowed_hosts:
            clean = host.strip().lower().rstrip(".")
            if clean:
                hosts.add(clean)
        self._allowed_hosts = hosts

    @property
    def rejected_count(self) -> int:
        return self._rejected

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def discovered_count(self) -> int:
        return len(self._seen)

    @property
    def visited_count(self) -> int:
        return len(self._visited)

    @property
    def spider_traps_blocked(self) -> int:
        return sum(
            count
            for reason, count in self._rejection_reasons.items()
            if reason.startswith("SPIDER_TRAP:")
        )

    @property
    def query_variants_blocked(self) -> int:
        return self._rejection_reasons["QUERY_VARIANT_LIMIT"]

    def rejection_count(self, reason: str) -> int:
        return self._rejection_reasons[reason]

    def _runtime_exceeded(self) -> bool:
        return (self._monotonic() - self._started_at) >= self.config.max_runtime_seconds

    def reject(self, reason: str) -> bool:
        self._rejected += 1
        self._rejection_reasons[reason] += 1
        return False

    def _query_family(self, parsed) -> Tuple[str, str, Tuple[str, ...]]:
        query = parse_qsl(parsed.query, keep_blank_values=True)
        keys = tuple(sorted({key.lower() for key, _ in query}))
        return (
            parsed.hostname.lower().rstrip("."),
            parsed.path or "/",
            keys,
        )

    def enqueue(
        self,
        raw_url: str,
        *,
        base_url: Optional[str] = None,
        depth: int = 0,
        parent_url: Optional[str] = None,
    ) -> bool:
        if depth > self.config.max_depth:
            return self.reject("MAX_DEPTH")

        if self._runtime_exceeded():
            self.stop_reason = StopReason.MAX_RUNTIME
            return self.reject("MAX_RUNTIME")

        normalized = UrlNormalizer.normalize(raw_url, base_url=base_url)
        parsed = urlparse(normalized)

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return self.reject("INVALID_OR_UNSUPPORTED_URL")

        host = parsed.hostname.lower().rstrip(".")
        if self._allowed_hosts and host not in self._allowed_hosts:
            return self.reject("OUT_OF_SCOPE_HOST")

        trap_reason = self._spider_detector.inspect(normalized)
        if trap_reason:
            return self.reject(f"SPIDER_TRAP:{trap_reason}")

        identity = f"{self.config.source_id}|{normalized}"
        if identity in self._seen:
            return False

        if len(self._seen) >= self.config.max_urls:
            self.stop_reason = StopReason.MAX_URLS
            return self.reject("MAX_URLS")

        query_value = parsed.query or ""
        family = self._query_family(parsed)
        variants = self._query_variants[family]
        if query_value not in variants and len(variants) >= self.config.max_query_variants:
            return self.reject("QUERY_VARIANT_LIMIT")
        variants.add(query_value)

        self._seen.add(identity)
        self._queue.append(
            DiscoveryItem(
                normalized_url=normalized,
                raw_url=raw_url,
                depth=depth,
                parent_url=parent_url,
            )
        )
        return True

    def register_resource(self, normalized_url: str) -> bool:
        """Cuenta una URL de recurso en max_urls sin encolarla para navegación."""
        identity = f"{self.config.source_id}|{normalized_url}"
        if identity in self._seen:
            return False
        if len(self._seen) >= self.config.max_urls:
            self.stop_reason = StopReason.MAX_URLS
            return self.reject("MAX_URLS")
        self._seen.add(identity)
        return True

    def seed(self, urls: Iterable[str]) -> None:
        for url in urls:
            self.enqueue(url, depth=0, parent_url=None)

    def pop(self) -> Optional[DiscoveryItem]:
        if self._runtime_exceeded():
            self.stop_reason = StopReason.MAX_RUNTIME
            return None
        if not self._queue:
            if self.stop_reason is None:
                self.stop_reason = StopReason.QUEUE_EXHAUSTED
            return None
        return self._queue.popleft()

    def mark_visited(self, item: DiscoveryItem) -> None:
        self._visited.add(f"{self.config.source_id}|{item.normalized_url}")
