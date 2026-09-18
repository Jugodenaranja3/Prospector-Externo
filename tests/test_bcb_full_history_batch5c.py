from __future__ import annotations

from pathlib import Path

import yaml

from prospector_externo.domain.grouping import GroupingContract, GroupingContractResolver
from prospector_externo.domain.models import ResourceCandidate
from prospector_externo.domain.projection import ProjectionBuilder

ROOT = Path(__file__).resolve().parents[1]


def _contract() -> GroupingContract:
    payload = yaml.safe_load((ROOT / "config/grouping/bcb.yaml").read_text(encoding="utf-8"))
    return GroupingContract.model_validate(payload)


def _resource(key: str, url: str, period: str | None, context: str) -> ResourceCandidate:
    ext = "." + url.rsplit(".", 1)[-1].lower()
    return ResourceCandidate(
        resource_key=key,
        url=url,
        raw_url=url,
        source_id="bcb",
        title="Ver archivo",
        file_extension=ext,
        discovered_from_url="https://www.bcb.gob.bo/?page=16&q=reporte-estadistico",
        period_label=period,
        discovery_method="html_link",
        anchor_text="Ver archivo Pdf",
        context_text=context,
    )


def test_bcb_monthly_rule_accepts_representative_historical_filename_eras():
    c = _contract()
    examples = [
        _resource("r2026", "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Julio%202026.xlsx", "2026-07", "OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - JULIO 2026"),
        _resource("r2022", "https://www.bcb.gob.bo/webdocs/sistema_pagos/SCRI%20REPORTE%20ESTADISTICO%20JUN-22.ods", "2022-06", "OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - JUNIO 2022"),
        _resource("r2018", "https://www.bcb.gob.bo/webdocs/sistema_pagos/REPORTE%20ESTADISTICO%20MAYO%202018.xlsx", "2018-05", "OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - MAYO 2018"),
        _resource("r2014", "https://www.bcb.gob.bo/webdocs/sistema_pagos/ENERO2014.pdf", "2014-01", "OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - ENERO 2014"),
        _resource("r2013", "https://www.bcb.gob.bo/webdocs/sistema_pagos/REPORTE%20ESTADISTICO%20ENERO%202013%20SCRI.pdf", "2013-01", "OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - ENERO 2013"),
    ]
    assert all(GroupingContractResolver.resolve(c, r) is not None for r in examples)
    assert {GroupingContractResolver.resolve(c, r).family_key for r in examples} == {"sistema-pagos-reporte-estadistico"}


def test_bcb_general_system_payments_pdf_does_not_inherit_adjacent_month():
    c = _contract()
    false_positive = _resource(
        "general",
        "https://www.bcb.gob.bo/webdocs/sistema_pagos/operactiponumero.pdf",
        "2013-01",
        "Operaciones del Sistema de Pagos Nacional Ver archivo Pdf | Operaciones del Sistema de Pagos Nacional - Enero 2013",
    )
    assert GroupingContractResolver.resolve(c, false_positive) is None


def test_bcb_historical_scope_keeps_false_positive_raw_but_excludes_downstream():
    c = _contract()
    true_pdf = _resource(
        "monthly",
        "https://www.bcb.gob.bo/webdocs/sistema_pagos/REPORTE%20ESTADISTICO%20ENERO%202013%20SCRI.pdf",
        "2013-01",
        "Operaciones del Sistema de Pagos Nacional - Enero 2013",
    )
    general_pdf = _resource(
        "general",
        "https://www.bcb.gob.bo/webdocs/sistema_pagos/operactiponumero.pdf",
        "2013-01",
        "Operaciones del Sistema de Pagos Nacional Ver archivo Pdf | Operaciones del Sistema de Pagos Nacional - Enero 2013",
    )
    projection = ProjectionBuilder.build(
        source_id="bcb",
        source_name="BCB",
        entrypoint="https://www.bcb.gob.bo/?q=reporte-estadistico",
        run_id="run",
        resources_hash="hash",
        resources=[true_pdf, general_pdf],
        grouping_contract=c,
    )
    assert projection.total_raw_resources == 2
    assert projection.total_selected_resources == 1
    assert projection.total_families == 1
    assert projection.families[0].periods[0].period_label == "2013-01"
    assert len(projection.families[0].periods[0].representations) == 1
    excluded = next(d for d in projection.decisions if d.resource_key == "general")
    assert excluded.selected is False
    assert "grouping_contract_unmatched" in excluded.reason_codes


def test_bcb_full_history_expected_distribution_is_mathematically_consistent():
    # 2013-01..2015-02 = 26 meses solo PDF
    # 2015-03..2018-12 = 46 meses PDF + XLSX
    # 2019-01..2026-07 = 91 meses PDF + XLSX + ODS
    periods = 26 + 46 + 91
    pdf = 26 + 46 + 91
    xlsx = 46 + 91
    ods = 91
    assert periods == 163
    assert (pdf, xlsx, ods) == (163, 137, 91)
    assert pdf + xlsx + ods == 391
