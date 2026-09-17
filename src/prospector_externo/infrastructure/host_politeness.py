"""
Control global de cortesía HTTP por host.

Este componente centraliza dos reglas transversales:

1. Limitar la concurrencia máxima contra un mismo host.
2. Garantizar un intervalo mínimo entre inicios de requests al mismo host.

No realiza HTTP, no interpreta robots.txt y no implementa retries.
Esas responsabilidades pertenecen a capas posteriores.

El reloj y la función de espera son inyectables para permitir pruebas
deterministas sin introducir sleeps reales.
"""

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
    """Vista inmutable del estado observable de un host."""

    host: str
    requests_started: int
    last_request_started_at: Optional[float]
    concurrency_limit: int
    min_interval_seconds: float


@dataclass
class _HostState:
    """Estado interno compartido de un host."""

    semaphore: asyncio.Semaphore
    timing_lock: asyncio.Lock
    concurrency_limit: int
    default_min_interval_seconds: float
    requests_started: int = 0
    last_request_started_at: Optional[float] = None


class HostPolitenessController:
    """
    Coordina cortesía HTTP de forma compartida durante una corrida.

    Todas las solicitudes dirigidas al mismo host deben utilizar la misma
    instancia de este controlador para evitar que múltiples workflows
    mantengan rate limits independientes.
    """

    def __init__(
        self,
        default_concurrency_per_host: int = 1,
        default_min_interval_seconds: float = 1.0,
        monotonic: MonotonicClock = time.monotonic,
        sleeper: AsyncSleeper = asyncio.sleep,
    ) -> None:
        if default_concurrency_per_host < 1:
            raise ValueError(
                "default_concurrency_per_host debe ser mayor o igual a 1"
            )

        if default_min_interval_seconds < 0:
            raise ValueError(
                "default_min_interval_seconds no puede ser negativo"
            )

        self._default_concurrency_per_host = default_concurrency_per_host
        self._default_min_interval_seconds = default_min_interval_seconds
        self._monotonic = monotonic
        self._sleeper = sleeper

        self._states: Dict[str, _HostState] = {}

    @staticmethod
    def _normalize_host(host: str) -> str:
        """
        Normaliza el identificador del host utilizado como clave interna.
        """

        normalized = host.strip().lower().rstrip(".")

        if not normalized:
            raise ValueError("host no puede estar vacío")

        return normalized

    def _get_or_create_state(self, host: str) -> _HostState:
        normalized_host = self._normalize_host(host)

        state = self._states.get(normalized_host)

        if state is None:
            state = _HostState(
                semaphore=asyncio.Semaphore(
                    self._default_concurrency_per_host
                ),
                timing_lock=asyncio.Lock(),
                concurrency_limit=self._default_concurrency_per_host,
                default_min_interval_seconds=(
                    self._default_min_interval_seconds
                ),
            )
            self._states[normalized_host] = state

        return state

    @asynccontextmanager
    async def slot(
        self,
        host: str,
        min_interval_seconds: Optional[float] = None,
    ) -> AsyncIterator[None]:
        """
        Reserva permiso para iniciar una operación HTTP contra ``host``.

        El contexto:

        - respeta la concurrencia máxima del host;
        - serializa el cálculo del próximo instante permitido;
        - espera únicamente cuando es necesario;
        - registra el momento de inicio de la operación.

        El slot permanece ocupado mientras el caller permanece dentro
        del ``async with``.
        """

        normalized_host = self._normalize_host(host)
        state = self._get_or_create_state(normalized_host)

        interval = (
            state.default_min_interval_seconds
            if min_interval_seconds is None
            else min_interval_seconds
        )

        if interval < 0:
            raise ValueError(
                "min_interval_seconds no puede ser negativo"
            )

        await state.semaphore.acquire()

        try:
            async with state.timing_lock:
                now = self._monotonic()

                if state.last_request_started_at is not None:
                    elapsed = now - state.last_request_started_at
                    remaining = interval - elapsed

                    if remaining > 0:
                        await self._sleeper(remaining)
                        now = self._monotonic()

                state.last_request_started_at = now
                state.requests_started += 1

            yield

        finally:
            state.semaphore.release()

    def snapshot(self, host: str) -> HostPolitenessSnapshot:
        """
        Devuelve métricas actuales del host sin modificar su estado.

        Si todavía no existe estado para ese host, devuelve una vista
        inicial con cero solicitudes.
        """

        normalized_host = self._normalize_host(host)
        state = self._states.get(normalized_host)

        if state is None:
            return HostPolitenessSnapshot(
                host=normalized_host,
                requests_started=0,
                last_request_started_at=None,
                concurrency_limit=self._default_concurrency_per_host,
                min_interval_seconds=self._default_min_interval_seconds,
            )

        return HostPolitenessSnapshot(
            host=normalized_host,
            requests_started=state.requests_started,
            last_request_started_at=state.last_request_started_at,
            concurrency_limit=state.concurrency_limit,
            min_interval_seconds=state.default_min_interval_seconds,
        )