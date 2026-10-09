"""Deterministic SOP progression primitives."""

from .state_machine import Evidence, SOPStateMachine, Transition

__all__ = ["Evidence", "SOPStateMachine", "Transition", "AuthorityResult", "VLMAuthorityBoundary", "observe_with_vlm_boundary"]


from .authority import AuthorityResult, VLMAuthorityBoundary, observe_with_vlm_boundary
