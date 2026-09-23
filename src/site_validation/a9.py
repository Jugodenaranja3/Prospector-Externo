from __future__ import annotations

import json
import socket
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from src.site_validation.core import AuditRoster

SPECIAL_CASES = ("mhe", "sigma", "transtats")


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "form":
            self._current = {
                "method": (data.get("method") or "GET").upper(),
                "action": data.get("action") or "",
                "fields": [],
            }
            self.forms.append(self._current)
        elif self._current is not None and tag in {"input", "select", "textarea", "button"}:
            name = data.get("name") or data.get("id") or ""
            if name:
                self._current["fields"].append(name)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._current = None


def _request_get(url: str, timeout: float) -> dict[str, Any]:
    headers = {
        "User-Agent": "DATAX-Site-Validation/1.0 (+independent-audit)",
        "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*;q=0.8",
    }
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        return {
            "requested_url": url,
            "final_url": str(response.url),
            "status_code": response.status_code,
            "ok": 200 <= response.status_code < 400,
            "content_type": (response.headers.get("content-type") or "").lower(),
            "text": response.text[:2_000_000],
            "error_type": None,
            "error": None,
        }
    except requests.exceptions.SSLError as exc:
        return {
            "requested_url": url,
            "final_url": None,
            "status_code": None,
            "ok": False,
            "content_type": None,
            "text": "",
            "error_type": "SSLError",
            "error": str(exc),
        }
    except requests.RequestException as exc:
        return {
            "requested_url": url,
            "final_url": None,
            "status_code": None,
            "ok": False,
            "content_type": None,
            "text": "",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _dns_probe(url: str) -> dict[str, Any]:
    host = urlparse(url).hostname or ""
    try:
        rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = sorted({row[4][0] for row in rows if row and row[4]})
        return {"host": host, "ok": True, "addresses": addresses, "error": None}
    except OSError as exc:
        return {"host": host, "ok": False, "addresses": [], "error": f"{type(exc).__name__}: {exc}"}


def classify_external_blocker(
    *,
    dns_ok: bool,
    homepage_ok: bool,
    homepage_error_type: str | None,
    robots_status: int | None,
    robots_error_type: str | None,
) -> str:
    if not dns_ok:
        return "EXTERNAL_DNS_BLOCKER_CONFIRMED"
    if homepage_error_type == "SSLError":
        return "EXTERNAL_TLS_BLOCKER_CONFIRMED"
    if robots_status is not None and robots_status >= 500:
        return "EXTERNAL_ROBOTS_5XX_CONFIRMED"
    if robots_error_type == "SSLError":
        return "EXTERNAL_ROBOTS_TLS_BLOCKER_CONFIRMED"
    if not homepage_ok and robots_error_type:
        return "EXTERNAL_ACCESS_BLOCKER_CONFIRMED"
    if homepage_ok and robots_status is not None and robots_status < 500:
        return "EXTERNAL_BLOCKER_STATE_CHANGED"
    if homepage_ok:
        return "EXTERNAL_BLOCKER_PARTIALLY_CHANGED"
    return "EXTERNAL_ACCESS_BLOCKER_CONFIRMED"


def audit_external_blocker(*, repo_root: Path, source_id: str, timeout: float) -> dict[str, Any]:
    source = AuditRoster(repo_root).source(source_id)
    entrypoint = source.entrypoint
    robots_url = urljoin(entrypoint, "/robots.txt")
    dns = _dns_probe(entrypoint)
    homepage = _request_get(entrypoint, timeout)
    robots = _request_get(robots_url, timeout)

    classification = classify_external_blocker(
        dns_ok=bool(dns["ok"]),
        homepage_ok=bool(homepage["ok"]),
        homepage_error_type=homepage.get("error_type"),
        robots_status=robots.get("status_code"),
        robots_error_type=robots.get("error_type"),
    )
    for row in (homepage, robots):
        row.pop("text", None)

    return {
        "source_id": source_id,
        "entrypoint": entrypoint,
        "baseline_expected": "EXTERNAL_BLOCKER",
        "classification": classification,
        "dns": dns,
        "homepage": homepage,
        "robots": robots,
        "safety": {
            "tls_verification_disabled": False,
            "robots_bypassed": False,
            "unsafe_methods_executed": [],
        },
    }


def _load_transtats_artifact(repo_root: Path, baseline_run: str) -> tuple[Path, dict[str, Any]]:
    candidates = [
        repo_root / "output" / "b13-pipeline" / baseline_run / "datax-package" / "latest" / "special" / "transtats" / "acquisition_job.json",
        repo_root / "output" / "checkpointed" / baseline_run / "transtats" / "transtats" / "downstream" / "acquisition_job.json",
    ]
    for path in candidates:
        if path.exists():
            return path, json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("No se encontro acquisition_job.json de TRANSTATS")


def validate_transtats(*, repo_root: Path, baseline_run: str, timeout: float) -> dict[str, Any]:
    artifact_path, artifact = _load_transtats_artifact(repo_root, baseline_run)
    policy = artifact.get("execution_policy") or {}
    allowed_methods = [str(x).upper() for x in policy.get("allowed_methods") or []]
    contract_checks = {
        "decision": artifact.get("decision") == "ACQUISITION_JOB_READY",
        "kind": artifact.get("kind") == "download_form_acquisition_job",
        "discovery_method": artifact.get("discovery_method") == "custom_form_acquisition_job",
        "allowed_methods": set(allowed_methods) == {"GET", "HEAD"},
        "submission_policy": policy.get("submission_policy") == "metadata_only_no_post",
        "legacy_not_applicable": (artifact.get("legacy_projection") or {}).get("status") == "NOT_APPLICABLE_YET",
    }
    contract_ok = all(contract_checks.values())
    url = str(artifact.get("url") or AuditRoster(repo_root).source("transtats").entrypoint)
    response = _request_get(url, timeout)

    forms: list[dict[str, Any]] = []
    if response.get("text"):
        parser = _FormParser()
        try:
            parser.feed(response["text"])
            forms = parser.forms
        except Exception:
            forms = []

    current_reachable = bool(response.get("ok"))
    if contract_ok and current_reachable and forms:
        classification = "ACQUISITION_JOB_VALIDATED"
    elif contract_ok and current_reachable:
        classification = "ACQUISITION_JOB_VALID_CURRENT_FORM_CHANGED"
    elif contract_ok:
        classification = "ACQUISITION_JOB_ARTIFACT_VALID_CURRENT_ACCESS_REVIEW"
    else:
        classification = "ACQUISITION_JOB_CONTRACT_MISMATCH"

    response.pop("text", None)
    return {
        "source_id": "transtats",
        "entrypoint": AuditRoster(repo_root).source("transtats").entrypoint,
        "artifact_path": str(artifact_path.relative_to(repo_root)),
        "classification": classification,
        "contract_checks": contract_checks,
        "contract_ok": contract_ok,
        "current_get": response,
        "forms_detected": len(forms),
        "forms": forms[:20],
        "safety": {
            "methods_executed": ["GET"],
            "post_executed": False,
            "unsafe_methods_executed": [],
        },
    }


def run_a9_special_cases(
    *,
    repo_root: Path,
    baseline_run: str,
    run_id: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    results = [
        audit_external_blocker(repo_root=repo_root, source_id="mhe", timeout=timeout),
        audit_external_blocker(repo_root=repo_root, source_id="sigma", timeout=timeout),
        validate_transtats(repo_root=repo_root, baseline_run=baseline_run, timeout=timeout),
    ]

    root = repo_root / "output" / "site-validation" / run_id
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "baseline_run": baseline_run,
        "targets": len(results),
        "results": results,
        "safety": {
            "verify_false_used": False,
            "robots_bypass_used": False,
            "post_requests_used": False,
        },
    }
    (root / "a9_special_cases_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        f"# A9 Special Cases — {run_id}",
        "",
        "| Source | Classification | Key evidence |",
        "|---|---|---|",
    ]
    for row in results:
        if row["source_id"] == "transtats":
            evidence = f"contract_ok={row['contract_ok']}; forms={row['forms_detected']}"
        else:
            evidence = (
                f"dns={row['dns']['ok']}; homepage={row['homepage']['status_code'] or row['homepage']['error_type']}; "
                f"robots={row['robots']['status_code'] or row['robots']['error_type']}"
            )
        lines.append(f"| {row['source_id']} | {row['classification']} | {evidence} |")
    (root / "A9_SPECIAL_CASES_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload
