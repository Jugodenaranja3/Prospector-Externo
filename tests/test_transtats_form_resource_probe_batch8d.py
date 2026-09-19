from __future__ import annotations

from apps.transtats_form_resource_probe.main import (
    classify_probe,
    page_signals,
    same_site,
)


def test_page_signals_detect_download_semantics():
    html = """
    <html>
      <body>
        <h1>Download Instructions</h1>
        <label>Filter Geography</label>
        <label>Filter Year</label>
        <label>Filter Period</label>
        <span>Field Name</span>
        <span>Select all fields</span>
        <form method="post">
          <input name="a">
          <input name="b">
          <input name="c">
          <input name="d">
          <input name="e">
          <select name="year"></select>
        </form>
      </body>
    </html>
    """
    signals = page_signals(
        html,
        ["Download Instructions", "Filter Year", "Filter Period"],
    )
    assert signals["required_signals_ok"] is True
    assert signals["input_count"] == 5
    assert signals["field_token_hits"] >= 3


def test_confirmed_page_promotes_custom_form_resource():
    status, route = classify_probe(
        status_code=200,
        final_url="https://www.transtats.bts.gov/DL_SelectFields.aspx?x=1",
        entrypoint="https://www.transtats.bts.gov",
        signals={
            "required_signals_ok": True,
            "input_count": 10,
            "field_token_hits": 5,
        },
        minimum_inputs=5,
    )
    assert status == "TRANSTATS_FORM_RESOURCE_CONFIRMED"
    assert route == "PROMOTE_CUSTOM_FORM_RESOURCE"


def test_reachable_without_semantics_does_not_promote():
    status, route = classify_probe(
        status_code=200,
        final_url="https://www.transtats.bts.gov/",
        entrypoint="https://www.transtats.bts.gov",
        signals={
            "required_signals_ok": False,
            "input_count": 2,
            "field_token_hits": 1,
        },
        minimum_inputs=5,
    )
    assert status == "TRANSTATS_PAGE_REACHABLE_SEMANTICS_INCOMPLETE"
    assert route == "B10_STATUS"


def test_same_site_accepts_www_variant():
    assert same_site(
        "https://transtats.bts.gov/a",
        "https://www.transtats.bts.gov",
    )
