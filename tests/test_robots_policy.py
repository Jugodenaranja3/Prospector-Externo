import asyncio

from prospector_externo.infrastructure.robots_policy import (
    RobotsFetchResult,
    RobotsPolicy,
)


def test_robots_allows_when_path_is_allowed() -> None:
    async def scenario() -> None:
        async def fetcher(url: str) -> RobotsFetchResult:
            assert url == "https://example.test/robots.txt"

            return RobotsFetchResult(
                status_code=200,
                text=(
                    "User-agent: *\n"
                    "Disallow: /private/\n"
                    "Allow: /\n"
                ),
                error=None,
            )

        policy = RobotsPolicy(fetcher=fetcher)

        decision = await policy.check(
            "https://example.test/public/data",
            user_agent="DATAX-Prospector",
        )

        assert decision.allowed is True
        assert decision.code == "ROBOTS_ALLOWED"

    asyncio.run(scenario())


def test_robots_blocks_disallowed_path() -> None:
    async def scenario() -> None:
        async def fetcher(url: str) -> RobotsFetchResult:
            return RobotsFetchResult(
                status_code=200,
                text=(
                    "User-agent: *\n"
                    "Disallow: /private/\n"
                ),
                error=None,
            )

        policy = RobotsPolicy(fetcher=fetcher)

        decision = await policy.check(
            "https://example.test/private/report.pdf",
            user_agent="DATAX-Prospector",
        )

        assert decision.allowed is False
        assert decision.code == "ROBOTS_DISALLOWED"

    asyncio.run(scenario())


def test_robots_404_allows_access() -> None:
    async def scenario() -> None:
        async def fetcher(url: str) -> RobotsFetchResult:
            return RobotsFetchResult(
                status_code=404,
                text=None,
                error=None,
            )

        policy = RobotsPolicy(fetcher=fetcher)

        decision = await policy.check(
            "https://example.test/data",
            user_agent="DATAX-Prospector",
        )

        assert decision.allowed is True
        assert decision.code == "ROBOTS_UNAVAILABLE_ALLOW"

    asyncio.run(scenario())


def test_robots_5xx_blocks_temporarily() -> None:
    async def scenario() -> None:
        async def fetcher(url: str) -> RobotsFetchResult:
            return RobotsFetchResult(
                status_code=503,
                text=None,
                error=None,
            )

        policy = RobotsPolicy(fetcher=fetcher)

        decision = await policy.check(
            "https://example.test/data",
            user_agent="DATAX-Prospector",
        )

        assert decision.allowed is False
        assert decision.code == "ROBOTS_UNREACHABLE"

    asyncio.run(scenario())


def test_robots_network_error_blocks_temporarily() -> None:
    async def scenario() -> None:
        async def fetcher(url: str) -> RobotsFetchResult:
            return RobotsFetchResult(
                status_code=None,
                text=None,
                error="CONNECTION_ERROR",
            )

        policy = RobotsPolicy(fetcher=fetcher)

        decision = await policy.check(
            "https://example.test/data",
            user_agent="DATAX-Prospector",
        )

        assert decision.allowed is False
        assert decision.code == "ROBOTS_UNREACHABLE"

    asyncio.run(scenario())


def test_authorized_override_requires_reason_and_allows() -> None:
    async def scenario() -> None:
        calls = 0

        async def fetcher(url: str) -> RobotsFetchResult:
            nonlocal calls
            calls += 1

            return RobotsFetchResult(
                status_code=200,
                text="User-agent: *\nDisallow: /\n",
                error=None,
            )

        policy = RobotsPolicy(fetcher=fetcher)

        decision = await policy.check(
            "https://example.test/private",
            user_agent="DATAX-Prospector",
            ignore_robots_txt=True,
            robots_override_reason="Autorización formal DATAX",
        )

        assert decision.allowed is True
        assert decision.code == "ROBOTS_OVERRIDE"
        assert decision.override_reason == "Autorización formal DATAX"

        # Un override válido no necesita consultar robots.txt.
        assert calls == 0

    asyncio.run(scenario())


def test_override_without_reason_is_rejected() -> None:
    async def scenario() -> None:
        async def fetcher(url: str) -> RobotsFetchResult:
            raise AssertionError("No debería ejecutarse")

        policy = RobotsPolicy(fetcher=fetcher)

        decision = await policy.check(
            "https://example.test/data",
            user_agent="DATAX-Prospector",
            ignore_robots_txt=True,
            robots_override_reason="",
        )

        assert decision.allowed is False
        assert decision.code == "ROBOTS_OVERRIDE_REASON_REQUIRED"

    asyncio.run(scenario())


def test_robots_is_cached_per_origin() -> None:
    async def scenario() -> None:
        calls = 0

        async def fetcher(url: str) -> RobotsFetchResult:
            nonlocal calls
            calls += 1

            return RobotsFetchResult(
                status_code=200,
                text="User-agent: *\nAllow: /\n",
                error=None,
            )

        policy = RobotsPolicy(fetcher=fetcher)

        first = await policy.check(
            "https://example.test/a",
            user_agent="DATAX-Prospector",
        )

        second = await policy.check(
            "https://example.test/b",
            user_agent="DATAX-Prospector",
        )

        assert first.allowed is True
        assert second.allowed is True

        assert calls == 1

    asyncio.run(scenario())