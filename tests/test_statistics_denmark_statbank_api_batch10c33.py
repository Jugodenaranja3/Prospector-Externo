from __future__ import annotations

from pathlib import Path

import yaml


STATBANK_TABLES = "https://api.statbank.dk/v1/tables?lang=en"


def _load():
    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load(
        (root / "config" / "sources.yaml").read_text(encoding="utf-8")
    )


def _row():
    raw = _load()
    return next(
        row
        for row in raw["sources"]
        if isinstance(row, dict)
        and row.get("source_id") == "statistics_denmark"
    )


def test_statistics_denmark_uses_official_statbank_api():
    row = _row()
    assert row["workflow"] == "api"
    assert row["entrypoint"] == STATBANK_TABLES
    assert row["seeds"] == [STATBANK_TABLES]


def test_statistics_denmark_api_host_is_explicitly_allowed():
    row = _row()
    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in row.get("allowed_hosts", [])
    }
    assert "api.statbank.dk" in hosts


def test_physical_source_count_is_preserved():
    raw = _load()
    assert len(raw["sources"]) == 35
