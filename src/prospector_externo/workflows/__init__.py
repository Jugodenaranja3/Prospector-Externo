"""
Módulo de workflows especializados y autoregistro en el WorkflowRegistry (Microkernel / Plugins).
"""

from prospector_externo.kernel.registry import WorkflowRegistry
from prospector_externo.workflows.html_workflow import HtmlWorkflow
from prospector_externo.workflows.commented_html import CommentedHtmlWorkflow
from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow
from prospector_externo.workflows.api_workflow import ApiWorkflow
from prospector_externo.workflows.custom_workflow import CustomWorkflow

# Registro automático de todos los workflows disponibles
WorkflowRegistry.register("html", HtmlWorkflow)
WorkflowRegistry.register("commented_html", CommentedHtmlWorkflow)
WorkflowRegistry.register("javascript", JavascriptWorkflow)
WorkflowRegistry.register("api", ApiWorkflow)
WorkflowRegistry.register("custom", CustomWorkflow)

__all__ = [
    "HtmlWorkflow",
    "CommentedHtmlWorkflow",
    "JavascriptWorkflow",
    "ApiWorkflow",
    "CustomWorkflow",
]
