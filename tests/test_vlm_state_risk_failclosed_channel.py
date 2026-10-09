"""Regression tests for the *fail-closed channel* of the state/risk contract.

Background
----------
`validate_inference` does not judge whether the model was right. It enforces
*self-consistency* between three channels:

  1. `window.observability`   — what the perception layer says it could not see
  2. `knowledge.risk_rules[]` — which observations a rule needs
  3. `inference.risk_assessments[]` — what the model reported

Two conclusions were verified by direct falsification (see
`research/vlm-state-risk-20260907/validation-stage/validation-protocol-20260917.md` §4.5):

  A. When `observability.missing` is **disjoint** from the rule's
     `required_observations`, fail-closed is silently **not enforced**: the model may
     legally emit `status="unknown"` with an **empty** `missing_evidence`, or mark a
     stage outside `applicable_stages` and emit `not_applicable`.
  B. When the vocabularies **align**, both escape hatches close: `not_triggered`
     requires a complete view, and `unknown` must name the absent observations.

These tests pin that behaviour so the semantics stay auditable. They are
expected-behaviour tests, not endorsements: (A) is recorded as a live risk
(V-R11) because nothing in the codebase checks that the two vocabularies agree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from display_project.vlm_sop.state_risk_contract import (
    StateRiskContractError,
    validate_inference,
)
from display_project.vlm_sop.state_risk_knowledge import validate_knowledge

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_PACK = REPO_ROOT / "configs" / "vlm" / "state_risk" / "meccano-development.yaml"
REFERENCE_CLIP_SHA256 = "1d2d3fbb9c0b331dd6aa08c0688603ffe2c7197ed47d2f03b0602abcbc55fd25"
QWEN35_9B_SHA256 = "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"


@pytest.fixture(scope="module")
def pack() -> dict:
    return validate_knowledge(yaml.safe_load(SHIPPED_PACK.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def config_sha256() -> str:
    return hashlib.sha256(SHIPPED_PACK.read_bytes()).hexdigest()


def _window(pack: dict, config_sha256: str, *, view: str, missing: list[str]) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": pack["task_id"],
        "window_id": "window-regression-a",
        "decision_time": 4.0,
        "frame_refs": [
            {
                "id": "frame-1",
                "time": 4.0,
                "image_sha256": "a" * 64,
                "image_verified": True,
                "cache_ref": "image-1",
            }
        ],
        "object_track_refs": [],
        "relation_events": [],
        "observability": {"view": view, "missing": missing, "absence_confirmed": []},
        "hashes": {
            "source_sha256": REFERENCE_CLIP_SHA256,
            "model_sha256": QWEN35_9B_SHA256,
            "config_sha256": config_sha256,
        },
    }


def _inference(
    pack: dict,
    *,
    stage: str | None,
    completion: str,
    status: str,
    evidence_refs: list[str],
    missing_evidence: list[str],
) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": pack["task_id"],
        "window_id": "window-regression-a",
        "estimated_stage": stage,
        "completion_state": completion,
        "deviations": [],
        "risk_assessments": [
            {
                "rule_id": "prerequisite_not_observed",
                "status": status,
                "evidence_refs": evidence_refs,
                "missing_evidence": missing_evidence,
            }
        ],
        "evidence_refs": evidence_refs,
        "conflicting_evidence_refs": [],
        "brief_rationale": "契约通道回归测试用例。",
        "authority": "state_estimation_only",
    }


def test_shipped_pack_rule_required_observations(pack: dict) -> None:
    """Pin the shipped vocabulary so a silent rename cannot disable fail-closed."""
    rule = next(r for r in pack["risk_rules"] if r["id"] == "prerequisite_not_observed")
    assert rule["required_observations"] == ["prerequisite_evidence"]
    assert rule["applicable_stages"] == [
        "align_components",
        "insert_screw",
        "tighten_screw",
        "verify_assembly",
    ]
    # `pick_components` is deliberately absent from applicable_stages.
    assert "pick_components" not in rule["applicable_stages"]


def test_declared_complete_view_admits_not_triggered(pack: dict, config_sha256: str) -> None:
    """(A) A window that declares nothing missing lets the model report `not_triggered`.

    This is the shape observed in the backbone A/B smoke run. It is contract-legal:
    fail-closed was never engaged because the fixture declared no missing observation.
    """
    window = _window(pack, config_sha256, view="complete", missing=[])
    result = validate_inference(
        _inference(
            pack,
            stage="align_components",
            completion="in_progress",
            status="not_triggered",
            evidence_refs=["frame-1"],
            missing_evidence=[],
        ),
        knowledge=pack,
        window=window,
    )
    assert result["risk_assessments"][0]["status"] == "not_triggered"
    assert result["risk_assessments"][0]["missing_evidence"] == []


def test_non_complete_view_rejects_not_triggered(pack: dict, config_sha256: str) -> None:
    """`not_triggered` is only reachable from a complete view."""
    window = _window(pack, config_sha256, view="occluded", missing=["connection_geometry"])
    with pytest.raises(StateRiskContractError, match="cannot be not_triggered without a complete view"):
        validate_inference(
            _inference(
                pack,
                stage="align_components",
                completion="in_progress",
                status="not_triggered",
                evidence_refs=["frame-1"],
                missing_evidence=[],
            ),
            knowledge=pack,
            window=window,
        )


def test_disjoint_vocabulary_permits_empty_missing_evidence(pack: dict, config_sha256: str) -> None:
    """(A) The live risk: a disjoint vocabulary silently disables the abstention guarantee.

    `observability.missing=["connection_geometry"]` shares no name with the rule's
    `required_observations=["prerequisite_evidence"]`, so the contract computes an empty
    `absent` set and accepts `unknown` with no `missing_evidence` at all.
    """
    window = _window(pack, config_sha256, view="occluded", missing=["connection_geometry"])
    result = validate_inference(
        _inference(
            pack,
            stage="align_components",
            completion="in_progress",
            status="unknown",
            evidence_refs=["frame-1"],
            missing_evidence=[],
        ),
        knowledge=pack,
        window=window,
    )
    assert result["risk_assessments"][0]["status"] == "unknown"
    assert result["risk_assessments"][0]["missing_evidence"] == []


def test_aligned_vocabulary_rejects_empty_missing_evidence(pack: dict, config_sha256: str) -> None:
    """(B) Once the vocabularies align, an empty `missing_evidence` is rejected."""
    window = _window(pack, config_sha256, view="occluded", missing=["prerequisite_evidence"])
    with pytest.raises(StateRiskContractError, match="missing_evidence must name absent required observations"):
        validate_inference(
            _inference(
                pack,
                stage="align_components",
                completion="in_progress",
                status="unknown",
                evidence_refs=["frame-1"],
                missing_evidence=[],
            ),
            knowledge=pack,
            window=window,
        )


def test_aligned_vocabulary_rejects_false_negative(pack: dict, config_sha256: str) -> None:
    """(B) Once aligned, `not_triggered` is rejected even when the view is occluded."""
    window = _window(pack, config_sha256, view="occluded", missing=["prerequisite_evidence"])
    with pytest.raises(StateRiskContractError, match="must be unknown when required observations are absent"):
        validate_inference(
            _inference(
                pack,
                stage="align_components",
                completion="in_progress",
                status="not_triggered",
                evidence_refs=["frame-1"],
                missing_evidence=[],
            ),
            knowledge=pack,
            window=window,
        )


def test_aligned_vocabulary_accepts_named_absent_observation(pack: dict, config_sha256: str) -> None:
    """The canonical correct abstention: name the absent observation."""
    window = _window(pack, config_sha256, view="occluded", missing=["prerequisite_evidence"])
    result = validate_inference(
        _inference(
            pack,
            stage="align_components",
            completion="in_progress",
            status="unknown",
            evidence_refs=["frame-1"],
            missing_evidence=["prerequisite_evidence"],
        ),
        knowledge=pack,
        window=window,
    )
    assert result["risk_assessments"][0]["missing_evidence"] == ["prerequisite_evidence"]


def test_unknown_stage_forces_named_estimated_stage(pack: dict, config_sha256: str) -> None:
    """With `applicable_stages` not containing `*`, an unknown stage forces `unknown`."""
    window = _window(pack, config_sha256, view="occluded", missing=["prerequisite_evidence"])
    with pytest.raises(StateRiskContractError):
        validate_inference(
            _inference(
                pack,
                stage=None,
                completion="unknown",
                status="unknown",
                evidence_refs=[],
                missing_evidence=["prerequisite_evidence"],
            ),
            knowledge=pack,
            window=window,
        )
    result = validate_inference(
        _inference(
            pack,
            stage=None,
            completion="unknown",
            status="unknown",
            evidence_refs=[],
            missing_evidence=["prerequisite_evidence", "estimated_stage"],
        ),
        knowledge=pack,
        window=window,
    )
    assert result["estimated_stage"] is None


def test_stage_outside_applicable_stages_can_downgrade_rule(pack: dict, config_sha256: str) -> None:
    """(A) A second escape hatch: pick a stage the rule does not apply to.

    Because `pick_components` is absent from `applicable_stages`, reporting it lets the
    model legally return `not_applicable` — the risk rule can never fire on step one.
    """
    window = _window(pack, config_sha256, view="complete", missing=[])
    with pytest.raises(StateRiskContractError, match="must be not_applicable for estimated stage"):
        validate_inference(
            _inference(
                pack,
                stage="pick_components",
                completion="in_progress",
                status="not_triggered",
                evidence_refs=["frame-1"],
                missing_evidence=[],
            ),
            knowledge=pack,
            window=window,
        )
    result = validate_inference(
        _inference(
            pack,
            stage="pick_components",
            completion="in_progress",
            status="not_applicable",
            evidence_refs=["frame-1"],
            missing_evidence=[],
        ),
        knowledge=pack,
        window=window,
    )
    assert result["risk_assessments"][0]["status"] == "not_applicable"


def test_completed_is_unconstrained_when_view_is_complete(pack: dict, config_sha256: str) -> None:
    """(C) The contract does not stop over-claiming completion.

    `completed` is only blocked when `observability.missing` names the *step's*
    `required_observations`. With a window that declares nothing missing, a claim of
    `completed` passes validation — so condition (a) of G-V0-6 is genuinely a model
    judgement, not a contract guarantee.
    """
    window = _window(pack, config_sha256, view="complete", missing=[])
    result = validate_inference(
        _inference(
            pack,
            stage="align_components",
            completion="completed",
            status="not_triggered",
            evidence_refs=["frame-1"],
            missing_evidence=[],
        ),
        knowledge=pack,
        window=window,
    )
    assert result["completion_state"] == "completed"


def test_completed_is_blocked_when_step_observation_missing(pack: dict, config_sha256: str) -> None:
    """(C) Over-claiming is blocked once the window names the step's own observation."""
    window = _window(pack, config_sha256, view="occluded", missing=["component_alignment"])
    with pytest.raises(StateRiskContractError, match="completed state requires all stage observations"):
        validate_inference(
            _inference(
                pack,
                stage="align_components",
                completion="completed",
                status="unknown",
                evidence_refs=["frame-1"],
                missing_evidence=["component_alignment"],
            ),
            knowledge=pack,
            window=window,
        )
