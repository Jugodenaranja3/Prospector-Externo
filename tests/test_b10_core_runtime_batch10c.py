from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from bs4.exceptions import ParserRejectedMarkup

from apps.crawler_batch.main import report_exit_code
from prospector_externo.application.dispatcher import SourceDispatcher
from prospector_externo.domain.discovery import DiscoveryFrontier, StopReason
from prospector_externo.domain.models import ResourceType, SourceConfig
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.kernel.registry import WorkflowRegistry
from prospector_externo.workflows.custom_workflow import CustomWorkflow
from prospector_externo.workflows.html_workflow import HtmlWorkflow


class _Budget:
    def __init__(self, remaining: int = 50) -> None:
        self.remaining = remaining


class _FakeApiSession:
    def __init__(self) -> None:
        self.budget = _Budget()
        self.requests_used = 0

    async def fetch_bytes_limited(self, url, *, max_bytes, accept=None):
        self.requests_used += 1
        self.budget.remaining -= 1
        payload = json.dumps(
            {
                "items": [
                    {"id": 1, "name": "palette"},
                ]
            }
        ).encode("utf-8")
        return (
            payload,
            200,
            None,
            {"content-type": "application/json"},
        )


class _FakeFormSession:
    def __init__(self) -> None:
        self.budget = _Budget()
        self.requests_used = 0

    async def fetch_document(
        self,
        url,
        *,
        conditional=False,
        accept=None,
    ):
        self.requests_used += 1
        self.budget.remaining -= 1
        return (
            "<html><body><form method='post'>"
            "<input name='x'></form></body></html>",
            200,
            None,
            {"content-type": "text/html; charset=utf-8"},
        )


class _FakeHtmlSession:
    def __init__(self) -> None:
        self.budget = _Budget()
        self.requests_used = 0

    async def fetch_document(
        self,
        url,
        *,
        conditional=False,
        accept=None,
    ):
        self.requests_used += 1
        self.budget.remaining -= 1
        return (
            "<html><body>broken parser fixture</body></html>",
            200,
            None,
            {"content-type": "text/html"},
        )


def _config(**overrides):
    values = {
        "source_id": "test",
        "name": "Test",
        "entrypoint": "https://example.test/",
        "seeds": ["https://example.test/"],
        "workflow": "html",
        "discover_sitemaps": False,
        "discover_apis": False,
        "max_urls": 2,
        "max_resources": 3,
        "max_depth": 0,
    }
    values.update(overrides)
    return SourceConfig(**values)


def test_frontier_resource_budget_is_independent_from_navigation_budget():
    frontier = DiscoveryFrontier(_config())

    assert frontier.enqueue("https://example.test/a")
    assert frontier.enqueue("https://example.test/b")
    assert not frontier.enqueue("https://example.test/c")
    assert frontier.stop_reason == StopReason.MAX_URLS

    assert frontier.register_resource("https://example.test/a.pdf")
    assert frontier.register_resource("https://example.test/b.xlsx")
    assert frontier.register_resource("https://example.test/c.csv")
    assert not frontier.register_resource("https://example.test/d.zip")

    # 2 páginas + 3 recursos, aunque max_urls sea 2.
    assert frontier.discovered_count == 5


def test_dispatcher_no_longer_defers_javascript(monkeypatch):
    class DummyJavascript:
        def bind_http_session(self, session):
            self.session = session

        async def run(self, config):
            return ExtractionResult(
                source_id=config.source_id,
                success=True,
            )

    monkeypatch.setattr(
        WorkflowRegistry,
        "resolve",
        classmethod(
            lambda cls, name: DummyJavascript()
        ),
    )

    runtime = SimpleNamespace(
        session_for=lambda config: object()
    )
    dispatcher = SourceDispatcher(runtime)

    result = asyncio.run(
        dispatcher.dispatch(
            _config(workflow="javascript")
        )
    )

    assert result.success is True
    assert result.failure_code is None


def test_custom_data_endpoint_is_cataloged_as_api_resource():
    session = _FakeApiSession()
    workflow = CustomWorkflow()
    workflow.bind_http_session(session)

    config = _config(
        workflow="custom",
        discover_apis=True,
        custom_config={
            "custom_kind": "data_endpoint",
            "allowed_methods": ["GET", "HEAD"],
            "data_endpoints": [
                "https://api.example.test/data"
            ],
        },
    )

    result = asyncio.run(
        workflow.run(config)
    )

    assert result.success is True
    assert result.resources
    assert any(
        resource.resource_type == ResourceType.API
        for resource in result.resources
    )


def test_custom_form_is_modeled_as_read_only_acquisition_resource():
    session = _FakeFormSession()
    workflow = CustomWorkflow()
    workflow.bind_http_session(session)

    config = _config(
        workflow="custom",
        custom_config={
            "custom_kind": "download_form_acquisition_job",
            "allowed_methods": ["GET", "HEAD"],
            "submission_policy": "metadata_only_no_post",
            "resource_url_patterns": [
                "/DL_SelectFields.aspx"
            ],
            "evidence_seed_urls": [
                "https://example.test/DL_SelectFields.aspx?q=x"
            ],
        },
    )

    result = asyncio.run(
        workflow.run(config)
    )

    assert result.success is True
    assert len(result.resources) == 1
    resource = result.resources[0]
    assert resource.discovery_method == "custom_form_acquisition_job"
    assert resource.resource_type == ResourceType.FILE


def test_html_parser_rejection_is_controlled_failure_not_exception():
    session = _FakeHtmlSession()
    workflow = HtmlWorkflow()
    workflow.bind_http_session(session)

    def reject_markup(*args, **kwargs):
        raise ParserRejectedMarkup("synthetic bad markup")

    workflow._extract_resources_and_links = reject_markup

    result = asyncio.run(
        workflow.run(_config())
    )

    assert result.success is False
    assert result.failure_code == "PARSER_REJECTED_MARKUP"


def test_batch_exit_code_propagates_failed_sources():
    assert report_exit_code(
        SimpleNamespace(sources_failed=0)
    ) == 0
    assert report_exit_code(
        SimpleNamespace(sources_failed=1)
    ) == 2


def test_operational_custom_configs_are_declarative():
    from pathlib import Path
    import yaml

    data = yaml.safe_load(
        Path("config/sources.yaml").read_text(
            encoding="utf-8"
        )
    )
    rows = {
        row["source_id"]: row
        for row in data["sources"]
    }

    assert (
        rows["fifa"]["custom_config"]["custom_kind"]
        == "data_endpoint"
    )
    assert (
        rows["transtats"]["custom_config"]["custom_kind"]
        == "download_form_acquisition_job"
    )
    assert (
        rows["transtats"]["custom_config"]["submission_policy"]
        == "metadata_only_no_post"
    )
