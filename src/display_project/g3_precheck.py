"""Non-authorizing G3 precheck for the currently blocked research state.

This module deliberately does not implement a G3 pass decision.  The frozen
analysis contract requires a held-out ``DEVELOPMENT_G3_EVALUATION`` partition,
per-task configuration bindings, a complete adjusted statistical gate, and an
out-of-band trust anchor.  None of those contracts may be replaced by a
self-hashed collection of point estimates.  Until they are implemented and G1
and G2 both pass, every artifact produced here is diagnostic-only and blocked.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

G3_CAPABILITY_SCHEMA = "g3-mechanism-precheck-1.0.0"
THRESHOLDS_SCHEMA = "g3-thresholds-manifest-1.0.0"
OPERATING_POINTS_SCHEMA = "g3-operating-points-manifest-1.0.0"
METHOD_MANIFEST_SCHEMA = "g3-method-manifest-1.0.0"
PAIRED_SUPPORT_SCHEMA = "g3-paired-support-manifest-1.0.0"
MECHANISM_RESULT_SCHEMA = "g3-mechanism-result-1.0.0"

DOWNSTREAM_STAGE_ALIASES = {
    "pilot": "pilot",
    "power": "power",
    "power_analysis": "power",
    "formal": "formal",
    "formal_test": "formal",
    "cross_task": "cross_task",
    "cross_task_generalization": "cross_task",
}

_DEFERRED_REQUIREMENTS = (
    {
        "id": "production_partition_contract",
        "status": "BLOCKING",
        "required": "DEVELOPMENT_G3_EVALUATION profile and isolated partition",
    },
    {
        "id": "canonical_identity_validation",
        "status": "BLOCKING",
        "required": "canonical non-fixture reserved-identity predicate",
    },
    {
        "id": "per_task_task_config_hashes",
        "status": "BLOCKING",
        "required": "distinct frozen task-config hash keyed by each G3 task",
    },
    {
        "id": "full_statistical_gate",
        "status": "BLOCKING",
        "required": (
            "Branch A/B alpha=0.025, Holm-adjusted comparator tests, task "
            "direction, coverage, latency, failure, abstention, and exclusion checks"
        ),
    },
    {
        "id": "out_of_band_pin",
        "status": "BLOCKING",
        "required": "consumer-supplied pinned capability or trust-anchor hash",
    },
)


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_evidence(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"status": "MISSING", "path": None, "file_sha256": None, "size": None}
    target = Path(path).resolve()
    evidence: dict[str, Any] = {
        "status": "MISSING",
        "path": target.as_posix(),
        "file_sha256": None,
        "size": None,
    }
    try:
        raw = target.read_bytes()
    except OSError as exc:
        evidence["error_type"] = type(exc).__name__
        return evidence
    evidence.update(
        status="PRESENT",
        file_sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
    )
    return evidence


def _unique_tasks(
    mechanism_result_paths: Mapping[str, str | Path] | None,
    task_ids: tuple[str, str] | None,
) -> list[str]:
    selected = list(task_ids or tuple((mechanism_result_paths or {}).keys()))
    return [str(task) for task in selected]


def build_g3_capability_artifact(
    mechanism_result_paths: Mapping[str, str | Path] | None,
    *,
    thresholds_manifest_path: str | Path | None,
    operating_point_manifest_path: str | Path | None,
    method_manifest_path: str | Path | None,
    paired_support_manifest_path: str | Path | None,
    ledger_path: str | Path | None = None,
    ledger_anchor_path: str | Path | None = None,
    expected_anchor_hash: str | None = None,
    split_manifest_path: str | Path | None = None,
    paired_metrics_freeze_path: str | Path | None = None,
    scorer_config_hash: str | None = None,
    task_config_hash: str | None = None,
    task_ids: tuple[str, str] | None = None,
    fixture: bool = False,
    joint_gate_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a hash-bound diagnostic artifact that can never authorize G3."""

    tasks = _unique_tasks(mechanism_result_paths, task_ids)
    source_evidence: dict[str, Any] = {
        "joint_gate": _source_evidence(joint_gate_path),
        "paired_ledger": _source_evidence(ledger_path),
        "ledger_anchor": _source_evidence(ledger_anchor_path),
        "split_manifest": _source_evidence(split_manifest_path),
        "paired_metrics_freeze": _source_evidence(paired_metrics_freeze_path),
        "thresholds_manifest": _source_evidence(thresholds_manifest_path),
        "operating_point_manifest": _source_evidence(operating_point_manifest_path),
        "method_manifest": _source_evidence(method_manifest_path),
        "paired_support_manifest": _source_evidence(paired_support_manifest_path),
        "mechanism_results": {
            str(task): _source_evidence(path)
            for task, path in sorted((mechanism_result_paths or {}).items())
        },
    }
    flat_sources = [
        evidence
        for key, evidence in source_evidence.items()
        if key != "mechanism_results"
    ] + list(source_evidence["mechanism_results"].values())
    missing = sorted(
        key
        for key, evidence in source_evidence.items()
        if key != "mechanism_results" and evidence["status"] != "PRESENT"
    )
    if not source_evidence["mechanism_results"]:
        missing.append("mechanism_results")

    body: dict[str, Any] = {
        "schema_version": G3_CAPABILITY_SCHEMA,
        "decision_type": "PRECHECK_ONLY",
        "status": "BLOCKED",
        "fixture": bool(fixture),
        "task_ids": tasks,
        "source_evidence": source_evidence,
        "input_hints": {
            "expected_anchor_hash": expected_anchor_hash,
            "scorer_config_hash": scorer_config_hash,
            "legacy_single_task_config_hash": task_config_hash,
        },
        "precheck_checks": {
            "exactly_two_unique_tasks": len(tasks) == 2 and len(set(tasks)) == 2,
            "all_supplied_sources_readable": all(
                evidence["status"] == "PRESENT" for evidence in flat_sources
            ),
            "actual_g3_implemented": False,
        },
        "deferred_requirements": [dict(item) for item in _DEFERRED_REQUIREMENTS],
        "errors": sorted(
            {
                "actual_g3_not_authorized",
                "precheck_only_not_authorizing",
                *(f"missing_source:{label}" for label in missing),
            }
        ),
        "downstream_authorized": False,
    }
    body["artifact_sha256"] = canonical_sha256(body)
    return body


