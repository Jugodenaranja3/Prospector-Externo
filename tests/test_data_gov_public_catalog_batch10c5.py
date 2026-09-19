from __future__ import annotations

from pathlib import Path

import yaml


CATALOG_URL = "https://catalog.data.gov/"


def _row():
    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load(
        (root / "config" / "sources.yaml").read_text(encoding="utf-8")
    )
    assert len(raw["sources"]) == 35
    return next(
        row
        for row in raw["sources"]
        if isinstance(row, dict)
        and row.get("source_id") == "data_gov"
    )


def test_data_gov_uses_public_catalog_html_surface():
    row = _row()

    assert row["workflow"] == "html"
    assert row["entrypoint"] == CATALOG_URL
    assert row["seeds"] == [CATALOG_URL]


def test_data_gov_catalog_hosts_are_in_scope():
    row = _row()
    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in row.get("allowed_hosts", [])
    }

    assert "catalog.data.gov" in hosts
    assert "data.gov" in hosts
    assert "www.data.gov" in hosts


def test_data_gov_does_not_bypass_robots():
    row = _row()
    assert row.get("ignore_robots_txt", False) is False
