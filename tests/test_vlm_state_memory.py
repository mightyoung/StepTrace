import pytest

from display_project.vlm_sop.state_memory import StateMemory

from tests.test_vlm_evidence_window import _window


H = "b" * 64


def _inference(window_id="window-1", time=2.0, stage="stage-a"):
    return {"schema_version": "1.0", "task_id": "task-a", "window_id": window_id, "decision_time": time, "estimated_stage": stage, "authority": "state_estimation_only"}


def test_duplicate_window_does_not_create_new_observation_or_history_pollution():
    memory = StateMemory("task-a")
    assert memory.record_window(_window()) is True
    assert memory.record_window(_window()) is False
    memory.append_estimate(_inference(), model_sha256=H)
    history = memory.prompt_history(3.0)
    assert len(history["observations"]) == 1
    assert history["estimates"] == [_inference()]


def test_history_is_causal_and_correction_keeps_versions_separate():
    memory = StateMemory("task-a")
    memory.record_window(_window())
    memory.append_estimate(_inference(stage="wrong"), model_sha256=H)
    corrected = memory.append_estimate(_inference(stage="correct"), model_sha256=H)
    assert corrected["version"] == 2
    assert memory.current_estimate["inference"]["estimated_stage"] == "correct"
    assert memory.prompt_history(2.0) == {"observations": [], "estimates": [], "failures": []}


def test_failure_clears_stale_current_status_for_same_window():
    memory = StateMemory("task-a")
    memory.record_window(_window())
    memory.append_estimate(_inference(), model_sha256=H)
    memory.record_failure("window-1", decision_time=2.0, reason="timeout")
    assert memory.current_estimate is None
    assert memory.estimate_history[-1]["kind"] == "failure"


def test_replayed_request_is_idempotent_and_older_correction_cannot_replace_current():
    memory = StateMemory()
    memory.record_window(_window(window_id="window-1", decision_time=2.0))
    memory.record_window(_window(window_id="window-2", decision_time=4.0))
    first = memory.append_estimate(_inference(window_id="window-1", time=2.0), model_sha256=H, request_id="request-1")
    replay = memory.append_estimate(_inference(window_id="window-1", time=2.0), model_sha256=H, request_id="request-1")
    assert replay == first
    memory.append_estimate(_inference(window_id="window-2", time=4.0, stage="newer"), model_sha256=H)
    memory.append_estimate(_inference(window_id="window-1", time=2.0, stage="corrected"), model_sha256=H)
    assert memory.current_estimate["inference"]["estimated_stage"] == "newer"
    assert [row["version"] for row in memory.estimate_history] == [1, 2, 3]


def test_recorded_window_binds_estimate_time_and_later_failure_clears_current():
    memory = StateMemory("task-a")
    memory.record_window(_window(window_id="window-1", decision_time=2.0))
    with pytest.raises(ValueError, match="match"):
        memory.append_estimate(_inference(time=3.0), model_sha256=H)
    memory.append_estimate(_inference(), model_sha256=H)
    memory.record_window(_window(window_id="window-2", decision_time=3.0))
    memory.record_failure("window-2", decision_time=3.0, reason="timeout")
    assert memory.current_estimate is None


def test_window_id_is_immutable_facts_do_not_repeat_at_new_decision_time_and_failure_watermark_blocks_stale_reply():
    memory = StateMemory("task-a")
    first = _window(window_id="window-1", decision_time=2.0)
    assert memory.record_window(first) is True
    assert memory.record_window(first) is False
    rewritten = _window(window_id="window-1", decision_time=3.0)
    with pytest.raises(ValueError, match="different evidence"):
        memory.record_window(rewritten)
    repeated_facts = _window(window_id="window-2", decision_time=3.0)
    assert memory.record_window(repeated_facts) is False
    memory.record_failure("window-2", decision_time=3.0, reason="timeout")
    memory.append_estimate(_inference(window_id="window-1", time=2.0), model_sha256=H)
    assert memory.current_estimate is None
    history = memory.prompt_history(4.0)
    assert history["failures"] == [{"window_id": "window-2", "decision_time": 3.0, "status": "missing"}]


def test_estimates_require_registered_state_only_window_and_reject_authority_injection():
    memory = StateMemory("task-a")
    with pytest.raises(ValueError, match="recorded"):
        memory.append_estimate(_inference(), model_sha256=H)
    memory.record_window(_window())
    injected = _inference()
    injected["execution_state"] = "released"
    with pytest.raises(ValueError, match="forbidden"):
        memory.append_estimate(injected, model_sha256=H)
