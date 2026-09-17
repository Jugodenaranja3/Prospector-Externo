"""
Pruebas unitarias para el adaptador de persistencia LocalJsonRepositoryAdapter.
Verifica exportación a los 3 formatos (Standard, Tree, Compact) y persistencia de reportes.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from prospector_externo.domain.models import Snapshot, ResourceCandidate
from prospector_externo.domain.observations import (
    RunReport, CoverageStats, SourceRunObservation, ExecutionStatus, ContentStatus
)
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter


def test_local_json_adapter_snapshot_export(tmp_path: Path):
    adapter = LocalJsonRepositoryAdapter(base_output_dir=tmp_path)

    resource = ResourceCandidate(
        resource_key="test_source:123456",
        url="https://example.org/doc/reporte_2026.pdf",
        source_id="test_source",
        title="Reporte 2026",
        file_extension=".pdf",
        content_length_bytes=2048,
        discovered_at=datetime.now(timezone.utc)
    )

    snapshot = Snapshot(
        source_id="test_source",
        run_id="run_test_001",
        captured_at=datetime.now(timezone.utc),
        resources_hash="hash_test_123",
        total_resources=1,
        resources=[resource]
    )

    adapter.save_snapshot(snapshot)

    # Verificar que existen los 3 archivos en tmp_path/test_source/
    source_dir = tmp_path / "test_source"
    assert (source_dir / "mapa_test_source.json").exists()
    assert (source_dir / "mapa_test_source_tree.json").exists()
    assert (source_dir / "mapa_test_source_compact.json").exists()

    # Validar formato estándar
    with open(source_dir / "mapa_test_source.json", "r", encoding="utf-8") as f:
        std_data = json.load(f)
    assert std_data["version"] == "1.0.0"
    assert std_data["source"]["id"] == "test_source"
    assert len(std_data["datasets"][0]["resources"]) == 1
    assert std_data["datasets"][0]["resources"][0]["file_extension"] == ".pdf"

    # Validar formato compacto
    with open(source_dir / "mapa_test_source_compact.json", "r", encoding="utf-8") as f:
        compact_data = json.load(f)
    assert compact_data["source_id"] == "test_source"
    assert len(compact_data["resources"]) == 1
    assert compact_data["resources"][0]["ext"] == ".pdf"

    # Validar formato árbol
    with open(source_dir / "mapa_test_source_tree.json", "r", encoding="utf-8") as f:
        tree_data = json.load(f)
    assert tree_data["type"] == "source"
    assert len(tree_data["children"]) == 1

    # Validar recuperación de último snapshot
    loaded_snap = adapter.get_latest_snapshot("test_source")
    assert loaded_snap is not None
    assert loaded_snap.run_id == "run_test_001"
    assert len(loaded_snap.resources) == 1


def test_local_json_adapter_run_report(tmp_path: Path):
    adapter = LocalJsonRepositoryAdapter(base_output_dir=tmp_path)

    report = RunReport(
        run_id="run_rep_001",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        sources_selected=1,
        sources_processed=1,
        sources_skipped=0,
        sources_failed=0,
        sources_with_changes=1,
        source_results=[
            SourceRunObservation(
                source_id="test_source",
                run_id="run_rep_001",
                workflow="html",
                execution_status=ExecutionStatus.SUCCESS,
                content_status=ContentStatus.CHANGED,
                coverage=CoverageStats(urls_discovered=10, resources_found=1)
            )
        ]
    )

    adapter.save_report(report)

    report_path = tmp_path / "reports" / "run_run_rep_001.json"
    assert report_path.exists()

    latest = adapter.get_report("run_rep_001")
    assert latest is not None
    assert latest.run_id == "run_rep_001"
    assert len(latest.source_results) == 1
