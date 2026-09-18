"""CustomWorkflow mínimo. Los custom reales deben justificarse y testearse individualmente."""

from prospector_externo.domain.models import SourceConfig
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.html_workflow import HtmlWorkflow


class CustomWorkflow(HtmlWorkflow):
    async def pre_crawl_hook(self, config: SourceConfig) -> None:
        return None

    async def post_crawl_hook(
        self, result: ExtractionResult, config: SourceConfig
    ) -> ExtractionResult:
        return result

    async def run(self, config: SourceConfig) -> ExtractionResult:
        await self.pre_crawl_hook(config)
        result = await super().run(config)
        return await self.post_crawl_hook(result, config)
