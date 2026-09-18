from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Dict, Optional

MonotonicClock = Callable[[], float]
AsyncSleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class HostPolitenessSnapshot:
    host: str
    requests_started: int
    last_request_started_at: Optional[float]
    concurrency_limit: int
    min_interval_seconds: float
    not_before_at: float


@dataclass
class _HostState:
    semaphore: asyncio.Semaphore
    timing_lock: asyncio.Lock
    concurrency_limit: int
    default_min_interval_seconds: float
    requests_started: int = 0
    last_request_started_at: Optional[float] = None
    not_before_at: float = 0.0


class HostPolitenessController:
    def __init__(
        self,
        default_concurrency_per_host: int = 1,
        default_min_interval_seconds: float = 1.0,
        monotonic: MonotonicClock = time.monotonic,
        sleeper: AsyncSleeper = asyncio.sleep,
    ) -> None:
        if default_concurrency_per_host < 1:
            raise ValueError("default_concurrency_per_host debe ser mayor o igual a 1")
        if default_min_interval_seconds < 0:
            raise ValueError("default_min_interval_seconds no puede ser negativo")
        self._default_concurrency_per_host = default_concurrency_per_host
        self._default_min_interval_seconds = default_min_interval_seconds
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._states: Dict[str, _HostState] = {}

    @staticmethod
    def _normalize_host(host: str) -> str:
        normalized = host.strip().lower().rstrip(".")
        if not normalized:
            raise ValueError("host no puede estar vacío")
        return normalized

    def _get_or_create_state(self, host: str) -> _HostState:
        normalized_host = self._normalize_host(host)
        state = self._states.get(normalized_host)
        if state is None:
            state = _HostState(
                semaphore=asyncio.Semaphore(self._default_concurrency_per_host),
                timing_lock=asyncio.Lock(),
                concurrency_limit=self._default_concurrency_per_host,
                default_min_interval_seconds=self._default_min_interval_seconds,
            )
            self._states[normalized_host] = state
        return state

    async def defer(self, host: str, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds no puede ser negativo")
        state = self._get_or_create_state(host)
        async with state.timing_lock:
            state.not_before_at = max(state.not_before_at, self._monotonic() + seconds)

    @asynccontextmanager
    async def slot(
        self,
        host: str,
        min_interval_seconds: Optional[float] = None,
    ) -> AsyncIterator[None]:
        normalized_host = self._normalize_host(host)
        state = self._get_or_create_state(normalized_host)
        requested_interval = (
            state.default_min_interval_seconds
            if min_interval_seconds is None
            else min_interval_seconds
        )
        if requested_interval < 0:
            raise ValueError("min_interval_seconds no puede ser negativo")

        # Un source_id del mismo host nunca puede relajar una política más conservadora ya registrada.
        state.default_min_interval_seconds = max(
            state.default_min_interval_seconds,
            requested_interval,
        )

        await state.semaphore.acquire()
        try:
            async with state.timing_lock:
                now = self._monotonic()
                interval_remaining = 0.0
                if state.last_request_started_at is not None:
                    elapsed = now - state.last_request_started_at
                    interval_remaining = max(
                        0.0,
                        state.default_min_interval_seconds - elapsed,
                    )
                defer_remaining = max(0.0, state.not_before_at - now)
                remaining = max(interval_remaining, defer_remaining)
                if remaining > 0:
                    await self._sleeper(remaining)
                    now = self._monotonic()
                state.last_request_started_at = now
                state.requests_started += 1
            yield
        finally:
            state.semaphore.release()

    def snapshot(self, host: str) -> HostPolitenessSnapshot:
        normalized_host = self._normalize_host(host)
        state = self._states.get(normalized_host)
        if state is None:
            return HostPolitenessSnapshot(
                host=normalized_host,
                requests_started=0,
                last_request_started_at=None,
                concurrency_limit=self._default_concurrency_per_host,
                min_interval_seconds=self._default_min_interval_seconds,
                not_before_at=0.0,
            )
        return HostPolitenessSnapshot(
            host=normalized_host,
            requests_started=state.requests_started,
            last_request_started_at=state.last_request_started_at,
            concurrency_limit=state.concurrency_limit,
            min_interval_seconds=state.default_min_interval_seconds,
            not_before_at=state.not_before_at,
        )
