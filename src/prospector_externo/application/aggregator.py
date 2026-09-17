"""
Agregador de resultados de corrida (ResultAggregator).
Consolida observaciones y estadísticas sin acoplarse a persistencia de negocio.
"""

from typing import List
from prospector_externo.domain.observations import SourceRunObservation, ExecutionStatus, ContentStatus


class ResultAggregator:
    """Acumula y calcula métricas consolidadas sobre un conjunto de observaciones de corrida."""

    def __init__(self):
        self.observations: List[SourceRunObservation] = []

    def add_observation(self, observation: SourceRunObservation) -> None:
        self.observations.append(observation)

    @property
    def total_processed(self) -> int:
        return sum(1 for o in self.observations if o.execution_status in (ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS))

    @property
    def total_failed(self) -> int:
        return sum(1 for o in self.observations if o.execution_status == ExecutionStatus.FAILED)

    @property
    def total_skipped(self) -> int:
        return sum(1 for o in self.observations if o.execution_status == ExecutionStatus.SKIPPED)

    @property
    def total_with_changes(self) -> int:
        return sum(1 for o in self.observations if o.content_status == ContentStatus.CHANGED)

    @property
    def total_without_changes(self) -> int:
        return sum(1 for o in self.observations if o.content_status == ContentStatus.NO_CHANGE)
