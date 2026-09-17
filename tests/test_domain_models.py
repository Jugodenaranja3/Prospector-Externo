"""
Pruebas unitarias para modelos del dominio y serialización.
"""

from datetime import datetime, timezone
import pytest
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate, SourceConfig, ChangeStatus


def test_resource_candidate_model():
    rc = ResourceCandidate(
        resource_key="src:123",
        url="https://example.com/file.pdf",
        source_id="src",
        title="Documento",
        file_extension=".pdf",
        content_length_bytes=1024,
        change_status=ChangeStatus.NEW
    )
    assert rc.resource_key == "src:123"
    assert rc.file_extension == ".pdf"
    assert rc.content_length_bytes == 1024

    dumped = rc.model_dump()
    assert dumped["title"] == "Documento"
    assert dumped["url"] == "https://example.com/file.pdf"


def test_snapshot_immutability_and_dump():
    now = datetime.now(timezone.utc)
    snap = Snapshot(
        source_id="finrural",
        run_id="run_100",
        captured_at=now,
        resources_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        total_resources=0,
        resources=[]
    )
    assert snap.source_id == "finrural"
    json_str = snap.model_dump_json()
    assert "finrural" in json_str


def test_source_model_validation():
    source = Source(
        source_id="bbv",
        name="Bolsa Boliviana de Valores",
        entrypoint="https://www.bbv.com.bo",
        workflow="commented_html"
    )
    assert source.source_id == "bbv"
    assert source.health_status == "ACTIVE"
    assert source.update_category == "DAILY"


def test_source_config_defaults():
    cfg = SourceConfig(
        source_id="test",
        entrypoint="https://test.com",
        workflow="html",
        seeds=["https://test.com"]
    )
    assert cfg.source_id == "test"
    assert cfg.workflow == "html"
    assert ".pdf" in cfg.allowed_extensions