def _embedded_hash_valid(capability: Mapping[str, Any]) -> bool:
    supplied = capability.get("artifact_sha256")
    body = {key: value for key, value in capability.items() if key != "artifact_sha256"}
    return isinstance(supplied, str) and supplied == canonical_sha256(body)


def validate_g3_capability(capability: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Reject every artifact as downstream authority, including valid prechecks."""

    reasons: list[str] = []
    if not isinstance(capability, Mapping):
        return False, ["schema_version", "precheck_only_not_authorizing"]
    if capability.get("schema_version") != G3_CAPABILITY_SCHEMA:
        reasons.append("schema_version")
    if not _embedded_hash_valid(capability):
        reasons.append("artifact_sha256")
    if capability.get("decision_type") != "PRECHECK_ONLY":
        reasons.append("decision_type")
    if capability.get("status") != "BLOCKED":
        reasons.append("status")
    if capability.get("downstream_authorized") is not False:
        reasons.append("downstream_authorized")
    requirements = capability.get("deferred_requirements")
    expected_ids = {item["id"] for item in _DEFERRED_REQUIREMENTS}
    if (
        not isinstance(requirements, list)
        or {item.get("id") for item in requirements if isinstance(item, Mapping)}
        != expected_ids
    ):
        reasons.append("deferred_requirements")
    reasons.append("precheck_only_not_authorizing")
    return False, sorted(set(reasons))


def normalize_downstream_stage(stage: str) -> str:
    try:
        return DOWNSTREAM_STAGE_ALIASES[stage]
    except KeyError as exc:
        raise ValueError(f"unknown downstream stage: {stage}") from exc


def require_g3_capability(
    capability_or_path: Mapping[str, Any] | str | Path,
    stage: str,
) -> dict[str, Any]:
    """Deny downstream work until a future, complete G3 contract is implemented."""

    normalize_downstream_stage(stage)
    if isinstance(capability_or_path, Mapping):
        capability: Mapping[str, Any] = capability_or_path
    else:
        try:
            loaded = json.loads(Path(capability_or_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PermissionError("G3 capability is absent or unreadable") from exc
        capability = loaded if isinstance(loaded, Mapping) else {}
    _valid, reasons = validate_g3_capability(capability)
    raise PermissionError("G3 downstream authorization denied: " + ",".join(reasons))


def write_g3_capability(path: str | Path, capability: Mapping[str, Any]) -> None:
    """Write only a valid, self-hashed blocked precheck artifact."""

    if (
        capability.get("schema_version") != G3_CAPABILITY_SCHEMA
        or capability.get("decision_type") != "PRECHECK_ONLY"
        or capability.get("status") != "BLOCKED"
        or capability.get("downstream_authorized") is not False
        or not _embedded_hash_valid(capability)
    ):
        raise ValueError("only a self-hashed blocked G3 precheck may be written")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(capability, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


__all__ = [
    "DOWNSTREAM_STAGE_ALIASES",
    "G3_CAPABILITY_SCHEMA",
    "MECHANISM_RESULT_SCHEMA",
    "METHOD_MANIFEST_SCHEMA",
    "OPERATING_POINTS_SCHEMA",
    "PAIRED_SUPPORT_SCHEMA",
    "THRESHOLDS_SCHEMA",
    "build_g3_capability_artifact",
    "canonical_sha256",
    "normalize_downstream_stage",
    "require_g3_capability",
    "validate_g3_capability",
    "write_g3_capability",
]
