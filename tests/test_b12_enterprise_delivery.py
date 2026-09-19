from __future__ import annotations

import hashlib
import json
from pathlib import Path

from apps.b12_enterprise_delivery.main import (
    build_consumer_contract,
    exact_payload_groups,
    validate_checksums,
)


def test_exact_payload_groups_identifies_shared_physical_aliases():
    manifest = {
        "sources": [
            {
                "logical_source_id": "a",
                "physical_source_id": "shared",
                "sha256": "abc",
            },
            {
                "logical_source_id": "b",
                "physical_source_id": "shared",
                "sha256": "abc",
            },
            {
                "logical_source_id": "c",
                "physical_source_id": "c",
                "sha256": "def",
            },
        ],
        "counts": {"datax_ready": 3},
    }

    groups = exact_payload_groups(manifest)

    assert len(groups) == 1
    assert groups[0]["logical_source_ids"] == ["a", "b"]
    assert groups[0]["physical_source_ids"] == ["shared"]
    assert groups[0]["shared_physical_source"] is True


def test_exact_payload_groups_marks_cross_physical_duplicate():
    manifest = {
        "sources": [
            {
                "logical_source_id": "a",
                "physical_source_id": "pa",
                "sha256": "abc",
            },
            {
                "logical_source_id": "b",
                "physical_source_id": "pb",
                "sha256": "abc",
            },
        ],
        "counts": {"datax_ready": 2},
    }

    groups = exact_payload_groups(manifest)

    assert len(groups) == 1
    assert groups[0]["shared_physical_source"] is False


def test_consumer_contract_uses_manifest_as_entrypoint():
    manifest = {
        "counts": {"datax_ready": 38},
        "sources": [],
    }

    contract = build_consumer_contract(
        manifest,
        [],
    )

    assert contract["package_entrypoint"] == "manifest.json"
    assert contract["selection_key"] == "logical_source_id"
    assert contract["artifact_field"] == "artifact"
    assert contract["legacy"]["root"] == "ESTADISTICAS"


def test_validate_checksums_requires_full_coverage(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"

    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")

    digest_a = hashlib.sha256(b"A").hexdigest()

    (tmp_path / "checksums.sha256").write_text(
        f"{digest_a}  a.txt\n",
        encoding="utf-8",
    )

    try:
        validate_checksums(tmp_path)
    except ValueError as exc:
        assert "Cobertura de checksums inconsistente" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError")


def test_validate_checksums_accepts_complete_valid_set(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"

    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")

    lines = []
    for path in (a, b):
        digest = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        lines.append(f"{digest}  {path.name}")

    (tmp_path / "checksums.sha256").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    result = validate_checksums(tmp_path)

    assert result == {
        "covered_files": 2,
        "mismatches": 0,
    }
