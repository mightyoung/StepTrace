from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .state_risk_knowledge import validate_knowledge
from .evidence_window import EvidenceWindowError, validate_window


class StateRiskContractError(ValueError):
    pass


_FIELDS = {"schema_version", "task_id", "window_id", "estimated_stage", "completion_state", "deviations", "risk_assessments", "evidence_refs", "conflicting_evidence_refs", "brief_rationale", "authority"}
_DEV_FIELDS = {"type_id", "evidence_refs"}
_RISK_FIELDS = {"rule_id", "status", "evidence_refs", "missing_evidence"}
_COMPLETION = {"not_started", "in_progress", "completed", "unknown"}
_RISK_STATUS = {"triggered", "not_triggered", "unknown", "not_applicable"}
_FORBIDDEN_AUTHORITY_KEYS = {"execution_state", "state_write", "state_writes", "state_write_allowed", "authorization_events", "control_command", "release_authority"}


def _error(message: str) -> None:
    raise StateRiskContractError(message)


def _exact(raw: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(raw) != fields:
        _error(f"{label} fields mismatch; missing={sorted(fields-set(raw))}, extra={sorted(set(raw)-fields)}")


def _text(value: object, label: str, *, allow_null: bool = False) -> str | None:
    if allow_null and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _error(f"{label} must be a non-empty string")
    return value.strip()


def _strings(value: object, label: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _error(f"{label} must be a list")
    result = [_text(item, f"{label}[]") for item in value]
    if len(result) != len(set(result)):
        _error(f"{label} must not contain duplicates")
    return result  # type: ignore[return-value]


def _reject_authority_escalation(value: object, path: str = "inference") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_AUTHORITY_KEYS:
                _error(f"forbidden authority field: {path}.{key}")
            _reject_authority_escalation(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _reject_authority_escalation(child, f"{path}[{index}]")


def _window_reference_ids(window: Mapping[str, object]) -> set[str]:
    ids: set[str] = set()
    for field in ("frame_refs", "object_track_refs", "relation_events"):
        entries = window.get(field)
        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            _error(f"window.{field} must be a list")
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                _error(f"window.{field}[{index}] must be a mapping")
            ref_id = _text(entry.get("id"), f"window.{field}[{index}].id")
            if ref_id in ids:
                _error(f"duplicate window evidence id: {ref_id}")
            ids.add(ref_id)  # type: ignore[arg-type]
    return ids


def _missing_observations(rule: Mapping[str, object], window: Mapping[str, object]) -> set[str]:
    observability = window.get("observability")
    if not isinstance(observability, Mapping):
        _error("window.observability must be a mapping")
    missing = observability.get("missing")
    if isinstance(missing, (str, bytes)) or not isinstance(missing, Sequence):
        _error("window.observability.missing must be a list")
    unavailable = set(_strings(missing, "window.observability.missing"))
    if observability.get("view") == "unavailable":
        return set(rule["required_observations"])  # type: ignore[arg-type]
    return set(rule["required_observations"]) & unavailable  # type: ignore[arg-type]


def validate_inference(raw: Mapping[str, object], *, knowledge: Mapping[str, object], window: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        _error("inference must be a mapping")
    _reject_authority_escalation(raw)
    _exact(raw, _FIELDS, "inference")
    normalized_knowledge = validate_knowledge(knowledge)
    try:
        normalized_window = validate_window(window)
    except EvidenceWindowError as exc:
        _error(f"invalid evidence window: {exc}")
    if raw["schema_version"] != "1.0":
        _error("unsupported inference schema_version")
    task_id = _text(raw["task_id"], "task_id")
    window_task = _text(normalized_window.get("task_id"), "window.task_id")
    if task_id != normalized_knowledge["task_id"] or task_id != window_task:
        _error("task_id must match knowledge and evidence window")
    window_id = _text(raw["window_id"], "window_id")
    if window_id != _text(normalized_window.get("window_id"), "window.window_id"):
        _error("window_id must match evidence window")
    authority = _text(raw["authority"], "authority")
    if authority != "state_estimation_only":
        _error("authority must be state_estimation_only")

    step_ids = {step["id"] for step in normalized_knowledge["steps"]}  # type: ignore[index,union-attr]
    stage = _text(raw["estimated_stage"], "estimated_stage", allow_null=True)
    if stage is not None and stage not in step_ids:
        _error(f"unknown estimated_stage: {stage}")
    completion = _text(raw["completion_state"], "completion_state")
    if completion not in _COMPLETION:
        _error(f"unsupported completion_state: {completion}")
    if stage is not None and completion == "completed":
        stage_spec = next(step for step in normalized_knowledge["steps"] if step["id"] == stage)  # type: ignore[union-attr]
        missing_for_completion = _missing_observations(stage_spec, normalized_window)
        if missing_for_completion:
            _error(
                "completed state requires all stage observations; "
                f"missing={sorted(missing_for_completion)}"
            )
    valid_refs = _window_reference_ids(normalized_window)

    evidence_refs = _strings(raw["evidence_refs"], "evidence_refs")
    conflicting_refs = _strings(raw["conflicting_evidence_refs"], "conflicting_evidence_refs")
    unknown_refs = (set(evidence_refs) | set(conflicting_refs)) - valid_refs
    if unknown_refs:
        _error(f"inference references unknown evidence: {sorted(unknown_refs)}")
    if (stage is not None or completion != "unknown") and not evidence_refs:
        _error("determinate stage or completion requires evidence_refs")

    raw_deviations = raw["deviations"]
    if isinstance(raw_deviations, (str, bytes)) or not isinstance(raw_deviations, Sequence):
        _error("deviations must be a list")
    deviations: list[dict[str, object]] = []
    configured_deviations = set(normalized_knowledge["deviation_types"])  # type: ignore[arg-type]
    for index, item in enumerate(raw_deviations):
        if not isinstance(item, Mapping):
            _error(f"deviations[{index}] must be a mapping")
        _exact(item, _DEV_FIELDS, f"deviations[{index}]")
        type_id = _text(item["type_id"], f"deviations[{index}].type_id")
        refs = _strings(item["evidence_refs"], f"deviations[{index}].evidence_refs")
        if type_id not in configured_deviations or set(refs) - valid_refs:
            _error(f"invalid deviation {type_id} or evidence reference")
        if set(refs) - set(evidence_refs):
            _error(f"deviation {type_id} evidence_refs must be included in inference evidence_refs")
        deviations.append({"type_id": type_id, "evidence_refs": refs})

    raw_assessments = raw["risk_assessments"]
    if isinstance(raw_assessments, (str, bytes)) or not isinstance(raw_assessments, Sequence):
        _error("risk_assessments must be a list")
    rules = {rule["id"]: rule for rule in normalized_knowledge["risk_rules"]}  # type: ignore[index,union-attr]
    assessments: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_assessments):
        if not isinstance(item, Mapping):
            _error(f"risk_assessments[{index}] must be a mapping")
        _exact(item, _RISK_FIELDS, f"risk_assessments[{index}]")
        rule_id = _text(item["rule_id"], f"risk_assessments[{index}].rule_id")
        if rule_id not in rules or rule_id in seen:
            _error(f"unknown or duplicate risk rule: {rule_id}")
        seen.add(rule_id)  # type: ignore[arg-type]
        status = _text(item["status"], f"risk_assessments[{index}].status")
        if status not in _RISK_STATUS:
            _error(f"unsupported risk status: {status}")
        refs = _strings(item["evidence_refs"], f"risk_assessments[{index}].evidence_refs")
        if set(refs) - valid_refs:
            _error(f"risk rule {rule_id} references unknown evidence")
        if set(refs) - set(evidence_refs):
            _error(f"risk rule {rule_id} evidence_refs must be included in inference evidence_refs")
        missing = _strings(item["missing_evidence"], f"risk_assessments[{index}].missing_evidence")
        absent = _missing_observations(rules[rule_id], normalized_window)  # type: ignore[index]
        scopes = rules[rule_id]["applicable_stages"]
        stage_unknown = stage is None and "*" not in scopes  # type: ignore[operator]
        applicable = "*" in scopes or (stage is not None and stage in scopes)  # type: ignore[operator]
        if stage_unknown:
            if status != "unknown":
                _error(f"risk rule {rule_id} must be unknown when estimated stage is unknown")
            absent.add("estimated_stage")
        if not stage_unknown and not applicable and status != "not_applicable":
            _error(f"risk rule {rule_id} must be not_applicable for estimated stage")
        if applicable and status == "not_applicable":
            _error(f"risk rule {rule_id} is applicable to estimated stage")
        if applicable and absent and status != "unknown":
            _error(f"risk rule {rule_id} must be unknown when required observations are absent")
        observability = normalized_window["observability"]
        if status == "not_triggered" and observability.get("view") != "complete":  # type: ignore[union-attr]
            _error(f"risk rule {rule_id} cannot be not_triggered without a complete view")
        if status == "unknown" and not absent.issubset(set(missing)):
            _error(f"risk rule {rule_id} missing_evidence must name absent required observations")
        if status != "unknown" and missing:
            _error(f"risk rule {rule_id} missing_evidence must be empty for status {status}")
        if status in {"triggered", "not_triggered"} and not refs:
            _error(f"{status} risk rule {rule_id} requires evidence_refs")
        assessments.append({"rule_id": rule_id, "status": status, "evidence_refs": refs, "missing_evidence": missing})
    if seen != set(rules):
        _error(f"every configured risk rule must appear exactly once; missing={sorted(set(rules)-seen)}")

    rationale = _text(raw["brief_rationale"], "brief_rationale")
    return deepcopy({"schema_version": "1.0", "task_id": task_id, "window_id": window_id, "estimated_stage": stage, "completion_state": completion, "deviations": deviations, "risk_assessments": assessments, "evidence_refs": evidence_refs, "conflicting_evidence_refs": conflicting_refs, "brief_rationale": rationale, "authority": "state_estimation_only"})


def inference_json_schema(knowledge: Mapping[str, object]) -> dict[str, object]:
    pack = validate_knowledge(knowledge)
    step_ids = [step["id"] for step in pack["steps"]]  # type: ignore[index]
    deviation_types = list(pack["deviation_types"])  # type: ignore[arg-type]
    rule_ids = [rule["id"] for rule in pack["risk_rules"]]  # type: ignore[index]
    ref_array = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
    # NOTE: `properties` must define *every* key listed in `required`; otherwise
    # `additionalProperties: false` makes the schema unsatisfiable. Keep the four
    # top-level fields below as siblings of the nested `deviations` / `risk_assessments`
    # definitions (regression covered by tests/test_vlm_state_risk_contract.py).
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Qwen joint state and risk inference",
        "type": "object", "additionalProperties": False, "required": sorted(_FIELDS),
        "properties": {
            "schema_version": {"const": "1.0"}, "task_id": {"const": pack["task_id"]}, "window_id": {"type": "string", "minLength": 1},
            "estimated_stage": {"type": ["string", "null"], "enum": step_ids + [None]}, "completion_state": {"enum": sorted(_COMPLETION)},
            "deviations": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": sorted(_DEV_FIELDS), "properties": {"type_id": {"enum": deviation_types}, "evidence_refs": ref_array}}},
            "risk_assessments": {"type": "array", "minItems": len(rule_ids), "maxItems": len(rule_ids), "items": {"type": "object", "additionalProperties": False, "required": sorted(_RISK_FIELDS), "properties": {"rule_id": {"enum": rule_ids}, "status": {"enum": sorted(_RISK_STATUS)}, "evidence_refs": ref_array, "missing_evidence": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}}}},
            "evidence_refs": ref_array, "conflicting_evidence_refs": ref_array, "brief_rationale": {"type": "string", "minLength": 1}, "authority": {"const": "state_estimation_only"},
        },
    }
