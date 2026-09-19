from __future__ import annotations

from pathlib import Path

import yaml


EXPECTED = {
    "mdryt_oap": {
        "entrypoint": "https://ruralytierras.gob.bo/",
        "workflow": "html",
        "hosts": {"ruralytierras.gob.bo", "www.ruralytierras.gob.bo"},
    },
    "asofin": {
        "entrypoint": "https://asofinbolivia.com/",
        "workflow": "html",
        "hosts": {"asofinbolivia.com", "www.asofinbolivia.com"},
    },
    "data_gov": {
        "entrypoint": "https://catalog.data.gov/",
        "workflow": "html",
        "hosts": {"catalog.data.gov", "data.gov", "www.data.gov"},
    },
    "ibce_cao": {
        "entrypoint": "https://ibce.org.bo/",
        "workflow": "html",
        "hosts": {"ibce.org.bo", "www.ibce.org.bo"},
    },
    "senamhi": {
        "entrypoint": "https://senamhi.gob.bo/",
        "workflow": "html",
        "hosts": {"senamhi.gob.bo", "www.senamhi.gob.bo"},
    },
}


def _rows():
    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load(
        (root / "config" / "sources.yaml").read_text(encoding="utf-8")
    )
    return raw, {
        row["source_id"]: row
        for row in raw["sources"]
        if isinstance(row, dict) and row.get("source_id")
    }


def test_canonical_hosts_are_promoted():
    raw, rows = _rows()
    assert len(raw["sources"]) == 35

    for source_id, expected in EXPECTED.items():
        row = rows[source_id]
        assert row["entrypoint"] == expected["entrypoint"]
        assert row["seeds"] == [expected["entrypoint"]]
        assert row["workflow"] == expected["workflow"]

        hosts = {
            str(host).strip().lower().rstrip(".")
            for host in row.get("allowed_hosts", [])
        }
        assert expected["hosts"].issubset(hosts)


def test_no_tls_or_robots_bypass_was_added():
    _, rows = _rows()

    for source_id in EXPECTED:
        row = rows[source_id]
        assert row.get("ignore_robots_txt", False) is False
        assert "verify_tls" not in row
        assert "verify_ssl" not in row
