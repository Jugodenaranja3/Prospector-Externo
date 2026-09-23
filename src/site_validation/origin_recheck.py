from __future__ import annotations

import json
import time
import urllib.robotparser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

from src.site_validation.core import (
    RESOURCE_EXTENSIONS,
    ReferenceCandidate,
    canonical_url,
    extension_from_url,
)


@dataclass
class OriginRecheckStats:
    origin_pages_total: int = 0
    origin_pages_rechecked: int = 0
    origin_pages_failed: int = 0
    origin_pages_blocked_robots: int = 0
    direct_links_seen: int = 0


def _raw_origin(item: dict[str, Any], base_url: str) -> str:
    return canonical_url(str(item.get("discovered_from_url") or ""), base_url)


def _raw_url(item: dict[str, Any], base_url: str) -> str:
    origin = _raw_origin(item, base_url)
    value = str(item.get("raw_url") or item.get("url") or "")
    return canonical_url(value, origin or base_url)


def _legacy_url(item: dict[str, Any], base_url: str) -> str:
    origin = canonical_url(str(item.get("url_origen") or ""), base_url)
    return canonical_url(str(item.get("url_descarga") or ""), origin or base_url)


def origin_pages_from_raw(raw: Iterable[dict[str, Any]], base_url: str) -> list[str]:
    values = {_raw_origin(item, base_url) for item in raw}
    return sorted(value for value in values if value)


def load_allowed_extensions(
    repo_root: Path,
    baseline_run: str,
    logical_source_id: str,
    config_source_id: str | None,
) -> set[str]:
    candidates = [
        repo_root / ".runtime" / "checkpointed_batch" / baseline_run / "source_configs" / f"{logical_source_id}.yaml",
    ]
    if config_source_id and config_source_id != logical_source_id:
        candidates.append(
            repo_root / ".runtime" / "checkpointed_batch" / baseline_run / "source_configs" / f"{config_source_id}.yaml"
        )

    for path in candidates:
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = data.get("sources", []) if isinstance(data, dict) else []
        if rows and isinstance(rows[0], dict):
            values = rows[0].get("allowed_extensions") or []
            normalized = {
                str(value).casefold() if str(value).startswith(".") else f".{str(value).casefold()}"
                for value in values
                if str(value).strip()
            }
            if normalized:
                return normalized
    return set(RESOURCE_EXTENSIONS)


class IndependentOriginRechecker:
    """Re-fetch only the provenance pages recorded by B13 and re-extract direct resources.

    This does not reuse Prospector discovery/parsing code. Baseline provenance is used only
    to define the pages whose completeness is being independently checked.
    """

    USER_AGENT = "DATAX-OriginRecheck/1.0 (+independent audit; GET-only)"

    def __init__(self, *, timeout: float = 20.0, delay_seconds: float = 0.10) -> None:
        self.timeout = timeout
        self.delay_seconds = max(0.0, delay_seconds)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        })
        self._robots: dict[tuple[str, str], urllib.robotparser.RobotFileParser] = {}

    def _get(self, url: str) -> requests.Response:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.session.get(url, timeout=self.timeout, allow_redirects=True)

    def _robot_parser(self, url: str) -> urllib.robotparser.RobotFileParser:
        parsed = urlparse(url)
        key = (parsed.scheme, parsed.netloc.casefold())
        cached = self._robots.get(key)
        if cached is not None:
            return cached
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = self._get(robots_url)
            if response.status_code < 400:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])
        except requests.RequestException:
            parser.parse([])
        self._robots[key] = parser
        return parser

    def run(
        self,
        *,
        entrypoint: str,
        raw: list[dict[str, Any]],
        allowed_extensions: set[str],
    ) -> tuple[list[ReferenceCandidate], OriginRecheckStats, dict[str, Any]]:
        entrypoint = canonical_url(entrypoint)
        if not entrypoint:
            raise ValueError("Entrypoint HTTP/HTTPS invalido")

        origins = origin_pages_from_raw(raw, entrypoint)
        stats = OriginRecheckStats(origin_pages_total=len(origins))
        successful_origins: list[str] = []
        failures: list[dict[str, Any]] = []
        redirects: list[dict[str, str]] = []
        resources: dict[str, ReferenceCandidate] = {}

        for origin in origins:
            parser = self._robot_parser(origin)
            if not parser.can_fetch(self.USER_AGENT, origin):
                stats.origin_pages_blocked_robots += 1
                failures.append({"origin": origin, "reason": "ROBOTS_DISALLOW"})
                continue
            try:
                response = self._get(origin)
            except requests.RequestException as exc:
                stats.origin_pages_failed += 1
                failures.append({"origin": origin, "reason": type(exc).__name__})
                continue
            if response.status_code >= 400:
                stats.origin_pages_failed += 1
                failures.append({"origin": origin, "reason": f"HTTP_{response.status_code}"})
                continue

            final_url = canonical_url(response.url) or origin
            content_type = response.headers.get("content-type", "").casefold()
            if content_type and "html" not in content_type and "xhtml" not in content_type:
                stats.origin_pages_failed += 1
                failures.append({"origin": origin, "reason": f"NON_HTML:{content_type}"})
                continue

            stats.origin_pages_rechecked += 1
            successful_origins.append(origin)
            if final_url != origin:
                redirects.append({"origin": origin, "final_url": final_url})

            soup = BeautifulSoup(response.text, "html.parser")
            for element in soup.find_all(["a", "link"], href=True):
                stats.direct_links_seen += 1
                raw_href = str(element.get("href") or "").strip()
                candidate = canonical_url(raw_href, final_url)
                if not candidate:
                    continue
                extension = extension_from_url(candidate)
                if extension not in allowed_extensions:
                    continue
                title = " ".join(element.stripped_strings).strip()
                resources.setdefault(candidate, ReferenceCandidate(
                    url=candidate,
                    raw_url=urljoin(final_url, raw_href),
                    title=title,
                    discovered_from=origin,
                    method="origin_recheck_html_link",
                    extension=extension,
                ))

        meta = {
            "successful_origins": successful_origins,
            "failures": failures,
            "redirects": redirects,
            "allowed_extensions": sorted(allowed_extensions),
        }
        return sorted(resources.values(), key=lambda item: item.url), stats, meta


