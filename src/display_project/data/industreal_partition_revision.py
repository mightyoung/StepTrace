"""Non-mutating split/ledger revision candidate for IndustReal complete jobs."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from display_project.data.industreal_complete_job_partition import (
    validate_candidate_partition,
)


SCHEMA = "display_project.industreal_partition_revision_candidate-1.0.0"
EDITABLE_SPLITS = {
    "DEVELOPMENT_TUNING",
    "CALIBRATION",
    "DEVELOPMENT_G3_EVALUATION",
}


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


def build_revision_candidate(
    partition: Mapping[str, Any], split_path: Path, ledger_path: Path
) -> dict[str, Any]:
    valid, reasons = validate_candidate_partition(partition)
    if not valid:
        raise ValueError("invalid partition candidate: " + ",".join(reasons))
    split = json.loads(split_path.read_text(encoding="utf-8-sig"))
    split_rows = split.get("records")
    if not isinstance(split_rows, list):
        raise ValueError("split manifest records are missing")
    with ledger_path.open(encoding="utf-8-sig", newline="") as stream:
        ledger_rows = list(csv.DictReader(stream))
    split_by_video = {
        str(row.get("video_id")): row
        for row in split_rows
        if str(row.get("source_dataset", "")).casefold() == "industreal"
    }
    ledger_by_root = {
        str(row.get("canonical_root_id")): row
        for row in ledger_rows
        if row.get("source_dataset") == "IndustReal"
        and row.get("record_type") == "CANONICAL_ROOT_VIDEO"
    }
    patches: list[dict[str, Any]] = []
    for row in partition["records"]:
        project_split = row["project_split"]
        if project_split not in EDITABLE_SPLITS:
            continue
        recording_id = row["recording_id"]
        split_row = split_by_video.get(recording_id)
        root_id = f"INDUSTREAL-{recording_id}"
        ledger_row = ledger_by_root.get(root_id)
        if split_row is None or ledger_row is None:
            raise ValueError(f"registered complete job is missing: {recording_id}")
        if (
            split_row.get("operator_id") != "UNKNOWN"
            or ledger_row.get("operator_id") != "UNKNOWN"
            or split_row.get("complete_job_id") != row["complete_job_id"]
            or ledger_row.get("complete_job_id") != row["complete_job_id"]
        ):
            raise ValueError(f"identity boundary mismatch: {recording_id}")
        patches.append(
            {
                "recording_id": recording_id,
                "canonical_root_id": root_id,
                "complete_job_id": row["complete_job_id"],
                "operator_id": "UNKNOWN",
                "operator_identity_status": "UNKNOWN_DISCLOSED",
                "cluster_level": "complete_job",
                "project_split": project_split,
                "split_patch": {
                    "project_split": project_split,
                    "independent_unit_eligible": "YES",
                    "formal_use_allowed": "NO",
                },
                "ledger_patch": {
                    "project_split": project_split,
                    "independent_unit_eligible": "YES",
                    "formal_use_allowed": "NO",
                    "split_locked": "YES",
                },
            }
        )
    body: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "CANDIDATE_ONLY",
        "apply_authorized": False,
        "g3_execution_authorized": False,
        "source_split_sha256": _sha256(split_path),
        "source_ledger_sha256": _sha256(ledger_path),
        "partition_artifact_sha256": partition["artifact_sha256"],
        "operator_identity_status": "UNKNOWN",
        "cluster_fallback": "canonical_complete_job",
        "patches": sorted(patches, key=lambda item: item["recording_id"]),
    }
    body["artifact_sha256"] = _canonical(body)
    return body


def validate_revision_candidate(value: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if value.get("schema_version") != SCHEMA:
        reasons.append("schema_version")
    supplied = value.get("artifact_sha256")
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    if supplied != _canonical(body):
        reasons.append("artifact_sha256")
    if value.get("status") != "CANDIDATE_ONLY":
        reasons.append("status")
    if value.get("apply_authorized") is not False:
        reasons.append("apply_authority")
    if value.get("g3_execution_authorized") is not False:
        reasons.append("g3_authority")
    patches = value.get("patches")
    if not isinstance(patches, list) or not patches:
        reasons.append("patches")
        return False, reasons
    ids = [row.get("recording_id") for row in patches if isinstance(row, Mapping)]
    if len(ids) != len(patches) or len(set(ids)) != len(ids):
        reasons.append("recording_ids")
    for row in patches:
        if not isinstance(row, Mapping):
            reasons.append("patch_schema")
            continue
        if row.get("operator_id") != "UNKNOWN":
            reasons.append("operator_identity_inferred")
        if row.get("operator_identity_status") != "UNKNOWN_DISCLOSED":
            reasons.append("operator_identity_status")
        if row.get("cluster_level") != "complete_job":
            reasons.append("cluster_level")
        if row.get("project_split") not in EDITABLE_SPLITS:
            reasons.append("sealed_or_unknown_split")
        for key in ("split_patch", "ledger_patch"):
            patch = row.get(key)
            if not isinstance(patch, Mapping):
                reasons.append(key)
                continue
            if patch.get("formal_use_allowed") != "NO":
                reasons.append("formal_use_escalation")
            if patch.get("independent_unit_eligible") != "YES":
                reasons.append("independent_unit_eligibility")
    return not reasons, list(dict.fromkeys(reasons))


def apply_revision_to_copies(
    revision: Mapping[str, Any],
    split_path: Path,
    ledger_path: Path,
    split_output: Path,
    ledger_output: Path,
) -> dict[str, Any]:
    """Apply a ratified candidate only to new versioned copies."""

    valid, reasons = validate_revision_candidate(revision)
    if not valid:
        raise ValueError("invalid revision candidate: " + ",".join(reasons))
    if split_output.resolve() == split_path.resolve() or ledger_output.resolve() == ledger_path.resolve():
        raise ValueError("refusing to overwrite authoritative split or ledger")
    split_payload = json.loads(split_path.read_text(encoding="utf-8-sig"))
    split_rows = split_payload.get("records")
    if not isinstance(split_rows, list):
        raise ValueError("split manifest records are missing")
    with ledger_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        ledger_rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    for field in ("cluster_level", "operator_identity_status", "truth_capability"):
        if field not in fieldnames:
            fieldnames.append(field)
    patches = {row["recording_id"]: row for row in revision["patches"]}
    changed = 0
    for row in split_rows:
        if row.get("source_dataset") not in {"IndustReal", "INDUSTREAL"}:
            continue
        patch = patches.get(str(row.get("video_id")))
        if patch is None:
            continue
        row.update(patch["split_patch"])
        row["cluster_level"] = "complete_job"
        row["operator_identity_status"] = "UNKNOWN_DISCLOSED"
        row["truth_capability"] = "ACTION_STEP_STATE_ONLY"
        changed += 1
    ledger_by_root = {
        str(row.get("canonical_root_id")): row
        for row in ledger_rows
        if row.get("source_dataset") == "IndustReal"
        and row.get("record_type") == "CANONICAL_ROOT_VIDEO"
    }
    for patch in patches.values():
        row = ledger_by_root.get(patch["canonical_root_id"])
        if row is None:
            raise ValueError(f"ledger row missing: {patch['canonical_root_id']}")
        row.update(patch["ledger_patch"])
        row["cluster_level"] = "complete_job"
        row["operator_identity_status"] = "UNKNOWN_DISCLOSED"
        row["truth_capability"] = "ACTION_STEP_STATE_ONLY"
    if changed != len(patches):
        raise ValueError(f"split patch count mismatch: {changed} != {len(patches)}")
    split_output.parent.mkdir(parents=True, exist_ok=True)
    ledger_output.parent.mkdir(parents=True, exist_ok=True)
    split_output.write_text(
        json.dumps(split_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with ledger_output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ledger_rows)
    return {
        "changed_records": changed,
        "source_split_sha256": _sha256(split_path),
        "source_ledger_sha256": _sha256(ledger_path),
        "output_split_sha256": _sha256(split_output),
        "output_ledger_sha256": _sha256(ledger_output),
    }
