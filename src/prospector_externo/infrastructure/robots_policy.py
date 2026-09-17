from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


@dataclass(frozen=True)
class RobotsFetchResult:
    status_code: Optional[int]
    text: Optional[str]
    error: Optional[str]


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    code: str
    robots_url: Optional[str] = None
    override_reason: Optional[str] = None
    from_cache: bool = False


@dataclass
class _CachedRobots:
    kind: str
    robots_url: str
    parser: Optional[RobotFileParser] = None


RobotsFetcher = Callable[[str], Awaitable[RobotsFetchResult]]


class RobotsPolicy:
    """Política robots.txt compartida y cacheada por origen."""

    def __init__(self, *, fetcher: RobotsFetcher) -> None:
        self._fetcher = fetcher
        self._cache: Dict[str, _CachedRobots] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _origin_and_robots_url(url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL HTTP/HTTPS inválida")
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        return origin, f"{origin}/robots.txt"

    async def check(
        self,
        url: str,
        *,
        user_agent: str,
        ignore_robots_txt: bool = False,
        robots_override_reason: Optional[str] = None,
    ) -> RobotsDecision:
        try:
            origin, robots_url = self._origin_and_robots_url(url)
        except ValueError:
            return RobotsDecision(False, "INVALID_URL")

        if ignore_robots_txt:
            reason = (robots_override_reason or "").strip()
            if not reason:
                return RobotsDecision(
                    False,
                    "ROBOTS_OVERRIDE_REASON_REQUIRED",
                    robots_url=robots_url,
                )
            return RobotsDecision(
                True,
                "ROBOTS_OVERRIDE",
                robots_url=robots_url,
                override_reason=reason,
            )

        cached = self._cache.get(origin)
        if cached is not None:
            return self._decision(cached, url=url, user_agent=user_agent, from_cache=True)

        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            cached = self._cache.get(origin)
            if cached is not None:
                return self._decision(cached, url=url, user_agent=user_agent, from_cache=True)

            result = await self._fetcher(robots_url)
            cached = self._classify(result, robots_url=robots_url)
            self._cache[origin] = cached
            return self._decision(cached, url=url, user_agent=user_agent, from_cache=False)

    @staticmethod
    def _classify(result: RobotsFetchResult, *, robots_url: str) -> _CachedRobots:
        status = result.status_code

        if status is None:
            return _CachedRobots("unreachable", robots_url)

        if 200 <= status < 300:
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse((result.text or "").splitlines())
            return _CachedRobots("rules", robots_url, parser)

        if status == 429:
            return _CachedRobots("unreachable", robots_url)

        if 400 <= status < 500:
            return _CachedRobots("unavailable", robots_url)

        return _CachedRobots("unreachable", robots_url)

    @staticmethod
    def _decision(
        cached: _CachedRobots,
        *,
        url: str,
        user_agent: str,
        from_cache: bool,
    ) -> RobotsDecision:
        if cached.kind == "unavailable":
            return RobotsDecision(
                True,
                "ROBOTS_UNAVAILABLE_ALLOW",
                robots_url=cached.robots_url,
                from_cache=from_cache,
            )

        if cached.kind == "unreachable":
            return RobotsDecision(
                False,
                "ROBOTS_UNREACHABLE",
                robots_url=cached.robots_url,
                from_cache=from_cache,
            )

        assert cached.parser is not None
        allowed = cached.parser.can_fetch(user_agent, url)
        return RobotsDecision(
            allowed,
            "ROBOTS_ALLOWED" if allowed else "ROBOTS_DISALLOWED",
            robots_url=cached.robots_url,
            from_cache=from_cache,
        )
