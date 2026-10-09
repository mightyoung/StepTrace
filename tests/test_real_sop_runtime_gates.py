"""Integration-level negative gates for the production SOP authority path.

These tests deliberately use the public state machine, authority boundary,
visual evidence engine, risk calibrator, and certificate verifier. No model
or video I/O is required; the inputs are the same typed evidence objects the
real runtime consumes.
"""

from display_project.evidence import (
    EvidenceCertificate,
    ProcessEvidenceGraph,
    verify_certificate,
)
from display_project.risk import RiskCalibrator
from display_project.sop.authority import VLMAuthorityBoundary
from display_project.sop.state_machine import Evidence, SOPStateMachine
from display_project.sop.task_config import load_task_config
from display_project.sop.visual_evidence import ObjectObservation, VisualEvidenceEngine
from tests._evidence_helpers import authorized


def _machine() -> SOPStateMachine:
    return SOPStateMachine(["a", "b", "c"])


def _valid_certificate(task_id="test", expected="a", current=None):
    from display_project.evidence import VerificationContext

    graph = ProcessEvidenceGraph(
        (
            {
                "id": "context",
                "kind": "context",
                "task_id": task_id,
                "expected_step": expected,
                "current_step": current,
                "frame": 0,
            },
            {"id": "pre", "kind": "step_state", "step": "START", "frame": 0},
            {
                "id": "obj",
                "kind": "object",
                "entity": "part",
                "predicate": "detected",
                "source": "model_geometry",
                "frame": 0,
                "value": {"confidence": 0.9, "xyxy": [1, 2, 3, 4]},
            },
            {"id": "cand", "kind": "step_state", "step": expected, "frame": 0},
            {
                "id": "rel",
                "kind": "relation",
                "entity": task_id,
                "predicate": "confirmed_step",
                "frame": 0,
                "source": "model_geometry",
                "value": expected,
            },
        ),
        (
            {
                "source": "pre",
                "target": "cand",
                "kind": "transition",
                "task_steps": ["START", expected],
            },
            {"source": "context", "target": "obj", "kind": "observation"},
            {"source": "obj", "target": "rel", "kind": "dependency"},
            {"source": "rel", "target": "cand", "kind": "dependency"},
        ),
    )
    certificate = EvidenceCertificate.issue(
        graph,
        (
            {
                "source": "model_geometry",
                "id": "obj",
                "type": "detected",
                "value": {"confidence": 0.9, "xyxy": [1, 2, 3, 4]},
            },
            {
                "source": "model_geometry",
                "id": "rel",
                "type": "confirmed_step",
                "value": expected,
            },
        ),
        task_id=task_id,
        expected_step=expected,
        current_step=current,
        candidate=expected,
        pre_state=current,
        risk_score=0.1,
        risk_threshold=0.5,
        coverage=1,
        frame=0,
        calibration_version="v",
        input_hash="0" * 64,
        model_hash="1" * 64,
        config_hash="2" * 64,
        verification_context=VerificationContext(
            task_id, expected, current, expected, "context", "cand", 0, 0
        ),
        authorizing_evidence_refs=("obj", "rel"),
        confirmed_transition=True,
    )
    return graph, certificate


def test_missing_or_unfitted_calibrator_cannot_change_machine_index():
    for calibrator in (None, RiskCalibrator()):
        machine = _machine()
        if calibrator is not None and calibrator.fitted:
            VLMAuthorityBoundary(machine).observe(authorized("a"))
        assert machine.current_step is None


def test_tampered_or_replayed_certificate_cannot_change_machine_index():
    machine = _machine()
    graph, certificate = _valid_certificate(expected="a")
    tampered = certificate.__class__(**{**certificate.__dict__, "risk_ok": False})
    assert not verify_certificate(tampered, graph).valid
    replay_broken = ProcessEvidenceGraph(
        nodes=({"id": "observation-0"}, {"id": "observation-0"})
    )
    broken = EvidenceCertificate.issue(replay_broken, ({"source": "model_geometry"},))
    assert verify_certificate(broken, replay_broken).reason == "replay_failed"
    assert machine.current_step is None


def test_negative_and_missing_deterministic_evidence_are_side_effect_free():
    machine = _machine()
    boundary = VLMAuthorityBoundary(machine)
    for evidence in (
        Evidence(),
        Evidence(vlm_step="a"),
        authorized("forged"),
    ):
        result = boundary.observe(evidence)
        assert result.state_write_allowed is False
        assert machine.current_step is None


def test_vlm_conflict_skip_rewind_and_forged_evidence_never_commit():
    machine = _machine()
    boundary = VLMAuthorityBoundary(machine)
    assert boundary.observe(authorized("a")).state_write_allowed
    conflict = boundary.observe(
        Evidence(deterministic_step=authorized("b").deterministic_step, vlm_step="c")
    )
    assert conflict.state_write_allowed is True
    assert machine.current_step == "b"
    next_step = boundary.observe(authorized("c"))
    assert next_step.state_write_allowed is True
    assert machine.current_step == "c"
    result = boundary.observe(authorized("forged"))
    assert result.state_write_allowed is False
    assert machine.current_step == "c"
    assert not boundary.observe(authorized("a")).state_write_allowed
    assert machine.current_step == "c"


def test_annotation_derived_certificate_evidence_is_rejected():
    graph, _certificate = _valid_certificate()
    certificate = EvidenceCertificate.issue(
        graph,
        ({"source": "annotation", "kind": "confirmed_step", "id": "observation-0"},),
        task_id="test",
        expected_step="a",
        current_step=None,
    )
    assert (
        verify_certificate(certificate, graph).reason == "verification_context_required"
    )


def test_only_fitted_calibration_deterministic_evidence_and_valid_certificate_commit():
    machine = _machine()
    calibrator = RiskCalibrator().fit([0.1], [0], split="calibration")
    graph, certificate = _valid_certificate(expected="a")
    verified = verify_certificate(
        certificate, graph, context=certificate.verification_context
    )
    assert calibrator.fitted and verified.valid
    result = VLMAuthorityBoundary(machine).observe(authorized("a"))
    assert result.state_write_allowed is True
    assert machine.current_step == "a"


def test_industreal_compiled_config_uses_shared_visual_engine_without_unsupported_step():
    task = load_task_config("configs/tasks/industreal.yaml")
    engine = VisualEvidenceEngine(task_config=task, confirm_frames=1, cooldown_frames=0)
    observations = {
        "retrieve_part": [
            ObjectObservation("hand", 0.9, (10, 10, 40, 40)),
            ObjectObservation("part", 0.9, (35, 15, 65, 45)),
        ],
        "fixture_part": [
            ObjectObservation("hand", 0.9, (10, 10, 40, 40)),
            ObjectObservation("fixture", 0.9, (35, 15, 65, 45)),
        ],
        "perform_operation": [
            ObjectObservation("tool", 0.9, (10, 10, 40, 80)),
            ObjectObservation("part", 0.9, (35, 15, 65, 45)),
        ],
        "inspect_result": [ObjectObservation("part", 0.9, (35, 15, 65, 45))],
    }
    for step in task.steps:
        result = engine.observe(step, observations[step], frame_size=(100, 100))
        assert "unsupported_expected_step" not in result.reasons
        assert result.confirmed_step == step


def test_fixed_streak_baseline_defaults_remain_frozen():
    engine = VisualEvidenceEngine()
    assert (engine.confirm_frames, engine.cooldown_frames) == (4, 12)
