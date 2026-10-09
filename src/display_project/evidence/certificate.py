from dataclasses import dataclass
import hashlib
import json
from typing import Mapping


AUTHORIZING_EVIDENCE_SOURCES = frozenset(
    {"model_geometry", "detector", "tracker", "segmentation", "deterministic"}
)


def _canon(x):
    return json.dumps(
        x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _raw_digest(graph):
    return hashlib.sha256(_canon(graph.canonical()).encode()).hexdigest()


@dataclass(frozen=True)
class VerificationContext:
    task_id: str
    expected_step: str
    pre_state: str | None
    candidate_step: str
    context_node_id: str
    candidate_node_id: str
    time_start: int
    time_end: int


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    reason: str = ""


@dataclass(frozen=True)
class EvidenceCertificate:
    graph_digest: str
    evidence: tuple
    risk_ok: bool
    negative: tuple = ()
    certificate_hash: str = ""
    risk_score: float | None = None
    risk_threshold: float | None = None
    task_id: str | None = None
    expected_step: str | None = None
    current_step: str | None = None
    method: str = "Proposed"
    calibration_version: str | None = None
    coverage: float | None = None
    frame: int | None = None
    pre_state: str | None = None
    candidate: str | None = None
    verification_context: VerificationContext | None = None
    input_hash: str | None = None
    model_hash: str | None = None
    config_hash: str | None = None
    annotation_used: bool = False
    authorizing_evidence_refs: tuple = ()
    negative_evidence_refs: tuple = ()
    confirmed_transition: bool = False

    def payload(self):
        return {
            k: v
            for k, v in {
                "graph_digest": self.graph_digest,
                "evidence": list(self.evidence),
                "risk_ok": self.risk_ok,
                "negative": list(self.negative),
                "risk_score": self.risk_score,
                "risk_threshold": self.risk_threshold,
                "task_id": self.task_id,
                "expected_step": self.expected_step,
                "current_step": self.current_step,
                "method": self.method,
                "calibration_version": self.calibration_version,
                "coverage": self.coverage,
                "frame": self.frame,
                "pre_state": self.pre_state,
                "candidate": self.candidate,
                "verification_context": self.verification_context.__dict__
                if self.verification_context
                else None,
                "input_hash": self.input_hash,
                "model_hash": self.model_hash,
                "config_hash": self.config_hash,
                "annotation_used": self.annotation_used,
                "authorizing_evidence_refs": list(self.authorizing_evidence_refs),
                "negative_evidence_refs": list(self.negative_evidence_refs),
                "confirmed_transition": self.confirmed_transition,
            }.items()
        }

    def canonical_json(self):
        return _canon(self.payload())

    def digest(self):
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    @classmethod
    def issue(
        cls,
        graph,
        evidence,
        risk_ok=True,
        negative=(),
        risk_score=None,
        risk_threshold=None,
        *,
        task_id=None,
        expected_step=None,
        current_step=None,
        method="Proposed",
        calibration_version=None,
        coverage=None,
        frame=None,
        pre_state=None,
        candidate=None,
        verification_context=None,
        input_hash=None,
        model_hash=None,
        config_hash=None,
        annotation_used=False,
        authorizing_evidence_refs=(),
        negative_evidence_refs=(),
        confirmed_transition=False,
    ):
        c = cls(
            _raw_digest(graph),
            tuple(dict(e) for e in evidence),
            bool(risk_ok),
            tuple(dict(e) for e in negative),
            "",
            risk_score,
            risk_threshold,
            task_id,
            expected_step,
            current_step,
            method,
            calibration_version,
            coverage,
            frame,
            pre_state,
            candidate,
            verification_context,
            input_hash,
            model_hash,
            config_hash,
            bool(annotation_used),
            tuple(authorizing_evidence_refs),
            tuple(negative_evidence_refs),
            bool(confirmed_transition),
        )
        return cls(**{**c.__dict__, "certificate_hash": c.digest()})


def verify_certificate(
    cert,
    graph=None,
    *,
    context=None,
    task_id=None,
    expected_step=None,
    current_step=None,
):
    try:
        if (
            not isinstance(cert, EvidenceCertificate)
            or cert.certificate_hash != cert.digest()
        ):
            return VerificationResult(False, "tampered")
        if graph is None:
            return VerificationResult(False, "graph_required")
        try:
            graph.replay()
        except ValueError:
            return VerificationResult(False, "replay_failed")
        if cert.graph_digest != graph.digest():
            return VerificationResult(False, "graph_mismatch")
        vc = cert.verification_context
        if context is None:
            return VerificationResult(False, "verification_context_required")
        if context != vc:
            return VerificationResult(False, "verification_context_mismatch")
        if vc is None:
            return VerificationResult(False, "verification_context_required")
        for field, expected in (
            ("task_id", task_id),
            ("expected_step", expected_step),
            ("current_step", current_step),
        ):
            if expected is not None and getattr(cert, field) != expected:
                return VerificationResult(False, f"{field}_mismatch")
        if cert.annotation_used:
            return VerificationResult(False, "annotation_not_authorizing")
        if not cert.evidence or not cert.confirmed_transition:
            return VerificationResult(False, "confirmed_transition_required")
        if (
            not all(
                isinstance(x, str)
                and len(x) == 64
                and x == x.lower()
                and all(c in "0123456789abcdef" for c in x)
                for x in (cert.input_hash, cert.model_hash, cert.config_hash)
            )
            or not isinstance(cert.calibration_version, str)
            or not cert.calibration_version
        ):
            return VerificationResult(False, "missing_provenance")
        if (
            cert.risk_score is None
            or cert.risk_threshold is None
            or cert.coverage is None
        ):
            return VerificationResult(False, "missing_risk_metadata")
        if not all(
            0 <= float(x) <= 1
            for x in (cert.risk_score, cert.risk_threshold, cert.coverage)
        ):
            return VerificationResult(False, "risk_range")
        if bool(cert.risk_ok) != (float(cert.risk_score) <= float(cert.risk_threshold)):
            return VerificationResult(False, "risk_threshold")
        if not cert.risk_ok:
            return VerificationResult(False, "risk_failed")
        by_id = {n["id"]: n for n in graph.nodes}
        context_node = by_id.get(vc.context_node_id)
        candidate_node = by_id.get(vc.candidate_node_id)
        if not context_node or context_node.get("kind") != "context":
            return VerificationResult(False, "context_node_missing")
        if not candidate_node or candidate_node.get("kind") != "step_state":
            return VerificationResult(False, "candidate_node_missing")
        if (vc.task_id, vc.expected_step, vc.pre_state, vc.candidate_step) != (
            cert.task_id,
            cert.expected_step,
            cert.pre_state,
            cert.candidate,
        ):
            return VerificationResult(False, "context_mismatch")
        if (
            context_node.get("task_id") != vc.task_id
            or context_node.get("expected_step") != vc.expected_step
            or context_node.get("current_step") != vc.pre_state
        ):
            return VerificationResult(False, "context_mismatch")
        if candidate_node.get("step") != vc.candidate_step:
            return VerificationResult(False, "candidate_mismatch")
        if not (
            vc.time_start
            <= (cert.frame if cert.frame is not None else vc.time_end)
            <= vc.time_end
        ):
            return VerificationResult(False, "time_window_mismatch")
        active_neg = [
            n
            for n in graph.nodes
            if n.get("kind") == "negative" and n.get("context_id") == vc.context_node_id
        ]
        negative_refs = set(cert.negative_evidence_refs)
        negative_payload_ids = {
            item.get("id") for item in cert.negative if isinstance(item, Mapping)
        }
        if active_neg:
            return VerificationResult(False, "negative_evidence")
        if negative_refs or cert.negative or negative_payload_ids:
            return VerificationResult(False, "negative_evidence_mismatch")
        allowed = AUTHORIZING_EVIDENCE_SOURCES
        refs = set(cert.authorizing_evidence_refs)
        evidence_ids = {e.get("id") for e in cert.evidence if isinstance(e, Mapping)}
        if not refs or refs != evidence_ids or len(refs) < 2:
            return VerificationResult(False, "evidence_unlinked")
        kinds = {n["id"]: n.get("kind") for n in graph.nodes}
        if not any(kinds.get(r) == "object" for r in refs) or not any(
            kinds.get(r) == "relation" for r in refs
        ):
            return VerificationResult(False, "evidence_types_required")
        if not any(
            by_id[r].get("kind") == "relation"
            and by_id[r].get("predicate") == "confirmed_step"
            and by_id[r].get("value") == vc.candidate_step
            for r in refs
        ):
            return VerificationResult(False, "transition_evidence_missing")
        edges = list(graph.edges)
        for e in cert.evidence:
            if not isinstance(e, Mapping) or e.get("source") not in allowed:
                return VerificationResult(False, "unauthorized_or_conflict")
            node = by_id.get(e.get("id"))
            if node is None or e.get("id") not in refs:
                return VerificationResult(False, "evidence_unlinked")
            if e.get("source") != node.get("source"):
                return VerificationResult(False, "evidence_source_mismatch")
            if e.get("type") != node.get("predicate"):
                return VerificationResult(False, "evidence_type_mismatch")
            if e.get("value") != node.get("value") or node.get("frame") != cert.frame:
                return VerificationResult(False, "evidence_value_mismatch")
            if node.get("kind") == "object" and not any(
                x.get("source") == vc.context_node_id
                and x.get("target") == node["id"]
                and x.get("kind") == "observation"
                for x in edges
            ):
                return VerificationResult(False, "context_observation_missing")
            seen = {node["id"]}
            pending = [node["id"]]
            while pending:
                current = pending.pop()
                for edge in edges:
                    if (
                        edge.get("source") == current
                        and edge.get("kind") == "dependency"
                        and edge.get("target") not in seen
                    ):
                        seen.add(edge["target"])
                        pending.append(edge["target"])
            if vc.candidate_node_id not in seen:
                return VerificationResult(False, "evidence_path_missing")
        pre_step = vc.pre_state or "START"
        if not any(
            e.get("kind") == "transition"
            and e.get("source") in by_id
            and by_id[e["source"]].get("kind") == "step_state"
            and by_id[e["source"]].get("step") == pre_step
            and e.get("target") == vc.candidate_node_id
            for e in edges
        ):
            return VerificationResult(False, "transition_proof_missing")
        return VerificationResult(True, "ok")
    except Exception:
        return VerificationResult(False, "malformed")
