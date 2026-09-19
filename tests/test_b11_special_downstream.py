from __future__ import annotations

import json

from apps.b11_special_downstream.main import (
    build_transtats_manifest,
)


def test_build_transtats_manifest_preserves_acquisition_semantics(tmp_path):
    evidence = tmp_path / "output" / "b10-remediation" / "transtats"
    snapshots = evidence / "state" / "snapshots"
    snapshots.mkdir(parents=True)

    snapshot = {
        "source_id": "transtats",
        "run_id": "run_test",
        "total_resources": 1,
        "resources": [
            {
                "title": "US Bureau of Transportation Statistics",
                "url": (
                    "https://www.transtats.bts.gov/"
                    "DL_SelectFields.aspx?gnoyr_VQ=GED"
                ),
                "content_type": "text/html",
                "resource_type": "file",
                "discovery_method": "custom_form_acquisition_job",
            }
        ],
    }
    (snapshots / "transtats_run_test.json").write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    audit = {
        "sources": [
            {
                "logical_source_id": "transtats",
                "physical_source_id": "transtats",
                "workflow": "custom",
                "evidence_root": str(evidence),
            }
        ]
    }

    output = build_transtats_manifest(
        repo_root=tmp_path,
        audit=audit,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["decision"] == "ACQUISITION_JOB_READY"
    assert payload["kind"] == "download_form_acquisition_job"
    assert payload["execution_policy"]["allowed_methods"] == ["GET", "HEAD"]
    assert (
        payload["execution_policy"]["submission_policy"]
        == "metadata_only_no_post"
    )
    assert payload["legacy_projection"]["status"] == "NOT_APPLICABLE_YET"
