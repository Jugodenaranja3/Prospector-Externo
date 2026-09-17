"""Pruebas para la fachada de consulta REST Catalog API Facade."""

import tempfile
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate


def test_catalog_api_routes():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = LocalJsonRepositoryAdapter(base_output_dir=Path(tmpdir))

        # Registrar datos de prueba
        test_source = Source(
            source_id="asfi",
            name="ASFI",
            entrypoint="https://www.asfi.gob.bo",
            workflow="javascript",
            health_status="ACTIVE"
        )
        repo.save_source(test_source)

        test_snap = Snapshot(
            source_id="asfi",
            run_id="run_123",
            resources_hash="abc",
            total_resources=1,
            resources=[
                ResourceCandidate(
                    resource_key="k1",
                    url="https://www.asfi.gob.bo/doc.pdf",
                    source_id="asfi",
                    title="Doc ASFI",
                    file_extension=".pdf"
                )
            ]
        )
        repo.save_snapshot(test_snap)

        app = FastAPI()
        router = create_catalog_router(catalog_repo=repo, report_repo=repo)
        app.include_router(router)

        client = TestClient(app)

        # GET /catalog/sources
        resp_sources = client.get("/catalog/sources")
        assert resp_sources.status_code == 200
        sources_data = resp_sources.json()
        assert len(sources_data) == 1
        assert sources_data[0]["source_id"] == "asfi"

        # GET /catalog/sources/asfi
        resp_detail = client.get("/catalog/sources/asfi")
        assert resp_detail.status_code == 200
        assert resp_detail.json()["workflow"] == "javascript"

        # GET /catalog/resources
        resp_res = client.get("/catalog/resources?source_id=asfi")
        assert resp_res.status_code == 200
        res_data = resp_res.json()
        assert len(res_data) == 1
        assert res_data[0]["resource_key"] == "k1"

        # GET /catalog/sources/inexistente -> 404
        resp_404 = client.get("/catalog/sources/inexistente")
        assert resp_404.status_code == 404
