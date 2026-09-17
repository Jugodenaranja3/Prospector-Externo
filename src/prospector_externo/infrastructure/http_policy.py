
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Dict, Optional

@dataclass(frozen=True)
class HttpTimeoutConfig:
    connect: float = 5.0
    read: float = 15.0
    write: float = 15.0
    pool: float = 5.0

    def __post_init__(self) -> None:
        for name, value in (
            ("connect", self.connect),
            ("read", self.read),
            ("write", self.write),
            ("pool", self.pool),
        ):
            if value <= 0:
                raise ValueError(f"{name} timeout debe ser mayor que cero")

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    max_retry_after_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts debe ser mayor o igual a 1")
        if self.base_backoff_seconds < 0:
            raise ValueError("base_backoff_seconds no puede ser negativo")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds no puede ser negativo")

    def backoff_for_attempt(self, attempt_number: int) -> float:
        if attempt_number < 1:
            raise ValueError("attempt_number debe ser mayor o igual a 1")
        return self.base_backoff_seconds * (2 ** (attempt_number - 1))

    def parse_retry_after(self, value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        raw = value.strip()
        if not raw:
            return None

        try:
            seconds = float(raw)
            if seconds < 0:
                return None
            return min(seconds, self.max_retry_after_seconds)
        except ValueError:
            pass

        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            seconds = max(0.0, (target - now).total_seconds())
            return min(seconds, self.max_retry_after_seconds)
        except (TypeError, ValueError, OverflowError):
            return None

class RequestBudget:
    def __init__(self, max_requests: int) -> None:
        if max_requests < 1:
            raise ValueError("max_requests debe ser mayor o igual a 1")
        self._max_requests = max_requests
        self._used = 0
        self._lock = asyncio.Lock()

    @property
    def max_requests(self) -> int:
        return self._max_requests

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._max_requests - self._used)

    async def consume(self) -> bool:
        async with self._lock:
            if self._used >= self._max_requests:
                return False
            self._used += 1
            return True

@dataclass(frozen=True)
class ConditionalMetadata:
    etag: Optional[str] = None
    last_modified: Optional[str] = None

class ConditionalRequestCache:
    def __init__(self) -> None:
        self._items: Dict[str, ConditionalMetadata] = {}

    def headers_for(self, url: str) -> Dict[str, str]:
        metadata = self._items.get(url)
        if metadata is None:
            return {}
        headers: Dict[str, str] = {}
        if metadata.etag:
            headers["If-None-Match"] = metadata.etag
        if metadata.last_modified:
            headers["If-Modified-Since"] = metadata.last_modified
        return headers

    def observe(self, url: str, headers: Dict[str, str]) -> None:
        normalized = {k.lower(): v for k, v in headers.items()}
        etag = normalized.get("etag")
        last_modified = normalized.get("last-modified")
        if etag is None and last_modified is None:
            return
        self._items[url] = ConditionalMetadata(
            etag=etag,
            last_modified=last_modified,
        )

    def get(self, url: str) -> Optional[ConditionalMetadata]:
        return self._items.get(url)
