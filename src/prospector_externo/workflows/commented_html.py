"""Workflow HTML que además inspecciona enlaces presentes en comentarios HTML."""

from typing import List, Set
from bs4 import BeautifulSoup, Comment

from prospector_externo.domain.discovery import DiscoveryFrontier, StopReason
from prospector_externo.domain.models import DiscoveredUrl, DiscoveryType, ResourceCandidate, SourceConfig
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.base import BaseWorkflow, ResourceDetector


class CommentedHtmlWorkflow(BaseWorkflow):
    def _extract_from_comments(
        self,
        html_content: str,
        current_url: str,
        config: SourceConfig,
        seen_resource_keys: Set[str],
    ) -> List[ResourceCandidate]:
        soup = BeautifulSoup(html_content, "html.parser")
        resources: List[ResourceCandidate] = []
        comments = soup.find_all(string=lambda text: isinstance(text, Comment))

        for comment in comments:
            comment_soup = BeautifulSoup(str(comment), "html.parser")
            for tag in comment_soup.find_all("a", href=True):
                raw_href = tag["href"].strip()
                if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                from prospector_externo.domain.normalizer import UrlNormalizer
                normalized = UrlNormalizer.normalize(raw_href, base_url=current_url)
                if not ResourceDetector.is_resource(normalized):
                    continue
                title = tag.get_text(" ", strip=True)
                resource = self._make_resource(
                    config=config,
                    raw_url=raw_href,
                    current_url=current_url,
                    title=title,
                    discovery_type=DiscoveryType.COMMENTED_HTML,
                    anchor_text=title,
                )
                if resource.resource_key in seen_resource_keys:
                    continue
                seen_resource_keys.add(resource.resource_key)
                resources.append(resource)
        return resources

    async def run(self, config: SourceConfig) -> ExtractionResult:
        session = self._session()
        frontier = DiscoveryFrontier(config)
        frontier.seed(config.seeds or [config.entrypoint])

        coverage = CoverageStats()
        discovered_urls: List[DiscoveredUrl] = []
        resources: List[ResourceCandidate] = []
        seen_resource_keys: Set[str] = set()
        consecutive_errors = 0
        successful_pages = 0

        while True:
            if session.budget.remaining <= 0:
                frontier.stop_reason = StopReason.REQUEST_BUDGET
                break
            item = frontier.pop()
            if item is None:
                break

            html, status, error = await session.fetch_html(item.normalized_url, conditional=False)
            frontier.mark_visited(item)
            coverage.pages_visited += 1

            if error:
                coverage.urls_failed += 1
                consecutive_errors += 1
                if error == "TIMEOUT":
                    coverage.timeouts += 1
                elif error == "HTTP_403":
                    coverage.http_403 += 1
                elif error == "HTTP_429":
                    coverage.http_429 += 1
                elif error == "ROBOTS_DISALLOWED":
                    coverage.robots_disallowed += 1
                elif error == "REQUEST_BUDGET_EXCEEDED":
                    frontier.stop_reason = StopReason.REQUEST_BUDGET
                    break
                if consecutive_errors >= config.max_consecutive_errors:
                    frontier.stop_reason = StopReason.MAX_CONSECUTIVE_ERRORS
                    break
                continue

            successful_pages += 1
            consecutive_errors = 0
            discovered_urls.append(
                DiscoveredUrl(
                    normalized_url=item.normalized_url,
                    raw_url=item.raw_url,
                    source_id=config.source_id,
                    discovery_type=DiscoveryType.COMMENTED_HTML,
                    parent_url=item.parent_url,
                    depth=item.depth,
                    http_status=status,
                )
            )

            dom_resources, links = self._extract_resources_and_links(
                html or "",
                item.normalized_url,
                config,
                discovery_type=DiscoveryType.COMMENTED_HTML,
                seen_resource_keys=seen_resource_keys,
            )
            comment_resources = self._extract_from_comments(
                html or "", item.normalized_url, config, seen_resource_keys
            )
            for resource in [*dom_resources, *comment_resources]:
                if frontier.register_resource(resource.url):
                    resources.append(resource)
                if frontier.stop_reason == StopReason.MAX_URLS:
                    break

            if frontier.stop_reason == StopReason.MAX_URLS:
                break

            if item.depth < config.max_depth:
                for raw_href, _title in links:
                    frontier.enqueue(
                        raw_href,
                        base_url=item.normalized_url,
                        depth=item.depth + 1,
                        parent_url=item.normalized_url,
                    )

        coverage.resources_found = len(resources)
        coverage.urls_discovered = frontier.discovered_count
        coverage.urls_rejected = frontier.rejected_count
        coverage.urls_pending = frontier.pending_count
        coverage.requests_total = session.requests_used
        coverage.stop_reason = frontier.stop_reason.value if frontier.stop_reason else None

        if successful_pages == 0 and coverage.urls_failed > 0:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                resources=resources,
                discovered_urls=discovered_urls,
                coverage=coverage,
                failure_code=coverage.stop_reason or "SOURCE_UNREACHABLE",
            )

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=resources,
            discovered_urls=discovered_urls,
            coverage=coverage,
        )
