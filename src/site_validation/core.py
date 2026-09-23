from __future__ import annotations

import json
import re
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup


RESOURCE_EXTENSIONS = {
    ".csv", ".tsv", ".xls", ".xlsx", ".ods",
    ".json", ".xml", ".geojson", ".ndjson", ".jsonstat", ".topojson",
    ".pdf", ".doc", ".docx",
    ".zip", ".tar", ".gz", ".tgz", ".rar", ".7z",
}
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid",
}
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
RESOURCE_CONTENT_TYPES = (
    "application/pdf",
    "text/csv",
    "application/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "application/geo+json",
    "application/zip",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
)


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_url(raw_url: str, base_url: str | None = None) -> str:
    """Small independent canonicalizer used only by the audit.

    It deliberately does not import ``UrlNormalizer`` from the Prospector.
    """
    if not raw_url:
        return ""
    value = urljoin(base_url, raw_url.strip()) if base_url else raw_url.strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return ""
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_PARAMS
    ]
    query.sort()
    return urlunparse((scheme, netloc, path, "", urlencode(query), ""))


def extension_from_url(url: str) -> str:
    path = urlparse(url).path.casefold()
    for ext in sorted(RESOURCE_EXTENSIONS, key=len, reverse=True):
        if path.endswith(ext):
            return ext
    return ""


def looks_like_resource(url: str) -> bool:
    return bool(extension_from_url(url))


def looks_like_html_page(url: str) -> bool:
    path = urlparse(url).path.casefold()
    suffix = Path(path).suffix
    return suffix not in RESOURCE_EXTENSIONS and suffix not in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".js",
        ".ico", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".avi", ".mov",
    }


def host_family(host: str) -> set[str]:
    host = host.casefold().strip(".")
    if host.startswith("www."):
        bare = host[4:]
        return {host, bare}
    return {host, f"www.{host}"}


@dataclass(frozen=True)
class ReferenceCandidate:
    url: str
    raw_url: str
    title: str
    discovered_from: str
    method: str
    extension: str


@dataclass
class ProbeStats:
    pages_visited: int = 0
    pages_failed: int = 0
    links_seen: int = 0
    sitemap_documents: int = 0
    sitemap_urls_seen: int = 0
    robots_status: int | None = None
    stop_reason: str = "QUEUE_EXHAUSTED"


@dataclass
class AuditSource:
    source_id: str
    logical_code: str
    name: str
    entrypoint: str
    historical_entrypoint: str
    operational_status: str
    next_phase: str
    config_source_id: str | None
    expected_baseline_classification: str | None


class AuditRoster:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        plan = load_yaml(repo_root / "config" / "source_operational_plan.yaml")
        execution = load_yaml(repo_root / "config" / "source_execution_map.yaml")
        self.plan_rows = {
            str(row["source_id"]): row
            for row in plan.get("sources", [])
            if isinstance(row, dict) and row.get("source_id")
        }
        self.execution_rows = {
            str(row["logical_source_id"]): row
            for row in execution.get("sources", [])
            if isinstance(row, dict) and row.get("logical_source_id")
        }
        if len(self.plan_rows) != 52:
            raise ValueError(f"Se esperaban 52 fuentes en el plan; encontradas={len(self.plan_rows)}")

    def source(self, source_id: str) -> AuditSource:
        row = self.plan_rows[source_id]
        mapped = self.execution_rows.get(source_id, {})
        return AuditSource(
            source_id=source_id,
            logical_code=str(row.get("logical_code") or source_id),
            name=str(row.get("name") or source_id),
            entrypoint=str(row.get("effective_entrypoint") or row.get("historical_entrypoint") or ""),
            historical_entrypoint=str(row.get("historical_entrypoint") or ""),
            operational_status=str(row.get("operational_status") or ""),
            next_phase=str(row.get("next_phase") or ""),
            config_source_id=(str(mapped.get("config_source_id")) if mapped.get("config_source_id") else None),
            expected_baseline_classification=None,
        )

    def all_sources(self) -> list[AuditSource]:
        return [self.source(source_id) for source_id in self.plan_rows]

    def surface_groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for source in self.all_sources():
            host = (urlparse(source.entrypoint).hostname or "").casefold().removeprefix("www.")
            groups.setdefault(host, []).append(source.source_id)
        return groups


