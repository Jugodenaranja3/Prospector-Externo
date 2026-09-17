"""
Pruebas para la configuración centralizada de fuentes (config/sources.yaml).
"""

from pathlib import Path
import yaml
from prospector_externo.domain.models import SourceConfig


def test_sources_yaml_structure():
    config_path = Path("config/sources.yaml")
    assert config_path.exists(), "config/sources.yaml no existe"

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "sources" in data
    sources_list = data["sources"]
    assert isinstance(sources_list, list)

    sources_by_id = {s["source_id"]: s for s in sources_list}
    assert "finrural" in sources_by_id
    assert "bbv" in sources_by_id

    # Validar fuente finrural
    finrural_cfg = SourceConfig(**sources_by_id["finrural"])
    assert finrural_cfg.source_id == "finrural"
    assert finrural_cfg.workflow == "html"
    assert len(finrural_cfg.seeds) > 0
    assert ".pdf" in finrural_cfg.allowed_extensions

    # Validar fuente bbv
    bbv_cfg = SourceConfig(**sources_by_id["bbv"])
    assert bbv_cfg.source_id == "bbv"
    assert bbv_cfg.workflow == "commented_html"
    assert len(bbv_cfg.seeds) > 0
