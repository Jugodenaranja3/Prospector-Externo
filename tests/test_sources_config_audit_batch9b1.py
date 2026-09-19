from __future__ import annotations

from apps.sources_config_audit.main import (
    iter_entries,
    match_plan_source,
    source_container,
)


def test_sources_list_container():
    cfg = {"sources": [{"source_id": "a"}]}
    assert source_container(cfg)[0] == "sources_list"
    assert len(iter_entries(cfg)) == 1


def test_sources_dict_container():
    cfg = {"sources": {"a": {"url": "https://example.org"}}}
    assert source_container(cfg)[0] == "sources_dict"
    assert iter_entries(cfg)[0][0] == "a"


def test_match_by_source_id():
    plan = {
        "source_id": "anapo",
        "logical_code": "ANAPO",
        "effective_entrypoint": "https://example.org",
    }
    cfg = {
        "sources": [
            {
                "source_id": "anapo",
                "url": "https://different.example.org",
            }
        ]
    }
    matches = match_plan_source(plan, iter_entries(cfg))
    assert len(matches) == 1
    assert matches[0]["identity_overlap"] == ["anapo"]


def test_match_by_url():
    plan = {
        "source_id": "new-id",
        "logical_code": "NEW",
        "effective_entrypoint": "https://example.org/",
    }
    cfg = {
        "sources": [
            {
                "code": "old",
                "base_url": "https://example.org",
            }
        ]
    }
    matches = match_plan_source(plan, iter_entries(cfg))
    assert len(matches) == 1
    assert matches[0]["url_overlap"] == ["https://example.org"]
