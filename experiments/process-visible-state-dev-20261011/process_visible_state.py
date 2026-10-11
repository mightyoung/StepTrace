"""Offline comparison of cached process-capture and current-state claims.

This reads only the pinned public AI-answer cache and deterministic audit code.
It performs no vision inference and is not wired into production or scoring.
"""
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
CACHE = ROOT / "experiments/real-reappearance-dev-20261010"
CACHE_SOURCE_COMMIT = "955d2bae7f047c5e699caa90ac094b74b9b738e3"
ANALYSIS_PARENT_COMMIT = "64e62dbff44e766551479f080e2a7ba60c613e83"
EXPECTED_CACHE_MANIFEST_SHA256 = "4c7638d6dd90119915513af511f19c3b8890e32106a9b911b7a850c9bfebc38c"


def load_claim_audit():
    path = ROOT / "experiments/claim-applicability-dev-20261011/audit.py"
    spec = importlib.util.spec_from_file_location("pinned_claim_applicability_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("claim_applicability_audit_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process_evidence(record):
    """Keep missing process input unknown; a result image is not a process trace."""
    captures = [row.get("process_capture") for row in record.get("historical_connections", [])]
    if captures and all(value == "missedprocess" for value in captures):
        input_scope = "process_not_supplied"
        explanation = "The cached answer labels the sampled input as lacking installation-process evidence."
    elif captures and all(value == "installation_observed" for value in captures):
        input_scope = "record_claims_process_observed"
        explanation = "This is a cached answer label, not independently verified process ground truth."
    else:
        input_scope = "unknown"
        explanation = "The cache does not establish a process observation status."
    return {
        "input_scope": input_scope,
        "recorded_process_capture_labels": captures,
        "step_completion": "unknown",
        "step_mapping": None,
        "explanation": explanation,
        "scope": "cached_answer_record_only",
    }


def summarize_record(packet_id, record, reference_events, audit):
    applicability = audit.adapt(record, reference_events, audit.PROVENANCE)
    direct = record["last_observation"]
    estimate = record["current_estimate"]
    process = process_evidence(record)
    visible = {
        "frame_id": direct["frame_id"],
        "direct_relation_by_record": direct["relation_state"],
        "estimate_relation_by_record": estimate["relation_state"],
        "estimate_basis": estimate["basis"],
        "claim_applicability": applicability["label"],
        "current_visible_confirmation_eligible": applicability[
            "current_visible_confirmation_eligible"
        ],
        "assumptions": deepcopy(estimate["assumptions"]),
        "historical_connections": deepcopy(record["historical_connections"]),
        "active_connection_ids": deepcopy(estimate["active_connection_ids"]),
        "scope": "cached_AI_answer_record_only_not_pixel_or_physical_ground_truth",
    }
    frame_labels = {item["frame_id"]: item for item in applicability["frame_labels"]}
    frame_history = [
        {
            "frame_id": observed["frame_id"],
            "pts": frame_labels[observed["frame_id"]]["pts"],
            "frame_sha256": frame_labels[observed["frame_id"]]["frame_sha256"],
            "relation_state": observed["relation_state"],
            "identity_status": observed["identity_status"],
            "origin": "cached_AI_answer_record",
            "human_gt": False,
        }
        for observed in record["frame_observations"]
    ]
    return {
        "packet_id": packet_id,
        "source_record_sha256": hashlib.sha256(
            (CACHE / "model_outputs" / f"{packet_id}.json").read_bytes()
        ).hexdigest(),
        "split": "development_one_source_event_related_prefix",
        "process_evidence": process,
        "current_visible_state": visible,
        "frame_history": frame_history,
        "combined_control": {
            "process_step_status": process["step_completion"],
            "current_relation_status": applicability["label"],
            "eligible_for_current_endpoint_confirmation": applicability[
                "current_visible_confirmation_eligible"
            ],
            "eligible_for_process_step_completion": False,
            "reason": "No authorized process-step mapping or process ground truth is present.",
        },
        "ground_truth": {
            "origin": "cached_AI_answer_record",
            "human_verified": False,
            "physical_state": "unknown",
            "mechanical_firmness": record["mechanical_firmness"],
            "safety": record["safety"],
        },
    }


def synthetic_control(case):
    """Apply a tiny stated rule to an explicitly synthetic event sequence."""
    historical_fact = None
    history_status = "none"
    current = "unknown"
    current_target_supported = False
    for event in case["observations"]:
        same_target = event["target_id"] == case["target_id"]
        same_identity = event["identity_status"] == "supported"
        if not same_target or not same_identity:
            current = "unknown"
            current_target_supported = False
            if historical_fact is not None:
                history_status = "retained_not_transferred"
            continue
        current_target_supported = True
        state = event["relation_state"]
        if state == "external_connection_supported":
            if historical_fact is None:
                historical_fact = {
                    "target_id": case["target_id"],
                    "event_id": event["event_id"],
                    "kind": "synthetic_historical_external_relation",
                }
            current = state
            history_status = "retained_currently_supported"
        elif state == "occluded_or_unknown":
            current = "unknown"
            if historical_fact is not None:
                history_status = (
                    "retained_conditional" if case.get("assumptions") else "retained_not_current"
                )
        elif state == "separation_supported":
            current = state
            if historical_fact is not None:
                history_status = "retained_revoked"
        else:
            raise ValueError("unsupported_synthetic_relation_state")
    return {
        "case_id": case["case_id"],
        "kind": "synthetic_logic_only",
        "counted_as_real_sample": False,
        "uses_media": False,
        "current_direct_state": current,
        "current_target_identity_supported": current_target_supported,
        "history_status": history_status,
        "historical_fact_retained": historical_fact is not None,
        "process_step_status": "unknown",
        "human_gt": False,
        "physical_truth": "unknown",
    }


def synthetic_cases():
    return [
        {
            "case_id": "synthetic_occlusion",
            "target_id": "part_a",
            "assumptions": ["same target object", "no observed intervening separation"],
            "observations": [
                {"event_id": "syn-o1", "target_id": "part_a", "identity_status": "supported",
                 "relation_state": "external_connection_supported"},
                {"event_id": "syn-o2", "target_id": "part_a", "identity_status": "supported",
                 "relation_state": "occluded_or_unknown"},
            ],
        },
        {
            "case_id": "synthetic_removal",
            "target_id": "part_a",
            "assumptions": [],
            "observations": [
                {"event_id": "syn-r1", "target_id": "part_a", "identity_status": "supported",
                 "relation_state": "external_connection_supported"},
                {"event_id": "syn-r2", "target_id": "part_a", "identity_status": "supported",
                 "relation_state": "separation_supported"},
            ],
        },
        {
            "case_id": "synthetic_identity_change",
            "target_id": "part_a",
            "assumptions": [],
            "observations": [
                {"event_id": "syn-i1", "target_id": "part_a", "identity_status": "supported",
                 "relation_state": "external_connection_supported"},
                {"event_id": "syn-i2", "target_id": "part_b", "identity_status": "different",
                 "relation_state": "external_connection_supported"},
            ],
        },
    ]


def run():
    audit = load_claim_audit()
    records, events, verified = audit.load_cache()
    manifest_bytes = (CACHE / "public_manifest.json").read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != EXPECTED_CACHE_MANIFEST_SHA256:
        raise ValueError("public_cache_manifest_pin_mismatch")
    rows = {
        packet: summarize_record(packet, records[packet], events[packet], audit)
        for packet in ("q31", "q68", "q92")
    }
    first, occluded = rows["q31"], rows["q68"]
    first_lossy = audit.lossy_projection(records["q31"])
    occluded_lossy = audit.lossy_projection(records["q68"])
    first_rich = {
        "direct_relation": first["current_visible_state"]["direct_relation_by_record"],
        "basis": first["current_visible_state"]["estimate_basis"],
        "applicability": first["current_visible_state"]["claim_applicability"],
        "assumptions": first["current_visible_state"]["assumptions"],
    }
    occluded_rich = {
        "direct_relation": occluded["current_visible_state"]["direct_relation_by_record"],
        "basis": occluded["current_visible_state"]["estimate_basis"],
        "applicability": occluded["current_visible_state"]["claim_applicability"],
        "assumptions": occluded["current_visible_state"]["assumptions"],
    }
    synthetic = [synthetic_control(case) for case in synthetic_cases()]
    frame_keys = {
        (frame["frame_id"], frame["frame_sha256"])
        for row in rows.values()
        for frame in row["frame_history"]
    }
    return {
        "schema_version": "process-visible-state-offline-control/1",
        "analysis_parent_commit": ANALYSIS_PARENT_COMMIT,
        "cache_source_commit": CACHE_SOURCE_COMMIT,
        "cache_manifest_sha256": manifest_sha256,
        "verified_manifest_entries": len(verified),
        "records": rows,
        "comparison": {
            "q31_q68_lossy_projection": {
                "q31": first_lossy,
                "q68": occluded_lossy,
                "collision": first_lossy == occluded_lossy,
            },
            "q31_q68_lossy_projection_collision": first_lossy == occluded_lossy,
            "q31_q68_evidence_qualified_projection": {
                "q31": first_rich,
                "q68": occluded_rich,
                "collision": first_rich == occluded_rich,
            },
            "q31_q68_evidence_qualified_collision": first_rich == occluded_rich,
            "process_completion_unknown_for_both": (
                first["process_evidence"]["step_completion"] == "unknown"
                and occluded["process_evidence"]["step_completion"] == "unknown"
            ),
            "interpretation": (
                "Relation plus active-history projection collides; current-frame evidence and basis "
                "separate direct record support from conditional history. Both process-step statuses "
                "remain unknown because no process trace or step mapping is supplied."
            ),
        },
        "sampling": {
            "source_events": 1,
            "related_prefixes": len(rows),
            "unique_cached_frames": len(frame_keys),
            "repeated_observation_rows": sum(len(row["frame_history"]) for row in rows.values()),
        },
        "synthetic_controls": synthetic,
        "metrics": {
            "recognition_accuracy": None,
            "process_step_accuracy": None,
            "physical_false_revocation_rate": None,
            "mechanism_gain": None,
            "human_ground_truth_count": 0,
        },
        "new_model_calls": 0,
        "new_training_runs": 0,
        "new_media_downloads": 0,
        "production_wired": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
