"""Fail-closed raw opportunity contract for the real G3 R producer."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from typing import Any, Mapping


INPUT_SCHEMA = "display_project.g3_statistical_input-1.0.0"
TASK_SPECIFIC_INPUT_SCHEMA = "display_project.g3_statistical_input-1.1.0"
GATE_MANIFEST_SCHEMA = "display_project.g3_mechanism_gate_manifest-1.0.0"
METHODS = {"proposed", "fixed_streak", "hmm_or_crf"}
TASKS = {"meccano", "industreal"}
BRANCHES = {"A", "B"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RESERVED = {"", "UNKNOWN", "UNASSIGNED", "NONE", "NULL", "N/A"}
TOP_LEVEL_KEYS = {
    "schema_version",
    "producer",
    "binding",
    "resampling",
    "gate_manifest",
    "opportunities",
}
TASK_SPECIFIC_TOP_LEVEL_KEYS = TOP_LEVEL_KEYS | {"cluster_level_by_task"}
OPPORTUNITY_KEYS = {
    "opportunity_id",
    "unit_id",
    "task",
    "method",
    "branch",
    "operator_id",
    "complete_job_id",
    "threshold",
    "scorable",
    "definitive",
    "advanced",
    "false_advance",
    "abstained",
    "latency_seconds",
    "censoring_seconds",
    "right_censored",
    "run_failure",
}


def canonical_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    return hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _finite_probability(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and 0 <= float(value) <= 1
    )


def _hash(value: object) -> bool:
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def _manifest_reasons(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return ["gate_manifest"]
    expected = {
        "schema_version",
        "c_star",
        "r_star",
        "delta_c",
        "delta_r",
        "minimum_coverage",
        "maximum_latency_seconds",
        "common_support_minimum_width",
        "common_support_lower",
        "common_support_upper",
        "maximum_differential_failure",
        "selected_thresholds",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(value) != expected or value.get("schema_version") != GATE_MANIFEST_SCHEMA:
        reasons.append("gate_manifest_schema")
    if not _hash(value.get("artifact_sha256")) or value.get(
        "artifact_sha256"
    ) != canonical_sha256(value):
        reasons.append("gate_manifest_hash")
    for key in (
        "c_star",
        "r_star",
        "delta_c",
        "delta_r",
        "minimum_coverage",
        "common_support_minimum_width",
        "common_support_lower",
        "common_support_upper",
        "maximum_differential_failure",
    ):
        if not _finite_probability(value.get(key)):
            reasons.append(f"gate_manifest_{key}")
    latency = value.get("maximum_latency_seconds")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(float(latency))
        or latency <= 0
    ):
        reasons.append("gate_manifest_maximum_latency")
    lower = value.get("common_support_lower")
    upper = value.get("common_support_upper")
    minimum_width = value.get("common_support_minimum_width")
    c_star = value.get("c_star")
    if all(_finite_probability(item) for item in (lower, upper, minimum_width, c_star)):
        if lower > upper or upper - lower < minimum_width or not lower <= c_star <= upper:
            reasons.append("gate_manifest_common_support")
    thresholds = value.get("selected_thresholds")
    expected_cells = {(b, t, m) for b in BRANCHES for t in TASKS for m in METHODS}
    if not isinstance(thresholds, list) or len(thresholds) != len(expected_cells):
        reasons.append("selected_thresholds")
    else:
        cells = []
        for row in thresholds:
            if not isinstance(row, Mapping) or set(row) != {
                "branch", "task", "method", "threshold"
            } or not _finite_probability(row.get("threshold")):
                reasons.append("selected_thresholds")
                continue
            cells.append((row.get("branch"), row.get("task"), row.get("method")))
        if set(cells) != expected_cells or len(cells) != len(set(cells)):
            reasons.append("selected_thresholds")
    return reasons


def validate_g3_statistical_input(
    value: Mapping[str, Any] | object,
) -> tuple[bool, list[str]]:
    """Validate raw paired opportunities; never accept caller-computed results."""
    if not isinstance(value, Mapping):
        return False, ["input_not_mapping"]
    reasons: list[str] = []
    schema = value.get("schema_version")
    task_specific = schema == TASK_SPECIFIC_INPUT_SCHEMA
    expected_top_level = (
        TASK_SPECIFIC_TOP_LEVEL_KEYS if task_specific else TOP_LEVEL_KEYS
    )
    if set(value) != expected_top_level:
        reasons.append("unexpected_top_level_keys")
    if schema not in {INPUT_SCHEMA, TASK_SPECIFIC_INPUT_SCHEMA}:
        reasons.append("schema_version")
    cluster_levels_by_task = value.get("cluster_level_by_task")
    if task_specific:
        if not isinstance(cluster_levels_by_task, Mapping) or set(
            cluster_levels_by_task
        ) != TASKS or any(
            cluster_levels_by_task.get(task) not in {"operator", "complete_job"}
            for task in TASKS
        ):
            reasons.append("cluster_level_by_task")
            cluster_levels_by_task = {}
    else:
        cluster_levels_by_task = {}
    producer = value.get("producer")
    if not isinstance(producer, Mapping) or set(producer) != {
        "fixture", "real_input_enabled", "code_sha256", "environment_sha256",
        "runtime_asset_sha256",
    }:
        reasons.append("producer")
    elif (
        producer.get("fixture") is not False
        or producer.get("real_input_enabled") is not True
        or not _hash(producer.get("code_sha256"))
        or not _hash(producer.get("environment_sha256"))
        or not isinstance(producer.get("runtime_asset_sha256"), Mapping)
        or not _hash(producer.get("runtime_asset_sha256", {}).get("aft_dll"))
    ):
        reasons.append("producer")
    binding = value.get("binding")
    if not isinstance(binding, Mapping) or set(binding) != {
        "admission_receipt_id",
        "admission_receipt_sha256",
        "evidence_bundle_sha256",
    }:
        reasons.append("binding")
    elif (
        not isinstance(binding.get("admission_receipt_id"), str)
        or not binding.get("admission_receipt_id")
        or not _hash(binding.get("admission_receipt_sha256"))
        or not _hash(binding.get("evidence_bundle_sha256"))
    ):
        reasons.append("binding")
    resampling = value.get("resampling")
    if not isinstance(resampling, Mapping) or set(resampling) != {
        "requested_replicates", "seed", "cluster_level"
    }:
        reasons.append("resampling")
    elif (
        resampling.get("requested_replicates") != 10000
        or isinstance(resampling.get("seed"), bool)
        or not isinstance(resampling.get("seed"), int)
        or resampling.get("cluster_level") not in {
            "operator",
            "complete_job",
            "task_specific",
        }
        or (
            resampling.get("cluster_level") == "task_specific"
            and not task_specific
        )
    ):
        reasons.append("resampling")
    reasons.extend(_manifest_reasons(value.get("gate_manifest")))
    manifest = value.get("gate_manifest")
    manifest_rows = (
        manifest.get("selected_thresholds", [])
        if isinstance(manifest, Mapping)
        else []
    )
    threshold_map = {
        (row["branch"], row["task"], row["method"]): float(row["threshold"])
        for row in manifest_rows
        if isinstance(row, Mapping)
        and set(row) == {"branch", "task", "method", "threshold"}
        and _finite_probability(row.get("threshold"))
    }
    rows = value.get("opportunities")
    if not isinstance(rows, list) or not rows:
        reasons.append("opportunities")
        return False, sorted(set(reasons))
    paired: dict[tuple[object, ...], list[Mapping[str, Any]]] = defaultdict(list)
    observed_cells: set[tuple[object, object, object]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != OPPORTUNITY_KEYS:
            reasons.append(f"opportunity_schema:{index}")
            continue
        task = row.get("task")
        for key in ("opportunity_id", "unit_id", "operator_id", "complete_job_id"):
            item = row.get(key)
            allow_unknown_operator = (
                key == "operator_id"
                and task == "industreal"
                and (
                    cluster_levels_by_task.get("industreal") == "complete_job"
                    and task_specific
                )
            )
            if (
                not isinstance(item, str)
                or (item.strip().upper() in RESERVED and not allow_unknown_operator)
            ):
                reasons.append(f"opportunity_identity:{index}:{key}")
        branch, task, method = row.get("branch"), task, row.get("method")
        if branch not in BRANCHES or task not in TASKS or method not in METHODS:
            reasons.append(f"opportunity_cell:{index}")
        observed_cells.add((branch, task, method))
        if not _finite_probability(row.get("threshold")) or threshold_map.get(
            (branch, task, method)
        ) != float(row.get("threshold", -1)):
            reasons.append(f"opportunity_threshold:{index}")
        for key in (
            "scorable", "definitive", "advanced", "false_advance", "abstained",
            "right_censored", "run_failure",
        ):
            if not isinstance(row.get(key), bool):
                reasons.append(f"opportunity_boolean:{index}:{key}")
        if row.get("advanced") and not row.get("definitive"):
            reasons.append(f"advanced_without_definitive:{index}")
        if row.get("false_advance") and not row.get("advanced"):
            reasons.append(f"false_advance_without_advance:{index}")
        if row.get("definitive") == row.get("abstained"):
            reasons.append(f"decision_abstention_inconsistent:{index}")
        latency = row.get("latency_seconds")
        if latency is not None and (
            isinstance(latency, bool)
            or not isinstance(latency, (int, float))
            or not math.isfinite(float(latency))
            or latency < 0
        ):
            reasons.append(f"opportunity_latency:{index}")
        censoring = row.get("censoring_seconds")
        if censoring is not None and (
            isinstance(censoring, bool)
            or not isinstance(censoring, (int, float))
            or not math.isfinite(float(censoring))
            or censoring <= 0
        ):
            reasons.append(f"opportunity_censoring_time:{index}")
        if row.get("right_censored") != (latency is None) or row.get(
            "right_censored"
        ) != (censoring is not None):
            reasons.append(f"opportunity_censoring:{index}")
        paired[(branch, task, row.get("unit_id"), row.get("opportunity_id"))].append(row)
    expected_cells = {(b, t, m) for b in BRANCHES for t in TASKS for m in METHODS}
    if observed_cells != expected_cells:
        reasons.append("two_task_three_method_two_branch_coverage")
    for key, group in paired.items():
        if {row.get("method") for row in group} != METHODS or len(group) != 3:
            reasons.append(f"unpaired_opportunity:{'|'.join(map(str, key))}")
            continue
        identity = {
            (row.get("operator_id"), row.get("complete_job_id")) for row in group
        }
        if len(identity) != 1:
            reasons.append(f"paired_identity_mismatch:{'|'.join(map(str, key))}")
    return not reasons, sorted(set(reasons))


__all__ = [
    "GATE_MANIFEST_SCHEMA",
    "INPUT_SCHEMA",
    "TASK_SPECIFIC_INPUT_SCHEMA",
    "canonical_sha256",
    "validate_g3_statistical_input",
]
