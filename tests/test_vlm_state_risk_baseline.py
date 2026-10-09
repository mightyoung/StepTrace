from __future__ import annotations

import pytest

from display_project.vlm_sop.state_risk_baseline import StateRiskBaseline, StateRiskBaselineError


def knowledge() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_id": "task-a",
        "steps": [
            {"id": "pick", "title": "Pick", "prerequisites": [], "completion_cues": ["component held"], "negative_cues": ["component remains on bench"], "required_observations": ["hand_component_relation"], "source_refs": ["sop"]},
            {"id": "install", "title": "Install", "prerequisites": ["pick"], "completion_cues": ["component seated"], "negative_cues": [], "required_observations": ["component_alignment"], "source_refs": ["sop"]},
        ],
        "risk_rules": [
            {"id": "misalignment", "category": "quality", "applicable_stages": ["install"], "trigger_conditions": ["component visibly misaligned"], "exceptions": [], "required_observations": ["component_alignment"], "severity": "moderate", "severity_source": "sop", "source_refs": ["sop"]},
            {"id": "hand_contact", "category": "safety", "applicable_stages": ["*"], "trigger_conditions": ["hand enters configured zone"], "exceptions": [], "required_observations": ["hand_zone_relation"], "severity": "high", "severity_source": "sop", "source_refs": ["sop"]},
        ],
        "deviation_types": [],
        "source_refs": ["sop"],
        "status": "SYNTHETIC_SOFTWARE_TEST_ONLY",
    }


def window(frames: list[dict[str, object]], events: list[dict[str, object]], *, missing: list[str] | None = None, view: str = "complete") -> dict[str, object]:
    return {
        "schema_version": "1.0", "task_id": "task-a", "window_id": "window-1",
        "decision_time": float(frames[-1]["time"]), "frame_refs": frames,
        "object_track_refs": [], "relation_events": events,
        "observability": {"view": view, "missing": missing or [], "absence_confirmed": []},
        "hashes": {"source_sha256": "a" * 64, "model_sha256": "b" * 64, "config_sha256": "c" * 64},
    }


def frame_events(start: int, cue: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    frames = [{"id": f"frame-{index}", "time": float(index), "image_sha256": "d" * 64, "image_verified": True} for index in range(start, start + 4)]
    events = [{"id": f"event-{index}", "time": float(index), "predicate": cue} for index in range(start, start + 4)]
    return frames, events


def test_fixed_streak_uses_event_time_and_does_not_count_repeated_window():
    frames, events = frame_events(1, "component held")
    baseline = StateRiskBaseline(knowledge(), cooldown_frames=0)
    result = baseline.infer(window(frames, events))
    assert result["estimated_stage"] == "pick"
    assert result["completion_state"] == "completed"
    assert baseline.infer(window(frames, events))["estimated_stage"] == "pick"


def test_next_step_requires_prerequisite_and_can_use_new_window():
    baseline = StateRiskBaseline(knowledge(), cooldown_frames=0)
    frames, events = frame_events(1, "component held")
    baseline.infer(window(frames, events))
    next_frames, next_events = frame_events(5, "component seated")
    next_events.extend({"id": f"align-{index}", "time": float(index), "predicate": "component visibly misaligned"} for index in range(5, 9))
    result = baseline.infer(window(next_frames, next_events))
    assert result["estimated_stage"] == "install"
    assert next(item for item in result["risk_assessments"] if item["rule_id"] == "misalignment")["status"] == "triggered"


def test_missing_required_observation_keeps_stage_unknown():
    frames, events = frame_events(1, "component held")
    result = StateRiskBaseline(knowledge()).infer(window(frames, events, missing=["hand_component_relation", "estimated_stage"]))
    assert result["estimated_stage"] is None
    assert result["completion_state"] == "unknown"


def test_forbidden_truth_and_invalid_window_are_rejected():
    frames, events = frame_events(1, "component held")
    raw = window(frames, events)
    raw["truth_store"] = "forbidden"
    with pytest.raises(StateRiskBaselineError, match="forbidden"):
        StateRiskBaseline(knowledge()).infer(raw)
    with pytest.raises(StateRiskBaselineError, match="evidence window"):
        StateRiskBaseline(knowledge()).infer({"observability": {}})


def test_constructor_rejects_invalid_streak_parameters():
    with pytest.raises(ValueError):
        StateRiskBaseline(knowledge(), confirm_frames=0)
    with pytest.raises(ValueError):
        StateRiskBaseline(knowledge(), cooldown_frames=-1)


def test_invalid_future_window_does_not_contaminate_following_partial_window():
    frames, events = frame_events(1, "component held")
    invalid = window(frames, events)
    invalid["relation_events"][0]["time"] = 99.0
    baseline = StateRiskBaseline(knowledge(), cooldown_frames=0)
    with pytest.raises(StateRiskBaselineError, match="future"):
        baseline.infer(invalid)
    valid_frames, valid_events = frame_events(1, "component held")
    result = baseline.infer(window(
        valid_frames,
        valid_events,
        view="occluded",
        missing=["hand_component_relation", "component_alignment", "hand_zone_relation", "estimated_stage"],
    ))
    assert result["estimated_stage"] is None
    assert result["completion_state"] == "unknown"


def test_completed_stage_becomes_unknown_when_current_required_observation_is_missing():
    baseline = StateRiskBaseline(knowledge(), cooldown_frames=0)
    frames, events = frame_events(1, "component held")
    baseline.infer(window(frames, events))
    later_frames, later_events = frame_events(5, "component held")
    result = baseline.infer(window(
        later_frames,
        later_events,
        view="occluded",
        missing=["hand_component_relation", "component_alignment", "hand_zone_relation"],
    ))
    assert result["estimated_stage"] == "pick"
    assert result["completion_state"] == "unknown"
