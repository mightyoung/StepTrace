"""Typed, fail-closed lifecycle contract for G3 experiment authority."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping


G3_CONTRACT_SCHEMA = "display_project.g3_contract_state-1.0.0"
CANONICAL_TASK_DATASET = {
    "meccano": "MECCANO",
    "industreal": "INDUSTREAL",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LOGICAL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_./-]*$")


class G3State(str, Enum):
    BLOCKED = "BLOCKED"
    IMPLEMENTATION_READY = "IMPLEMENTATION_READY"
    RUN_ADMISSION_READY = "G3_RUN_ADMISSION_READY"
    DECIDED = "G3_DECIDED"


class TransitionEvent(str, Enum):
    OBSERVE = "OBSERVE"
    ADVANCE = "ADVANCE"
    REVOKE = "REVOKE"
    SUPERSEDE = "SUPERSEDE"


_FORWARD_TRANSITIONS = {
    G3State.BLOCKED: G3State.IMPLEMENTATION_READY,
    G3State.IMPLEMENTATION_READY: G3State.RUN_ADMISSION_READY,
    G3State.RUN_ADMISSION_READY: G3State.DECIDED,
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


def _logical_id_valid(value: object) -> bool:
    if not isinstance(value, str) or LOGICAL_ID_RE.fullmatch(value) is None:
        return False
    _, path = value.split(":", 1)
    return all(part not in {"", ".", ".."} for part in path.split("/"))


def validate_transition(
    previous: G3State | str | None,
    current: G3State | str,
    event: TransitionEvent | str,
) -> tuple[bool, str | None]:
    """Validate one lifecycle transition without inferring missing stages."""
    try:
        current_state = G3State(current)
        transition = TransitionEvent(event)
        previous_state = None if previous is None else G3State(previous)
    except ValueError:
        return False, "UNKNOWN_STATE_OR_EVENT"
    if transition is TransitionEvent.OBSERVE:
        return (
            (True, None)
            if previous_state is None and current_state is G3State.BLOCKED
            else (False, "INVALID_INITIAL_STATE")
        )
    if previous_state is None:
        return False, "MISSING_PREVIOUS_STATE"
    if transition is TransitionEvent.ADVANCE:
        if _FORWARD_TRANSITIONS.get(previous_state) is current_state:
            return True, None
        return False, "SKIPPED_OR_BACKWARD_TRANSITION"
    if transition in {TransitionEvent.REVOKE, TransitionEvent.SUPERSEDE}:
        if current_state is G3State.BLOCKED and previous_state is not G3State.BLOCKED:
            return True, None
        return False, "INVALID_REVOCATION_TRANSITION"
    return False, "UNKNOWN_STATE_OR_EVENT"


def validate_task_dataset_bindings(
    bindings: object,
) -> tuple[bool, list[str]]:
    """Require the exact two-task/two-dataset G3 bijection and distinct configs."""
    if not isinstance(bindings, list):
        return False, ["TASK_DATASET_BINDINGS_NOT_LIST"]
    reasons: list[str] = []
    if len(bindings) != 2 or any(not isinstance(item, Mapping) for item in bindings):
        return False, ["TASK_DATASET_BINDING_COUNT_INVALID"]
    expected_keys = {"task_id", "dataset_id", "task_config_hash"}
    if any(set(item) != expected_keys for item in bindings):
        reasons.append("TASK_DATASET_BINDING_SCHEMA_INVALID")
    tasks = [item.get("task_id") for item in bindings]
    datasets = [item.get("dataset_id") for item in bindings]
    hashes = [item.get("task_config_hash") for item in bindings]
    if set(tasks) != set(CANONICAL_TASK_DATASET) or len(set(tasks)) != 2:
        reasons.append("CANONICAL_TASK_SET_INVALID")
    if set(datasets) != set(CANONICAL_TASK_DATASET.values()) or len(set(datasets)) != 2:
        reasons.append("CANONICAL_DATASET_SET_INVALID")
    if any(CANONICAL_TASK_DATASET.get(task) != dataset for task, dataset in zip(tasks, datasets)):
        reasons.append("TASK_DATASET_BIJECTION_INVALID")
    if any(not isinstance(value, str) or HASH_RE.fullmatch(value) is None for value in hashes):
        reasons.append("TASK_CONFIG_HASH_INVALID")
    elif len(set(hashes)) != 2:
        reasons.append("TASK_CONFIG_HASH_DUPLICATE")
    return not reasons, reasons


def _prohibitions(state: G3State) -> list[str]:
    shared = ["pilot", "power_analysis", "formal_test", "cross_task_generalization"]
    if state in {G3State.BLOCKED, G3State.IMPLEMENTATION_READY}:
        return ["G3_method_panel_execution", "G3_decision", *shared]
    if state is G3State.RUN_ADMISSION_READY:
        return ["G3_decision_without_complete_admitted_evidence", *shared]
    return ["G3_method_panel_reexecution", *shared]


def build_state_artifact(
    *,
    logical_id: str,
    previous_state: G3State | str | None,
    state: G3State | str,
    event: TransitionEvent | str,
    observed_at: str,
    source_hashes: Mapping[str, str],
    reason_codes: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    valid, error = validate_transition(previous_state, state, event)
    if not valid:
        raise ValueError(error)
    if not _logical_id_valid(logical_id):
        raise ValueError("LOGICAL_ID_INVALID")
    if not isinstance(observed_at, str) or not observed_at.endswith("Z"):
        raise ValueError("OBSERVED_AT_NOT_UTC")
    if not source_hashes or any(
        not _logical_id_valid(key) or HASH_RE.fullmatch(value) is None
        for key, value in source_hashes.items()
    ):
        raise ValueError("SOURCE_HASHES_INVALID")
    folded = [key.casefold() for key in source_hashes]
    if len(folded) != len(set(folded)):
        raise ValueError("SOURCE_LOGICAL_ID_CASE_COLLISION")
    if not reason_codes or any(
        not isinstance(reason, str) or not reason.strip() for reason in reason_codes
    ):
        raise ValueError("REASON_CODES_INVALID")
    current = G3State(state)
    body: dict[str, Any] = {
        "schema_version": G3_CONTRACT_SCHEMA,
        "logical_id": logical_id,
        "previous_state": (
            None if previous_state is None else G3State(previous_state).value
        ),
        "state": current.value,
        "transition_event": TransitionEvent(event).value,
        "observed_at": observed_at,
        "source_hashes": dict(sorted(source_hashes.items())),
        "reason_codes": list(reason_codes),
        "execute_g3_method_panel_allowed": current is G3State.RUN_ADMISSION_READY,
        "downstream_authorized": False,
        "prohibited_actions": _prohibitions(current),
    }
    body["artifact_sha256"] = canonical_sha256(body)
    return body


def validate_state_artifact(value: Mapping[str, Any] | object) -> tuple[bool, list[str]]:
    """Validate exact schema, hash, identifiers and lifecycle semantics."""
    if not isinstance(value, Mapping):
        return False, ["ARTIFACT_NOT_MAPPING"]
    expected = {
        "schema_version",
        "logical_id",
        "previous_state",
        "state",
        "transition_event",
        "observed_at",
        "source_hashes",
        "reason_codes",
        "execute_g3_method_panel_allowed",
        "downstream_authorized",
        "prohibited_actions",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(value) != expected:
        reasons.append("SCHEMA_KEYS_INVALID")
    if value.get("schema_version") != G3_CONTRACT_SCHEMA:
        reasons.append("SCHEMA_VERSION_INVALID")
    supplied_hash = value.get("artifact_sha256")
    try:
        body = {key: item for key, item in value.items() if key != "artifact_sha256"}
        if supplied_hash != canonical_sha256(body):
            reasons.append("ARTIFACT_HASH_INVALID")
    except (TypeError, ValueError):
        reasons.append("ARTIFACT_HASH_INVALID")
    valid_transition, transition_error = validate_transition(
        value.get("previous_state"),
        value.get("state", ""),
        value.get("transition_event", ""),
    )
    if not valid_transition:
        reasons.append(transition_error or "TRANSITION_INVALID")
    source_hashes = value.get("source_hashes")
    if not _logical_id_valid(value.get("logical_id")):
        reasons.append("LOGICAL_ID_INVALID")
    if not isinstance(source_hashes, Mapping) or not source_hashes:
        reasons.append("SOURCE_HASHES_INVALID")
    else:
        keys = list(source_hashes)
        if any(
            not _logical_id_valid(key)
            or not isinstance(item, str)
            or HASH_RE.fullmatch(item) is None
            for key, item in source_hashes.items()
        ):
            reasons.append("SOURCE_HASHES_INVALID")
        if len({str(key).casefold() for key in keys}) != len(keys):
            reasons.append("SOURCE_LOGICAL_ID_CASE_COLLISION")
    try:
        state = G3State(value.get("state", ""))
        expected_execute = state is G3State.RUN_ADMISSION_READY
        if value.get("execute_g3_method_panel_allowed") is not expected_execute:
            reasons.append("EXECUTION_PERMISSION_INVALID")
        if value.get("prohibited_actions") != _prohibitions(state):
            reasons.append("PROHIBITIONS_INVALID")
    except ValueError:
        pass
    if value.get("downstream_authorized") is not False:
        reasons.append("DOWNSTREAM_AUTHORITY_INVALID")
    return not reasons, reasons
