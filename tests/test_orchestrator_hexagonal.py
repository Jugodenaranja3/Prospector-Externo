"""Pruebas de integración del orquestador hexagonal y checkpointing incremental."""

import tempfile
import yaml
from pathlib import Path
from unittest.mock import MagicMock

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.domain.observations import ExecutionStatus, ContentStatus
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.models import ResourceCandidate


def test_orchestrator_incremental_runs_and_change_detection(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        output_dir = tmp_path / "output"
        config_file = tmp_path / "sources.yaml"

        config_data = {
            "sources": [
                {
                    "source_id": "test_src",
                    "name": "Fuente de Prueba",
                    "entrypoint": "https://test.gob.bo",
                    "workflow": "html",
                    "update_category": "DAILY"
                }
            ]
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        repo = LocalJsonRepositoryAdapter(base_output_dir=output_dir)
        orchestrator = RunOrchestrator(catalog_repo=repo, report_repo=repo, config_path=config_file)

        # Mock del workflow dispatch
        sample_resources = [
            ResourceCandidate(
                resource_key="key_1",
                url="https://test.gob.bo/doc1.pdf",
                source_id="test_src",
                title="Doc 1",
                file_extension=".pdf"
            )
        ]

        mock_dispatch = MagicMock()
        mock_dispatch.return_value = ExtractionResult(
            source_id="test_src",
            success=True,
            resources=sample_resources
        )
        monkeypatch.setattr(orchestrator.dispatcher, "dispatch", mock_dispatch)

        # 1. Primera Corrida: Debe ser SUCCESS y CHANGED
        report1 = orchestrator.run_batch(force=True)
        assert report1.sources_processed == 1
        assert report1.sources_failed == 0
        assert report1.sources_with_changes == 1
        obs1 = report1.source_results[0]
        assert obs1.execution_status == ExecutionStatus.SUCCESS
        assert obs1.content_status == ContentStatus.CHANGED

        # Validar que los archivos de salida existen en output/test_src/
        assert (output_dir / "test_src" / "mapa_test_src.json").exists()
        assert (output_dir / "test_src" / "mapa_test_src_compact.json").exists()
        assert (output_dir / "test_src" / "mapa_test_src_tree.json").exists()
        assert (output_dir / "reports" / f"run_{report1.run_id}.json").exists()

        # 2. Segunda Corrida (sin cambios): Debe detectar NO_CHANGE
        report2 = orchestrator.run_batch(force=True)
        assert report2.sources_processed == 1
        assert report2.sources_with_changes == 0
        assert report2.sources_without_changes == 1
        obs2 = report2.source_results[0]
        assert obs2.content_status == ContentStatus.NO_CHANGE