class IndependentReferenceProbe:
    """Bounded, GET-only reference discovery independent from Prospector code."""

    USER_AGENT = "DATAX-SiteValidation/1.0 (+independent audit; GET-only)"

    def __init__(
        self,
        *,
        max_pages: int = 150,
        max_resources: int = 5000,
        max_sitemaps: int = 10,
        max_sitemap_urls: int = 2000,
        timeout: float = 15.0,
        delay_seconds: float = 0.05,
    ) -> None:
        self.max_pages = max_pages
        self.max_resources = max_resources
        self.max_sitemaps = max_sitemaps
        self.max_sitemap_urls = max_sitemap_urls
        self.timeout = timeout
        self.delay_seconds = max(0.0, delay_seconds)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.6",
        })

    def _get(self, url: str) -> requests.Response:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.session.get(url, timeout=self.timeout, allow_redirects=True)

    def _robots(self, entrypoint: str) -> tuple[urllib.robotparser.RobotFileParser, list[str], int | None]:
        parsed = urlparse(entrypoint)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        sitemaps: list[str] = []
        status: int | None = None
        try:
            response = self._get(robots_url)
            status = response.status_code
            if response.status_code < 400:
                text = response.text
                parser.parse(text.splitlines())
                for line in text.splitlines():
                    if line.casefold().startswith("sitemap:"):
                        candidate = line.split(":", 1)[1].strip()
                        if candidate:
                            sitemaps.append(candidate)
            else:
                parser.parse([])
        except requests.RequestException:
            parser.parse([])
        return parser, sitemaps, status

    def _sitemap_candidates(
        self,
        entrypoint: str,
        robots_sitemaps: Iterable[str],
        allowed_hosts: set[str],
        stats: ProbeStats,
    ) -> tuple[list[str], list[ReferenceCandidate]]:
        parsed = urlparse(entrypoint)
        base = f"{parsed.scheme}://{parsed.netloc}"
        queue = deque()
        seen_docs: set[str] = set()
        seen_urls: set[str] = set()
        page_urls: list[str] = []
        resources: list[ReferenceCandidate] = []

        for candidate in [*robots_sitemaps, f"{base}/sitemap.xml", f"{base}/sitemap_index.xml"]:
            normalized = canonical_url(candidate)
            if normalized:
                queue.append(normalized)

        while queue and len(seen_docs) < self.max_sitemaps and len(seen_urls) < self.max_sitemap_urls:
            sitemap_url = queue.popleft()
            if sitemap_url in seen_docs:
                continue
            seen_docs.add(sitemap_url)
            try:
                response = self._get(sitemap_url)
            except requests.RequestException:
                continue
            if response.status_code >= 400:
                continue
            content_type = response.headers.get("content-type", "").casefold()
            if "xml" not in content_type and not response.text.lstrip().startswith("<"):
                continue
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                continue
            stats.sitemap_documents += 1
            for loc in root.findall(".//{*}loc"):
                if not loc.text or len(seen_urls) >= self.max_sitemap_urls:
                    break
                url = canonical_url(loc.text.strip())
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                stats.sitemap_urls_seen += 1
                host = (urlparse(url).hostname or "").casefold()
                if url.casefold().endswith(".xml") and ("sitemap" in url.casefold()):
                    if host in allowed_hosts and len(seen_docs) + len(queue) < self.max_sitemaps:
                        queue.append(url)
                elif looks_like_resource(url):
                    resources.append(ReferenceCandidate(
                        url=url,
                        raw_url=loc.text.strip(),
                        title="",
                        discovered_from=sitemap_url,
                        method="sitemap",
                        extension=extension_from_url(url),
                    ))
                elif host in allowed_hosts and looks_like_html_page(url):
                    page_urls.append(url)
        return page_urls, resources

    def run(self, entrypoint: str) -> tuple[list[ReferenceCandidate], ProbeStats, dict[str, Any]]:
        entrypoint = canonical_url(entrypoint)
        if not entrypoint:
            raise ValueError("Entrypoint HTTP/HTTPS inválido")
        base_host = (urlparse(entrypoint).hostname or "").casefold()
        allowed_hosts = host_family(base_host)
        robots, robots_sitemaps, robots_status = self._robots(entrypoint)
        stats = ProbeStats(robots_status=robots_status)

        sitemap_pages, sitemap_resources = self._sitemap_candidates(
            entrypoint, robots_sitemaps, allowed_hosts, stats
        )

        resources: dict[str, ReferenceCandidate] = {item.url: item for item in sitemap_resources}
        queue = deque([entrypoint])
        # Sitemaps are useful independent seeds, but bounded so huge sites remain manageable.
        for url in sitemap_pages[: self.max_pages * 2]:
            queue.append(url)
        seen_pages: set[str] = set()

        while queue:
            if stats.pages_visited >= self.max_pages:
                stats.stop_reason = "AUDIT_MAX_PAGES"
                break
            if len(resources) >= self.max_resources:
                stats.stop_reason = "AUDIT_MAX_RESOURCES"
                break

            page_url = queue.popleft()
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            if not robots.can_fetch(self.USER_AGENT, page_url):
                continue

            try:
                response = self._get(page_url)
            except requests.RequestException:
                stats.pages_failed += 1
                continue
            stats.pages_visited += 1
            if response.status_code >= 400:
                stats.pages_failed += 1
                continue

            content_type = response.headers.get("content-type", "").casefold()
            final_url = canonical_url(response.url) or page_url
            if any(token in content_type for token in RESOURCE_CONTENT_TYPES):
                resources.setdefault(final_url, ReferenceCandidate(
                    url=final_url,
                    raw_url=response.url,
                    title="",
                    discovered_from=page_url,
                    method="content_type",
                    extension=extension_from_url(final_url),
                ))
                continue
            if content_type and not any(token in content_type for token in HTML_CONTENT_TYPES):
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            for element in soup.find_all(["a", "link"], href=True):
                stats.links_seen += 1
                raw = str(element.get("href") or "").strip()
                candidate = canonical_url(raw, final_url)
                if not candidate:
                    continue
                title = " ".join(element.stripped_strings).strip()
                host = (urlparse(candidate).hostname or "").casefold()
                if looks_like_resource(candidate):
                    resources.setdefault(candidate, ReferenceCandidate(
                        url=candidate,
                        raw_url=urljoin(final_url, raw),
                        title=title,
                        discovered_from=final_url,
                        method="html_link",
                        extension=extension_from_url(candidate),
                    ))
                    continue
                if host in allowed_hosts and looks_like_html_page(candidate) and candidate not in seen_pages:
                    queue.append(candidate)

        meta = {
            "entrypoint": entrypoint,
            "allowed_hosts": sorted(allowed_hosts),
            "robots_sitemaps": robots_sitemaps,
        }
        return sorted(resources.values(), key=lambda item: item.url), stats, meta


