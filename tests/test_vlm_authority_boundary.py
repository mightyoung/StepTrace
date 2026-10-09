from tests._evidence_helpers import authorized
from display_project.sop.authority import VLMAuthorityBoundary
from display_project.sop.state_machine import Evidence, SOPStateMachine


def _boundary():
    return VLMAuthorityBoundary(SOPStateMachine(["a", "b", "c"]))


def test_vlm_only_evidence_is_audit_only_and_does_not_write_state():
    boundary = _boundary()
    result = boundary.observe(Evidence(vlm_step="a"))
    assert result.transition is None
    assert result.state_write_allowed is False
    assert result.annotation_used is False
    assert boundary.machine.current_step is None


def test_conflicting_vlm_and_deterministic_evidence_keeps_deterministic_authority():
    boundary = _boundary()
    result = boundary.observe(
        Evidence(deterministic_step=authorized("a").deterministic_step, vlm_step="b")
    )
    assert result.transition is not None
    assert result.state_write_allowed is True
    assert result.annotation_used is False
    assert result.transition.current_step == "a"


def test_unavailable_evidence_is_unknown_and_cannot_write():
    boundary = _boundary()
    result = boundary.observe(Evidence())
    assert result.transition is None
    assert result.state_write_allowed is False
    assert result.reason == "vlm_only_or_unavailable"


def test_forged_or_unrecognized_deterministic_step_cannot_advance():
    boundary = _boundary()
    result = boundary.observe(
        authorized("forged")
    )
    assert result.transition is not None
    assert result.transition.decision == "unknown"
    assert result.state_write_allowed is False
    assert boundary.machine.current_step is None


def test_skip_and_reverse_are_deviations_without_state_mutation():
    boundary = _boundary()
    assert (
        boundary.observe(
            authorized("a")
        ).state_write_allowed
        is True
    )
    skipped = boundary.observe(
        authorized("c")
    )
    assert skipped.transition.decision == "deviation"
    assert skipped.state_write_allowed is False
    assert boundary.machine.current_step == "a"
    reversed_ = boundary.observe(
        authorized("a")
    )
    assert (
        reversed_.transition.decision == "normal"
    )  # current-step re-observation is safe
    assert reversed_.state_write_allowed is True
    assert reversed_.annotation_used is False


def test_deterministic_evidence_progresses_in_order_and_never_uses_annotations():
    boundary = _boundary()
    for step in ("a", "b", "c"):
        result = boundary.observe(
            Evidence(deterministic_step=authorized(step).deterministic_step)
        )
        assert result.transition.decision == "normal"
        assert result.state_write_allowed is True
        assert result.annotation_used is False
    assert boundary.machine.is_complete
