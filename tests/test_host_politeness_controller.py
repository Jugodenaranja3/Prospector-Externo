import asyncio

from prospector_externo.infrastructure.host_politeness import (
    HostPolitenessController,
)


class FakeClock:
    """Reloj determinista para probar delays sin esperar tiempo real."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        # Ceder control al event loop sin introducir espera real.
        await asyncio.sleep(0)


def test_same_host_respects_minimum_interval() -> None:
    async def scenario() -> None:
        clock = FakeClock()

        controller = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=1.0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        async with controller.slot("datos.gob.bo"):
            pass

        async with controller.slot("datos.gob.bo"):
            pass

        assert clock.sleeps == [1.0]

        snapshot = controller.snapshot("datos.gob.bo")
        assert snapshot.requests_started == 2

    asyncio.run(scenario())


def test_different_hosts_do_not_share_delay() -> None:
    async def scenario() -> None:
        clock = FakeClock()

        controller = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=1.0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        async with controller.slot("bcb.gob.bo"):
            pass

        async with controller.slot("asfi.gob.bo"):
            pass

        assert clock.sleeps == []

        assert controller.snapshot("bcb.gob.bo").requests_started == 1
        assert controller.snapshot("asfi.gob.bo").requests_started == 1

    asyncio.run(scenario())


def test_host_names_are_normalized() -> None:
    async def scenario() -> None:
        clock = FakeClock()

        controller = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        async with controller.slot("BCB.GOB.BO"):
            pass

        assert controller.snapshot("bcb.gob.bo").requests_started == 1
        assert controller.snapshot("BCB.GOB.BO").requests_started == 1

    asyncio.run(scenario())


def test_custom_interval_can_be_stricter_than_default() -> None:
    async def scenario() -> None:
        clock = FakeClock()

        controller = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.5,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        async with controller.slot(
            "ejemplo.gob.bo",
            min_interval_seconds=2.0,
        ):
            pass

        async with controller.slot(
            "ejemplo.gob.bo",
            min_interval_seconds=2.0,
        ):
            pass

        assert clock.sleeps == [2.0]

    asyncio.run(scenario())


def test_same_host_respects_concurrency_limit() -> None:
    async def scenario() -> None:
        controller = HostPolitenessController(
            default_concurrency_per_host=1,
            default_min_interval_seconds=0.0,
        )

        active = 0
        max_active = 0

        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def first_request() -> None:
            nonlocal active, max_active

            async with controller.slot("bcb.gob.bo"):
                active += 1
                max_active = max(max_active, active)
                first_entered.set()

                await release_first.wait()

                active -= 1

        async def second_request() -> None:
            nonlocal active, max_active

            await first_entered.wait()

            async with controller.slot("bcb.gob.bo"):
                active += 1
                max_active = max(max_active, active)
                active -= 1

        task_1 = asyncio.create_task(first_request())
        task_2 = asyncio.create_task(second_request())

        await first_entered.wait()

        # Permitimos que la segunda tarea intente entrar al mismo host.
        await asyncio.sleep(0)

        # Con límite 1, la segunda todavía no debe haber entrado.
        assert max_active == 1

        release_first.set()

        await asyncio.gather(task_1, task_2)

        assert max_active == 1
        assert controller.snapshot("bcb.gob.bo").requests_started == 2

    asyncio.run(scenario())