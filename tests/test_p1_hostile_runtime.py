"""Persistent hostile regressions for the strict P1 runtime.

These tests intentionally exercise public PEG/certificate/runtime entry points.
They are fail-closed checks: malformed or unauthorised evidence must never
advance the state machine, while a valid cumulative two-step graph must commit.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from display_project.evidence import (
    EvidenceCertificate,
    ProcessEvidenceGraph,
    VerificationContext,
    verify_certificate,
)
from display_project.risk import RiskCalibrator
from display_project.sop.task_config import load_task_config
from display_project.sop.visual_evidence import ObjectObservation
from display_project.tracking.real_sop_zh import evaluate_and_commit_frame
from display_project.experiments import FixedStreakConfig
from display_project.experiments.paired_runner import fixed_streak_adapter


HASHES = ("0" * 64, "1" * 64, "2" * 64)


def _task():
    return load_task_config("configs/tasks/meccano.yaml")


def _visual(step, *, reasons=()):
    return SimpleNamespace(
        candidate_step=step, confirmed_step=step, reasons=tuple(reasons)
    )


def _obs(confidence=0.95):
    return (
        ObjectObservation("hand", confidence, (10, 10, 40, 40)),
        ObjectObservation("metal bar", confidence, (35, 15, 65, 45)),
    )


def _calibrator():
    return RiskCalibrator().fit([0.1], [0], split="calibration")


def _run(machine, *, frame, step, graph=None, review=None, confidence=0.95, **kwargs):
    return evaluate_and_commit_frame(
        machine=machine,
        task=_task(),
        visual=_visual(step, reasons=kwargs.pop("reasons", ())),
        review=review,
        risk_calibrator=kwargs.pop("risk_calibrator", _calibrator()),
        graph=graph,
        frame=frame,
        observations=_obs(confidence),
        input_hash=kwargs.pop("input_hash", HASHES[0]),
        model_hash=kwargs.pop("model_hash", HASHES[1]),
        config_hash=kwargs.pop("config_hash", HASHES[2]),
        risk_threshold=kwargs.pop("risk_threshold", 0.5),
        calibration_version=kwargs.pop("calibration_version", "frozen-v1"),
        **kwargs,
    )


def test_cumulative_graph_commits_two_consecutive_steps():
    machine = _task().state_machine()
    first = _run(machine, frame=0, step="pick_components")
    assert first["committed"]
    second = _run(machine, frame=1, step="align_components", graph=first["graph"])
    assert second["committed"]
    assert machine.current_step == "align_components"


def test_uppercase_runtime_provenance_cannot_commit():
    machine = _task().state_machine()
    result = _run(
        machine,
        frame=0,
        step="pick_components",
        input_hash="A" * 64,
    )
    assert result["committed"] is False
    assert result["certificate"] is None
    assert machine.current_step is None


def test_review_negative_is_audit_only_while_visual_negative_blocks():
    machine = _task().state_machine()
    reviewed = _run(machine, frame=0, step="pick_components", review={"negative": True})
    assert reviewed["committed"]
    machine = _task().state_machine()
    blocked = _run(
        machine, frame=0, step="pick_components", reasons=("missing_component",)
    )
    assert not blocked["committed"]
    recovered = _run(machine, frame=1, step="pick_components", graph=blocked["graph"])
    assert recovered["committed"]
    assert machine.current_step == "pick_components"


def test_low_confidence_risk_abstains_while_high_confidence_commits():
    low_machine = _task().state_machine()
    assert not _run(low_machine, frame=0, step="pick_components", confidence=0.2)[
        "committed"
    ]
    high_machine = _task().state_machine()
    assert _run(high_machine, frame=0, step="pick_components", confidence=0.95)[
        "committed"
    ]


@pytest.mark.parametrize("field", ["input_hash", "model_hash", "config_hash"])
def test_missing_or_invalid_provenance_hash_abstains(field):
    machine = _task().state_machine()
    assert not _run(machine, frame=0, step="pick_components", **{field: None})[
        "committed"
    ]
    machine = _task().state_machine()
    assert not _run(machine, frame=0, step="pick_components", **{field: "bad"})[
        "committed"
    ]


def test_unfitted_calibrator_and_metadata_mismatch_abstain():
    machine = _task().state_machine()
    assert not _run(
        machine, frame=0, step="pick_components", risk_calibrator=RiskCalibrator()
    )["committed"]
    machine = _task().state_machine()
    assert not _run(
        machine, frame=0, step="pick_components", calibration_version="wrong"
    )["committed"]
    machine = _task().state_machine()
    assert not _run(machine, frame=0, step="pick_components", risk_threshold=0.2)[
        "committed"
    ]


@pytest.mark.parametrize(
    "review",
    [
        {"observed_action": "align_components"},
        {"annotation_used": True},
        {"source": "cvat"},
        {"source": "oracle", "step": "pick_components"},
        {"source": "truth", "step": "pick_components"},
        {"source": "dataset", "step": "pick_components"},
    ],
)
def test_vlm_and_annotation_reviews_cannot_change_deterministic_commit(review):
    machine = _task().state_machine()
    assert _run(machine, frame=0, step="pick_components", review=review)["committed"]


def test_vlm_only_without_deterministic_candidate_never_commit():
    machine = _task().state_machine()
    result = evaluate_and_commit_frame(
        machine=machine,
        task=_task(),
        visual=SimpleNamespace(candidate_step=None, confirmed_step=None, reasons=()),
        review={"observed_action": "pick_components"},
        risk_calibrator=_calibrator(),
        frame=0,
        observations=(),
        input_hash=HASHES[0],
        model_hash=HASHES[1],
        config_hash=HASHES[2],
    )
    assert not result["committed"]


@pytest.mark.parametrize(
    "step", ["pick_components", "verify_assembly"], ids=["rewind", "skip"]
)
def test_skip_and_rewind_never_commit(step):
    machine = _task().state_machine()
    first = _run(machine, frame=0, step="pick_components")
    assert first["committed"]
    result = _run(machine, frame=1, step=step, graph=first["graph"])
    assert not result["committed"]
    assert machine.current_step == "pick_components"


def _valid_certificate_graph():
    graph = ProcessEvidenceGraph(
        nodes=(
            {
                "id": "ctx",
                "kind": "context",
                "task_id": "test",
                "expected_step": "a",
                "current_step": None,
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
                "value": {"confidence": 0.9},
            },
            {
                "id": "rel",
                "kind": "relation",
                "entity": "test",
                "predicate": "confirmed_step",
                "source": "model_geometry",
                "frame": 0,
                "value": "a",
            },
            {"id": "cand", "kind": "step_state", "step": "a", "frame": 0},
        ),
        edges=(
            {"source": "ctx", "target": "obj", "kind": "observation"},
            {"source": "obj", "target": "rel", "kind": "dependency"},
            {"source": "rel", "target": "cand", "kind": "dependency"},
            {
                "source": "pre",
                "target": "cand",
                "kind": "transition",
                "task_steps": ["START", "a"],
            },
        ),
    )
    vc = VerificationContext("test", "a", None, "a", "ctx", "cand", 0, 0)
    cert = EvidenceCertificate.issue(
        graph,
        (
            {
                "source": "model_geometry",
                "id": "obj",
                "type": "detected",
                "value": {"confidence": 0.9},
            },
            {
                "source": "model_geometry",
                "id": "rel",
                "type": "confirmed_step",
                "value": "a",
            },
        ),
        task_id="test",
        expected_step="a",
        current_step=None,
        candidate="a",
        pre_state=None,
        risk_ok=True,
        risk_score=0.1,
        risk_threshold=0.5,
        coverage=1.0,
        frame=0,
        calibration_version="v1",
        input_hash=HASHES[0],
        model_hash=HASHES[1],
        config_hash=HASHES[2],
        verification_context=vc,
        authorizing_evidence_refs=("obj", "rel"),
        confirmed_transition=True,
    )
    return graph, cert, vc


@pytest.mark.parametrize(
    "mutator",
    [
        lambda c, g: replace(c, verification_context=None),
        lambda c, g: replace(
            c,
            verification_context=replace(
                c.verification_context, context_node_id="missing"
            ),
        ),
        lambda c, g: replace(
            c,
            verification_context=replace(
                c.verification_context, candidate_node_id="missing"
            ),
        ),
        lambda c, g: replace(
            c,
            evidence=(
                {
                    "source": "model_geometry",
                    "id": "obj",
                    "type": "detected",
                    "value": {"confidence": 0.8},
                },
                {
                    "source": "model_geometry",
                    "id": "rel",
                    "type": "confirmed_step",
                    "value": "a",
                },
            ),
        ),
        lambda c, g: replace(c, authorizing_evidence_refs=("obj",)),
        lambda c, g: replace(c, negative_evidence_refs=("forged",)),
    ],
)
def test_certificate_context_refs_values_and_negative_refs_tamper_rejected(mutator):
    graph, cert, vc = _valid_certificate_graph()
    tampered = mutator(cert, graph)
    assert not verify_certificate(tampered, graph, context=vc).valid


@pytest.mark.parametrize(
    "bad_graph",
    [
        ProcessEvidenceGraph(
            nodes=({"id": "a", "kind": "context"}, {"id": "b", "kind": "object"}),
            edges=({"source": "a", "target": "b"},),
        ),
        ProcessEvidenceGraph(
            nodes=({"id": "a", "kind": "context"}, {"id": "b", "kind": "object"}),
            edges=({"source": "b", "target": "a", "kind": "observation"},),
        ),
        ProcessEvidenceGraph(
            nodes=({"id": "a", "kind": "context"}),
            edges=({"source": "a", "target": "a", "kind": "observation"},),
        ),
        ProcessEvidenceGraph(
            nodes=({"id": "a", "kind": "negative"}, {"id": "b", "kind": "step_state"}),
            edges=(
                {"source": "a", "target": "b", "kind": "negative"},
                {"source": "b", "target": "a", "kind": "dependency"},
            ),
        ),
    ],
)
def test_graph_missing_kind_direction_self_loop_and_cycle_rejected(bad_graph):
    with pytest.raises(ValueError):
        bad_graph.replay()


def test_fixed_streak_baseline_remains_four_and_twelve():
    assert fixed_streak_adapter().config == FixedStreakConfig(
        confirm_frames=4, cooldown_frames=12
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "object_frame",
        "object_source",
        "object_type",
        "object_path",
        "relation_value",
        "remove_relation",
    ],
)
def test_object_relation_frame_source_type_and_path_tamper_rejected(tamper):
    graph, cert, vc = _valid_certificate_graph()
    nodes = [dict(n) for n in graph.nodes]
    edges = list(graph.edges)
    if tamper == "object_frame":
        nodes[2]["frame"] = 1
    elif tamper == "object_source":
        nodes[2]["source"] = "annotation"
    elif tamper == "object_type":
        nodes[2]["predicate"] = "forged"
    elif tamper == "object_path":
        edges = [
            e for e in edges if not (e["source"] == "obj" and e["target"] == "rel")
        ]
    elif tamper == "relation_value":
        nodes[3]["value"] = "forged"
    elif tamper == "remove_relation":
        nodes = [n for n in nodes if n["id"] != "rel"]
    tampered_graph = ProcessEvidenceGraph(tuple(nodes), tuple(edges))
    assert not verify_certificate(cert, tampered_graph, context=vc).valid


def test_forged_negative_evidence_reference_rejected_even_with_recomputed_hash():
    graph, cert, vc = _valid_certificate_graph()
    forged = replace(
        cert, negative_evidence_refs=("forged-negative",), certificate_hash=""
    )
    forged = replace(forged, certificate_hash=forged.digest())
    assert not verify_certificate(forged, graph, context=vc).valid
