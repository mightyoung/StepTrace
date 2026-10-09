"""Runtime authority boundary for optional VLM evidence."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .state_machine import Evidence, SOPStateMachine, Transition


@dataclass(frozen=True, slots=True)
class AuthorityResult:
    transition: Transition | None
    state_write_allowed: bool
    annotation_used: bool
    reason: str


class VLMAuthorityBoundary:
    """Ensure VLM output is audit-only and can never mutate SOP state."""

    def __init__(self, machine: SOPStateMachine):
        self.machine = machine

    def observe(
        self, evidence: Evidence | None = None, **kwargs: Any
    ) -> AuthorityResult:
        evidence = evidence or Evidence(**kwargs)
        if evidence.deterministic_step is None:
            return AuthorityResult(None, False, False, "vlm_only_or_unavailable")
        # VLM is retained for audit only; deterministic evidence remains authoritative
        # even when the audit prediction conflicts.
        transition = self.machine.observe(evidence)
        return AuthorityResult(
            transition, transition.decision == "normal", False, transition.reason
        )

    def authorize(
        self, evidence: Evidence | None = None, **kwargs: Any
    ) -> AuthorityResult:
        return self.observe(evidence, **kwargs)


def observe_with_vlm_boundary(
    machine: SOPStateMachine, evidence: Evidence
) -> AuthorityResult:
    return VLMAuthorityBoundary(machine).observe(evidence)
