from __future__ import annotations

from datetime import timezone

from apps.b13_closure_audit.main import (
    count_legacy_records,
    human_duration,
    parse_dt,
)


def test_parse_dt_accepts_zulu():
    value = parse_dt("2026-09-19T01:29:08Z")
    assert value is not None
    assert value.tzinfo is not None
    assert value.astimezone(timezone.utc).hour == 1


def test_human_duration_formats_hours_minutes_seconds():
    assert human_duration(3661) == "1 h 1 min 1 s"
    assert human_duration(125) == "2 min 5 s"
    assert human_duration(9) == "9 s"


def test_count_legacy_records_accepts_complete_contract():
    document = {
        "ESTADISTICAS": {
            "A": {
                "x.csv": {
                    "descripcion": "X",
                    "url_descarga": "https://x.test/x.csv",
                    "fecha_actualizacion": "2026",
                    "tipo_archivo": "CSV",
                    "url_origen": "https://x.test/",
                    "metodo_deteccion": "html_link",
                }
            }
        }
    }
    assert count_legacy_records(document) == 1


def test_count_legacy_records_rejects_incomplete():
    document = {
        "ESTADISTICAS": {
            "A": {
                "x.csv": {
                    "descripcion": "X",
                    "url_descarga": "https://x.test/x.csv",
                }
            }
        }
    }

    try:
        count_legacy_records(document)
    except ValueError as exc:
        assert "incompletos" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError")
