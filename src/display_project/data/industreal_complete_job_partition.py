"""Candidate-only IndustReal partitioning when operator identity is unavailable."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


SCHEMA = "display_project.industreal_complete_job_partition_candidate-1.0.0"
SEED = 20260821
PROJECT_SPLITS = {
    "train": "DEVELOPMENT_TUNING",
    "test": "FORMAL_TEST_SEALED",
}


def canonical_sha256(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _val_assignment(tokens: set[str], seed: int) -> dict[str, str]:
    ordered = sorted(
        tokens,
        key=lambda token: hashlib.sha256(f"{seed}:{token}".encode()).hexdigest(),
    )
    if len(ordered) < 2:
        raise ValueError("at least two validation grouping tokens are required")
    calibration_count = (len(ordered) + 1) // 2
    return {
        token: (
            "CALIBRATION" if index < calibration_count else "DEVELOPMENT_G3_EVALUATION"
        )
        for index, token in enumerate(ordered)
    }


def build_candidate_partition(
    registry: Mapping[str, Any], *, seed: int = SEED
) -> dict[str, Any]:
    if registry.get("schema_version") != "industreal-registry-1.1.0":
        raise ValueError("registry schema is not supported")
    records = registry.get("records")
    if not isinstance(records, list) or len(records) != 84:
        raise ValueError("exactly 84 action-labeled records are required")
    if registry.get("action_labeled_recording_count") != len(records):
        raise ValueError("action-label count does not match records")
    archive_only = registry.get("archive_only_recording_ids")
    if not isinstance(archive_only, list) or len(archive_only) != 2:
        raise ValueError("exactly two archive-only recordings must be declared")
    recording_ids = [row.get("recording_id") for row in records]
    complete_jobs = [row.get("complete_job_id") for row in records]
    if len(set(recording_ids)) != len(records) or len(set(complete_jobs)) != len(records):
        raise ValueError("recording and complete-job identities must be unique")
    if set(recording_ids) & set(archive_only):
        raise ValueError("archive-only recordings cannot have action labels")
    official_splits = {row.get("official_split") for row in records}
    if official_splits != {"train", "val", "test"}:
        raise ValueError("official train/val/test coverage is required")
    val_tokens = {
        str(row.get("operator_token"))
        for row in records
        if row.get("official_split") == "val"
    }
    val_assignment = _val_assignment(val_tokens, seed)
    partition_rows: list[dict[str, Any]] = []
    for row in records:
        official_split = str(row["official_split"])
        group_token = str(row.get("operator_token"))
        project_split = (
            val_assignment[group_token]
            if official_split == "val"
            else PROJECT_SPLITS[official_split]
        )
        partition_rows.append(
            {
                "recording_id": str(row["recording_id"]),
                "complete_job_id": str(row["complete_job_id"]),
                "official_split": official_split,
                "unverified_group_token": group_token,
                "group_token_use": "LEAKAGE_PREVENTION_ONLY_NOT_OPERATOR_IDENTITY",
                "operator_id": "UNKNOWN",
                "cluster_level": "complete_job",
                "project_split": project_split,
                "truth_capability": "ACTION_STEP_STATE_ONLY",
            }
        )
    body: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "CANDIDATE_ONLY",
        "seed": seed,
        "source_registry_canonical_sha256": registry.get("canonical_sha256"),
        "operator_identity_status": "UNKNOWN",
        "cluster_fallback": "canonical_complete_job",
        "operator_generalization_limitation_required": True,
        "authoritative_ledger_mutation_allowed": False,
        "g3_execution_authorized": False,
        "excluded_archive_only_recording_ids": sorted(archive_only),
        "records": sorted(partition_rows, key=lambda row: row["recording_id"]),
    }
    counts: dict[str, int] = {}
    for row in partition_rows:
        counts[row["project_split"]] = counts.get(row["project_split"], 0) + 1
    body["counts"] = dict(sorted(counts.items()))
    body["artifact_sha256"] = canonical_sha256(body)
    return body


def validate_candidate_partition(value: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if value.get("schema_version") != SCHEMA:
        reasons.append("schema_version")
    supplied = value.get("artifact_sha256")
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    if supplied != canonical_sha256(body):
        reasons.append("artifact_sha256")
    if value.get("status") != "CANDIDATE_ONLY":
        reasons.append("status")
    if value.get("authoritative_ledger_mutation_allowed") is not False:
        reasons.append("ledger_authority")
    if value.get("g3_execution_authorized") is not False:
        reasons.append("g3_authority")
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) != 84:
        reasons.append("records")
        return False, reasons
    required = {
        "recording_id", "complete_job_id", "official_split",
        "unverified_group_token", "group_token_use", "operator_id",
        "cluster_level", "project_split", "truth_capability",
    }
    if any(not isinstance(row, Mapping) or set(row) != required for row in rows):
        reasons.append("record_schema")
        return False, reasons
    if any(row["operator_id"] != "UNKNOWN" for row in rows):
        reasons.append("operator_identity_inferred")
    if any(row["cluster_level"] != "complete_job" for row in rows):
        reasons.append("cluster_level")
    if any(row["truth_capability"] != "ACTION_STEP_STATE_ONLY" for row in rows):
        reasons.append("truth_capability")
    groups: dict[str, set[str]] = {}
    for row in rows:
        groups.setdefault(str(row["unverified_group_token"]), set()).add(
            str(row["project_split"])
        )
    if any(len(splits) != 1 for splits in groups.values()):
        reasons.append("group_leakage")
    if any(
        row["official_split"] == "test"
        and row["project_split"] != "FORMAL_TEST_SEALED"
        for row in rows
    ):
        reasons.append("official_test_unsealed")
    if any(
        row["official_split"] == "train"
        and row["project_split"] != "DEVELOPMENT_TUNING"
        for row in rows
    ):
        reasons.append("official_train_reassigned")
    val_splits = {
        row["project_split"] for row in rows if row["official_split"] == "val"
    }
    if val_splits != {"CALIBRATION", "DEVELOPMENT_G3_EVALUATION"}:
        reasons.append("validation_partition_coverage")
    return not reasons, reasons
