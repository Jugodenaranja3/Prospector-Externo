from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.b12_enterprise_delivery.main import (
    audit_enterprise_package,
)


DEFAULT_ENTERPRISE_REPORT = Path(
    ".runtime/b12_enterprise_audit/latest.json"
)
DEFAULT_PACKAGE_DIR = Path("output/datax-package/latest")
DEFAULT_ZIP = Path(
    "output/datax-package/datax_package_latest.zip"
)
DEFAULT_OUTPUT = Path(".runtime/b12_closure/latest.json")


EXPECTED_STATUS = "ENTERPRISE_PACKAGE_READY"
EXPECTED_OPERATIONAL = 41
EXPECTED_LEGACY_FILES = 38
EXPECTED_LEGACY_RECORDS = 5407
EXPECTED_ACQUISITION = 1
EXPECTED_BLOCKERS = 2
EXPECTED_EXACT_DUPLICATE_GROUPS = 5
EXPECTED_SHARED_PHYSICAL_GROUPS = 5
EXPECTED_CROSS_PHYSICAL_GROUPS = 0


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(
    *,
    enterprise_report: dict[str, Any],
    fresh_audit: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}

    checks["enterprise_status"] = (
        enterprise_report.get("status") == EXPECTED_STATUS
        and fresh_audit.get("status") == EXPECTED_STATUS
    )

    index = fresh_audit.get("index") or {}
    base = fresh_audit.get("base_verification") or {}
    duplicates = fresh_audit.get("duplicates") or {}
    checksums = fresh_audit.get("checksums") or {}
    zip_info = fresh_audit.get("zip") or {}

    checks["operational_sources"] = (
        index.get("manifest_sources") == EXPECTED_OPERATIONAL
        and index.get("csv_rows") == EXPECTED_OPERATIONAL
    )

    checks["legacy_files"] = (
        base.get("legacy_files") == EXPECTED_LEGACY_FILES
    )
    checks["legacy_records"] = (
        base.get("legacy_records") == EXPECTED_LEGACY_RECORDS
    )
    checks["acquisition_manifest"] = (
        base.get("special_acquisition") == EXPECTED_ACQUISITION
    )
    checks["external_blockers"] = (
        base.get("external_blockers") == EXPECTED_BLOCKERS
    )

    checks["duplicate_groups"] = (
        duplicates.get("exact_duplicate_groups")
        == EXPECTED_EXACT_DUPLICATE_GROUPS
        and duplicates.get("shared_physical_groups")
        == EXPECTED_SHARED_PHYSICAL_GROUPS
        and duplicates.get("cross_physical_groups")
        == EXPECTED_CROSS_PHYSICAL_GROUPS
    )

    checks["checksum_integrity"] = (
        checksums.get("mismatches") == 0
    )
    checks["zip_integrity"] = (
        zip_info.get("content_mismatches") == 0
    )

    consumer_entrypoint = fresh_audit.get(
        "consumer_entrypoint"
    )
    checks["consumer_contract"] = (
        consumer_entrypoint == "manifest.json"
    )

    checksum_files = checksums.get("covered_files")
    zip_entries = zip_info.get("zip_entries")

    # checksums.sha256 intentionally cannot include its own digest. Therefore
    # the ZIP contains exactly one more file than checksum coverage.
    checks["checksum_zip_count_relation"] = (
        isinstance(checksum_files, int)
        and isinstance(zip_entries, int)
        and zip_entries == checksum_files + 1
    )

    failures = [
        name
        for name, passed in checks.items()
        if not passed
    ]

    status = (
        "CLOSED"
        if not failures
        else "OPEN"
    )

    return {
        "closure_status": status,
        "checks": checks,
        "failed_checks": failures,
        "enterprise": {
            "status": fresh_audit.get("status"),
            "consumer_entrypoint": consumer_entrypoint,
            "operational_sources": index.get(
                "manifest_sources"
            ),
            "legacy_files": base.get("legacy_files"),
            "legacy_records": base.get("legacy_records"),
            "acquisition_manifests": base.get(
                "special_acquisition"
            ),
            "external_blockers": base.get(
                "external_blockers"
            ),
            "checksum_covered_files": checksum_files,
            "zip_entries": zip_entries,
            "exact_duplicate_groups": duplicates.get(
                "exact_duplicate_groups"
            ),
            "shared_physical_groups": duplicates.get(
                "shared_physical_groups"
            ),
            "cross_physical_groups": duplicates.get(
                "cross_physical_groups"
            ),
            "zip_sha256": fresh_audit.get("zip_sha256"),
            "package_bytes": fresh_audit.get("package_bytes"),
            "zip_bytes": fresh_audit.get("zip_bytes"),
        },
        "note": (
            "ZIP entries = checksum-covered files + 1 because "
            "checksums.sha256 is intentionally excluded from hashing itself."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditor final de cierre B12."
    )
    parser.add_argument(
        "--enterprise-report",
        type=Path,
        default=DEFAULT_ENTERPRISE_REPORT,
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=DEFAULT_PACKAGE_DIR,
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=DEFAULT_ZIP,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    repo_root = Path(".").resolve()

    enterprise_report_path = (
        args.enterprise_report
        if args.enterprise_report.is_absolute()
        else repo_root / args.enterprise_report
    )
    package_dir = (
        args.package_dir
        if args.package_dir.is_absolute()
        else repo_root / args.package_dir
    )
    zip_path = (
        args.zip
        if args.zip.is_absolute()
        else repo_root / args.zip
    )
    output_path = (
        args.output
        if args.output.is_absolute()
        else repo_root / args.output
    )

    enterprise_report = load_json(
        enterprise_report_path
    )

    # Fresh offline validation of the package bytes that currently exist.
    fresh_audit = audit_enterprise_package(
        output_dir=package_dir,
        zip_path=zip_path,
    )

    report = evaluate(
        enterprise_report=enterprise_report,
        fresh_audit=fresh_audit,
    )
    report["generated_at"] = (
        datetime.now(timezone.utc).isoformat()
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    enterprise = report["enterprise"]

    print("=" * 78)
    print("B12 — FINAL ENTERPRISE DELIVERY CLOSURE")
    print("=" * 78)
    print(
        f"Closure:                 {report['closure_status']}"
    )
    print(
        "Enterprise status:       "
        f"{enterprise['status']}"
    )
    print(
        "Consumer entrypoint:     "
        f"{enterprise['consumer_entrypoint']}"
    )
    print(
        "Operational sources:     "
        f"{enterprise['operational_sources']}/41"
    )
    print(
        "Legacy files:            "
        f"{enterprise['legacy_files']}"
    )
    print(
        "Legacy records:          "
        f"{enterprise['legacy_records']}"
    )
    print(
        "Acquisition manifests:   "
        f"{enterprise['acquisition_manifests']}"
    )
    print(
        "External blockers:       "
        f"{enterprise['external_blockers']}"
    )
    print(
        "Checksum-covered files:  "
        f"{enterprise['checksum_covered_files']}"
    )
    print(
        "ZIP entries:             "
        f"{enterprise['zip_entries']}"
    )
    print(
        "Exact duplicate groups:  "
        f"{enterprise['exact_duplicate_groups']}"
    )
    print(
        "Shared-physical groups:  "
        f"{enterprise['shared_physical_groups']}"
    )
    print(
        "Cross-physical groups:   "
        f"{enterprise['cross_physical_groups']}"
    )
    print(
        "ZIP SHA256:              "
        f"{enterprise['zip_sha256']}"
    )

    print()
    print(report["note"])

    if report["failed_checks"]:
        print()
        print("FAILED CHECKS:")
        for check in report["failed_checks"]:
            print(f"  - {check}")

    try:
        shown = output_path.relative_to(repo_root)
    except ValueError:
        shown = output_path

    print(f"\nJSON: {shown}")

    return 0 if report["closure_status"] == "CLOSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
