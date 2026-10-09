import math

import pytest

from display_project.experiments.paired_runner import (
    MethodSpec,
    PairedExperimentRunner,
    _classify_exit,
)
from tests.test_paired_runner_integration import _frame_trace, _make_fixture_video


def malformed_events(video, *, seed=0, retry=0):
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": 5,
        "events": "forged",
    }


def wrong_hash(video, *, seed=0, retry=0):
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": 5,
        "events": [],
        "video_hash": "0" * 64,
    }


def _hostile_source_payload(video, mutation):
    trace = _frame_trace(video)
    events = []
    for item in trace:
        event = {
            **item,
            "decision": "unknown",
            "observed_step": None,
        }
        if item["frame"] == 0:
            event.update(decision="normal", observed_step="step-1")
            if mutation == "tuple_annotation":
                event.update(
                    evidence_source="deterministic",
                    evidence=({"source": "annotation"},),
                )
            elif mutation == "nested_oracle":
                event.update(
                    evidence_source="deterministic",
                    evidence=[{"source": "oracle"}],
                )
            else:
                event["evidence_source"] = mutation
        events.append(event)
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": len(events),
        "events": events,
        "frame_trace": trace,
    }


def hostile_tuple_annotation(video, *, seed=0, retry=0):
    return _hostile_source_payload(video, "tuple_annotation")


def hostile_root_oracle(video, *, seed=0, retry=0):
    return _hostile_source_payload(video, "oracle")


def hostile_root_none_state(video, *, seed=0, retry=0):
    return _hostile_source_payload(video, "none")


def hostile_nested_oracle(video, *, seed=0, retry=0):
    return _hostile_source_payload(video, "nested_oracle")


def test_expected_frame_mismatch_rejects_before_child_spawn(tmp_path):
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"fixed_streak": malformed_events}, frozen_schedule=["fixed_streak"]
    )
    row = runner.run_video(video, expected_frames=4).attempts[0]
    assert row.status == "missing_frames"
    assert row.error_code == "EXPECTED_FRAMES_MISMATCH"


def test_malformed_child_events_fail_closed(tmp_path):
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"fixed_streak": malformed_events},
        frozen_schedule=["fixed_streak"],
        timeout_s=2,
    )
    row = runner.run_video(video, expected_frames=5).attempts[0]
    assert row.status == "crash"
    assert row.error_code == "CHILD_RESULT_SCHEMA"
    assert row.complete_video is False


def test_wrong_child_hash_fails_closed(tmp_path):
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"proposed": wrong_hash}, frozen_schedule=["proposed"], timeout_s=2
    )
    row = runner.run_video(video, expected_frames=5).attempts[0]
    assert row.status == "crash"
    assert row.error_code == "VIDEO_HASH_MISMATCH"


@pytest.mark.parametrize(
    "adapter",
    [
        hostile_tuple_annotation,
        hostile_root_oracle,
        hostile_root_none_state,
        hostile_nested_oracle,
    ],
)
def test_non_deterministic_or_unknown_evidence_sources_fail_closed(tmp_path, adapter):
    video = _make_fixture_video(tmp_path)
    row = (
        PairedExperimentRunner({"yolo": adapter}, frozen_schedule=["yolo"], timeout_s=2)
        .run_video(video, expected_frames=5)
        .attempts[0]
    )
    assert row.status == "crash"
    assert row.error_code == "EVIDENCE_AUTHORITY_VIOLATION"


def test_only_known_exit_codes_are_oom():
    assert _classify_exit(-9)[0] == "oom"
    assert _classify_exit(137)[0] == "oom"
    assert _classify_exit(1)[0] == "crash"


def hostile_vlm_authorization(video, *, seed=0, retry=0):
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": 5,
        "events": [{"frame": i, "decision": "unknown"} for i in range(5)],
        "authorizations": [{"frame": 0}],
        "frame_trace": [{"frame": i} for i in range(5)],
    }


def test_vlm_authorizations_are_rejected(tmp_path):
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"vlm_only": hostile_vlm_authorization},
        frozen_schedule=["vlm_only"],
        timeout_s=2,
    )
    row = runner.run_video(video, expected_frames=5).attempts[0]
    assert row.status == "crash"
    assert row.error_code == "VLM_AUTHORITY_VIOLATION"


def hostile_partial_trace_missing(video, *, seed=0, retry=0):
    return {
        "status": "partial",
        "annotation_used": False,
        "frames_processed": 2,
        "events": [],
        "partial_events": [],
    }


def hostile_partial_trace_hash_mismatch(video, *, seed=0, retry=0):
    trace = _frame_trace(video)[:2]
    trace[1]["source_frame_sha256"] = "0" * 64
    return {
        "status": "partial",
        "annotation_used": False,
        "frames_processed": 2,
        "events": [],
        "partial_events": [],
        "frame_trace": trace,
    }


def valid_partial_prefix(video, *, seed=0, retry=0):
    return {
        "status": "partial",
        "annotation_used": False,
        "frames_processed": 2,
        "events": [],
        "partial_events": [],
        "frame_trace": _frame_trace(video)[:2],
    }


