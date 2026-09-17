from __future__ import annotations

from typing import Dict, Optional, Tuple

import httpx

from prospector_externo.infrastructure.host_politeness import HostPolitenessController
from prospector_externo.infrastructure.robots_policy import RobotsFetchResult, RobotsPolicy


HttpRequestResult = Tuple[Optional[httpx.Response], Optional[int], Optional[str]]


class AsyncResilientHttpClient:
    DEFAULT_USER_AGENT = "DATAX-Prospector/1.0"
    ROBOTS_USER_AGENT = "DATAX-Prospector"

    def __init__(
        self,
        *,
        politeness_controller: HostPolitenessController,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        robots_policy: Optional[RobotsPolicy] = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout debe ser mayor que cero")

        self._politeness = politeness_controller
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=True,
        )
        self._closed = False
        self._robots_policy = robots_policy or RobotsPolicy(fetcher=self._fetch_robots_txt)

    async def _send_request(
        self,
        method: str,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
    ) -> HttpRequestResult:
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

        try:
            async with self._politeness.slot(host, min_interval_seconds=rate_limit_delay):
                if stream:
                    request = self._client.build_request(
                        normalized_method,
                        url,
                        headers=headers,
                    )
                    response = await self._client.send(request, stream=True)
                else:
                    response = await self._client.request(
                        normalized_method,
                        url,
                        headers=headers,
                    )

            status_code = response.status_code
            if response.is_error:
                if stream:
                    await response.aclose()
                return None, status_code, f"HTTP_{status_code}"

            return response, status_code, None

        except httpx.TimeoutException:
            return None, None, "TIMEOUT"
        except httpx.ConnectError:
            return None, None, "CONNECTION_ERROR"
        except httpx.RequestError:
            return None, None, "REQUEST_ERROR"

    async def _fetch_robots_txt(self, robots_url: str) -> RobotsFetchResult:
        response, status_code, error = await self._send_request(
            "GET",
            robots_url,
            headers={"Accept": "text/plain,*/*;q=0.1"},
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
    ) -> HttpRequestResult:
        if check_robots:
            decision = await self._robots_policy.check(
                url,
                user_agent=self.ROBOTS_USER_AGENT,
                ignore_robots_txt=ignore_robots_txt,
                robots_override_reason=robots_override_reason,
            )
            if not decision.allowed:
                return None, None, decision.code

        return await self._send_request(
            method,
            url,
            rate_limit_delay=rate_limit_delay,
            headers=headers,
            stream=stream,
        )

    async def fetch_html(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        response, status_code, error = await self._request(
            "GET",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
            ignore_robots_txt=ignore_robots_txt,
            robots_override_reason=robots_override_reason,
        )
        if response is None:
            return None, status_code, error
        return response.text, status_code, None

    async def fetch_headers(
        self,
        url: str,
        *,
        rate_limit_delay: Optional[float] = None,
        check_robots: bool = True,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, str]], Optional[int], Optional[str]]:
        response, status_code, error = await self._request(
            "HEAD",
            url,
            rate_limit_delay=rate_limit_delay,
            check_robots=check_robots,
            ignore_robots_txt=ignore_robots_txt,
            robots_override_reason=robots_override_reason,
        )

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
        )

        if fallback_response is None:
            return None, fallback_status, fallback_error

        try:
            return (
                self._normalize_headers(fallback_response.headers),
                fallback_status,
                None,
            )
        finally:
            await fallback_response.aclose()

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
