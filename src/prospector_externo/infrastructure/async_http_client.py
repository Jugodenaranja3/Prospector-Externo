from __future__ import annotations

import contextvars
from typing import Dict, Iterable, Optional, Set, Tuple
from urllib.parse import urljoin

import httpx

from prospector_externo.infrastructure.host_politeness import HostPolitenessController
from prospector_externo.infrastructure.http_policy import (
    ConditionalRequestCache,
    HttpTimeoutConfig,
    RequestBudget,
    RetryPolicy,
)
from prospector_externo.infrastructure.robots_policy import RobotsFetchResult, RobotsPolicy

HttpRequestResult = Tuple[Optional[httpx.Response], Optional[int], Optional[str]]


class AsyncResilientHttpClient:
    DEFAULT_USER_AGENT = "DATAX-Prospector/1.0"
    ROBOTS_USER_AGENT = "DATAX-Prospector"
    REDIRECT_STATUSES = {301, 302, 303, 307, 308}
    DEFAULT_MAX_REDIRECTS = 5

    def __init__(
        self,
        *,
        politeness_controller: HostPolitenessController,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: Optional[float] = None,
        timeout_config: Optional[HttpTimeoutConfig] = None,
        retry_policy: Optional[RetryPolicy] = None,
        request_budget: Optional[RequestBudget] = None,
        conditional_cache: Optional[ConditionalRequestCache] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        robots_policy: Optional[RobotsPolicy] = None,
    ) -> None:
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout debe ser mayor que cero")

        cfg = (
            HttpTimeoutConfig(connect=timeout, read=timeout, write=timeout, pool=timeout)
            if timeout is not None
            else (timeout_config or HttpTimeoutConfig())
        )

        self._politeness = politeness_controller
        self._retry_policy = retry_policy or RetryPolicy()
        self._request_budget = request_budget
        self._conditional_cache = conditional_cache or ConditionalRequestCache()

        self._active_budget: contextvars.ContextVar[Optional[RequestBudget]] = (
            contextvars.ContextVar("prospector_active_request_budget", default=None)
        )
        self._active_rate_limit: contextvars.ContextVar[Optional[float]] = (
            contextvars.ContextVar("prospector_active_rate_limit", default=None)
        )
        self._active_allowed_redirect_hosts: contextvars.ContextVar[
            Optional[frozenset[str]]
        ] = contextvars.ContextVar(
            "prospector_active_allowed_redirect_hosts",
            default=None,
        )
        self._active_max_redirects: contextvars.ContextVar[int] = contextvars.ContextVar(
            "prospector_active_max_redirects",
            default=self.DEFAULT_MAX_REDIRECTS,
        )

        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(
                connect=cfg.connect,
                read=cfg.read,
                write=cfg.write,
                pool=cfg.pool,
            ),
            transport=transport,
            # Redirecciones manuales: cada hop debe pasar budget/politeness/robots.
            follow_redirects=False,
        )
        self._closed = False
        self._robots_policy = robots_policy or RobotsPolicy(fetcher=self._fetch_robots_txt)

    @staticmethod
    def _merge_headers(*parts: Optional[Dict[str, str]]) -> Dict[str, str]:
        merged: Dict[str, str] = {}
        for part in parts:
            if part:
                merged.update(part)
        return merged

    @staticmethod
    def _normalize_allowed_hosts(
        hosts: Optional[Iterable[str]],
    ) -> Optional[frozenset[str]]:
        if hosts is None:
            return None
        normalized = frozenset(
            host.strip().lower().rstrip(".")
            for host in hosts
            if host and host.strip()
        )
        return normalized

    @staticmethod
    def _parsed_host(url: str) -> Optional[str]:
        try:
            parsed = httpx.URL(url)
        except Exception:
            return None
        host = parsed.host
        return host.lower().rstrip(".") if host else None

    @classmethod
    def _redirect_allowed(
        cls,
        url: str,
        allowed_hosts: Optional[frozenset[str]],
    ) -> bool:
        host = cls._parsed_host(url)
        if host is None:
            return False
        if allowed_hosts is None:
            return True
        return host in allowed_hosts

    async def _consume_budget(self, budget: Optional[RequestBudget]) -> bool:
        active = budget or self._active_budget.get() or self._request_budget
        if active is None:
            return True
        return await active.consume()

    async def _perform_once(
        self,
        method: str,
        url: str,
        *,
        host: str,
        rate_limit_delay: Optional[float],
        headers: Optional[Dict[str, str]],
        stream: bool,
    ) -> httpx.Response:
        async with self._politeness.slot(host, min_interval_seconds=rate_limit_delay):
            if stream:
                request = self._client.build_request(method, url, headers=headers)
                return await self._client.send(request, stream=True)
            return await self._client.request(method, url, headers=headers)

    async def _send_single(
        self,
        method: str,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
        conditional: bool = False,
        request_budget: Optional[RequestBudget] = None,
    ) -> HttpRequestResult:
        """Envía un único URL físico. Retries consumen budget individualmente."""

        if self._closed:
            raise RuntimeError("AsyncResilientHttpClient ya fue cerrado")

        try:
            parsed_url = httpx.URL(url)
        except Exception:
            return None, None, "INVALID_URL"

        host = parsed_url.host
        if not host:
            return None, None, "INVALID_URL"

        normalized_method = method.strip().upper()
        if not normalized_method:
            return None, None, "INVALID_HTTP_METHOD"

        conditional_headers = self._conditional_cache.headers_for(url) if conditional else {}
        request_headers = self._merge_headers(conditional_headers, headers)

        retryable_statuses = {429, 500, 502, 503, 504}
        last_error: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            if not await self._consume_budget(request_budget):
                return None, None, "REQUEST_BUDGET_EXCEEDED"

            try:
                response = await self._perform_once(
                    normalized_method,
                    url,
                    host=host,
                    rate_limit_delay=rate_limit_delay,
                    headers=request_headers or None,
                    stream=stream,
                )
            except httpx.TimeoutException:
                last_error = "TIMEOUT"
                if attempt >= self._retry_policy.max_attempts:
                    return None, None, last_error
                await self._politeness.defer(
                    host, self._retry_policy.backoff_for_attempt(attempt)
                )
                continue
            except httpx.ConnectError:
                last_error = "CONNECTION_ERROR"
                if attempt >= self._retry_policy.max_attempts:
                    return None, None, last_error
                await self._politeness.defer(
                    host, self._retry_policy.backoff_for_attempt(attempt)
                )
                continue
            except httpx.RequestError:
                return None, None, "REQUEST_ERROR"

            last_status = response.status_code

            if response.status_code == 304:
                if stream:
                    await response.aclose()
                return None, 304, None

            if response.status_code in retryable_statuses:
                if stream:
                    await response.aclose()
                if attempt >= self._retry_policy.max_attempts:
                    return None, response.status_code, f"HTTP_{response.status_code}"
                retry_after = self._retry_policy.parse_retry_after(
                    response.headers.get("Retry-After")
                )
                delay = (
                    retry_after
                    if retry_after is not None
                    else self._retry_policy.backoff_for_attempt(attempt)
                )
                await self._politeness.defer(host, delay)
                continue

            if response.is_error:
                if stream:
                    await response.aclose()
                return None, response.status_code, f"HTTP_{response.status_code}"

            if conditional:
                self._conditional_cache.observe(url, dict(response.headers))

            return response, response.status_code, None

        return None, last_status, last_error or "REQUEST_ERROR"

    async def _send_request(
        self,
        method: str,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
        conditional: bool = False,
        request_budget: Optional[RequestBudget] = None,
        allowed_redirect_hosts: Optional[Iterable[str]] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
    ) -> HttpRequestResult:
        """Ejecuta redirects manuales y auditables.

        Cada hop:
        - consume request budget;
        - pasa por HostPolitenessController;
        - vuelve a consultar robots para el destino (salvo fetch interno de robots);
        - respeta la lista de hosts permitidos.
        """

        if max_redirects < 0:
            raise ValueError("max_redirects no puede ser negativo")

        allowed_hosts = self._normalize_allowed_hosts(allowed_redirect_hosts)
        current_url = url
        current_method = method.strip().upper()
        visited_urls: Set[str] = set()
        redirects_followed = 0

        while True:
            if current_url in visited_urls:
                return None, None, "REDIRECT_LOOP"
            visited_urls.add(current_url)

            if not self._redirect_allowed(current_url, allowed_hosts):
                return None, None, "REDIRECT_OUT_OF_SCOPE"

            if check_robots:
                decision = await self._robots_policy.check(
                    current_url,
                    user_agent=self.ROBOTS_USER_AGENT,
                    ignore_robots_txt=ignore_robots_txt,
                    robots_override_reason=robots_override_reason,
                )
                if not decision.allowed:
                    return None, None, decision.code

            response, status_code, error = await self._send_single(
                current_method,
                current_url,
                rate_limit_delay=rate_limit_delay,
                headers=headers,
                stream=stream,
                conditional=conditional,
                request_budget=request_budget,
            )
            if response is None:
                return None, status_code, error

            if response.status_code not in self.REDIRECT_STATUSES:
                return response, status_code, None

            location = response.headers.get("Location")
            if not location:
                if stream:
                    await response.aclose()
                return None, response.status_code, "REDIRECT_LOCATION_MISSING"

            if redirects_followed >= max_redirects:
                if stream:
                    await response.aclose()
                return None, response.status_code, "TOO_MANY_REDIRECTS"

            target = urljoin(current_url, location)
            try:
                parsed_target = httpx.URL(target)
            except Exception:
                if stream:
                    await response.aclose()
                return None, response.status_code, "INVALID_REDIRECT_URL"

            if parsed_target.scheme not in {"http", "https"} or not parsed_target.host:
                if stream:
                    await response.aclose()
                return None, response.status_code, "INVALID_REDIRECT_URL"

            if not self._redirect_allowed(target, allowed_hosts):
                if stream:
                    await response.aclose()
                return None, response.status_code, "REDIRECT_OUT_OF_SCOPE"

            # Liberar siempre la conexión antes de seguir al siguiente hop.
            await response.aclose()

            if response.status_code == 303 and current_method != "HEAD":
                current_method = "GET"

            current_url = str(parsed_target)
            redirects_followed += 1

    async def _fetch_robots_txt(self, robots_url: str) -> RobotsFetchResult:
        response, status_code, error = await self._send_request(
            "GET",
            robots_url,
            rate_limit_delay=self._active_rate_limit.get(),
            check_robots=False,
            headers={"Accept": "text/plain,*/*;q=0.1"},
            conditional=False,
            request_budget=self._active_budget.get(),
            allowed_redirect_hosts=self._active_allowed_redirect_hosts.get(),
            max_redirects=self._active_max_redirects.get(),
        )
        if response is None:
            return RobotsFetchResult(status_code, None, error)
        return RobotsFetchResult(status_code, response.text, None)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
        conditional: bool = False,
        request_budget: Optional[RequestBudget] = None,
        allowed_redirect_hosts: Optional[Iterable[str]] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
    ) -> HttpRequestResult:
        normalized_hosts = self._normalize_allowed_hosts(allowed_redirect_hosts)
        budget_token = self._active_budget.set(request_budget or self._request_budget)
        rate_token = self._active_rate_limit.set(rate_limit_delay)
        hosts_token = self._active_allowed_redirect_hosts.set(normalized_hosts)
        redirects_token = self._active_max_redirects.set(max_redirects)
        try:
            return await self._send_request(
                method,
                url,
                rate_limit_delay=rate_limit_delay,
                check_robots=check_robots,
                ignore_robots_txt=ignore_robots_txt,
                robots_override_reason=robots_override_reason,
                headers=headers,
                stream=stream,
                conditional=conditional,
                request_budget=request_budget,
                allowed_redirect_hosts=normalized_hosts,
                max_redirects=max_redirects,
            )
        finally:
            self._active_max_redirects.reset(redirects_token)
            self._active_allowed_redirect_hosts.reset(hosts_token)
            self._active_rate_limit.reset(rate_token)
            self._active_budget.reset(budget_token)

    async def fetch_document(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
        conditional: bool = True,
        request_budget: Optional[RequestBudget] = None,
        allowed_redirect_hosts: Optional[Iterable[str]] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        accept: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[int], Optional[str], Dict[str, str]]:
        headers = {"Accept": accept} if accept else None
        response, status_code, error = await self._request(
            "GET",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
            ignore_robots_txt=ignore_robots_txt,
            robots_override_reason=robots_override_reason,
            headers=headers,
            conditional=conditional,
            request_budget=request_budget,
            allowed_redirect_hosts=allowed_redirect_hosts,
            max_redirects=max_redirects,
        )
        if response is None:
            return None, status_code, error, {}
        return response.text, status_code, None, self._normalize_headers(response.headers)

    async def fetch_html(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
        conditional: bool = True,
        request_budget: Optional[RequestBudget] = None,
        allowed_redirect_hosts: Optional[Iterable[str]] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        text, status_code, error, _headers = await self.fetch_document(
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
            ignore_robots_txt=ignore_robots_txt,
            robots_override_reason=robots_override_reason,
            conditional=conditional,
            request_budget=request_budget,
            allowed_redirect_hosts=allowed_redirect_hosts,
            max_redirects=max_redirects,
        )
        return text, status_code, error

    async def fetch_headers(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
        conditional: bool = True,
        request_budget: Optional[RequestBudget] = None,
        allowed_redirect_hosts: Optional[Iterable[str]] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
    ) -> Tuple[Optional[Dict[str, str]], Optional[int], Optional[str]]:
        response, status_code, error = await self._request(
            "HEAD",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
            ignore_robots_txt=ignore_robots_txt,
            robots_override_reason=robots_override_reason,
            conditional=conditional,
            request_budget=request_budget,
            allowed_redirect_hosts=allowed_redirect_hosts,
            max_redirects=max_redirects,
        )

        if status_code == 304:
            return {}, 304, None
        if response is not None:
            return self._normalize_headers(response.headers), status_code, None
        if status_code not in {405, 501}:
            return None, status_code, error

        fallback_response, fallback_status, fallback_error = await self._request(
            "GET",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
            ignore_robots_txt=ignore_robots_txt,
            robots_override_reason=robots_override_reason,
            headers={"Range": "bytes=0-0"},
            stream=True,
            conditional=conditional,
            request_budget=request_budget,
            allowed_redirect_hosts=allowed_redirect_hosts,
            max_redirects=max_redirects,
        )

        if fallback_status == 304:
            return {}, 304, None
        if fallback_response is None:
            return None, fallback_status, fallback_error
        try:
            return self._normalize_headers(fallback_response.headers), fallback_status, None
        finally:
            await fallback_response.aclose()

    async def robots_sitemaps(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
        request_budget: Optional[RequestBudget] = None,
        allowed_redirect_hosts: Optional[Iterable[str]] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
    ):
        normalized_hosts = self._normalize_allowed_hosts(allowed_redirect_hosts)
        budget_token = self._active_budget.set(request_budget or self._request_budget)
        rate_token = self._active_rate_limit.set(rate_limit_delay)
        hosts_token = self._active_allowed_redirect_hosts.set(normalized_hosts)
        redirects_token = self._active_max_redirects.set(max_redirects)
        try:
            return await self._robots_policy.sitemap_urls_for(
                url,
                user_agent=self.ROBOTS_USER_AGENT,
                ignore_robots_txt=ignore_robots_txt,
                robots_override_reason=robots_override_reason,
            )
        finally:
            self._active_max_redirects.reset(redirects_token)
            self._active_allowed_redirect_hosts.reset(hosts_token)
            self._active_rate_limit.reset(rate_token)
            self._active_budget.reset(budget_token)

    async def fetch_bytes_limited(
        self,
        url: str,
        *,
        max_bytes: int,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
        request_budget: Optional[RequestBudget] = None,
        allowed_redirect_hosts: Optional[Iterable[str]] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        accept: Optional[str] = None,
    ) -> Tuple[Optional[bytes], Optional[int], Optional[str], Dict[str, str]]:
        if max_bytes <= 0:
            raise ValueError("max_bytes debe ser mayor que cero")

        response, status_code, error = await self._request(
            "GET",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
            ignore_robots_txt=ignore_robots_txt,
            robots_override_reason=robots_override_reason,
            headers={"Accept": accept} if accept else None,
            stream=True,
            conditional=False,
            request_budget=request_budget,
            allowed_redirect_hosts=allowed_redirect_hosts,
            max_redirects=max_redirects,
        )
        if response is None:
            return None, status_code, error, {}

        headers = self._normalize_headers(response.headers)
        try:
            content_length = headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        return None, status_code, "RESPONSE_TOO_LARGE", headers
                except ValueError:
                    pass

            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    return None, status_code, "RESPONSE_TOO_LARGE", headers
            return bytes(data), status_code, None, headers
        finally:
            await response.aclose()

    @staticmethod
    def _normalize_headers(headers: httpx.Headers) -> Dict[str, str]:
        return {key.lower(): value for key, value in headers.items()}

    async def aclose(self) -> None:
        if self._closed:
            return
        await self._client.aclose()
        self._closed = True

    async def __aenter__(self) -> "AsyncResilientHttpClient":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.aclose()
