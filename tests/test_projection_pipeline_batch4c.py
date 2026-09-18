import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.legacy_stats_exporter import LegacyStatsContractValidator
from prospector_externo.application.projection_export_pipeline import DataxProjectionExportPipeline


FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_ID = "finrural_smoke"
RUN_ID = "run_20260918_004708_3c15e5"


def _prepare_real_finrural_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    snapshots = state / "snapshots"
    snapshots.mkdir(parents=True)
    source = json.loads((FIXTURES / "finrural_real_source_batch4c.json").read_text(encoding="utf-8"))
    (state / "sources.json").write_text(
        json.dumps({SOURCE_ID: source}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    snapshot_text = (FIXTURES / "finrural_real_snapshot_batch4c.json").read_text(encoding="utf-8")
    (snapshots / f"{SOURCE_ID}_{RUN_ID}.json").write_text(snapshot_text, encoding="utf-8")
    return tmp_path


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _records_by_year(legacy_document):
    root = legacy_document["ESTADISTICAS"]["files"]["Financiera"]
    return {year: len(records) for year, records in root.items()}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_contract_validator_accepts_double_digit_legacy_slots():
    document = {
        "ESTADISTICAS": {
            "x": {
                "Descargar_10.csv": {
                    "descripcion": "x",
                    "url_descarga": "https://e.test/x.pdf",
                    "fecha_actualizacion": "2026-01",
                    "tipo_archivo": "PDF",
                    "url_origen": "https://e.test",
                    "metodo_deteccion": "html_link",
                }
            }
        }
    }
    assert LegacyStatsContractValidator.validate(document) == 1


@pytest.mark.parametrize("slot", ["Descargar_1.csv", "Descargar_01.csv", "Descargar_0.csv"])
def test_contract_validator_still_rejects_invalid_slot_numbers(slot):
    document = {
        "ESTADISTICAS": {
            "x": {
                slot: {
                    "descripcion": "x",
                    "url_descarga": "https://e.test/x.pdf",
                    "fecha_actualizacion": "2026-01",
                    "tipo_archivo": "PDF",
                    "url_origen": "https://e.test",
                    "metodo_deteccion": "html_link",
                }
            }
        }
    }
    with pytest.raises(ValueError):
        LegacyStatsContractValidator.validate(document)


def test_real_finrural_snapshot_projects_and_exports_all_24_resources(tmp_path):
    base = _prepare_real_finrural_state(tmp_path)
    before_sources = (base / "state" / "sources.json").read_bytes()
    before_snapshot = (base / "state" / "snapshots" / f"{SOURCE_ID}_{RUN_ID}.json").read_bytes()

    repo = LocalJsonRepositoryAdapter(base)
    manifest = DataxProjectionExportPipeline(repo).export(SOURCE_ID, base)

    assert manifest.source_id == SOURCE_ID
    assert manifest.run_id == RUN_ID
    assert manifest.raw_resources == 24
    assert manifest.selected_resources == 24
    assert manifest.families == 1
    assert manifest.legacy_records == 24

    projection_path = base / manifest.projection_relpath
    legacy_path = base / manifest.legacy_relpath
    assert projection_path.exists()
    assert legacy_path.exists()
    assert manifest.projection_sha256 == _sha256(projection_path)
    assert manifest.legacy_sha256 == _sha256(legacy_path)

    projection = _load_json(projection_path)
    assert projection["source_name"] == "FINRURAL — Smoke real controlado"
    assert projection["resources_hash"] == "24361015f5277b408258a3dba6f72e0f7e0058f46c0ac8ad262cd246c239f381"
    assert projection["total_raw_resources"] == 24
    assert projection["total_selected_resources"] == 24
    assert projection["total_families"] == 1
    family = projection["families"][0]
    assert family["family_key"] == "financiera"
    assert family["latest_period"] == "2026-07"
    assert family["available_formats"] == ["pdf"]
    assert len(family["periods"]) == 24

    legacy = _load_json(legacy_path)
    assert LegacyStatsContractValidator.validate(legacy) == 24
    assert _records_by_year(legacy) == {"2026": 7, "2025": 12, "2024": 5}
    records_2025 = legacy["ESTADISTICAS"]["files"]["Financiera"]["2025"]
    assert "Descargar_10.csv" in records_2025
    assert "Descargar_11.csv" in records_2025
    assert "Descargar_12.csv" in records_2025
    assert all(item["tipo_archivo"] == "PDF" for item in records_2025.values())
    assert all("?x16877" in item["url_descarga"] for item in records_2025.values())

    # La etapa downstream no reescribe estado bruto.
    assert (base / "state" / "sources.json").read_bytes() == before_sources
    assert (base / "state" / "snapshots" / f"{SOURCE_ID}_{RUN_ID}.json").read_bytes() == before_snapshot


def test_real_finrural_export_is_deterministic_on_repeat(tmp_path):
    base = _prepare_real_finrural_state(tmp_path)
    repo = LocalJsonRepositoryAdapter(base)
    pipeline = DataxProjectionExportPipeline(repo)
    first = pipeline.export(SOURCE_ID, base)
    projection_bytes = (base / first.projection_relpath).read_bytes()
    legacy_bytes = (base / first.legacy_relpath).read_bytes()

    second = pipeline.export(SOURCE_ID, base)
    assert second.projection_sha256 == first.projection_sha256
    assert second.legacy_sha256 == first.legacy_sha256
    assert (base / second.projection_relpath).read_bytes() == projection_bytes
    assert (base / second.legacy_relpath).read_bytes() == legacy_bytes


def test_manifest_is_written_with_relative_paths_and_traceability(tmp_path):
    base = _prepare_real_finrural_state(tmp_path)
    repo = LocalJsonRepositoryAdapter(base)
    manifest = DataxProjectionExportPipeline(repo).export(SOURCE_ID, base)
    manifest_path = base / SOURCE_ID / "downstream" / "export_manifest.json"
    saved = _load_json(manifest_path)
    assert saved == manifest.model_dump(mode="json")
    assert saved["projection_relpath"] == f"{SOURCE_ID}/downstream/datax_projection.json"
    assert saved["legacy_relpath"] == f"{SOURCE_ID}/downstream/legacy_estadisticas.json"
    assert not Path(saved["projection_relpath"]).is_absolute()
    assert saved["input_resources_hash"] == "24361015f5277b408258a3dba6f72e0f7e0058f46c0ac8ad262cd246c239f381"


def test_pipeline_fails_loudly_when_source_or_snapshot_is_missing(tmp_path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    pipeline = DataxProjectionExportPipeline(repo)
    with pytest.raises(ValueError, match="Fuente no encontrada"):
        pipeline.export("missing", tmp_path)

    source = json.loads((FIXTURES / "finrural_real_source_batch4c.json").read_text(encoding="utf-8"))
    (tmp_path / "state" / "sources.json").write_text(
        json.dumps({SOURCE_ID: source}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="No existe snapshot"):
        pipeline.export(SOURCE_ID, tmp_path)


def test_projection_cli_runs_end_to_end_on_real_finrural_fixture(tmp_path):
    base = _prepare_real_finrural_state(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "apps.datax_projection.main",
            "--output-dir",
            str(base),
            "--source",
            SOURCE_ID,
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "raw=24 selected=24 families=1 legacy=24" in proc.stdout
    assert (base / SOURCE_ID / "downstream" / "datax_projection.json").exists()
    assert (base / SOURCE_ID / "downstream" / "legacy_estadisticas.json").exists()
    assert (base / SOURCE_ID / "downstream" / "export_manifest.json").exists()


def test_projection_pipeline_module_contains_no_network_clients():
    text = (
        Path(__file__).resolve().parents[1]
        / "src/prospector_externo/application/projection_export_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "import httpx" not in text
    assert "import requests" not in text
    assert "AsyncResilientHttpClient" not in text