def _vlm_payload(status):
    return {
        "status": status,
        "annotation_used": False,
        "frames_processed": 0,
        "events": [],
        "partial_events": [],
        "state_writes": [{"frame": 0}],
    }


def hostile_vlm_partial(video, *, seed=0, retry=0):
    return _vlm_payload("partial")


def hostile_vlm_unavailable(video, *, seed=0, retry=0):
    return _vlm_payload("unavailable")


def hostile_vlm_crash(video, *, seed=0, retry=0):
    return _vlm_payload("crash")


def vlm_unavailable(video, *, seed=0, retry=0):
    return {
        "status": "unavailable",
        "annotation_used": False,
        "frames_processed": 0,
        "events": [],
        "partial_events": [],
    }


@pytest.mark.parametrize("timeout", [True, False, 0, -1, math.nan, math.inf, -math.inf])
def test_timeout_values_are_rejected_before_child_spawn(timeout):
    with pytest.raises(ValueError):
        PairedExperimentRunner(
            {"fixed_streak": malformed_events},
            frozen_schedule=["fixed_streak"],
            timeout_s=timeout,
        )
    with pytest.raises(ValueError):
        MethodSpec("fixed_streak", malformed_events, timeout_s=timeout)


@pytest.mark.parametrize("status", ["partial", "unavailable", "crash"])
def test_vlm_authority_payload_is_rejected_for_any_status(tmp_path, status):
    hostile = {
        "partial": hostile_vlm_partial,
        "unavailable": hostile_vlm_unavailable,
        "crash": hostile_vlm_crash,
    }[status]
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"vlm_only": hostile}, frozen_schedule=["vlm_only"], timeout_s=2
    )
    row = runner.run_video(video, expected_frames=5).attempts[0]
    assert row.status == "crash"
    assert row.error_code == "VLM_AUTHORITY_VIOLATION"


def test_vlm_unavailable_without_authority_payload_remains_valid(tmp_path):
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"vlm_only": vlm_unavailable}, frozen_schedule=["vlm_only"], timeout_s=2
    )
    row = runner.run_video(video, expected_frames=5).attempts[0]
    assert row.status == "unavailable"


@pytest.mark.parametrize(
    "adapter", [hostile_partial_trace_missing, hostile_partial_trace_hash_mismatch]
)
def test_non_success_processed_frames_require_bound_prefix_trace(tmp_path, adapter):
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"proposed": adapter}, frozen_schedule=["proposed"], timeout_s=2
    )
    row = runner.run_video(video, expected_frames=5).attempts[0]
    assert row.status == "crash"
    assert row.error_code == "PARTIAL_TRACE_INVALID"


def test_non_success_valid_prefix_trace_is_retained(tmp_path):
    video = _make_fixture_video(tmp_path)
    runner = PairedExperimentRunner(
        {"proposed": valid_partial_prefix}, frozen_schedule=["proposed"], timeout_s=2
    )
    row = runner.run_video(video, expected_frames=5).attempts[0]
    assert row.status == "partial"
    assert row.error_code is None
    assert row.frames_processed == 2


def _legal_hmm_payload(video):
    trace = _frame_trace(video)
    events = [
        {
            "frame": item["frame"],
            "source_frame_sha256": item["source_frame_sha256"],
            "decision": "definitive" if item["frame"] % 2 else "abstain",
            "observed_step": "step-1" if item["frame"] % 2 else None,
        }
        for item in trace
    ]
    method_decisions = [event for event in events if event["decision"] == "definitive"]
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": len(events),
        "events": events,
        "partial_events": [],
        "frame_trace": trace,
        "authority_scope": "experiment_prediction_only",
        "authorizations": [],
        "certificates": [],
        "state_writes": 0,
        "method_decisions": method_decisions,
        "coverage": {
            "opportunities": len(events),
            "definitive": len(method_decisions),
            "value": len(method_decisions) / len(events),
        },
    }


def legal_hmm(video, *, seed=0, retry=0):
    return _legal_hmm_payload(video)


def hostile_hmm_scope(video, *, seed=0, retry=0):
    result = _legal_hmm_payload(video)
    result["authority_scope"] = "runtime_state_authority"
    return result


def hostile_hmm_authorization(video, *, seed=0, retry=0):
    result = _legal_hmm_payload(video)
    result["authorizations"] = [{"frame": 1}]
    return result


def hostile_hmm_certificate(video, *, seed=0, retry=0):
    result = _legal_hmm_payload(video)
    result["certificates"] = [{"certificate_hash": "0" * 64}]
    return result


def hostile_hmm_state_write(video, *, seed=0, retry=0):
    result = _legal_hmm_payload(video)
    result["state_writes"] = 1
    return result


def hostile_hmm_decisions(video, *, seed=0, retry=0):
    result = _legal_hmm_payload(video)
    result["method_decisions"] = result["events"]
    return result


def hostile_hmm_coverage(video, *, seed=0, retry=0):
    result = _legal_hmm_payload(video)
    result["coverage"] = {"opportunities": 5, "definitive": 5, "value": 1.0}
    return result


