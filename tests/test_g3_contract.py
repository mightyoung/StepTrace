from __future__ import annotations

import pytest

from display_project.g3_contract import (
    G3State,
    TransitionEvent,
    build_state_artifact,
    validate_state_artifact,
    validate_task_dataset_bindings,
    validate_transition,
)


SOURCE_HASHES = {"gate:g1-g2-current": "a" * 64}


def artifact(**updates):
    values = {
        "logical_id": "state:g3-current",
        "previous_state": None,
        "state": G3State.BLOCKED,
        "event": TransitionEvent.OBSERVE,
        "observed_at": "2026-08-20T12:00:00Z",
        "source_hashes": SOURCE_HASHES,
        "reason_codes": ["RUN_ADMISSION_MISSING"],
    }
    values.update(updates)
    return build_state_artifact(**values)


def test_initial_state_is_fail_closed_and_non_authorizing():
    value = artifact()
    valid, reasons = validate_state_artifact(value)
    assert valid, reasons
    assert value["execute_g3_method_panel_allowed"] is False
    assert value["downstream_authorized"] is False
    assert "G3_method_panel_execution" in value["prohibited_actions"]


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (G3State.BLOCKED, G3State.RUN_ADMISSION_READY),
        (G3State.BLOCKED, G3State.DECIDED),
        (G3State.IMPLEMENTATION_READY, G3State.BLOCKED),
        (G3State.DECIDED, G3State.RUN_ADMISSION_READY),
    ],
)
def test_advance_rejects_skipped_and_backward_transitions(previous, current):
    valid, reason = validate_transition(previous, current, TransitionEvent.ADVANCE)
    assert valid is False
    assert reason == "SKIPPED_OR_BACKWARD_TRANSITION"


def test_only_admission_ready_state_can_authorize_method_panel():
    value = artifact(
        previous_state=G3State.IMPLEMENTATION_READY,
        state=G3State.RUN_ADMISSION_READY,
        event=TransitionEvent.ADVANCE,
        reason_codes=["ADMISSION_RECEIPT_VALID"],
    )
    valid, reasons = validate_state_artifact(value)
    assert valid, reasons
    assert value["execute_g3_method_panel_allowed"] is True
    assert value["downstream_authorized"] is False


def test_tampered_permission_and_hash_fail_validation():
    value = artifact()
    value["execute_g3_method_panel_allowed"] = True
    valid, reasons = validate_state_artifact(value)
    assert valid is False
    assert "ARTIFACT_HASH_INVALID" in reasons
    assert "EXECUTION_PERMISSION_INVALID" in reasons


def test_explicit_revocation_returns_to_blocked():
    value = artifact(
        previous_state=G3State.RUN_ADMISSION_READY,
        state=G3State.BLOCKED,
        event=TransitionEvent.REVOKE,
        reason_codes=["SOURCE_MUTATED_AFTER_ADMISSION"],
    )
    valid, reasons = validate_state_artifact(value)
    assert valid, reasons


def test_precheck_cannot_be_relabelled_as_state_artifact():
    valid, reasons = validate_state_artifact(
        {
            "schema_version": "g3-mechanism-precheck-1.0.0",
            "decision_type": "PRECHECK_ONLY",
            "status": "BLOCKED",
        }
    )
    assert valid is False
    assert "SCHEMA_VERSION_INVALID" in reasons
    assert "SCHEMA_KEYS_INVALID" in reasons


def test_logical_id_traversal_and_case_collisions_are_rejected():
    with pytest.raises(ValueError, match="LOGICAL_ID_INVALID"):
        artifact(logical_id="state:../g3")
    with pytest.raises(ValueError, match="CASE_COLLISION"):
        artifact(
            source_hashes={
                "gate:Current": "a" * 64,
                "gate:current": "b" * 64,
            }
        )


def test_exact_task_dataset_bijection_requires_distinct_task_configs():
    valid, reasons = validate_task_dataset_bindings(
        [
            {
                "task_id": "meccano",
                "dataset_id": "MECCANO",
                "task_config_hash": "a" * 64,
            },
            {
                "task_id": "industreal",
                "dataset_id": "INDUSTREAL",
                "task_config_hash": "b" * 64,
            },
        ]
    )
    assert valid, reasons


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("task_id", "MECCANO", "CANONICAL_TASK_SET_INVALID"),
        ("dataset_id", "IndustReal", "CANONICAL_DATASET_SET_INVALID"),
        ("dataset_id", "MECCANO", "TASK_DATASET_BIJECTION_INVALID"),
        ("task_config_hash", "a" * 64, "TASK_CONFIG_HASH_DUPLICATE"),
    ],
)
def test_task_dataset_alias_swap_and_duplicate_hash_fail_closed(
    field, value, reason
):
    bindings = [
        {
            "task_id": "meccano",
            "dataset_id": "MECCANO",
            "task_config_hash": "a" * 64,
        },
        {
            "task_id": "industreal",
            "dataset_id": "INDUSTREAL",
            "task_config_hash": "b" * 64,
        },
    ]
    bindings[1][field] = value
    valid, reasons = validate_task_dataset_bindings(bindings)
    assert valid is False
    assert reason in reasons
