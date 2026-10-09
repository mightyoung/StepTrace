from __future__ import annotations

import copy

from display_project.g3_statistics import (
    STATISTICAL_RESULT_SCHEMA,
    canonical_result_sha256,
    validate_statistical_result,
)


def _result(*, fixture: bool = True) -> dict:
    value = {
        "schema_version": STATISTICAL_RESULT_SCHEMA,
        "producer": {
            "id": "g3-primary-r",
            "version": "1.0.0",
            "runtime": "R 4.6.1",
            "code_sha256": "a" * 64,
            "environment_sha256": "b" * 64,
            "fixture": fixture,
            "runtime_asset_sha256": {"aft_dll": "e" * 64},
        },
        "binding": {
            "admission_receipt_id": "fixture-receipt",
            "admission_receipt_sha256": "d" * 64,
            "evidence_bundle_sha256": "c" * 64,
        },
        "resampling": {
            "algorithm": "paired_hierarchical_cluster_bootstrap",
            "cluster_level": "operator",
            "seed": 20260812,
            "rng": "L'Ecuyer-CMRG",
            "requested_replicates": 10000,
            "completed_replicates": 10000,
        },
        "model": {
            "family": "binomial",
            "link": "logit",
            "primary_engine": "lme4",
            "fallback_engine": "brglm2",
            "fallback_triggered": False,
            "diagnostics": {"converged": True, "singular": False, "separation": False},
        },
        "multiplicity": {
            "branch_alpha": {"A": 0.025, "B": 0.025},
            "within_branch_method": "holm",
        },
        "comparators": ["fixed_streak", "hmm_or_crf"],
        "task_directions": {
            "A": {"meccano": "favorable", "industreal": "favorable"},
            "B": {"meccano": "favorable", "industreal": "favorable"},
        },
        "branches": {
            "A": {
                "complete": True,
                "adjusted_p_values": {"fixed_streak": 0.01, "hmm_or_crf": 0.02},
                "confirmatory_latency_model_complete": True,
            },
            "B": {
                "complete": True,
                "adjusted_p_values": {"fixed_streak": 0.01, "hmm_or_crf": 0.02},
                "confirmatory_latency_model_complete": True,
            },
        },
        "common_support": {"complete": True, "lower": 0.4, "upper": 0.8, "minimum_width": 0.1},
        "endpoints": {
            "fsar": {}, "coverage": {}, "latency": {}, "failures": {},
            "abstention": {}, "exclusions": {},
        },
    }
    value["artifact_sha256"] = canonical_result_sha256(value)
    return value


def test_complete_fixture_contract_validates_but_is_not_real_evidence():
    valid, reasons = validate_statistical_result(_result(), allow_fixture=True)
    assert valid is True and reasons == []

    valid, reasons = validate_statistical_result(_result(), allow_fixture=False)
    assert valid is False
    assert "fixture_producer" in reasons


def test_real_contract_requires_exact_pin_and_ten_thousand_replicates():
    result = _result(fixture=False)
    valid, reasons = validate_statistical_result(
        result,
        allow_fixture=False,
        expected_producer_id="g3-primary-r",
        expected_producer_version="1.0.0",
        expected_runtime="R 4.6.1",
        expected_code_sha256="a" * 64,
        expected_environment_sha256="b" * 64,
        expected_receipt_id="fixture-receipt",
        expected_receipt_sha256="d" * 64,
        expected_evidence_bundle_sha256="c" * 64,
        expected_runtime_asset_sha256={"aft_dll": "e" * 64},
    )
    assert valid is True and reasons == []

    result["resampling"]["completed_replicates"] = 9999
    result["artifact_sha256"] = canonical_result_sha256(result)
    valid, reasons = validate_statistical_result(result, allow_fixture=False)
    assert valid is False and "bootstrap_replicates" in reasons


def test_point_estimates_or_caller_p_values_cannot_replace_producer_contract():
    valid, reasons = validate_statistical_result(
        {"point_estimate": -0.1, "p_value": 0.01}, allow_fixture=False
    )
    assert valid is False
    assert {"schema_version", "artifact_sha256", "producer"} <= set(reasons)


def test_real_result_cannot_validate_without_external_expected_pins():
    valid, reasons = validate_statistical_result(_result(fixture=False), allow_fixture=False)
    assert valid is False and "real_pin_contract" in reasons


def test_schema_extras_are_rejected_even_when_rehashed():
    result = _result()
    result["caller_p_value"] = 0.001
    result["artifact_sha256"] = canonical_result_sha256(result)
    valid, reasons = validate_statistical_result(result, allow_fixture=True)
    assert valid is False and "unexpected_keys" in reasons


def test_tamper_and_pin_mismatch_are_rejected():
    result = _result(fixture=False)
    result["branches"]["A"]["adjusted_p_values"]["fixed_streak"] = 0.9
    valid, reasons = validate_statistical_result(result, allow_fixture=False)
    assert "artifact_sha256" in reasons

    result = _result(fixture=False)
    valid, reasons = validate_statistical_result(
        result, allow_fixture=False, expected_environment_sha256="d" * 64
    )
    assert valid is False and "producer_environment_sha256" in reasons


def test_required_endpoints_tasks_comparators_and_holm_are_fail_closed():
    mutations = []
    x = _result()
    x["comparators"] = ["fixed_streak"]
    mutations.append((x, "comparators"))
    x = _result()
    del x["task_directions"]["A"]["industreal"]
    mutations.append((x, "task_directions"))
    x = _result()
    x["multiplicity"]["within_branch_method"] = "bonferroni"
    mutations.append((x, "holm"))
    x = _result()
    del x["endpoints"]["latency"]
    mutations.append((x, "endpoints"))
    for value, reason in mutations:
        value["artifact_sha256"] = canonical_result_sha256(value)
        valid, reasons = validate_statistical_result(value, allow_fixture=True)
        assert valid is False and reason in reasons


def test_common_support_classification_distinguishes_failure_from_invalid_evidence():
    empty = _result()
    empty["common_support"].update(lower=0.8, upper=0.4)
    empty["artifact_sha256"] = canonical_result_sha256(empty)
    valid, reasons = validate_statistical_result(empty, allow_fixture=True)
    assert valid is True and reasons == []

    missing = copy.deepcopy(empty)
    del missing["common_support"]["complete"]
    missing["artifact_sha256"] = canonical_result_sha256(missing)
    valid, reasons = validate_statistical_result(missing, allow_fixture=True)
    assert valid is False and "common_support_evidence" in reasons
