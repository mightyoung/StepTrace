from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml  # type: ignore[import-untyped]


class StateRiskKnowledgeError(ValueError):
    pass


_STATUS = {"DEVELOPMENT_ONLY", "SYNTHETIC_SOFTWARE_TEST_ONLY"}
_CATEGORIES = {"quality", "safety", "procedure"}
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FORBIDDEN_MATERIAL = (
    "annotation", "correct_step", "dataset", "ground_truth", "label",
    "meccano", "industreal", "oracle", "psr_label", "target_step", "truth",
)
_EXACT_TOP = {
    "schema_version", "task_id", "steps", "risk_rules", "deviation_types",
    "source_refs", "status",
}
_EXACT_STEP = {
    "id", "title", "prerequisites", "completion_cues", "negative_cues",
    "required_observations", "source_refs",
}
_EXACT_RULE = {
    "id", "category", "applicable_stages", "trigger_conditions", "exceptions",
    "required_observations", "severity", "severity_source", "source_refs",
}


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateRiskKnowledgeError(f"{field} must be a mapping")
    return value


def _exact(raw: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(raw)
    if actual != expected:
        raise StateRiskKnowledgeError(
            f"{field} fields mismatch; missing={sorted(expected-actual)}, extra={sorted(actual-expected)}"
        )


def _text(value: object, field: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateRiskKnowledgeError(f"{field} must be a non-empty string")
    result = value.strip()
    lowered = result.casefold().replace("-", "_")
    if any(marker in lowered for marker in _FORBIDDEN_MATERIAL):
        raise StateRiskKnowledgeError(f"{field} contains forbidden truth or dataset material")
    if identifier and not _ID.fullmatch(result):
        raise StateRiskKnowledgeError(f"{field} must be an anonymous lowercase identifier")
    return result


def _strings(value: object, field: str, *, nonempty: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise StateRiskKnowledgeError(f"{field} must be a list")
    result = [_text(item, f"{field}[]") for item in value]
    if nonempty and not result:
        raise StateRiskKnowledgeError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise StateRiskKnowledgeError(f"{field} must not contain duplicates")
    return result


def validate_knowledge(raw: Mapping[str, object]) -> dict[str, object]:
    payload = _mapping(raw, "knowledge")
    _exact(payload, _EXACT_TOP, "knowledge")
    if payload["schema_version"] != "1.0":
        raise StateRiskKnowledgeError("unsupported knowledge schema_version")
    task_id = _text(payload["task_id"], "task_id", identifier=True)
    status = _text(payload["status"], "status")
    if status not in _STATUS:
        raise StateRiskKnowledgeError(f"unsupported knowledge status: {status}")
    source_refs = _strings(payload["source_refs"], "source_refs", nonempty=True)

    raw_steps = payload["steps"]
    if isinstance(raw_steps, (str, bytes)) or not isinstance(raw_steps, Sequence) or not raw_steps:
        raise StateRiskKnowledgeError("steps must be a non-empty list")
    steps: list[dict[str, object]] = []
    step_ids: set[str] = set()
    for index, item in enumerate(raw_steps):
        step = _mapping(item, f"steps[{index}]")
        _exact(step, _EXACT_STEP, f"steps[{index}]")
        step_id = _text(step["id"], f"steps[{index}].id", identifier=True)
        if step_id in step_ids:
            raise StateRiskKnowledgeError(f"duplicate step id: {step_id}")
        step_ids.add(step_id)
        normalized = {
            "id": step_id,
            "title": _text(step["title"], f"steps[{index}].title"),
            "prerequisites": _strings(step["prerequisites"], f"steps[{index}].prerequisites"),
            "completion_cues": _strings(step["completion_cues"], f"steps[{index}].completion_cues", nonempty=True),
            "negative_cues": _strings(step["negative_cues"], f"steps[{index}].negative_cues"),
            "required_observations": _strings(step["required_observations"], f"steps[{index}].required_observations", nonempty=True),
            "source_refs": _strings(step["source_refs"], f"steps[{index}].source_refs", nonempty=True),
        }
        steps.append(normalized)
    for step in steps:
        unknown_steps = set(step["prerequisites"]) - step_ids  # type: ignore[arg-type]
        unknown_sources = set(step["source_refs"]) - set(source_refs)  # type: ignore[arg-type]
        if unknown_steps:
            raise StateRiskKnowledgeError(f"step {step['id']} has unknown prerequisites: {sorted(unknown_steps)}")
        if unknown_sources:
            raise StateRiskKnowledgeError(f"step {step['id']} has unknown source_refs: {sorted(unknown_sources)}")

    dependencies = {step["id"]: step["prerequisites"] for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise StateRiskKnowledgeError("step prerequisites must be acyclic")
        if step_id in visited:
            return
        visiting.add(step_id)
        for prerequisite in dependencies[step_id]:
            visit(prerequisite)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in dependencies:
        visit(step_id)

    raw_rules = payload["risk_rules"]
    if isinstance(raw_rules, (str, bytes)) or not isinstance(raw_rules, Sequence):
        raise StateRiskKnowledgeError("risk_rules must be a list")
    rules: list[dict[str, object]] = []
    rule_ids: set[str] = set()
    for index, item in enumerate(raw_rules):
        rule = _mapping(item, f"risk_rules[{index}]")
        _exact(rule, _EXACT_RULE, f"risk_rules[{index}]")
        rule_id = _text(rule["id"], f"risk_rules[{index}].id", identifier=True)
        if rule_id in rule_ids:
            raise StateRiskKnowledgeError(f"duplicate risk rule id: {rule_id}")
        rule_ids.add(rule_id)
        category = _text(rule["category"], f"risk_rules[{index}].category")
        if category not in _CATEGORIES:
            raise StateRiskKnowledgeError(f"unsupported risk category: {category}")
        stages = _strings(rule["applicable_stages"], f"risk_rules[{index}].applicable_stages", nonempty=True)
        if set(stages) - (step_ids | {"*"}):
            raise StateRiskKnowledgeError(f"risk rule {rule_id} references unknown stages")
        refs = _strings(rule["source_refs"], f"risk_rules[{index}].source_refs", nonempty=True)
        if set(refs) - set(source_refs):
            raise StateRiskKnowledgeError(f"risk rule {rule_id} references unknown source_refs")
        severity_source = _text(rule["severity_source"], f"risk_rules[{index}].severity_source")
        if severity_source not in refs:
            raise StateRiskKnowledgeError(
                f"risk rule {rule_id} severity_source must be included in source_refs"
            )
        rules.append({
            "id": rule_id,
            "category": category,
            "applicable_stages": stages,
            "trigger_conditions": _strings(rule["trigger_conditions"], f"risk_rules[{index}].trigger_conditions", nonempty=True),
            "exceptions": _strings(rule["exceptions"], f"risk_rules[{index}].exceptions"),
            "required_observations": _strings(rule["required_observations"], f"risk_rules[{index}].required_observations", nonempty=True),
            "severity": _text(rule["severity"], f"risk_rules[{index}].severity"),
            "severity_source": severity_source,
            "source_refs": refs,
        })

    deviations = _strings(payload["deviation_types"], "deviation_types")
    for item in deviations:
        if not _ID.fullmatch(item):
            raise StateRiskKnowledgeError("deviation_types must contain lowercase identifiers")
    return deepcopy({
        "schema_version": "1.0", "task_id": task_id, "steps": steps,
        "risk_rules": rules, "deviation_types": deviations,
        "source_refs": source_refs, "status": status,
    })


def load_knowledge(path: str | Path) -> dict[str, object]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise StateRiskKnowledgeError("knowledge document must contain a mapping")
    return validate_knowledge(payload)
