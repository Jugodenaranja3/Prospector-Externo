from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import socket
import ssl
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx


TARGET_FORENSIC_REASONS = {
    "NO_HTTP_TRACE_EXECUTION_FAILURE",
    "ROBOTS_HTTP_5XX_BLOCKING",
    "ROBOTS_REDIRECT_WITHOUT_SITE_CRAWL",
    "UNCLASSIFIED_EXECUTION_FAILURE",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON inválido: {path}")
    return data


def host_from_url(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def resolve_host(host: str) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses: list[str] = []
        for info in infos:
            sockaddr = info[4]
            if not sockaddr:
                continue
            value = sockaddr[0]
            if value not in addresses:
                addresses.append(value)
        return {
            "ok": bool(addresses),
            "addresses": addresses,
            "error_type": None,
            "error": None,
            "resolved_at_utc": started.isoformat(),
        }
    except socket.gaierror as exc:
        return {
            "ok": False,
            "addresses": [],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "resolved_at_utc": started.isoformat(),
        }


def candidate_variants(entrypoint: str) -> list[str]:
    parsed = urlparse(entrypoint)
    host = (parsed.hostname or "").lower()
    if not host:
        return [entrypoint]

    base = host[4:] if host.startswith("www.") else host
    hosts = [host]
    alt = base if host.startswith("www.") else f"www.{base}"
    if alt not in hosts:
        hosts.append(alt)

    schemes = [parsed.scheme or "https"]
    if "https" not in schemes:
        schemes.append("https")
    if "http" not in schemes:
        schemes.append("http")

    variants: list[str] = []
    for scheme in schemes:
        for candidate_host in hosts:
            netloc = candidate_host
            if parsed.port:
                netloc = f"{candidate_host}:{parsed.port}"
            candidate = urlunparse(
                (
                    scheme,
                    netloc,
                    parsed.path or "/",
                    "",
                    parsed.query,
                    "",
                )
            )
            if candidate not in variants:
                variants.append(candidate)
    return variants


def classify_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, httpx.ConnectTimeout):
        return "CONNECT_TIMEOUT"
    if isinstance(exc, httpx.ReadTimeout):
        return "READ_TIMEOUT"
    if isinstance(exc, httpx.TooManyRedirects):
        return "TOO_MANY_REDIRECTS"
    if isinstance(exc, httpx.ProxyError):
        return "PROXY_ERROR"
    if isinstance(exc, httpx.ConnectError):
        if "certificate" in text or "ssl" in text or "tls" in text:
            return "TLS_ERROR"
        if "refused" in text:
            return "CONNECTION_REFUSED"
        if "name" in text or "getaddrinfo" in text:
            return "DNS_OR_NAME_RESOLUTION"
        return "CONNECT_ERROR"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "REMOTE_PROTOCOL_ERROR"
    return type(exc).__name__.upper()


def probe_url(
    client: httpx.Client,
    url: str,
    *,
    max_body_bytes: int,
) -> dict[str, Any]:
    try:
        with client.stream("GET", url) as response:
            body = b""
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                remaining = max_body_bytes - len(body)
                if remaining <= 0:
                    break
                body += chunk[:remaining]
                if len(body) >= max_body_bytes:
                    break

            history = [
                {
                    "url": str(item.url),
                    "status_code": item.status_code,
                    "location": item.headers.get("location"),
                }
                for item in response.history
            ]

            return {
                "ok": True,
                "requested_url": url,
                "final_url": str(response.url),
                "status_code": response.status_code,
                "history": history,
                "content_type": response.headers.get("content-type"),
                "content_length_header": response.headers.get("content-length"),
                "sample_bytes": len(body),
                "error_type": None,
                "error": None,
            }
    except Exception as exc:
        return {
            "ok": False,
            "requested_url": url,
            "final_url": None,
            "status_code": None,
            "history": [],
            "content_type": None,
            "content_length_header": None,
            "sample_bytes": 0,
            "error_type": classify_exception(exc),
            "error": str(exc),
        }


def summarize_variant(
    variant: str,
    dns: dict[str, Any],
    root: dict[str, Any] | None,
    robots: dict[str, Any] | None,
) -> str:
    if not dns["ok"]:
        return "DNS_FAILURE"

    if root is None:
        return "NO_ROOT_PROBE"

    if not root["ok"]:
        return root["error_type"] or "REQUEST_ERROR"

    code = root["status_code"]
    if code is None:
        return "NO_HTTP_STATUS"
    if 200 <= code < 300:
        return "HTTP_2XX"
    if 300 <= code < 400:
        return "HTTP_3XX"
    if code in {401, 403}:
        return "ACCESS_RESTRICTED"
    if code == 404:
        return "HTTP_404"
    if 400 <= code < 500:
        return "HTTP_4XX"
    if code >= 500:
        return "HTTP_5XX"
    return "HTTP_OTHER"


def choose_best_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    rank = {
        "HTTP_2XX": 100,
        "HTTP_3XX": 90,
        "ACCESS_RESTRICTED": 80,
        "HTTP_404": 70,
        "HTTP_4XX": 60,
        "HTTP_5XX": 50,
        "TLS_ERROR": 40,
        "CONNECTION_REFUSED": 35,
        "CONNECT_TIMEOUT": 30,
        "READ_TIMEOUT": 25,
        "CONNECT_ERROR": 20,
        "DNS_FAILURE": 10,
        "NO_ROOT_PROBE": 0,
    }
    if not variants:
        return None
    return max(variants, key=lambda row: rank.get(row["summary"], 1))


def final_status(best: dict[str, Any] | None) -> tuple[str, str]:
    if best is None:
        return "UNRESOLVED", "MANUAL_REVIEW"

    summary = best["summary"]
    if summary == "HTTP_2XX":
        return "RECOVERED_HTTP", "ENTRYPOINT_REVIEW"
    if summary == "HTTP_3XX":
        return "REDIRECTING", "ENTRYPOINT_REDIRECT_REVIEW"
    if summary == "ACCESS_RESTRICTED":
        return "ACCESS_RESTRICTED", "ACCESS_REVIEW"
    if summary in {"HTTP_404", "HTTP_4XX"}:
        return "HTTP_CLIENT_ERROR", "SOURCE_URL_REVIEW"
    if summary == "HTTP_5XX":
        return "SERVER_ERROR", "SOURCE_AVAILABILITY_REVIEW"
    if summary == "DNS_FAILURE":
        return "DNS_FAILURE", "SOURCE_URL_REVIEW"
    if summary == "TLS_ERROR":
        return "TLS_FAILURE", "TLS_OR_SOURCE_REVIEW"
    if summary in {"CONNECT_TIMEOUT", "READ_TIMEOUT", "CONNECTION_REFUSED", "CONNECT_ERROR"}:
        return "NETWORK_FAILURE", "SOURCE_AVAILABILITY_REVIEW"
    return "UNRESOLVED", "MANUAL_REVIEW"


def target_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = audit.get("unresolved_results")
    if not isinstance(rows, list):
        raise ValueError("Audit report sin unresolved_results")
    return [
        row
        for row in rows
        if row.get("forensic_reason") in TARGET_FORENSIC_REASONS
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recovery HTTP dirigido para fuentes B6 no resueltas. "
            "No usa browser ni descarga binarios."
        )
    )
    parser.add_argument(
        "--audit-report",
        default=".runtime/source_evidence_audit/latest.json",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/source_recovery",
    )
    parser.add_argument("--connect-timeout", type=float, default=8.0)
    parser.add_argument("--read-timeout", type=float, default=10.0)
    parser.add_argument("--max-body-bytes", type=int, default=65536)
    parser.add_argument("--max-redirects", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    audit = load_json(Path(args.audit_report))
    rows = target_rows(audit)

    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit debe ser > 0")
        rows = rows[: args.limit]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(
        connect=args.connect_timeout,
        read=args.read_timeout,
        write=args.read_timeout,
        pool=args.connect_timeout,
    )

    headers = {
        "User-Agent": "DATAX-Prospector-Externo/1.0 source-recovery-diagnostic",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
    }

    results: list[dict[str, Any]] = []

    print("=" * 78)
    print("B6E — TARGETED HTTP RECOVERY")
    print("=" * 78)
    print(f"Fuentes físicas objetivo: {len(rows)}")
    print("Browser: NO")
    print("Binarios: NO")
    print("TLS verify: SÍ")
    print()

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=args.max_redirects,
        headers=headers,
        verify=True,
    ) as client:
        for index, row in enumerate(rows, 1):
            entrypoint = row["entrypoint"]
            source_id = row["physical_probe_source_id"]
            variants_out: list[dict[str, Any]] = []

            print(f"[{index:02d}/{len(rows):02d}] {source_id:<22} {entrypoint}")

            for variant in candidate_variants(entrypoint):
                host = host_from_url(variant)
                dns = resolve_host(host)

                root = None
                robots = None

                if dns["ok"]:
                    root = probe_url(
                        client,
                        variant,
                        max_body_bytes=args.max_body_bytes,
                    )
                    robots_url = urljoin(variant.rstrip("/") + "/", "robots.txt")
                    robots = probe_url(
                        client,
                        robots_url,
                        max_body_bytes=min(args.max_body_bytes, 32768),
                    )

                summary = summarize_variant(variant, dns, root, robots)
                variant_result = {
                    "variant": variant,
                    "host": host,
                    "dns": dns,
                    "root": root,
                    "robots": robots,
                    "summary": summary,
                }
                variants_out.append(variant_result)

                root_code = root.get("status_code") if isinstance(root, dict) else None
                root_error = root.get("error_type") if isinstance(root, dict) else None
                print(
                    f"    {variant:<52} "
                    f"{summary:<22} "
                    f"root={root_code if root_code is not None else '-'} "
                    f"err={root_error or '-'}"
                )

            best = choose_best_variant(variants_out)
            status, route = final_status(best)

            result = {
                "physical_probe_source_id": source_id,
                "logical_codes": row.get("logical_codes") or [],
                "original_entrypoint": entrypoint,
                "previous_forensic_reason": row.get("forensic_reason"),
                "recovery_status": status,
                "recommended_route": route,
                "best_variant": best["variant"] if best else None,
                "best_final_url": (
                    best.get("root", {}).get("final_url")
                    if best and isinstance(best.get("root"), dict)
                    else None
                ),
                "variants": variants_out,
            }
            results.append(result)

            print(
                f"    => {status} best={result['best_variant'] or '-'} "
                f"next={route}"
            )

    status_counts = Counter(row["recovery_status"] for row in results)
    route_counts = Counter(row["recommended_route"] for row in results)

    payload = {
        "schema_version": "source-recovery-report-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "targets": len(results),
        "summary_by_status": dict(sorted(status_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "results": results,
    }
    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = [
            "physical_probe_source_id",
            "original_entrypoint",
            "previous_forensic_reason",
            "recovery_status",
            "recommended_route",
            "best_variant",
            "best_final_url",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B6E — Targeted HTTP Recovery",
        "",
        f"- Fuentes físicas objetivo: **{len(results)}**",
        "",
        "## Estados",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(status_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Fuentes",
        "",
        "| Fuente | Estado | Mejor variante | Final URL | Ruta |",
        "|---|---|---|---|---|",
    ])
    for row in results:
        lines.append(
            f"| {row['physical_probe_source_id']} | "
            f"{row['recovery_status']} | "
            f"{row['best_variant'] or '-'} | "
            f"{row['best_final_url'] or '-'} | "
            f"{row['recommended_route']} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("B6E COMPLETADO")
    print("=" * 78)
    for key, count in sorted(status_counts.items()):
        print(f"{key:<30} {count}")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"CSV:  {output_dir / 'latest.csv'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
