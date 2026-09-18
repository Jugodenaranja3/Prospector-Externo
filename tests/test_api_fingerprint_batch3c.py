import asyncio
import tempfile
from pathlib import Path

import httpx

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.catalog_service import CatalogApplicationService
from prospector_externo.domain.api_fingerprint import ApiContentFingerprint
from prospector_externo.domain.models import ChangeStatus, ResourceCandidate, ResourceType, SourceConfig
from prospector_externo.domain.observations import ContentStatus
from prospector_externo.infrastructure.http_policy import RetryPolicy
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.api_workflow import ApiWorkflow


def _robots() -> httpx.Response:
    return httpx.Response(200, text="User-agent: *\nAllow: /\n")


def test_json_fingerprint_is_canonical_but_value_sensitive():
    first = '{"b": 2, "a": {"y": 4, "x": 3}}'
    reordered = '{\n  "a": {"x":3,"y":4},\n  "b":2\n}'
    changed = '{"b": 2, "a": {"y": 999, "x": 3}}'

    assert ApiContentFingerprint.page_hash(first, "json") == ApiContentFingerprint.page_hash(
        reordered, "json"
    )
    assert ApiContentFingerprint.page_hash(first, "json") != ApiContentFingerprint.page_hash(
        changed, "json"
    )


def test_paginated_api_builds_aggregate_content_hash_and_server_hints():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return _robots()
            page = request.url.params.get("page")
            if page == "1":
                return httpx.Response(
                    200,
                    json={"items": [{"id": 1, "value": "A"}], "next": "/api/data?page=2"},
                    headers={
                        "Content-Type": "application/json",
                        "ETag": '"seed-etag"',
                        "Last-Modified": "Fri, 18 Sep 2026 01:00:00 GMT",
                    },
                )
            if page == "2":
                return httpx.Response(
                    200,
                    json={"items": [{"id": 2, "value": "B"}]},
                    headers={"Content-Type": "application/json"},
                )
            raise AssertionError(f"request inesperado: {request.url}")

        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1, base_backoff_seconds=0, jitter_ratio=0),
        )
        try:
            config = SourceConfig(
                source_id="fingerprint_api",
                entrypoint="https://example.test/api/data?page=1",
                workflow="api",
                rate_limit_seconds=0,
                max_api_pages=2,
                max_requests=5,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        resource = next(r for r in result.resources if r.resource_type == ResourceType.API)
        assert resource.content_hash is not None
        assert len(resource.content_hash) == 64
        assert resource.etag == '"seed-etag"'
        assert resource.last_modified_header == "Fri, 18 Sep 2026 01:00:00 GMT"
        assert resource.api is not None
        assert resource.api.pages_sampled == 2
        assert resource.api.records_sampled == 2

        first_hash = ApiContentFingerprint.page_hash(
            '{"items":[{"id":1,"value":"A"}],"next":"/api/data?page=2"}', "json"
        )
        second_hash = ApiContentFingerprint.page_hash(
            '{"items":[{"id":2,"value":"B"}]}', "json"
        )
        assert resource.content_hash == ApiContentFingerprint.aggregate_hash(
            [first_hash, second_hash], "json"
        )

    asyncio.run(scenario())


def _api_resource(content_hash: str | None) -> ResourceCandidate:
    return ResourceCandidate(
        resource_key="stable-key",
        url="https://example.test/api/data?page=1",
        source_id="api_change",
        title="API",
        resource_type=ResourceType.API,
        content_hash=content_hash,
    )


def test_catalog_detects_same_key_with_changed_api_content_hash():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = LocalJsonRepositoryAdapter(base_output_dir=Path(tmpdir))
        service = CatalogApplicationService(repo)
        config = SourceConfig(
            source_id="api_change",
            name="API Change",
            entrypoint="https://example.test/api/data?page=1",
            workflow="api",
        )

        first = ExtractionResult(source_id="api_change", success=True, resources=[_api_resource("a" * 64)])
        obs1 = service.reconcile_and_checkpoint(config, "run_001", first)
        assert obs1.content_status == ContentStatus.CHANGED
        assert first.resources[0].change_status == ChangeStatus.NEW

        second = ExtractionResult(source_id="api_change", success=True, resources=[_api_resource("a" * 64)])
        obs2 = service.reconcile_and_checkpoint(config, "run_002", second)
        assert obs2.content_status == ContentStatus.NO_CHANGE
        assert second.resources[0].change_status == ChangeStatus.UNCHANGED

        third = ExtractionResult(source_id="api_change", success=True, resources=[_api_resource("b" * 64)])
        obs3 = service.reconcile_and_checkpoint(config, "run_003", third)
        assert obs3.content_status == ContentStatus.CHANGED
        assert third.resources[0].change_status == ChangeStatus.MODIFIED


def test_legacy_snapshot_without_hash_becomes_one_time_modified_baseline():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = LocalJsonRepositoryAdapter(base_output_dir=Path(tmpdir))
        service = CatalogApplicationService(repo)
        config = SourceConfig(
            source_id="api_change",
            name="API Change",
            entrypoint="https://example.test/api/data?page=1",
            workflow="api",
        )

        legacy = ExtractionResult(source_id="api_change", success=True, resources=[_api_resource(None)])
        service.reconcile_and_checkpoint(config, "run_001", legacy)

        upgraded = ExtractionResult(
            source_id="api_change", success=True, resources=[_api_resource("c" * 64)]
        )
        obs = service.reconcile_and_checkpoint(config, "run_002", upgraded)
        assert obs.content_status == ContentStatus.CHANGED
        assert upgraded.resources[0].change_status == ChangeStatus.MODIFIED

        stable = ExtractionResult(
            source_id="api_change", success=True, resources=[_api_resource("c" * 64)]
        )
        obs2 = service.reconcile_and_checkpoint(config, "run_003", stable)
        assert obs2.content_status == ContentStatus.NO_CHANGE
        assert stable.resources[0].change_status == ChangeStatus.UNCHANGED


def test_non_hashed_legacy_resource_keeps_key_only_change_semantics():
    service = CatalogApplicationService.__new__(CatalogApplicationService)
    first = ResourceCandidate(
        resource_key="file-key",
        url="https://example.test/a.pdf",
        source_id="src",
    )
    second = ResourceCandidate(
        resource_key="file-key",
        url="https://example.test/a.pdf",
        source_id="src",
        title="metadata changed but content not fingerprinted",
    )
    assert service.compute_resources_hash([first]) == service.compute_resources_hash([second])
