from prospector_externo.domain.discovery import DiscoveryFrontier
from prospector_externo.domain.discovery_intelligence import PaginationYieldPolicy
from prospector_externo.domain.models import SourceConfig


def make_config(**overrides):
    values = {
        "source_id": "test",
        "entrypoint": "https://example.test/",
        "workflow": "html",
        "max_urls": 200,
        "max_query_variants": 3,
        "max_calendar_variants": 3,
        "max_runtime_seconds": 60,
    }
    values.update(overrides)
    return SourceConfig(**values)


def test_pagination_empty_family_is_stopped():
    policy = PaginationYieldPolicy(
        min_pages_before_cutoff=2,
        max_consecutive_empty=2,
        recent_window=2,
    )

    policy.observe("https://example.test/list?page=2", new_resources=0)
    assert policy.should_enqueue("https://example.test/list?page=3") is True

    policy.observe("https://example.test/list?page=3", new_resources=0)
    assert policy.should_enqueue("https://example.test/list?page=4") is False
    assert policy.pages_observed == 2
    assert policy.families_stopped == 1


def test_pagination_productive_family_continues():
    policy = PaginationYieldPolicy(
        min_pages_before_cutoff=2,
        max_consecutive_empty=2,
        recent_window=3,
    )

    policy.observe("https://example.test/list?page=2", new_resources=0)
    policy.observe("https://example.test/list?page=3", new_resources=4)
    policy.observe("https://example.test/list?page=4", new_resources=0)

    assert policy.should_enqueue("https://example.test/list?page=5") is True
    assert policy.families_stopped == 0


def test_calendar_spider_trap_limits_path_variants():
    frontier = DiscoveryFrontier(make_config(max_calendar_variants=3))

    accepted = [
        frontier.enqueue(f"https://example.test/calendar/2026/09/{day:02d}")
        for day in range(1, 8)
    ]

    assert accepted[:3] == [True, True, True]
    assert accepted[3:] == [False, False, False, False]
    assert frontier.spider_traps_blocked == 4


def test_query_variant_cap_is_scoped_by_query_key_family():
    frontier = DiscoveryFrontier(make_config(max_query_variants=2))

    assert frontier.enqueue("https://example.test/data?page=1") is True
    assert frontier.enqueue("https://example.test/data?page=2") is True
    assert frontier.enqueue("https://example.test/data?page=3") is False

    # Otro conjunto de keys representa otra familia técnica y no queda bloqueado
    # por las variantes de `page`.
    assert frontier.enqueue("https://example.test/data?year=2025") is True
    assert frontier.enqueue("https://example.test/data?year=2026") is True

    assert frontier.query_variants_blocked == 1


def test_obvious_repeated_path_segments_are_rejected():
    frontier = DiscoveryFrontier(make_config())
    assert frontier.enqueue("https://example.test/a/a/a/a/report") is False
    assert frontier.spider_traps_blocked == 1