def latest_json(directory: Path) -> Path | None:
    values = sorted(path for path in directory.glob("*.json") if path.is_file()) if directory.exists() else []
    return values[-1] if values else None


def baseline_raw(repo_root: Path, baseline_run: str, logical_source_id: str) -> list[dict[str, Any]]:
    path = latest_json(repo_root / "output" / "checkpointed" / baseline_run / logical_source_id / "state" / "snapshots")
    if path is None:
        return []
    document = load_json(path)
    resources = document.get("resources", []) if isinstance(document, dict) else []
    return [item for item in resources if isinstance(item, dict)]


def walk_legacy_records(value: Any) -> Iterable[dict[str, Any]]:
    required = {
        "descripcion", "url_descarga", "fecha_actualizacion",
        "tipo_archivo", "url_origen", "metodo_deteccion",
    }
    if isinstance(value, dict):
        if required.issubset(value):
            yield value
        for child in value.values():
            yield from walk_legacy_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_legacy_records(child)


def baseline_legacy(repo_root: Path, baseline_run: str, logical_source_id: str) -> list[dict[str, Any]]:
    path = (
        repo_root / "output" / "b13-pipeline" / baseline_run /
        "datax-package" / "latest" / "sources" / f"{logical_source_id}.json"
    )
    if not path.exists():
        return []
    return list(walk_legacy_records(load_json(path)))


