import json
import hashlib
from pathlib import Path

from display_project.data.industreal_complete_job_partition import (
    build_candidate_partition,
    validate_candidate_partition,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "research" / "audits" / "industreal-registry.json"
REMOTE = (
    ROOT
    / "research"
    / "audits"
    / "industreal-complete-job-partition.remote.json"
)


def _registry():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_candidate_partition_is_deterministic_complete_job_fallback():
    first = build_candidate_partition(_registry())
    second = build_candidate_partition(_registry())
    assert first == second
    valid, reasons = validate_candidate_partition(first)
    assert valid is True, reasons
    assert len(first["records"]) == 84
    assert first["excluded_archive_only_recording_ids"] == [
        "15_main_0_1",
        "21_assy_1_1",
    ]
    assert first["g3_execution_authorized"] is False
    assert first["authoritative_ledger_mutation_allowed"] is False


def test_group_tokens_never_cross_candidate_partitions():
    artifact = build_candidate_partition(_registry())
    groups = {}
    for row in artifact["records"]:
        groups.setdefault(row["unverified_group_token"], set()).add(
            row["project_split"]
        )
        assert row["operator_id"] == "UNKNOWN"
        assert row["cluster_level"] == "complete_job"
    assert all(len(splits) == 1 for splits in groups.values())


def test_official_test_stays_sealed_and_val_supplies_calibration_and_g3():
    rows = build_candidate_partition(_registry())["records"]
    assert {
        row["project_split"] for row in rows if row["official_split"] == "test"
    } == {"FORMAL_TEST_SEALED"}
    assert {
        row["project_split"] for row in rows if row["official_split"] == "val"
    } == {"CALIBRATION", "DEVELOPMENT_G3_EVALUATION"}


def test_validator_rejects_group_leakage_and_authority_escalation():
    artifact = build_candidate_partition(_registry())
    token = artifact["records"][0]["unverified_group_token"]
    rows = [row for row in artifact["records"] if row["unverified_group_token"] == token]
    if len(rows) > 1:
        rows[0]["project_split"] = "CALIBRATION"
        rows[1]["project_split"] = "DEVELOPMENT_G3_EVALUATION"
    artifact["g3_execution_authorized"] = True
    valid, reasons = validate_candidate_partition(artifact)
    assert valid is False
    assert "artifact_sha256" in reasons
    assert "g3_authority" in reasons
    if len(rows) > 1:
        assert "group_leakage" in reasons


def test_remote_registry_rebuild_matches_pinned_candidate():
    local = ROOT / "research" / "audits" / "industreal-complete-job-partition.candidate.json"
    assert REMOTE.read_bytes() == local.read_bytes()
    assert hashlib.sha256(REMOTE.read_bytes()).hexdigest() == (
        "3096d2ce3a8ea9e2673fc16a67b88c77c4f7df65efb601d930b62ae59a80f324"
    )
    valid, reasons = validate_candidate_partition(
        json.loads(REMOTE.read_text(encoding="utf-8"))
    )
    assert valid is True, reasons
