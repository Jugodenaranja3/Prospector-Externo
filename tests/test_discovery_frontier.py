from prospector_externo.domain.discovery import DiscoveryFrontier, StopReason
from prospector_externo.domain.models import SourceConfig
from prospector_externo.domain.normalizer import UrlNormalizer


def config(**overrides):
    data = dict(
        source_id="src_a",
        entrypoint="https://example.test",
        seeds=["https://example.test/"],
        max_depth=2,
        max_urls=10,
        max_query_variants=2,
        max_runtime_seconds=60,
    )
    data.update(overrides)
    return SourceConfig(**data)


def test_frontier_canonical_dedupe_scope_and_query_cap():
    frontier = DiscoveryFrontier(config())
    assert frontier.enqueue("/stats?utm_source=x&a=1", base_url="https://example.test")
    assert not frontier.enqueue("https://EXAMPLE.test/stats?a=1#frag")
    assert not frontier.enqueue("https://outside.test/stats")

    assert frontier.enqueue("/page?p=1", base_url="https://example.test")
    assert frontier.enqueue("/page?p=2", base_url="https://example.test")
    assert not frontier.enqueue("/page?p=3", base_url="https://example.test")
    assert frontier.rejected_count == 2


def test_frontier_enforces_max_urls():
    frontier = DiscoveryFrontier(config(max_urls=2))
    assert frontier.enqueue("https://example.test/a")
    assert frontier.enqueue("https://example.test/b")
    assert not frontier.enqueue("https://example.test/c")
    assert frontier.stop_reason == StopReason.MAX_URLS


def test_resource_key_is_scoped_by_source_and_ref_is_preserved():
    url = UrlNormalizer.normalize("https://example.test/data?ref=series-a&utm_source=x")
    assert url == "https://example.test/data?ref=series-a"
    assert UrlNormalizer.compute_resource_key("a", url) != UrlNormalizer.compute_resource_key("b", url)
