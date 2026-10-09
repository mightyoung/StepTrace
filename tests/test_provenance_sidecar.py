import json

import pytest

from display_project.evidence import (
    EvidenceCertificate,
    ProcessEvidenceGraph,
    VerificationContext,
    verify_certificate,
)
from display_project.provenance import (
    compiled_task_hash,
    composite_model_hash,
    load_sidecar_record,
    sha256_asset,
    sidecar_record,
)
from display_project.sop.task_config import load_task_config
from display_project.sop.visual_evidence import ObjectObservation, VisualEvidenceResult
from display_project.risk import RiskCalibrator
from display_project.tracking.real_sop_zh import evaluate_and_commit_frame


def _certificate():
    graph = (
        ProcessEvidenceGraph()
        .add_node(
            {
                "id": "ctx",
                "kind": "context",
                "task_id": "t",
                "expected_step": "a",
                "current_step": None,
            }
        )
        .add_node(
            {
                "id": "obj",
                "kind": "object",
                "frame": 0,
                "entity": "part",
                "predicate": "detected",
                "source": "model_geometry",
                "value": {"confidence": 0.95, "xyxy": [1, 2, 3, 4]},
            }
        )
        .add_node({"id": "pre", "kind": "step_state", "step": "START", "frame": 0})
        .add_node(
            {
                "id": "rel",
                "kind": "relation",
                "frame": 0,
                "entity": "t",
                "predicate": "confirmed_step",
                "source": "model_geometry",
                "value": "a",
            }
        )
        .add_node({"id": "cand", "kind": "step_state", "step": "a", "frame": 0})
        .add_edge({"source": "ctx", "target": "obj", "kind": "observation"})
        .add_edge({"source": "obj", "target": "rel", "kind": "dependency"})
        .add_edge({"source": "rel", "target": "cand", "kind": "dependency"})
        .add_edge(
            {
                "source": "pre",
                "target": "cand",
                "kind": "transition",
                "task_steps": ["START", "a"],
            }
        )
    )
    vc = VerificationContext("t", "a", None, "a", "ctx", "cand", 0, 0)
    cert = EvidenceCertificate.issue(
        graph,
        (
            {
                "source": "model_geometry",
                "id": "obj",
                "type": "detected",
                "value": {"confidence": 0.95, "xyxy": [1, 2, 3, 4]},
            },
            {
                "source": "model_geometry",
                "id": "rel",
                "type": "confirmed_step",
                "value": "a",
            },
        ),
        task_id="t",
        expected_step="a",
        current_step=None,
        candidate="a",
        pre_state=None,
        risk_score=0.1,
        risk_threshold=0.5,
        coverage=1.0,
        frame=0,
        calibration_version="v1",
        verification_context=vc,
        input_hash="0" * 64,
        model_hash="1" * 64,
        config_hash="2" * 64,
        authorizing_evidence_refs=("obj", "rel"),
        confirmed_transition=True,
    )
    return cert, graph, vc


def test_file_tree_and_composite_hashes_are_content_based(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (left, right):
        (root / "nested").mkdir(parents=True)
        (root / "nested" / "weights.bin").write_bytes(b"abc")
    assert sha256_asset(left) == sha256_asset(right)
    assert composite_model_hash(
        left, left / "nested" / "weights.bin"
    ) == composite_model_hash(right, right / "nested" / "weights.bin")
    (right / "nested" / "weights.bin").write_bytes(b"changed")
    assert composite_model_hash(
        left, left / "nested" / "weights.bin"
    ) != composite_model_hash(right, right / "nested" / "weights.bin")
    with pytest.raises(ValueError):
        sha256_asset(tmp_path / "empty") if (
            tmp_path / "empty"
        ).mkdir() is None else None


def test_compiled_task_hash_covers_all_dataclass_fields():
    task = load_task_config("configs/tasks/meccano.yaml")
    altered = task.__class__(
        task.task_id,
        task.schema_version,
        task.step_specs,
        task.allowed_classes + ("new",),
        task.detector_prompts,
        task.class_aliases,
    )
    assert compiled_task_hash(task) != compiled_task_hash(altered)


def test_sidecar_roundtrip_and_tamper_rejection():
    cert, graph, vc = _certificate()
    record = sidecar_record(cert, graph)
    restored_cert, restored_graph = load_sidecar_record(json.loads(json.dumps(record)))
    assert verify_certificate(restored_cert, restored_graph, context=vc).valid
    tampered_cert = json.loads(json.dumps(record))
    tampered_cert["certificate"]["risk_score"] = 0.9
    c2, g2 = load_sidecar_record(tampered_cert)
    assert not verify_certificate(c2, g2, context=vc).valid
    tampered_graph = json.loads(json.dumps(record))
    tampered_graph["graph"]["nodes"][1]["value"]["confidence"] = 0.1
    c3, g3 = load_sidecar_record(tampered_graph)
    assert not verify_certificate(c3, g3, context=vc).valid
    tampered_hash = json.loads(json.dumps(record))
    tampered_hash["certificate"]["certificate_hash"] = "f" * 64
    c4, g4 = load_sidecar_record(tampered_hash)
    assert not verify_certificate(c4, g4, context=vc).valid


def test_runtime_evaluate_commits_with_real_observation_and_context():
    task = load_task_config("configs/tasks/meccano.yaml")
    machine = task.state_machine()
    step = task.steps[0]
    visual = VisualEvidenceResult(step, step, 4, (), {})
    result = evaluate_and_commit_frame(
        machine=machine,
        task=task,
        visual=visual,
        observations=(ObjectObservation("metal bar", 0.95, (1, 1, 20, 20)),),
        risk_calibrator=RiskCalibrator().fit([0.0], [0.0]),
        input_hash="0" * 64,
        model_hash="1" * 64,
        config_hash="2" * 64,
    )
    assert result["committed"] is True
    assert result["certificate_result"].valid is True
