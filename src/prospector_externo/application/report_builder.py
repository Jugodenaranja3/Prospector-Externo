"""
Constructor estructurado del ReporteDeCorrida (RunReportBuilder).
Genera el contrato consolidado al finalizar la exploración oportuna.
"""

from datetime import datetime
from prospector_externo.domain.observations import RunReport
from prospector_externo.application.aggregator import ResultAggregator


class RunReportBuilder:
    """Construye el DTO inmutable RunReport a partir de métricas consolidadas."""

    @classmethod
    def build(
        cls,
        run_id: str,
        started_at: datetime,
        finished_at: datetime,
        total_selected: int,
        aggregator: ResultAggregator
    ) -> RunReport:
        return RunReport(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            sources_selected=total_selected,
            sources_processed=aggregator.total_processed,
            sources_skipped=aggregator.total_skipped,
            sources_failed=aggregator.total_failed,
            sources_with_changes=aggregator.total_with_changes,
            sources_without_changes=aggregator.total_without_changes,
            source_results=aggregator.observations
        )
