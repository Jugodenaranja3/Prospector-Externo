"""
Pruebas unitarias para el administrador de concurrencia y Bulkhead (AsyncWorkerPool).
"""

import time
import pytest
from prospector_externo.infrastructure.concurrency import AsyncWorkerPool
from prospector_externo.domain.models import SourceConfig


def test_async_worker_pool_bulkhead_isolation():
    pool = AsyncWorkerPool(max_http_workers=2, max_browser_workers=1)

    http_config = SourceConfig(
        source_id="http_test",
        entrypoint="https://example.com",
        workflow="html"
    )
    browser_config = SourceConfig(
        source_id="browser_test",
        entrypoint="https://example.com",
        workflow="javascript"
    )

    def dummy_task(cfg):
        time.sleep(0.05)
        return f"OK_{cfg.workflow}"

    future_http = pool.submit_workflow(dummy_task, http_config)
    future_browser = pool.submit_workflow(dummy_task, browser_config)

    assert future_http.result() == "OK_html"
    assert future_browser.result() == "OK_javascript"

    pool.shutdown(wait=True)


def test_async_worker_pool_run_parallel():
    pool = AsyncWorkerPool(max_http_workers=4)

    items = [1, 2, 3, 4, 5]

    def square(n):
        return n * n

    results = pool.run_parallel(square, items, max_workers=4)
    assert sorted(results) == [1, 4, 9, 16, 25]

    pool.shutdown(wait=True)
