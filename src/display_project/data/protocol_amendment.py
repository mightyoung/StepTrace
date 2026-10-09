"""Versioned protocol-amendment records for the IndustReal identity boundary.

The draft artifact is intentionally non-authorizing.  A separate ratified
artifact records the corresponding-author decision and the hashes of the new
non-authoritative copies to which the amendment was applied.  It never grants
pilot, formal-test, or operator-generalization authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


SCHEMA = "display_project.protocol_amendment_draft-1.0.0"
RATIFIED_SCHEMA = "display_project.protocol_amendment_ratified-1.0.0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def build_industreal_identity_amendment(
    *,
    claim_freeze_path: Path,
    analysis_plan_path: Path,
    partition_candidate_path: Path,
    revision_candidate_path: Path,
    capability_path: Path,
) -> dict[str, Any]:
    partition = json.loads(partition_candidate_path.read_text(encoding="utf-8"))
    revision = json.loads(revision_candidate_path.read_text(encoding="utf-8"))
    capability = json.loads(capability_path.read_text(encoding="utf-8"))
    if partition.get("g3_execution_authorized") is not False:
        raise ValueError("partition candidate already escalates authority")
    if revision.get("apply_authorized") is not False:
        raise ValueError("revision candidate already escalates authority")
    if capability.get("capability_status") != "ACTION_STEP_STATE_ONLY":
        raise ValueError("IndustReal capability boundary is not action/step/state only")
    body: dict[str, Any] = {
        "schema_version": SCHEMA,
        "draft_version": "1.4.0-draft",
        "supersedes": "1.3.0",
        "status": "DRAFT_PENDING_CORRESPONDING_AUTHOR_RATIFICATION",
        "approval_status": "USER_AUTHORIZED_DEVELOPMENT_DRAFT_ONLY",
        "corresponding_author_ratification": "REQUIRED_BEFORE_APPLY_OR_FORMAL_UNSEAL",
        "pilot_data_accessed": False,
        "formal_data_accessed": False,
        "affected_claims": ["C1", "C3"],
        "affected_endpoints": [
            "FSAR_MATCHED_COVERAGE",
            "COVERAGE_MATCHED_FSAR",
        ],
        "rationale": (
            "IndustReal has 84 action-labeled complete recordings but no verified "
            "operator mapping. The amendment permits a disclosed complete-job "
            "cluster fallback for calibration and DEVELOPMENT_G3_EVALUATION only; "
            "it does not infer operator identity or authorize pilot/formal use."
        ),
        "proposed_contract_changes": {
            "unknown_operator_rule": (
                "IndustReal operator_id remains UNKNOWN; only complete_job may be "
                "the inferential cluster, with operator-generalization limitation."
            ),
            "allowed_project_splits": [
                "DEVELOPMENT_TUNING",
                "CALIBRATION",
                "DEVELOPMENT_G3_EVALUATION",
            ],
            "forbidden_project_splits": ["PILOT", "FORMAL_TEST_SEALED", "FORMAL"],
            "truth_boundary": "ACTION_STEP_STATE_ONLY",
            "minimum_complete_jobs": 2,
            "operator_generalization_claim": "PROHIBITED",
        },
        "source_bindings": {
            "claim_freeze_sha256": _sha256(claim_freeze_path),
            "analysis_plan_sha256": _sha256(analysis_plan_path),
            "industreal_capability_sha256": _sha256(capability_path),
            "partition_candidate_sha256": _sha256(partition_candidate_path),
            "revision_candidate_sha256": _sha256(revision_candidate_path),
        },
        "apply_authorized": False,
        "g3_execution_authorized": False,
    }
    body["artifact_sha256"] = _canonical(body)
    return body


def validate_amendment_draft(value: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if value.get("schema_version") != SCHEMA:
        reasons.append("schema_version")
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    if value.get("artifact_sha256") != _canonical(body):
        reasons.append("artifact_sha256")
    if value.get("status") != "DRAFT_PENDING_CORRESPONDING_AUTHOR_RATIFICATION":
        reasons.append("status")
    if value.get("approval_status") != "USER_AUTHORIZED_DEVELOPMENT_DRAFT_ONLY":
        reasons.append("approval_status")
    if value.get("corresponding_author_ratification") != "REQUIRED_BEFORE_APPLY_OR_FORMAL_UNSEAL":
        reasons.append("ratification")
    if value.get("pilot_data_accessed") is not False:
        reasons.append("pilot_data_accessed")
    if value.get("formal_data_accessed") is not False:
        reasons.append("formal_data_accessed")
    if value.get("apply_authorized") is not False:
        reasons.append("apply_authority")
    if value.get("g3_execution_authorized") is not False:
        reasons.append("g3_authority")
    changes = value.get("proposed_contract_changes")
    if not isinstance(changes, Mapping):
        reasons.append("proposed_contract_changes")
    else:
        if changes.get("truth_boundary") != "ACTION_STEP_STATE_ONLY":
            reasons.append("truth_boundary")
        if changes.get("operator_generalization_claim") != "PROHIBITED":
            reasons.append("operator_generalization_claim")
        if changes.get("minimum_complete_jobs") != 2:
            reasons.append("minimum_complete_jobs")
        if "FORMAL" not in " ".join(changes.get("forbidden_project_splits", [])):
            reasons.append("formal_forbidden")
    bindings = value.get("source_bindings")
    if not isinstance(bindings, Mapping) or any(
        not isinstance(item, str) or len(item) != 64 for item in bindings.values()
    ):
        reasons.append("source_bindings")
    return not reasons, list(dict.fromkeys(reasons))


def ratify_industreal_identity_amendment(
    draft: Mapping[str, Any],
    *,
    ratified_on: str,
    applied_outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a hash-bound ratification record without authorizing G3.

    ``applied_outputs`` must describe only versioned split/ledger copies.  The
    authoritative source files are never rewritten by this function, and the
    resulting record is deliberately not accepted as a formal-test authority.
    """

    valid, reasons = validate_amendment_draft(draft)
    if not valid:
        raise ValueError("invalid amendment draft: " + ",".join(reasons))
    if not ratified_on or len(ratified_on) != 10:
        raise ValueError("ratified_on must be an ISO date")
    required_outputs = {
        "output_split_sha256",
        "output_ledger_sha256",
        "changed_records",
    }
    if not required_outputs <= set(applied_outputs):
        raise ValueError("applied_outputs is missing versioned-copy evidence")
    for key in ("output_split_sha256", "output_ledger_sha256"):
        value = str(applied_outputs[key])
        if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
            raise ValueError(f"invalid {key}")
    changed = int(applied_outputs["changed_records"])
    if changed < 1:
        raise ValueError("ratification must bind at least one changed record")
    body: dict[str, Any] = {
        "schema_version": RATIFIED_SCHEMA,
        "amendment_version": "1.4.0",
        "supersedes": draft["draft_version"],
        "status": "RATIFIED_APPLIED_TO_VERSIONED_COPIES",
        "approval_status": "CORRESPONDING_AUTHOR_RATIFIED",
        "corresponding_author_ratification": "RATIFIED",
        "ratified_on": ratified_on,
        "pilot_data_accessed": False,
        "formal_data_accessed": False,
        "apply_authorized": True,
        "application_scope": "VERSIONED_NON_AUTHORITATIVE_COPIES_ONLY",
        "g3_execution_authorized": False,
        "operator_generalization_claim": "PROHIBITED",
        "truth_boundary": "ACTION_STEP_STATE_ONLY",
        "source_bindings": dict(draft["source_bindings"]),
        "applied_outputs": dict(applied_outputs),
        "draft_artifact_sha256": draft["artifact_sha256"],
    }
    body["artifact_sha256"] = _canonical(body)
    return body


