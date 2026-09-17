"""
Registro central de workflows para resolución dinámica por nombre (Plugin Registry).
"""

from typing import Dict, Type, Optional
from prospector_externo.kernel.workflow_port import SourceWorkflow


class WorkflowRegistry:
    """Administra y resuelve las implementaciones de SourceWorkflow registradas."""

    _registry: Dict[str, Type[SourceWorkflow]] = {}

    @classmethod
    def register(cls, name: str, workflow_cls: Type[SourceWorkflow]) -> None:
        """Registra un nuevo workflow bajo una clave identificadora."""
        cls._registry[name.lower()] = workflow_cls

    @classmethod
    def resolve(cls, name: str) -> SourceWorkflow:
        """
        Instancia y retorna el workflow registrado para el nombre especificado.
        Lanza ValueError si no existe.
        """
        key = name.lower()
        if key not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(f"Workflow '{name}' no registrado. Disponibles: {available}")
        return cls._registry[key]()

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Verifica si un workflow se encuentra registrado."""
        return name.lower() in cls._registry

    @classmethod
    def clear(cls) -> None:
        """Limpia el registro (útil para pruebas unitarias)."""
        cls._registry.clear()