def compare_urls(
    reference: list[ReferenceCandidate],
    raw: list[dict[str, Any]],
    legacy: list[dict[str, Any]],
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    ref_by_url = {canonical_url(item.url): asdict(item) for item in reference if canonical_url(item.url)}
    raw_by_url: dict[str, dict[str, Any]] = {}
    for item in raw:
        raw_value = str(item.get("raw_url") or item.get("url") or "")
        discovered_from = str(item.get("discovered_from_url") or "")
        normalized = canonical_url(raw_value, discovered_from or base_url)
        if normalized:
            raw_by_url[normalized] = item
    legacy_by_url: dict[str, dict[str, Any]] = {}
    for item in legacy:
        raw_download = str(item.get("url_descarga") or "")
        origin = str(item.get("url_origen") or "")
        normalized = canonical_url(raw_download, origin or base_url)
        if normalized:
            legacy_by_url[normalized] = item

    ref_urls, raw_urls, legacy_urls = set(ref_by_url), set(raw_by_url), set(legacy_by_url)

    def records(urls: set[str], source: dict[str, Any]) -> list[dict[str, Any]]:
        return [source[url] for url in sorted(urls)]

    return {
        "counts": {
            "reference_candidates": len(ref_urls),
            "baseline_raw_records": len(raw),
            "baseline_raw": len(raw_urls),
            "baseline_legacy_records": len(legacy),
            "baseline_legacy": len(legacy_urls),
            "reference_matched_raw": len(ref_urls & raw_urls),
            "reference_only_raw": len(ref_urls - raw_urls),
            "raw_only_reference": len(raw_urls - ref_urls),
            "reference_matched_legacy": len(ref_urls & legacy_urls),
            "reference_only_legacy": len(ref_urls - legacy_urls),
            "legacy_only_reference": len(legacy_urls - ref_urls),
            "raw_not_in_legacy": len(raw_urls - legacy_urls),
        },
        "reference_only_raw": records(ref_urls - raw_urls, ref_by_url),
        "raw_only_reference": records(raw_urls - ref_urls, raw_by_url),
        "reference_only_legacy": records(ref_urls - legacy_urls, ref_by_url),
        "legacy_only_reference": records(legacy_urls - ref_urls, legacy_by_url),
    }


def write_audit_output(
    *,
    output_dir: Path,
    source: AuditSource,
    reference: list[ReferenceCandidate],
    stats: ProbeStats,
    meta: dict[str, Any],
    raw: list[dict[str, Any]],
    legacy: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reference_candidates.json").write_text(
        json.dumps([asdict(item) for item in reference], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "source": asdict(source),
        "probe": {**asdict(stats), **meta},
        "baseline": {"raw_resources": len(raw), "legacy_records": len(legacy)},
        "comparison_counts": comparison["counts"],
        "interpretation": {
            "reference_is_gold_set": False,
            "recall_is_final": False,
            "note": (
                "Las diferencias son candidatos para adjudicación A5. "
                "El reference probe es independiente, pero no constituye por sí solo un ground truth exhaustivo."
            ),
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    c = comparison["counts"]
    lines = [
        f"# Site Validation — {source.logical_code}",
        "",
        f"- Source ID: `{source.source_id}`",
        f"- Entrypoint auditado: `{source.entrypoint}`",
        f"- Estado operacional previo: `{source.operational_status}`",
        f"- Páginas visitadas por auditor: **{stats.pages_visited}**",
        f"- Stop reason auditor: `{stats.stop_reason}`",
        "",
        "## Comparación preliminar",
        "",
        "| Métrica | Cantidad |",
        "|---|---:|",
        f"| Reference candidates | {c['reference_candidates']} |",
        f"| Baseline raw records | {c['baseline_raw_records']} |",
        f"| Baseline raw unique URLs | {c['baseline_raw']} |",
        f"| Baseline legacy records | {c['baseline_legacy_records']} |",
        f"| Baseline legacy unique URLs | {c['baseline_legacy']} |",
        f"| Reference ∩ raw | {c['reference_matched_raw']} |",
        f"| Reference solo | {c['reference_only_raw']} |",
        f"| Raw solo | {c['raw_only_reference']} |",
        f"| Reference ∩ legacy | {c['reference_matched_legacy']} |",
        f"| Reference no presente en legacy | {c['reference_only_legacy']} |",
        f"| Legacy no observado por auditor | {c['legacy_only_reference']} |",
        "",
        "## Interpretación",
        "",
        "Estas cifras **no son todavía recall/precision finales**. Las diferencias deben adjudicarse en A5 para construir el conjunto de referencia confirmado.",
        "",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