def compare_origin_recheck(
    *,
    reference: list[ReferenceCandidate],
    raw: list[dict[str, Any]],
    legacy: list[dict[str, Any]],
    successful_origins: Iterable[str],
    base_url: str,
) -> dict[str, Any]:
    successful = {canonical_url(value, base_url) for value in successful_origins}
    successful.discard("")

    ref_by_url = {canonical_url(item.url): asdict(item) for item in reference if canonical_url(item.url)}
    raw_by_url: dict[str, dict[str, Any]] = {}
    raw_recheckable: dict[str, dict[str, Any]] = {}
    for item in raw:
        normalized = _raw_url(item, base_url)
        if not normalized:
            continue
        raw_by_url[normalized] = item
        if _raw_origin(item, base_url) in successful:
            raw_recheckable[normalized] = item

    legacy_by_url = {
        normalized: item
        for item in legacy
        if (normalized := _legacy_url(item, base_url))
    }

    ref_urls = set(ref_by_url)
    raw_urls = set(raw_by_url)
    recheckable_urls = set(raw_recheckable)
    legacy_urls = set(legacy_by_url)

    def records(urls: set[str], source: dict[str, Any]) -> list[dict[str, Any]]:
        return [source[url] for url in sorted(urls)]

    return {
        "counts": {
            "reference_direct_unique": len(ref_urls),
            "raw_total_unique": len(raw_urls),
            "raw_recheckable_unique": len(recheckable_urls),
            "legacy_total_unique": len(legacy_urls),
            "reference_matched_raw": len(ref_urls & recheckable_urls),
            "reference_only_raw": len(ref_urls - raw_urls),
            "raw_recheckable_not_observed": len(recheckable_urls - ref_urls),
            "reference_matched_legacy": len(ref_urls & legacy_urls),
            "reference_only_legacy": len(ref_urls - legacy_urls),
        },
        "reference_only_raw": records(ref_urls - raw_urls, ref_by_url),
        "raw_recheckable_not_observed": records(recheckable_urls - ref_urls, raw_recheckable),
        "reference_only_legacy": records(ref_urls - legacy_urls, ref_by_url),
    }


def write_origin_recheck_output(
    *,
    output_dir: Path,
    source_id: str,
    entrypoint: str,
    reference: list[ReferenceCandidate],
    stats: OriginRecheckStats,
    meta: dict[str, Any],
    comparison: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "origin_reference_candidates.json").write_text(
        json.dumps([asdict(item) for item in reference], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "origin_recheck.json").write_text(
        json.dumps({
            "source_id": source_id,
            "entrypoint": entrypoint,
            "stats": asdict(stats),
            "meta": meta,
            "comparison": comparison,
            "interpretation": {
                "high_confidence_scope": (
                    "Links descargables observados directamente en paginas de procedencia que B13 registro."
                ),
                "reference_only_means": (
                    "Candidato nuevo o potencialmente omitido; requiere distinguir cambio posterior al baseline de miss real."
                ),
                "raw_not_observed_means": (
                    "No implica falso positivo: la pagina pudo cambiar, paginar distinto o servir contenido dinamico."
                ),
            },
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    c = comparison["counts"]
    lines = [
        f"# Origin Recheck — {source_id}",
        "",
        f"- Origin pages baseline: **{stats.origin_pages_total}**",
        f"- Origin pages rechecked: **{stats.origin_pages_rechecked}**",
        f"- Origin pages failed: **{stats.origin_pages_failed}**",
        f"- Origin pages blocked by robots: **{stats.origin_pages_blocked_robots}**",
        "",
        "| Metrica | Cantidad |",
        "|---|---:|",
        f"| Direct reference URLs | {c['reference_direct_unique']} |",
        f"| Raw total unique URLs | {c['raw_total_unique']} |",
        f"| Raw URLs whose origin was rechecked | {c['raw_recheckable_unique']} |",
        f"| Reference ∩ raw recheckable | {c['reference_matched_raw']} |",
        f"| Reference only vs raw | {c['reference_only_raw']} |",
        f"| Raw recheckable not observed now | {c['raw_recheckable_not_observed']} |",
        f"| Reference ∩ legacy | {c['reference_matched_legacy']} |",
        f"| Reference direct not in legacy | {c['reference_only_legacy']} |",
        "",
        "Las diferencias todavia deben separar cambios posteriores al baseline de omisiones reales.",
        "",
    ]
    (output_dir / "ORIGIN_RECHECK.md").write_text("\n".join(lines), encoding="utf-8")