def test_legal_hmm_prediction_only_contract_succeeds(tmp_path):
    video = _make_fixture_video(tmp_path)
    row = (
        PairedExperimentRunner(
            {"hmm_or_crf": legal_hmm},
            frozen_schedule=["hmm_or_crf"],
            timeout_s=2,
        )
        .run_video(video, expected_frames=5)
        .attempts[0]
    )
    assert row.status == "success"
    assert row.output["authority_scope"] == "experiment_prediction_only"
    assert row.output["state_writes"] == 0


@pytest.mark.parametrize(
    "adapter",
    [
        hostile_hmm_scope,
        hostile_hmm_authorization,
        hostile_hmm_certificate,
        hostile_hmm_state_write,
    ],
)
def test_hmm_runtime_authority_payloads_fail_closed(tmp_path, adapter):
    video = _make_fixture_video(tmp_path)
    row = (
        PairedExperimentRunner(
            {"hmm_or_crf": adapter},
            frozen_schedule=["hmm_or_crf"],
            timeout_s=2,
        )
        .run_video(video, expected_frames=5)
        .attempts[0]
    )
    assert row.status == "crash"
    assert row.error_code == "HMM_AUTHORITY_VIOLATION"


@pytest.mark.parametrize("adapter", [hostile_hmm_decisions, hostile_hmm_coverage])
def test_hmm_decision_and_coverage_must_match_events(tmp_path, adapter):
    video = _make_fixture_video(tmp_path)
    row = (
        PairedExperimentRunner(
            {"hmm_or_crf": adapter},
            frozen_schedule=["hmm_or_crf"],
            timeout_s=2,
        )
        .run_video(video, expected_frames=5)
        .attempts[0]
    )
    assert row.status == "crash"
    assert row.error_code == "HMM_DECISION_SCHEMA_INVALID"


def hostile_direct_event_source(video, *, seed=0, retry=0):
    result = _hostile_source_payload(video, "deterministic")
    result["events"][0]["source"] = "oracle"
    return result


def hostile_direct_authorization_source(video, *, seed=0, retry=0):
    result = _hostile_source_payload(video, "deterministic")
    result["authorizations"] = [{"source": "oracle"}]
    return result


def hostile_direct_certificate_source(video, *, seed=0, retry=0):
    result = _hostile_source_payload(video, "deterministic")
    result["certificates"] = [{"source": "annotation"}]
    return result


def hostile_nested_authorization_source(video, *, seed=0, retry=0):
    result = _hostile_source_payload(video, "deterministic")
    result["authorizations"] = [{"meta": {"source": "oracle"}}]
    return result


def hostile_malformed_event_decision(video, *, seed=0, retry=0):
    result = _hostile_source_payload(video, "deterministic")
    result["events"][0]["decision"] = []
    return result


def hostile_malformed_observed_step(video, *, seed=0, retry=0):
    result = _hostile_source_payload(video, "deterministic")
    result["events"][0]["observed_step"] = []
    return result


def hostile_oracle_key(video, *, seed=0, retry=0):
    result = _hostile_source_payload(video, "deterministic")
    result["authorizations"] = [{"oracle_step": "step-1"}]
    return result


@pytest.mark.parametrize(
    "adapter",
    [
        hostile_direct_event_source,
        hostile_direct_authorization_source,
        hostile_direct_certificate_source,
        hostile_nested_authorization_source,
        hostile_malformed_event_decision,
        hostile_malformed_observed_step,
        hostile_oracle_key,
    ],
)
def test_direct_authority_sources_and_oracle_keys_fail_closed(tmp_path, adapter):
    video = _make_fixture_video(tmp_path)
    row = (
        PairedExperimentRunner({"yolo": adapter}, frozen_schedule=["yolo"], timeout_s=2)
        .run_video(video, expected_frames=5)
        .attempts[0]
    )
    assert row.status == "crash"
    assert row.error_code == "EVIDENCE_AUTHORITY_VIOLATION"


def test_non_fixture_runner_rejects_forged_mapping_capability():
    with pytest.raises(TypeError):
        PairedExperimentRunner({"yolo": malformed_events}, frozen_schedule=["yolo"], metadata={"experiment_profile": "DEVELOPMENT"}, capability={"status": "PASS"})


def test_non_fixture_runner_revalidates_capability_on_each_run(tmp_path, monkeypatch):
    from display_project import gate_audit
    capability = tmp_path / "gate.json"
    capability.write_text("valid", encoding="utf-8")
    calls = []
    def verify(source, *, permission):
        calls.append((capability.__class__(source).read_text(encoding="utf-8"), permission))
        if calls[-1][0] != "valid":
            raise ValueError("mutated capability")
        return {}
    monkeypatch.setattr(gate_audit, "verify_joint_gate_permission", verify)
    runner = PairedExperimentRunner({"yolo": malformed_events}, frozen_schedule=["yolo"], metadata={"experiment_profile": "DEVELOPMENT"}, capability=capability)
    assert len(calls) == 1
    capability.write_text("mutated", encoding="utf-8")
    with pytest.raises(ValueError, match="mutated capability"):
        runner.run_video(tmp_path / "missing.mp4")
    assert len(calls) == 2
