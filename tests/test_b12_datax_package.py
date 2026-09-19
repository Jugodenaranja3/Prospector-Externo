from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.b12_datax_package.main import (
    REQUIRED_LEGACY_FIELDS,
    validate_audit,
    validate_legacy_document,
)


def _record():
    return {
        "descripcion": "Dataset",
        "url_descarga": "https://example.test/a.csv",
        "fecha_actualizacion": "2026-09",
        "tipo_archivo": "CSV",
        "url_origen": "https://example.test/",
        "metodo_deteccion": "html_link",
    }


def test_validate_legacy_document_accepts_exact_contract():
    document = {
        "ESTADISTICAS": {
            "CAT": {
                "RAMA": {
                    "a.csv": _record(),
                }
            }
        }
    }

    assert validate_legacy_document(document) == 1


def test_validate_legacy_document_rejects_incomplete_record():
    broken = _record()
    broken.pop("url_origen")

    document = {
        "ESTADISTICAS": {
            "CAT": {
                "RAMA": {
                    "a.csv": broken,
                }
            }
        }
    }

    with pytest.raises(ValueError):
        validate_legacy_document(document)


def test_required_legacy_contract_is_stable():
    assert REQUIRED_LEGACY_FIELDS == {
        "descripcion",
        "url_descarga",
        "fecha_actualizacion",
        "tipo_archivo",
        "url_origen",
        "metodo_deteccion",
    }


def test_validate_audit_accepts_closed_b11_shape():
    rows = []

    for index in range(38):
        rows.append(
            {
                "logical_source_id": f"ready_{index}",
                "classification": "DATAX_READY",
            }
        )

    rows.append(
        {
            "logical_source_id": "transtats",
            "classification": "ACQUISITION_JOB_READY",
        }
    )
    rows.append(
        {
            "logical_source_id": "mhe",
            "classification": "EXTERNAL_BLOCKER",
        }
    )
    rows.append(
        {
            "logical_source_id": "sigma",
            "classification": "EXTERNAL_BLOCKER",
        }
    )

    audit = {"sources": rows}
    selected = validate_audit(audit)

    assert len(selected) == 41


def test_validate_audit_rejects_unresolved_source():
    rows = []

    for index in range(37):
        rows.append(
            {
                "logical_source_id": f"ready_{index}",
                "classification": "DATAX_READY",
            }
        )

    rows.extend(
        [
            {
                "logical_source_id": "bad",
                "classification": "RAW_READY_NO_PROJECTION",
            },
            {
                "logical_source_id": "transtats",
                "classification": "ACQUISITION_JOB_READY",
            },
            {
                "logical_source_id": "mhe",
                "classification": "EXTERNAL_BLOCKER",
            },
            {
                "logical_source_id": "sigma",
                "classification": "EXTERNAL_BLOCKER",
            },
        ]
    )

    with pytest.raises(ValueError):
        validate_audit({"sources": rows})
