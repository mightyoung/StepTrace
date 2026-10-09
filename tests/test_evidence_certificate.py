import pytest
from dataclasses import replace
from display_project.evidence import (
    EvidenceCertificate,
    VerificationContext,
    ProcessEvidenceGraph,
    verify_certificate,
)


def graph(frame=0, negative=False):
    g = (
        ProcessEvidenceGraph()
        .add_node(
            {
                "id": "ctx",
                "kind": "context",
                "task_id": "t",
                "expected_step": "a",
                "current_step": None,
                "frame": frame,
            }
        )
        .add_node({"id": "pre", "kind": "step_state", "step": "START", "frame": frame})
        .add_node({"id": "cand", "kind": "step_state", "step": "a", "frame": frame})
        .add_node(
            {
                "id": "obj",
                "kind": "object",
                "frame": frame,
                "entity": "part",
                "predicate": "detected",
                "source": "model_geometry",
                "value": {"confidence": 0.9, "xyxy": [1, 2, 3, 4]},
            }
        )
        .add_node(
            {
                "id": "rel",
                "kind": "relation",
                "frame": frame,
                "entity": "t",
                "predicate": "confirmed_step",
                "source": "model_geometry",
                "value": "a",
            }
        )
        .add_edge(
            {
                "source": "pre",
                "target": "cand",
                "kind": "transition",
                "task_steps": ["START", "a"],
            }
        )
        .add_edge({"source": "ctx", "target": "obj", "kind": "observation"})
        .add_edge({"source": "obj", "target": "rel", "kind": "dependency"})
        .add_edge({"source": "rel", "target": "cand", "kind": "dependency"})
    )
    if negative:
        g = g.add_node(
            {
                "id": "neg",
                "kind": "negative",
                "frame": frame,
                "context_id": "ctx",
                "reasons": ["missing"],
            }
        ).add_edge({"source": "neg", "target": "cand", "kind": "negative"})
    return g


def cert(g=None, **kw):
    g = g or graph()
    vc = VerificationContext("t", "a", None, "a", "ctx", "cand", 0, 0)
    return EvidenceCertificate.issue(
        g,
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
        coverage=1,
        frame=0,
        calibration_version="cal-v1",
        verification_context=vc,
        input_hash="0" * 64,
        model_hash="1" * 64,
        config_hash="2" * 64,
        authorizing_evidence_refs=("obj", "rel"),
        confirmed_transition=True,
        **kw,
    )


def test_valid_and_order_independent():
    g = graph()
    c = cert(g)
    assert verify_certificate(c, g, context=c.verification_context).valid
    assert (
        g.digest()
        == ProcessEvidenceGraph(
            tuple(reversed(g.nodes)), tuple(reversed(g.edges))
        ).digest()
    )


def test_required_context_risk_provenance_and_transition_proof():
    c = cert()
    assert (
        verify_certificate(replace(c, verification_context=None), graph()).reason
        == "tampered"
    )
    assert verify_certificate(replace(c, risk_score=None), graph()).reason == "tampered"
    assert verify_certificate(replace(c, input_hash=None), graph()).reason == "tampered"
    assert (
        verify_certificate(replace(c, confirmed_transition=False), graph()).reason
        == "tampered"
    )
    assert (
        verify_certificate(
            replace(
                c,
                verification_context=VerificationContext(
                    "t", "a", None, "a", "ctx", "missing", 0, 0
                ),
            ),
            graph(),
        ).reason
        == "tampered"
    )


def test_uppercase_provenance_hash_is_rejected_after_rehash():
    original = cert()
    forged = replace(original, input_hash="A" * 64, certificate_hash="")
    forged = replace(forged, certificate_hash=forged.digest())
    assert (
        verify_certificate(forged, graph(), context=forged.verification_context).reason
        == "missing_provenance"
    )


def test_negative_same_context_blocks_but_history_does_not():
    assert (
        verify_certificate(
            cert(graph(negative=True)),
            graph(negative=True),
            context=cert(graph(negative=True)).verification_context,
        ).reason
        == "negative_evidence"
    )
    assert verify_certificate(
        cert(graph()), graph(), context=cert(graph()).verification_context
    ).valid


def test_vlm_annotation_and_tamper_fail_closed():
    g = graph()
    assert (
        verify_certificate(replace(cert(g), annotation_used=True), g).reason
        == "tampered"
    )
    assert (
        verify_certificate(
            replace(cert(g), evidence=({"source": "vlm", "id": "rel"},)), g
        ).reason
        == "tampered"
    )
    assert verify_certificate(replace(cert(g), risk_ok=False), g).reason == "tampered"


def test_graph_hostile_edges_fail_closed():
    with pytest.raises(ValueError):
        ProcessEvidenceGraph(
            ({"id": "a", "kind": "context"}, {"id": "b", "kind": "step_state"}),
            ({"source": "a", "target": "b", "kind": "dependency"},),
        ).replay()
    with pytest.raises(ValueError):
        ProcessEvidenceGraph(
            ({"id": "a", "kind": "step_state"},),
            ({"source": "a", "target": "a", "kind": "transition"},),
        ).replay()
