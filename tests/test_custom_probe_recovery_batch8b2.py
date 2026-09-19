from __future__ import annotations

import json
from pathlib import Path

import yaml

from apps.custom_probe.aggregate import aggregate_results


def test_aggregate_recovers_all_source_results(tmp_path: Path):
    strategies = {
        "sources": [
            {"source_id": "a"},
            {"source_id": "b"},
        ]
    }
    strategies_path = tmp_path / "strategies.yaml"
    strategies_path.write_text(
        yaml.safe_dump(strategies),
        encoding="utf-8",
    )

    output_dir = tmp_path / "custom_probe"

    fixtures = [
        {
            "source_id": "a",
            "logical_code": "A",
            "strategy": "CUSTOM_SITE_REVIEW",
            "status": "CUSTOM_NO_DATA_EVIDENCE",
            "recommended_route": "B10_STATUS",
        },
        {
            "source_id": "b",
            "logical_code": "B",
            "strategy": "API_SEMANTIC_PROBE",
            "status": "CUSTOM_DATA_ENDPOINT_CONFIRMED",
            "recommended_route": "PROMOTE_CUSTOM_WORKFLOW",
        },
    ]

    for result in fixtures:
        source_dir = output_dir / "sources" / result["source_id"]
        source_dir.mkdir(parents=True)
        (source_dir / "result.json").write_text(
            json.dumps(result),
            encoding="utf-8",
        )

    payload = aggregate_results(
        strategies_path,
        output_dir,
    )

    assert payload["sources"] == 2
    assert payload["summary_by_status"] == {
        "CUSTOM_DATA_ENDPOINT_CONFIRMED": 1,
        "CUSTOM_NO_DATA_EVIDENCE": 1,
    }
    assert payload["summary_by_route"] == {
        "B10_STATUS": 1,
        "PROMOTE_CUSTOM_WORKFLOW": 1,
    }
    assert (output_dir / "latest.json").exists()
    assert (output_dir / "latest.csv").exists()
    assert (output_dir / "latest.md").exists()


def test_aggregate_requires_every_expected_source(tmp_path: Path):
    strategies_path = tmp_path / "strategies.yaml"
    strategies_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {"source_id": "missing"},
                ]
            }
        ),
        encoding="utf-8",
    )

    try:
        aggregate_results(
            strategies_path,
            tmp_path / "custom_probe",
        )
    except RuntimeError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("Debió fallar por resultado faltante")
