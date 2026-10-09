from __future__ import annotations

import copy

from display_project.g3_statistical_input import (
    GATE_MANIFEST_SCHEMA,
    INPUT_SCHEMA,
    TASK_SPECIFIC_INPUT_SCHEMA,
    canonical_sha256,
    validate_g3_statistical_input,
)


METHODS = ("proposed", "fixed_streak", "hmm_or_crf")
TASKS = ("meccano", "industreal")
BRANCHES = ("A", "B")


def _input() -> dict:
    thresholds = [
        {"branch": branch, "task": task, "method": method, "threshold": 0.5}
        for branch in BRANCHES
        for task in TASKS
        for method in METHODS
    ]
    manifest = {
        "schema_version": GATE_MANIFEST_SCHEMA,
        "c_star": 0.7,
        "r_star": 0.1,
        "delta_c": 0.05,
        "delta_r": 0.02,
        "minimum_coverage": 0.5,
        "maximum_latency_seconds": 5.0,
        "common_support_minimum_width": 0.1,
        "common_support_lower": 0.5,
        "common_support_upper": 0.9,
        "maximum_differential_failure": 0.05,
        "selected_thresholds": thresholds,
    }
    manifest["artifact_sha256"] = canonical_sha256(manifest)
    opportunities = []
    for branch in BRANCHES:
        for task in TASKS:
            for method in METHODS:
                opportunities.append(
                    {
                        "opportunity_id": f"opp-{branch}-{task}-1",
                        "unit_id": f"unit-{task}-1",
                        "task": task,
                        "method": method,
                        "branch": branch,
                        "operator_id": f"operator-{task}-1",
                        "complete_job_id": f"job-{task}-1",
                        "threshold": 0.5,
                        "scorable": True,
                        "definitive": True,
                        "advanced": method != "proposed",
                        "false_advance": False,
                        "abstained": False,
                        "latency_seconds": 1.0,
                        "censoring_seconds": None,
                        "right_censored": False,
                        "run_failure": False,
                    }
                )
    return {
        "schema_version": INPUT_SCHEMA,
        "producer": {
            "fixture": False,
            "real_input_enabled": True,
            "code_sha256": "e" * 64,
            "environment_sha256": "a" * 64,
            "runtime_asset_sha256": {"aft_dll": "f" * 64},
        },
        "binding": {
            "admission_receipt_id": "batch:fixture",
            "admission_receipt_sha256": "b" * 64,
            "evidence_bundle_sha256": "c" * 64,
        },
        "resampling": {
            "requested_replicates": 10000,
            "seed": 20260820,
            "cluster_level": "operator",
        },
        "gate_manifest": manifest,
        "opportunities": opportunities,
    }


def test_complete_raw_paired_opportunity_contract_validates():
    valid, reasons = validate_g3_statistical_input(_input())
    assert valid, reasons


def test_caller_computed_p_values_or_branch_results_are_forbidden():
    value = _input()
    value["branch_B"] = {"adjusted_p_values": [0.001, 0.001]}
    valid, reasons = validate_g3_statistical_input(value)
    assert valid is False
    assert "unexpected_top_level_keys" in reasons


def test_each_opportunity_must_contain_all_three_paired_methods():
    value = _input()
    value["opportunities"].pop()
    valid, reasons = validate_g3_statistical_input(value)
    assert valid is False
    assert "two_task_three_method_two_branch_coverage" in reasons
    assert any(reason.startswith("unpaired_opportunity:") for reason in reasons)


def test_unknown_operator_and_unfrozen_threshold_fail_closed():
    value = _input()
    value["opportunities"][0]["operator_id"] = "UNKNOWN"
    value["opportunities"][1]["threshold"] = 0.7
    valid, reasons = validate_g3_statistical_input(value)
    assert valid is False
    assert "opportunity_identity:0:operator_id" in reasons
    assert "opportunity_threshold:1" in reasons


def test_ratified_industreal_complete_job_fallback_is_explicitly_scoped():
    value = _input()
    value["schema_version"] = TASK_SPECIFIC_INPUT_SCHEMA
    value["cluster_level_by_task"] = {"meccano": "operator", "industreal": "complete_job"}
    value["resampling"]["cluster_level"] = "task_specific"
    for row in value["opportunities"]:
        if row["task"] == "industreal":
            row["operator_id"] = "UNKNOWN"
    valid, reasons = validate_g3_statistical_input(value)
    assert valid, reasons


def test_complete_job_fallback_cannot_be_used_for_meccano_unknown_operator():
    value = _input()
    value["schema_version"] = TASK_SPECIFIC_INPUT_SCHEMA
    value["cluster_level_by_task"] = {"meccano": "complete_job", "industreal": "complete_job"}
    value["resampling"]["cluster_level"] = "task_specific"
    for row in value["opportunities"]:
        if row["task"] == "meccano":
            row["operator_id"] = "UNKNOWN"
    valid, reasons = validate_g3_statistical_input(value)
    assert valid is False
    assert "opportunity_identity:0:operator_id" in reasons


def test_false_advance_requires_an_actual_advance():
    value = _input()
    row = value["opportunities"][0]
    row.update(advanced=False, false_advance=True)
    valid, reasons = validate_g3_statistical_input(value)
    assert valid is False
    assert "false_advance_without_advance:0" in reasons


def test_gate_manifest_tamper_is_detected():
    value = _input()
    tampered = copy.deepcopy(value)
    tampered["gate_manifest"]["c_star"] = 0.9
    valid, reasons = validate_g3_statistical_input(tampered)
    assert valid is False
    assert "gate_manifest_hash" in reasons


def test_common_support_must_contain_target_and_meet_frozen_width():
    value = _input()
    value["gate_manifest"].update(
        common_support_lower=0.75,
        common_support_upper=0.80,
    )
    value["gate_manifest"]["artifact_sha256"] = canonical_sha256(
        value["gate_manifest"]
    )
    valid, reasons = validate_g3_statistical_input(value)
    assert valid is False
    assert "gate_manifest_common_support" in reasons


def test_right_censoring_requires_an_observed_followup_time():
    value = _input()
    row = value["opportunities"][0]
    row.update(
        definitive=False,
        advanced=False,
        abstained=True,
        latency_seconds=None,
        censoring_seconds=None,
        right_censored=True,
    )
    valid, reasons = validate_g3_statistical_input(value)
    assert valid is False
    assert "opportunity_censoring:0" in reasons

    row["censoring_seconds"] = 5.0
    valid, reasons = validate_g3_statistical_input(value)
    assert valid, reasons
