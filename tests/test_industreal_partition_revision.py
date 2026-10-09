import json
import hashlib
from pathlib import Path

from display_project.data.industreal_partition_revision import (
    build_revision_candidate,
    validate_revision_candidate,
)


ROOT = Path(__file__).resolve().parents[1]


def _build():
    partition = json.loads(
        (ROOT / "research/audits/industreal-complete-job-partition.candidate.json")
        .read_text(encoding="utf-8")
    )
    return build_revision_candidate(
        partition,
        ROOT / "research/split-manifest.json",
        ROOT / "research/dataset-ledger.csv",
    )


def test_revision_is_non_mutating_and_excludes_sealed_test():
    artifact = _build()
    valid, reasons = validate_revision_candidate(artifact)
    assert valid is True, reasons
    assert artifact["apply_authorized"] is False
    assert artifact["g3_execution_authorized"] is False
    assert len(artifact["patches"]) == 52
    assert {row["project_split"] for row in artifact["patches"]} == {
        "DEVELOPMENT_TUNING",
        "CALIBRATION",
        "DEVELOPMENT_G3_EVALUATION",
    }
    assert all(row["operator_id"] == "UNKNOWN" for row in artifact["patches"])
    assert all(row["cluster_level"] == "complete_job" for row in artifact["patches"])


def test_revision_validator_rejects_formal_escalation():
    artifact = _build()
    artifact["patches"][0]["ledger_patch"]["formal_use_allowed"] = "YES"
    artifact["apply_authorized"] = True
    valid, reasons = validate_revision_candidate(artifact)
    assert valid is False
    assert "artifact_sha256" in reasons
    assert "apply_authority" in reasons
    assert "formal_use_escalation" in reasons


def test_remote_revision_rebuild_matches_local_source_hashes():
    local = ROOT / "research/audits/industreal-partition-revision.candidate.json"
    remote = ROOT / "research/audits/industreal-partition-revision.remote.json"
    assert remote.read_bytes() == local.read_bytes()
    assert hashlib.sha256(remote.read_bytes()).hexdigest() == (
        "6843f23d7edc9d88c7bbf46aa47303b35b6972760f9cad9e285f0bd1dd7a3212"
    )
    valid, reasons = validate_revision_candidate(
        json.loads(remote.read_text(encoding="utf-8"))
    )
    assert valid is True, reasons
