"""
Servicio HTTP persistente Catalog API Facade (FastAPI).
Expone consultas de solo lectura para DATAX y Reportes Perdidos sobre el inventario bruto.
"""

import sys
import os
from pathlib import Path
from fastapi import FastAPI

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.adapters.api.routes import create_catalog_router

output_dir = Path(os.environ.get("PROSPECTOR_OUTPUT_DIR", str(project_root / "output")))
backend = os.environ.get("PROSPECTOR_BACKEND", "json")
mongo_uri = os.environ.get("PROSPECTOR_MONGO_URI", "mongodb://localhost:27017")

if backend == "mongo":
    from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
    repo = MongoPersistenceAdapter(connection_uri=mongo_uri)
else:
    repo = LocalJsonRepositoryAdapter(base_output_dir=output_dir)

app = FastAPI(
    title="Prospector Externo — Catalog API Facade",
    description="API REST de solo lectura para consulta de catálogo, snapshots, observaciones y reportes de corrida.",
    version="1.0.0"
)

router = create_catalog_router(catalog_repo=repo, report_repo=repo)
app.include_router(router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "UP", "service": "catalog-api-facade"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apps.catalog_api.main:app", host="0.0.0.0", port=8000, reload=True)
