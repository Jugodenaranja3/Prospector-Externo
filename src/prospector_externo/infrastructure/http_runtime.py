"""Runtime HTTP compartido por corrida y sesiones acotadas por source_id."""

from __future__ import annotations

from typing import Optional
import httpx

from prospector_externo.domain.models import SourceConfig
from prospector_externo.infrastructure.async_http_client import AsyncResilientHttpClient
from prospector_externo.infrastructure.host_politeness import HostPolitenessController
from prospector_externo.infrastructure.http_policy import (
    ConditionalRequestCache,
    HttpTimeoutConfig,
    RequestBudget,
    RetryPolicy,
)


class SourceHttpSession:
    """Vista por fuente sobre un cliente compartido, con budget independiente."""

    def __init__(
        self,
        *,
        config: SourceConfig,
        client: AsyncResilientHttpClient,
    ) -> None:
        self.config = config
        self.client = client
        self.budget = RequestBudget(config.max_requests)

    @property
    def requests_used(self) -> int:
        return self.budget.used

    async def fetch_html(self, url: str, *, conditional: bool = False):
        return await self.client.fetch_html(
            url,
            rate_limit_delay=self.config.rate_limit_seconds,
            ignore_robots_txt=self.config.ignore_robots_txt,
            robots_override_reason=self.config.robots_override_reason,
            conditional=conditional,
            request_budget=self.budget,
        )

    async def fetch_headers(self, url: str, *, conditional: bool = True):
        return await self.client.fetch_headers(
            url,
            rate_limit_delay=self.config.rate_limit_seconds,
            ignore_robots_txt=self.config.ignore_robots_txt,
            robots_override_reason=self.config.robots_override_reason,
            conditional=conditional,
            request_budget=self.budget,
        )


class AsyncHttpRuntime:
    """Infraestructura HTTP única por corrida: pool, politeness, robots y cache compartidos."""

    def __init__(
        self,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        default_concurrency_per_host: int = 1,
        default_min_interval_seconds: float = 0.0,
        timeout_config: Optional[HttpTimeoutConfig] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> None:
        self.politeness = HostPolitenessController(
            default_concurrency_per_host=default_concurrency_per_host,
            default_min_interval_seconds=default_min_interval_seconds,
        )
        self.conditional_cache = ConditionalRequestCache()
        self.client = AsyncResilientHttpClient(
            politeness_controller=self.politeness,
            timeout_config=timeout_config or HttpTimeoutConfig(),
            retry_policy=retry_policy or RetryPolicy(jitter_ratio=0.2),
            conditional_cache=self.conditional_cache,
            transport=transport,
        )

    def session_for(self, config: SourceConfig) -> SourceHttpSession:
        return SourceHttpSession(config=config, client=self.client)

    async def aclose(self) -> None:
        await self.client.aclose()
