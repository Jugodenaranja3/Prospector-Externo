from __future__ import annotations

from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from prospector_externo.domain.grouping import GroupingContract, GroupingContractResolver
from prospector_externo.domain.models import DiscoveryType, SourceConfig
from prospector_externo.workflows.html_workflow import HtmlWorkflow

ROOT = Path(__file__).resolve().parents[1]


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="bcb",
        name="BCB test",
        entrypoint="https://www.bcb.gob.bo/?q=pub_boletin-mensual",
        workflow="html",
        seeds=["https://www.bcb.gob.bo/?q=pub_boletin-mensual"],
    )


def _contract() -> GroupingContract:
    payload = yaml.safe_load(
        (ROOT / "config/grouping/bcb.yaml").read_text(encoding="utf-8")
    )
    return GroupingContract.model_validate(payload)


def _resource_from_html(label: str, href: str):
    html = f"""
    <div class="view-row">
      <div class="bulletin-label">{label}</div>
      <div class="file-cell">/ <a href="{href}">Ver archivo Pdf</a></div>
    </div>
    """
    workflow = HtmlWorkflow()
    soup = BeautifulSoup(html, "html.parser")
    anchor = soup.find("a")
    context = workflow._context_for_anchor(anchor)
    return workflow._make_resource(
        config=_config(),
        raw_url=href,
        current_url="https://www.bcb.gob.bo/?page=1&q=pub_boletin-mensual",
        title="Ver archivo Pdf",
        discovery_type=DiscoveryType.HTML,
        anchor_text="Ver archivo Pdf",
        context_text=context,
    )


def test_historical_bulletin_label_is_recovered_as_context():
    resource = _resource_from_html(
        "BOLETÍN MENSUAL 301 - ENERO 2020",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2020/03/02/INDICE%20BASE%20-%20BOLETIN%20301.pdf",
    )
    assert "BOLETÍN MENSUAL 301 - ENERO 2020" in (resource.context_text or "")
    assert resource.period_label == "2020-01"


def test_old_filename_without_year_uses_human_bulletin_context():
    resource = _resource_from_html(
        "Boletín Mensual 193 - enero 2011",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/mensualeneroxxx.pdf",
    )
    assert resource.period_label == "2011-01"


def test_abbreviated_old_filename_does_not_override_context_year():
    resource = _resource_from_html(
        "Boletín Mensual 229 - enero 2014",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/mensualenero14.pdf",
    )
    assert resource.period_label == "2014-01"


def test_historical_monthly_pdf_gets_single_publication_family():
    contract = _contract()
    resource = _resource_from_html(
        "BOLETÍN MENSUAL 301 - ENERO 2020",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2020/03/02/INDICE%20BASE%20-%20BOLETIN%20301.pdf",
    )
    resolution = GroupingContractResolver.resolve(contract, resource)
    assert resolution is not None
    assert resolution.rule_id == "bcb_boletin_mensual_publicacion_pdf"
    assert resolution.family_key == "boletin-mensual-publicacion-completa"


def test_global_pdf_on_bulletin_page_stays_unmatched():
    workflow = HtmlWorkflow()
    resource = workflow._make_resource(
        config=_config(),
        raw_url="https://www.bcb.gob.bo/webdocs/Otros/the_wolfsberg_group.pdf",
        current_url="https://www.bcb.gob.bo/?q=pub_boletin-mensual",
        title="Cuestionario Wolfsberg sobre Prevención de Lavado de Dinero",
        discovery_type=DiscoveryType.HTML,
        anchor_text="Cuestionario Wolfsberg sobre Prevención de Lavado de Dinero",
        context_text=None,
    )
    assert GroupingContractResolver.resolve(_contract(), resource) is None


def test_real_sample_periods_cover_the_six_historical_epochs():
    cases = [
        ("BOLETÍN MENSUAL 337 - ENERO 2023",
         "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2023/07/19/%C3%8Dndice%20Boletin%20Mensual%20Enero%202023.pdf",
         "2023-01"),
        ("BOLETÍN MENSUAL 301 - ENERO 2020",
         "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2020/03/02/INDICE%20BASE%20-%20BOLETIN%20301.pdf",
         "2020-01"),
        ("BOLETÍN MENSUAL 265 - ENERO 2017",
         "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2017/04/09/BOLETIN_MENSUAL_ENERO_2017.pdf",
         "2017-01"),
        ("BOLETÍN MENSUAL 229 - ENERO 2014",
         "https://www.bcb.gob.bo/webdocs/publicacionesbcb/mensualenero14.pdf",
         "2014-01"),
        ("BOLETÍN MENSUAL 193 - ENERO 2011",
         "https://www.bcb.gob.bo/webdocs/publicacionesbcb/mensualeneroxxx.pdf",
         "2011-01"),
        ("Boletin Mensual 157 -enero 2008",
         "https://www.bcb.gob.bo/webdocs/publicacionesbcb/mensualenero.pdf",
         "2008-01"),
    ]
    contract = _contract()
    for label, url, expected_period in cases:
        resource = _resource_from_html(label, url)
        assert resource.period_label == expected_period
        resolution = GroupingContractResolver.resolve(contract, resource)
        assert resolution is not None
        assert resolution.family_key == "boletin-mensual-publicacion-completa"
