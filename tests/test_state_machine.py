import pytest
from tests._evidence_helpers import authorized
from display_project.sop.state_machine import (
    Evidence,
    DeterministicEvidence,
    SOPStateMachine,
)


def test_five_steps_advance_only_in_order():
    m = SOPStateMachine([f"step_{i}" for i in range(1, 6)])
    for i in range(1, 6):
        t = m.observe(authorized(f"step_{i}"))
        assert t.decision == "normal"
    assert m.is_complete


def test_skipped_step_is_deviation_and_does_not_advance_progress():
    m = SOPStateMachine(["step_1", "step_2", "step_3"])
    m.observe(authorized("step_1"))
    t = m.observe(authorized("step_3"))
    assert t.decision == "deviation" and m.current_step == "step_1"


def test_reverse_step_is_deviation_and_does_not_rewind_progress():
    m = SOPStateMachine(["step_1", "step_2", "step_3"])
    m.observe(authorized("step_1"))
    m.observe(authorized("step_2"))
    t = m.observe(authorized("step_1"))
    assert t.decision == "deviation" and m.current_step == "step_2"


def test_insufficient_evidence_is_unknown_and_vlm_does_not_advance():
    m = SOPStateMachine(["step_1"])
    t = m.observe(Evidence(vlm_step="step_1"))
    assert t.decision == "unknown" and m.current_step is None


def test_deterministic_evidence_wins_when_vlm_disagrees():
    e = authorized("step_1")
    t = SOPStateMachine(["step_1"]).observe(
        Evidence(deterministic_step=e.deterministic_step, vlm_step="step_4")
    )
    assert t.current_step == "step_1"


def test_raw_deterministic_string_without_source_fails_closed():
    with pytest.raises(TypeError):
        Evidence(deterministic_step="step_1")


def test_direct_string_with_source_fails_closed():
    with pytest.raises(TypeError):
        Evidence(deterministic_step="a", deterministic_source="model_geometry")


def test_direct_capability_constructor_fails_closed():
    with pytest.raises(TypeError):
        DeterministicEvidence("a", "model_geometry")
