"""Pruebas del registro de workflows (Plugin Registry)."""

import pytest
from prospector_externo.kernel.registry import WorkflowRegistry
from prospector_externo.workflows.html_workflow import HtmlWorkflow
from prospector_externo.workflows.commented_html import CommentedHtmlWorkflow
from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow
from prospector_externo.workflows.api_workflow import ApiWorkflow
from prospector_externo.workflows.custom_workflow import CustomWorkflow


def test_registry_resolution():
    import prospector_externo.workflows  # Asegurar registro

    assert isinstance(WorkflowRegistry.resolve("html"), HtmlWorkflow)
    assert isinstance(WorkflowRegistry.resolve("commented_html"), CommentedHtmlWorkflow)
    assert isinstance(WorkflowRegistry.resolve("javascript"), JavascriptWorkflow)
    assert isinstance(WorkflowRegistry.resolve("api"), ApiWorkflow)
    assert isinstance(WorkflowRegistry.resolve("custom"), CustomWorkflow)

    with pytest.raises(ValueError):
        WorkflowRegistry.resolve("non_existent_workflow")
