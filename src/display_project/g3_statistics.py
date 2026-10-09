"""Strict consumer contract for independently produced G3 statistics.

This module validates provenance and completeness.  It deliberately performs no
statistical inference and grants no run or downstream authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

STATISTICAL_RESULT_SCHEMA = "g3-statistical-result-1.1.0"
REQUIRED_COMPARATORS = {"fixed_streak", "hmm_or_crf"}
REQUIRED_TASKS = {"meccano", "industreal"}
REQUIRED_ENDPOINTS = {
    "fsar",
    "coverage",
    "latency",
    "failures",
    "abstention",
    "exclusions",
}
REQUIRED_TOP_LEVEL_KEYS = {
    "artifact_sha256",
    "binding",
    "branches",
    "common_support",
    "comparators",
    "endpoints",
    "model",
    "multiplicity",
    "producer",
    "resampling",
    "schema_version",
    "task_directions",
}


def canonical_result_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    raw = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pin_reason(
    reasons: list[str], actual: object, expected: str | None, reason: str
) -> None:
    if expected is not None and actual != expected:
        reasons.append(reason)


def validate_statistical_result(
    result: Mapping[str, Any] | object,
    *,
    allow_fixture: bool,
    expected_producer_id: str | None = None,
    expected_producer_version: str | None = None,
    expected_runtime: str | None = None,
    expected_code_sha256: str | None = None,
    expected_environment_sha256: str | None = None,
    expected_receipt_id: str | None = None,
    expected_receipt_sha256: str | None = None,
    expected_evidence_bundle_sha256: str | None = None,
    expected_runtime_asset_sha256: Mapping[str, str] | None = None,
) -> tuple[bool, list[str]]:
    """Validate a pinned producer result without interpreting its effect."""

    if not isinstance(result, Mapping):
        return False, ["artifact_sha256", "producer", "schema_version"]
    reasons: list[str] = []
    if set(result) != REQUIRED_TOP_LEVEL_KEYS:
        reasons.append("unexpected_keys")
    if result.get("schema_version") != STATISTICAL_RESULT_SCHEMA:
        reasons.append("schema_version")
    supplied_hash = result.get("artifact_sha256")
    try:
        hash_valid = supplied_hash == canonical_result_sha256(result)
    except (TypeError, ValueError):
        hash_valid = False
    if not isinstance(supplied_hash, str) or not hash_valid:
        reasons.append("artifact_sha256")

    producer = _mapping(result.get("producer"))
    producer_keys = {
        "id", "version", "runtime", "code_sha256", "environment_sha256", "fixture"
    }
    allowed_producer_keys = {
        frozenset(producer_keys),
        frozenset(producer_keys | {"runtime_asset_sha256"}),
    }
    if not producer or frozenset(producer) not in allowed_producer_keys:
        reasons.append("producer")
    else:
        if not isinstance(producer.get("fixture"), bool):
            reasons.append("producer")
        elif producer.get("fixture") is True and not allow_fixture:
            reasons.append("fixture_producer")
    _pin_reason(reasons, producer.get("id"), expected_producer_id, "producer_id")
    _pin_reason(
        reasons, producer.get("version"), expected_producer_version, "producer_version"
    )
    _pin_reason(reasons, producer.get("runtime"), expected_runtime, "producer_runtime")
    _pin_reason(
        reasons, producer.get("code_sha256"), expected_code_sha256, "producer_code_sha256"
    )
    _pin_reason(
        reasons,
        producer.get("environment_sha256"),
        expected_environment_sha256,
        "producer_environment_sha256",
    )
    if expected_runtime_asset_sha256 is not None and producer.get(
        "runtime_asset_sha256"
    ) != expected_runtime_asset_sha256:
        reasons.append("producer_runtime_asset_sha256")
    for field in ("id", "version", "runtime", "code_sha256", "environment_sha256"):
        if not isinstance(producer.get(field), str) or not producer.get(field):
            reasons.append("producer")
    if not allow_fixture and any(
        value is None
        for value in (
            expected_producer_id,
            expected_producer_version,
            expected_runtime,
            expected_code_sha256,
            expected_environment_sha256,
            expected_receipt_id,
            expected_receipt_sha256,
            expected_evidence_bundle_sha256,
            expected_runtime_asset_sha256,
        )
    ):
        reasons.append("real_pin_contract")

    binding = _mapping(result.get("binding"))
    if set(binding) != {
        "admission_receipt_id", "admission_receipt_sha256", "evidence_bundle_sha256"
    }:
        reasons.append("binding")
    _pin_reason(
        reasons,
        binding.get("admission_receipt_id"),
        expected_receipt_id,
        "admission_receipt_id",
    )
    _pin_reason(
        reasons,
        binding.get("admission_receipt_sha256"),
        expected_receipt_sha256,
        "admission_receipt_sha256",
    )
    _pin_reason(
        reasons,
        binding.get("evidence_bundle_sha256"),
        expected_evidence_bundle_sha256,
        "evidence_bundle_sha256",
    )
    if any(
        not isinstance(binding.get(field), str) or not binding.get(field)
        for field in (
            "admission_receipt_id", "admission_receipt_sha256",
            "evidence_bundle_sha256",
        )
    ):
        reasons.append("binding")

    resampling = _mapping(result.get("resampling"))
    if (
        resampling.get("algorithm") != "paired_hierarchical_cluster_bootstrap"
        or resampling.get("cluster_level") not in {
            "operator",
            "complete_job",
            "task_specific",
        }
        or resampling.get("requested_replicates") != 10000
        or resampling.get("completed_replicates") != 10000
        or not isinstance(resampling.get("seed"), int)
        or resampling.get("rng") != "L'Ecuyer-CMRG"
    ):
        reasons.append("bootstrap_replicates")

    model = _mapping(result.get("model"))
    diagnostics = _mapping(model.get("diagnostics"))
    if (
        model.get("family") != "binomial"
        or model.get("link") != "logit"
        or model.get("primary_engine") not in {"lme4", "glmmTMB"}
        or model.get("fallback_engine") != "brglm2"
        or not isinstance(model.get("fallback_triggered"), bool)
        or not {"converged", "singular", "separation"} <= set(diagnostics)
    ):
        reasons.append("model_diagnostics")

    multiplicity = _mapping(result.get("multiplicity"))
    alpha = _mapping(multiplicity.get("branch_alpha"))
    if alpha != {"A": 0.025, "B": 0.025}:
        reasons.append("branch_alpha")
    if multiplicity.get("within_branch_method") != "holm":
        reasons.append("holm")

    if set(result.get("comparators", [])) != REQUIRED_COMPARATORS:
        reasons.append("comparators")
    task_directions = _mapping(result.get("task_directions"))
    if set(task_directions) != {"A", "B"} or any(
        set(_mapping(task_directions.get(branch))) != REQUIRED_TASKS
        or any(
            _mapping(task_directions.get(branch)).get(task)
            not in {"favorable", "unfavorable"}
            for task in REQUIRED_TASKS
        )
        for branch in ("A", "B")
    ):
        reasons.append("task_directions")

    branches = _mapping(result.get("branches"))
    if set(branches) != {"A", "B"} or any(
        set(_mapping(_mapping(branches.get(branch)).get("adjusted_p_values")))
        != REQUIRED_COMPARATORS
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 1
            for value in _mapping(
                _mapping(branches.get(branch)).get("adjusted_p_values")
            ).values()
        )
        or _mapping(branches.get(branch)).get("complete") is not True
        for branch in ("A", "B")
    ):
        reasons.append("branches")
    if not allow_fixture and any(
        _mapping(branches.get(branch)).get("confirmatory_latency_model_complete")
        is not True
        for branch in ("A", "B")
    ):
        reasons.append("confirmatory_latency_model")
    if not allow_fixture and (
        not isinstance(producer.get("runtime_asset_sha256"), Mapping)
        or not producer.get("runtime_asset_sha256")
    ):
        reasons.append("producer_runtime_assets")

    support = _mapping(result.get("common_support"))
    if (
        support.get("complete") is not True
        or not all(
            isinstance(support.get(k), (int, float))
            for k in ("lower", "upper", "minimum_width")
        )
    ):
        reasons.append("common_support_evidence")
    endpoints = _mapping(result.get("endpoints"))
    if set(endpoints) != REQUIRED_ENDPOINTS:
        reasons.append("endpoints")

    return not reasons, sorted(set(reasons))


__all__ = [
    "STATISTICAL_RESULT_SCHEMA",
    "canonical_result_sha256",
    "validate_statistical_result",
]
