"""
Pool de ejecución con Bulkhead para aislar tareas livianas HTTP de tareas de navegador.
Permite concurrencia controlada para fuentes completas y subtareas (como obtención de cabeceras).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Callable, List, Any, TypeVar, Optional
from prospector_externo.domain.models import SourceConfig

T = TypeVar("T")
R = TypeVar("R")

logger = logging.getLogger("prospector.infrastructure.concurrency")


class AsyncWorkerPool:
    """
    Administrador de concurrencia con aislamiento Bulkhead.
    Separa workers para tareas HTTP livianas (Scraping estático / API)
    de workers para tareas de Navegador pesadas (Playwright headless Chromium).
    """

    def __init__(self, max_http_workers: int = 6, max_browser_workers: int = 2):
        self.max_http_workers = max_http_workers
        self.max_browser_workers = max_browser_workers
        self._http_executor = ThreadPoolExecutor(
            max_workers=self.max_http_workers,
            thread_name_prefix="http-worker"
        )
        self._browser_executor = ThreadPoolExecutor(
            max_workers=self.max_browser_workers,
            thread_name_prefix="browser-worker"
        )

    def submit_workflow(self, task_func: Callable[[SourceConfig], R], config: SourceConfig) -> Future:
        """
        Despacha la ejecución de un workflow al executor adecuado según su naturaleza (Bulkhead).
        """
        is_browser = config.workflow.lower() == "javascript"
        executor = self._browser_executor if is_browser else self._http_executor
        return executor.submit(task_func, config)

    def run_parallel(
        self,
        task_func: Callable[[T], R],
        items: List[T],
        is_browser_task: bool = False,
        max_workers: Optional[int] = None
    ) -> List[R]:
        """
        Ejecuta una lista de subtareas (ej. cabeceras HTTP) en paralelo.
        """
        if not items:
            return []

        if max_workers:
            with ThreadPoolExecutor(max_workers=max_workers) as temp_pool:
                futures = [temp_pool.submit(task_func, item) for item in items]
                results = []
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as e:
                        logger.warning(f"Error en tarea paralela: {e}")
                return results

        executor = self._browser_executor if is_browser_task else self._http_executor
        futures = [executor.submit(task_func, item) for item in items]
        results = []
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                logger.warning(f"Error en tarea paralela: {e}")
        return results

    def shutdown(self, wait: bool = True) -> None:
        """Libera los recursos y apaga los pools."""
        self._http_executor.shutdown(wait=wait)
        self._browser_executor.shutdown(wait=wait)
