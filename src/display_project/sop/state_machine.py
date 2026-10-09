"""A deterministic, non-rewinding state machine for configurable SOPs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

Decision = Literal["normal", "deviation", "unknown"]
EvidenceSource = Literal["deterministic", "vlm", "none"]
DeterministicEvidenceSource = Literal[
    "model_geometry", "detector_tracking", "certificate_verified"
]


_ISSUANCE_TOKEN = object()


class _CertificateProof:
    __slots__ = ("certificate_hash",)
    def __init__(self, certificate_hash):
        self.certificate_hash = certificate_hash


@dataclass(frozen=True, slots=True, init=False)
class DeterministicEvidence:
    """Privately issued, proof-carrying evidence that may drive SOP state."""

    step: str = field(init=False)
    source: DeterministicEvidenceSource = field(init=False)
    _proof: object = field(init=False, repr=False, compare=False)

    def __init__(self, *args, **kwargs):
        raise TypeError("deterministic evidence is privately issued from verified proof")

    @classmethod
    def _issue(cls, step, source, proof):
        if source == "certificate_verified":
            if not isinstance(proof, _CertificateProof) or len(proof.certificate_hash) != 64:
                raise ValueError("certificate evidence requires a bound certificate proof")
        elif proof is not _ISSUANCE_TOKEN:
            raise ValueError("deterministic evidence requires verified proof")
        if not isinstance(step, str) or not step.strip():
            raise ValueError("deterministic evidence step must be a non-empty string")
        if source not in {"model_geometry", "detector_tracking", "certificate_verified"}:
            raise ValueError("deterministic evidence source is not runtime-authorized")
        self = object.__new__(cls)
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "_proof", proof)
        return self


def issue_model_geometry_evidence(*, visual, graph, expected_step):
    from .visual_evidence import VisualEvidenceResult
    from display_project.evidence import ProcessEvidenceGraph
    if not isinstance(visual, VisualEvidenceResult) or not isinstance(graph, ProcessEvidenceGraph):
        raise TypeError("model geometry issuance requires VisualEvidenceResult and ProcessEvidenceGraph")
    if not isinstance(expected_step, str) or visual.confirmed_step != expected_step:
        raise ValueError("visual result does not confirm expected step")
    nodes = graph.nodes
    if not any(n.get("kind") == "object" and n.get("source") == "model_geometry" for n in nodes):
        raise ValueError("model geometry object evidence is required")
    if not any(n.get("kind") == "relation" and n.get("source") == "model_geometry" and n.get("predicate") == "confirmed_step" and n.get("value") == expected_step for n in nodes):
        raise ValueError("model geometry relation evidence is required")
    return DeterministicEvidence._issue(expected_step, "model_geometry", _ISSUANCE_TOKEN)


def issue_certificate_evidence(*, certificate, graph, context, task_id, expected_step, current_step):
    from display_project.evidence import EvidenceCertificate, verify_certificate
    if not isinstance(certificate, EvidenceCertificate):
        raise TypeError("certificate evidence requires an EvidenceCertificate")
    result = verify_certificate(certificate, graph, context=context, task_id=task_id, expected_step=expected_step, current_step=current_step)
    if not result.valid:
        raise ValueError(f"certificate verification failed: {result.reason}")
    return DeterministicEvidence._issue(
        certificate.candidate or expected_step, "certificate_verified",
        _CertificateProof(certificate.certificate_hash),
    )


@dataclass(frozen=True, slots=True)
class Evidence:
    """Step evidence supplied by deterministic detectors and optional VLM review.

    ``deterministic_step`` must be typed runtime evidence. A raw string is only
    accepted when paired with an explicit, runtime-authorized source. A VLM
    prediction is retained for audit, but it is not sufficient to advance the
    SOP and never overrides deterministic evidence.
    """

    deterministic_step: DeterministicEvidence | None = None
    vlm_step: str | None = None
    deterministic_source: DeterministicEvidenceSource | None = None

    def __post_init__(self) -> None:
        if isinstance(self.deterministic_step, DeterministicEvidence):
            if (
                self.deterministic_source is not None
                and self.deterministic_source != self.deterministic_step.source
            ):
                raise ValueError("deterministic evidence source mismatch")
            object.__setattr__(
                self, "deterministic_source", self.deterministic_step.source
            )
        elif self.deterministic_step is not None:
            raise TypeError("deterministic_step must be privately issued DeterministicEvidence")
        elif self.deterministic_source is not None:
            raise ValueError("deterministic source requires deterministic evidence")


@dataclass(frozen=True, slots=True)
class Transition:
    decision: Decision
    expected_step: str | None
    observed_step: str | None
    previous_step: str | None
    current_step: str | None
    reason: str
    evidence_source: EvidenceSource


class SOPStateMachine:
    """Track progress through an ordered, unique SOP (five steps by default).

    Deviations and unknown observations are side-effect free. This makes the
    state reproducible when video evidence arrives late, out of order, or is
    inconclusive.
    """

    def __init__(self, steps: Sequence[str] | None = None) -> None:
        configured = tuple(steps or (f"step_{index}" for index in range(1, 6)))
        if not configured:
            raise ValueError("an SOP must contain at least one step")
        if any(not isinstance(step, str) or not step.strip() for step in configured):
            raise ValueError("every SOP step must be a non-empty string")
        if len(set(configured)) != len(configured):
            raise ValueError("SOP steps must be unique")

        self._steps = configured
        self._current_index = -1

    @property
    def steps(self) -> tuple[str, ...]:
        return self._steps

    @property
    def current_step(self) -> str | None:
        if self._current_index < 0:
            return None
        return self._steps[self._current_index]

    @property
    def expected_step(self) -> str | None:
        next_index = self._current_index + 1
        if next_index >= len(self._steps):
            return None
        return self._steps[next_index]

    @property
    def is_complete(self) -> bool:
        return self._current_index == len(self._steps) - 1

    def observe(self, evidence: Evidence) -> Transition:
        previous = self.current_step
        expected = self.expected_step

        if evidence.deterministic_step is None:
            return Transition(
                decision="unknown",
                expected_step=expected,
                observed_step=None,
                previous_step=previous,
                current_step=previous,
                reason="insufficient_deterministic_evidence",
                evidence_source="vlm" if evidence.vlm_step is not None else "none",
            )

        if not isinstance(evidence.deterministic_step, DeterministicEvidence):
            raise TypeError("deterministic evidence was not normalized")
        observed = evidence.deterministic_step.step
        if observed not in self._steps:
            return Transition(
                decision="unknown",
                expected_step=expected,
                observed_step=None,
                previous_step=previous,
                current_step=previous,
                reason="unrecognized_deterministic_step",
                evidence_source="deterministic",
            )

        observed_index = self._steps.index(observed)
        next_index = self._current_index + 1

        if observed_index == next_index:
            self._current_index = observed_index
            return Transition(
                decision="normal",
                expected_step=expected,
                observed_step=observed,
                previous_step=previous,
                current_step=self.current_step,
                reason="expected_step_observed",
                evidence_source="deterministic",
            )

        if observed_index == self._current_index:
            return Transition(
                decision="normal",
                expected_step=expected,
                observed_step=observed,
                previous_step=previous,
                current_step=previous,
                reason="current_step_reobserved",
                evidence_source="deterministic",
            )

        reason = (
            "step_reversed" if observed_index < self._current_index else "step_skipped"
        )
        return Transition(
            decision="deviation",
            expected_step=expected,
            observed_step=observed,
            previous_step=previous,
            current_step=previous,
            reason=reason,
            evidence_source="deterministic",
        )
