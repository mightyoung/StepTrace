import json
from pathlib import Path

from display_project.data.protocol_amendment import (
    build_industreal_identity_amendment,
    ratify_industreal_identity_amendment,
    validate_amendment_draft,
    validate_ratified_amendment,
)


ROOT = Path(__file__).resolve().parents[1]


def _build():
    return build_industreal_identity_amendment(
        claim_freeze_path=ROOT / "research/claim-freeze.yaml",
        analysis_plan_path=ROOT / "research/analysis-plan.md",
        partition_candidate_path=ROOT / "research/audits/industreal-complete-job-partition.candidate.json",
        revision_candidate_path=ROOT / "research/audits/industreal-partition-revision.candidate.json",
        capability_path=ROOT / "research/audits/industreal-capability.json",
    )


def test_amendment_is_draft_only_and_binds_all_sources():
    artifact = _build()
    valid, reasons = validate_amendment_draft(artifact)
    assert valid is True, reasons
    assert artifact["draft_version"] == "1.4.0-draft"
    assert artifact["apply_authorized"] is False
    assert artifact["g3_execution_authorized"] is False
    assert artifact["pilot_data_accessed"] is False
    assert artifact["formal_data_accessed"] is False
    assert len(artifact["source_bindings"]) == 5


def test_amendment_cannot_be_relabelled_as_ratified_or_formal():
    artifact = _build()
    artifact["status"] = "RATIFIED"
    artifact["apply_authorized"] = True
    artifact["proposed_contract_changes"]["operator_generalization_claim"] = "ALLOWED"
    valid, reasons = validate_amendment_draft(artifact)
    assert valid is False
    assert "artifact_sha256" in reasons
    assert "status" in reasons
    assert "apply_authority" in reasons
    assert "operator_generalization_claim" in reasons


def test_corresponding_author_ratification_is_versioned_and_still_not_g3_authority():
    draft = _build()
    ratified = ratify_industreal_identity_amendment(
        draft,
        ratified_on="2026-08-21",
        applied_outputs={
            "changed_records": 52,
            "output_split_sha256": "a" * 64,
            "output_ledger_sha256": "b" * 64,
        },
    )
    valid, reasons = validate_ratified_amendment(ratified)
    assert valid is True, reasons
    assert ratified["approval_status"] == "CORRESPONDING_AUTHOR_RATIFIED"
    assert ratified["application_scope"] == "VERSIONED_NON_AUTHORITATIVE_COPIES_ONLY"
    assert ratified["apply_authorized"] is True
    assert ratified["g3_execution_authorized"] is False


def test_ratified_artifact_cannot_enable_formal_or_operator_generalization():
    draft = _build()
    ratified = ratify_industreal_identity_amendment(
        draft,
        ratified_on="2026-08-21",
        applied_outputs={
            "changed_records": 52,
            "output_split_sha256": "a" * 64,
            "output_ledger_sha256": "b" * 64,
        },
    )
    ratified["g3_execution_authorized"] = True
    ratified["operator_generalization_claim"] = "ALLOWED"
    valid, reasons = validate_ratified_amendment(ratified)
    assert valid is False
    assert "artifact_sha256" in reasons
    assert "g3_authority" in reasons
    assert "operator_generalization_claim" in reasons