def validate_ratified_amendment(value: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Validate a ratification record and keep formal authority fail-closed."""

    reasons: list[str] = []
    if value.get("schema_version") != RATIFIED_SCHEMA:
        reasons.append("schema_version")
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    if value.get("artifact_sha256") != _canonical(body):
        reasons.append("artifact_sha256")
    if value.get("status") != "RATIFIED_APPLIED_TO_VERSIONED_COPIES":
        reasons.append("status")
    if value.get("approval_status") != "CORRESPONDING_AUTHOR_RATIFIED":
        reasons.append("approval_status")
    if value.get("corresponding_author_ratification") != "RATIFIED":
        reasons.append("ratification")
    if value.get("apply_authorized") is not True:
        reasons.append("apply_authority")
    if value.get("application_scope") != "VERSIONED_NON_AUTHORITATIVE_COPIES_ONLY":
        reasons.append("application_scope")
    if value.get("g3_execution_authorized") is not False:
        reasons.append("g3_authority")
    if value.get("pilot_data_accessed") is not False:
        reasons.append("pilot_data_accessed")
    if value.get("formal_data_accessed") is not False:
        reasons.append("formal_data_accessed")
    if value.get("operator_generalization_claim") != "PROHIBITED":
        reasons.append("operator_generalization_claim")
    if value.get("truth_boundary") != "ACTION_STEP_STATE_ONLY":
        reasons.append("truth_boundary")
    outputs = value.get("applied_outputs")
    if not isinstance(outputs, Mapping):
        reasons.append("applied_outputs")
    else:
        for key in ("output_split_sha256", "output_ledger_sha256"):
            raw = str(outputs.get(key, ""))
            if len(raw) != 64 or any(char not in "0123456789abcdefABCDEF" for char in raw):
                reasons.append(key)
        try:
            if int(outputs.get("changed_records", 0)) < 1:
                reasons.append("changed_records")
        except (TypeError, ValueError):
            reasons.append("changed_records")
    bindings = value.get("source_bindings")
    if not isinstance(bindings, Mapping) or any(
        not isinstance(item, str) or len(item) != 64 for item in bindings.values()
    ):
        reasons.append("source_bindings")
    return not reasons, list(dict.fromkeys(reasons))
