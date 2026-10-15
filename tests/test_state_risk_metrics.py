from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from display_project.metrics.state_risk import DEVELOPMENT_ONLY, predictions_from_run_dir, score_state_risk


def _truth() -> dict:
    return {
        "schema_version": "1.0", "evaluation_id": "development-fixture",
        "no_event_category_policy": "exclude_empty_categories_from_macro_f1",
        "matching_tolerance_s": 1.0, "enabled_risk_ids": ["missing", "orientation"],
        "jobs": [
            {"job_id": "j1", "task_id": "a", "video_id": "v1", "duration_s": 60.0},
            {"job_id": "j2", "task_id": "b", "video_id": "v2", "duration_s": 60.0},
        ],
        "opportunities": [
            {"opportunity_id": "o1", "job_id": "j1", "timestamp_s": 5.0, "stage_id": "fit", "completion_state": "completed"},
            {"opportunity_id": "o2", "job_id": "j1", "timestamp_s": 10.0, "stage_id": "check", "completion_state": "in_progress"},
            {"opportunity_id": "o3", "job_id": "j2", "timestamp_s": 5.0, "stage_id": "fit", "completion_state": "completed"},
        ],
        "risk_events": [{"event_id": "r1", "job_id": "j1", "risk_id": "missing", "start_s": 10.0}],
        "transitions": [{"transition_id": "t1", "job_id": "j1", "timestamp_s": 5.0, "stage_id": "fit", "completion_state": "completed"}],
    }


def _predictions() -> dict:
    return {
        "schema_version": "1.0", "evaluation_id": "development-fixture", "method_id": "P",
        "opportunities": [
            {"opportunity_id": "o1", "job_id": "j1", "timestamp_s": 5.0, "estimated_stage": "fit", "completion_state": "completed", "status": "ok"},
            {"opportunity_id": "o2", "job_id": "j1", "timestamp_s": 10.0, "status": "timeout"},
        ],
        "risk_events": [
            {"event_id": "p1", "job_id": "j1", "risk_id": "missing", "timestamp_s": 10.5},
            {"event_id": "p2", "job_id": "j1", "risk_id": "missing", "timestamp_s": 10.8},
            {"event_id": "p3", "job_id": "j2", "risk_id": "orientation", "timestamp_s": 20.0},
        ],
        "terminal_jobs": [{"job_id": "j1", "status": "completed"}, {"job_id": "j2", "status": "timeout"}],
    }


def test_failure_heavy_score_keeps_missing_unknown_and_no_event_false_alarm() -> None:
    result = score_state_risk(_truth(), _predictions())
    assert result["status"] == DEVELOPMENT_ONLY
    assert result["fixed_opportunity_universe"] == 3
    assert result["state"]["missing"] == 1
    assert result["state"]["unknown"] == 1
    assert result["state"]["wrong_certain_rate"] == 0.0
    assert result["risk"]["tp"] == 1
    assert result["risk"]["fp"] == 2
    assert result["risk"]["fn"] == 0
    assert result["transitions"]["missed_transitions"] == 0
    assert result["terminal_availability"]["availability"] == 0.5


def test_nonempty_temporal_event_output_on_all_negative_truth_is_false_alarm_not_step_evidence() -> None:
    truth = _truth()
    truth["risk_events"] = []
    truth["transitions"] = []
    for opportunity in truth["opportunities"]:
        opportunity["completion_state"] = "incomplete"

    predictions = {
        "schema_version": "1.0",
        "evaluation_id": "development-fixture",
        "method_id": "temporal-synthetic",
        "opportunities": [
            {
                "opportunity_id": opportunity["opportunity_id"],
                "job_id": opportunity["job_id"],
                "timestamp_s": opportunity["timestamp_s"],
                "status": "unknown",
            }
            for opportunity in truth["opportunities"]
        ],
        # Synthetic non-empty event output despite an all-negative reference.
        "risk_events": [
            {"event_id": "synthetic-segment-a", "job_id": "j1", "risk_id": "missing", "timestamp_s": 12.0},
            {"event_id": "synthetic-segment-b", "job_id": "j2", "risk_id": "orientation", "timestamp_s": 20.0},
        ],
        "terminal_jobs": [
            {"job_id": "j1", "status": "completed"},
            {"job_id": "j2", "status": "completed"},
        ],
    }

    result = score_state_risk(truth, predictions)

    assert result["state"]["unknown"] == len(truth["opportunities"])
    assert result["state"]["coverage"] == 0.0
    assert result["risk"]["tp"] == 0
    assert result["risk"]["fp"] == 2
    assert result["risk"]["fn"] == 0
    assert result["risk"]["event_macro_f1"] == 0.0
    assert result["transitions"]["matched_transitions"] == 0


def test_one_to_one_event_matching_makes_duplicate_alarm_a_false_positive() -> None:
    truth = _truth()
    prediction = _predictions()
    prediction["risk_events"] = [
        {"event_id": "later", "job_id": "j1", "risk_id": "missing", "timestamp_s": 10.9},
        {"event_id": "nearest", "job_id": "j1", "risk_id": "missing", "timestamp_s": 10.1},
    ]
    result = score_state_risk(truth, prediction)
    assert result["risk"]["tp"] == 1
    assert result["risk"]["fp"] == 1
    assert result["risk"]["mean_detection_latency_s"] == pytest.approx(0.1)


def test_event_matching_maximizes_valid_pairs_instead_of_greedy_nearest_pairs() -> None:
    truth = _truth()
    truth["matching_tolerance_s"] = 1.0
    truth["risk_events"] = [
        {"event_id": "r1", "job_id": "j1", "risk_id": "missing", "start_s": 0.0},
        {"event_id": "r2", "job_id": "j1", "risk_id": "missing", "start_s": 1.5},
    ]
    prediction = _predictions()
    prediction["risk_events"] = [
        {"event_id": "flexible", "job_id": "j1", "risk_id": "missing", "timestamp_s": 0.6},
        {"event_id": "first-only", "job_id": "j1", "risk_id": "missing", "timestamp_s": 0.0},
    ]
    result = score_state_risk(truth, prediction)
    assert result["risk"]["tp"] == 2
    assert result["risk"]["fp"] == 0
    assert result["risk"]["fn"] == 0


def test_false_only_risk_category_remains_in_macro_f1() -> None:
    prediction = _predictions()
    prediction["risk_events"] = [
        {"event_id": "false-orientation", "job_id": "j2", "risk_id": "orientation", "timestamp_s": 20.0},
    ]
    result = score_state_risk(_truth(), prediction)
    assert result["risk"]["macro_categories"] == 2


def test_transition_matching_requires_exact_stage_and_completion_label() -> None:
    truth = _truth()
    truth["transitions"].append({
        "transition_id": "t2", "job_id": "j1", "timestamp_s": 10.0,
        "stage_id": "check", "completion_state": "in_progress",
    })
    prediction = _predictions()
    prediction["opportunities"] = [
        {"opportunity_id": "o1", "job_id": "j1", "timestamp_s": 5.0,
         "estimated_stage": "check", "completion_state": "in_progress", "status": "ok"},
        {"opportunity_id": "o2", "job_id": "j1", "timestamp_s": 10.0,
         "estimated_stage": "fit", "completion_state": "completed", "status": "ok"},
    ]
    result = score_state_risk(truth, prediction)
    assert result["transitions"]["matched_transitions"] == 0
    assert result["transitions"]["missed_transitions"] == 2


def test_rejects_duplicate_or_inconsistent_fixed_opportunity_ids() -> None:
    prediction = _predictions()
    prediction["opportunities"].append(dict(prediction["opportunities"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        score_state_risk(_truth(), prediction)
    prediction = _predictions()
    prediction["opportunities"][0]["timestamp_s"] = 5.1
    with pytest.raises(ValueError, match="inconsistent timestamp"):
        score_state_risk(_truth(), prediction)


def test_rejects_nonfinite_clocks_and_incomplete_complete_job_statuses() -> None:
    truth = _truth()
    truth["opportunities"][0]["timestamp_s"] = float("inf")
    with pytest.raises(ValueError, match="finite"):
        score_state_risk(truth, _predictions())

    prediction = _predictions()
    prediction["terminal_jobs"] = [{"job_id": "j1", "status": "completed"}]
    with pytest.raises(ValueError, match="exactly"):
        score_state_risk(_truth(), prediction)


def test_cli_marks_result_development_only_and_binds_input_hashes(tmp_path: Path) -> None:
    truth_path = tmp_path / "truth.json"
    predictions_path = tmp_path / "predictions.json"
    output_path = tmp_path / "result.json"
    truth_path.write_text(json.dumps(_truth()), encoding="utf-8")
    predictions_path.write_text(json.dumps(_predictions()), encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "score_vlm_state_risk.py"
    completed = subprocess.run([sys.executable, str(script), "--truth", str(truth_path), "--predictions", str(predictions_path), "--output", str(output_path)], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == DEVELOPMENT_ONLY
    assert len(result["input_hashes"]["truth_sha256"]) == 64
    assert len(result["output_payload_sha256"]) == 64


def test_run_export_adapter_preserves_missing_jobs_and_uses_available_alarm_time(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source_hash = "a" * 64
    (run_dir / "manifest.json").write_text(json.dumps({"terminal": {
        "method_id": "P", "source_sha256": source_hash, "status": "completed_with_failures",
    }}), encoding="utf-8")
    estimates = [
        {"window_id": "w1", "decision_time": 5.0, "available_time": 6.0, "status": "ok",
         "inference": {"estimated_stage": "fit", "completion_state": "completed",
                       "risk_assessments": [{"rule_id": "missing", "status": "triggered"},
                                            {"rule_id": "orientation", "status": "unknown"}]}},
        {"window_id": "w2", "decision_time": 7.0, "available_time": 8.0, "status": "ok",
         "inference": {"estimated_stage": "fit", "completion_state": "completed",
                       "risk_assessments": [{"rule_id": "missing", "status": "unknown"},
                                            {"rule_id": "orientation", "status": "unknown"}]}},
        {"window_id": "w3", "decision_time": 10.0, "available_time": 12.0, "status": "ok",
         "inference": {"estimated_stage": "check", "completion_state": "in_progress",
                       "risk_assessments": [{"rule_id": "missing", "status": "triggered"},
                                            {"rule_id": "orientation", "status": "not_triggered"}]}},
    ]
    (run_dir / "estimates.jsonl").write_text("".join(json.dumps(row) + "\n" for row in estimates), encoding="utf-8")
    truth = _truth()
    truth["jobs"][0]["video_sha256"] = source_hash
    predictions, metadata = predictions_from_run_dir(truth, run_dir, job_id="j1", method_id="P")
    assert metadata["source_binding"]["status"] == "MATCHED"
    assert metadata["unmatched_estimates"] == 1
    assert predictions["terminal_jobs"] == [{"job_id": "j1", "status": "completed"}, {"job_id": "j2", "status": "unavailable"}]
    assert [row["timestamp_s"] for row in predictions["risk_events"]] == [6.0, 12.0]
    result = score_state_risk(truth, predictions)
    assert result["state"]["missing"] == 1
    truth_path = tmp_path / "truth.json"
    output_path = tmp_path / "result.json"
    truth_path.write_text(json.dumps(truth), encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "score_vlm_state_risk.py"
    completed = subprocess.run([
        sys.executable, str(script), "--truth", str(truth_path), "--run-dir", str(run_dir),
        "--job-id", "j1", "--method-id", "P", "--output", str(output_path),
    ], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    cli_result = json.loads(output_path.read_text(encoding="utf-8"))
    assert cli_result["input_hashes"]["source_binding"]["status"] == "MATCHED"
    assert len(cli_result["input_hashes"]["estimates_sha256"]) == 64


def test_run_export_adapter_rejects_source_mismatch_and_retroactive_clock(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"terminal": {
        "method_id": "P", "source_sha256": "a" * 64, "status": "completed",
    }}), encoding="utf-8")
    (run_dir / "estimates.jsonl").write_text(json.dumps({
        "decision_time": 5.0, "available_time": 4.0, "status": "failed", "inference": None,
    }) + "\n", encoding="utf-8")
    truth = _truth()
    truth["jobs"][0]["video_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="does not match"):
        predictions_from_run_dir(truth, run_dir, job_id="j1", method_id="P")
    truth["jobs"][0]["video_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="cannot precede"):
        predictions_from_run_dir(truth, run_dir, job_id="j1", method_id="P")


def test_run_export_adapter_failed_row_resets_risk_episode(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"terminal": {
        "method_id": "P", "source_sha256": "a" * 64, "status": "completed",
    }}), encoding="utf-8")
    rows = [
        {"decision_time": 5.0, "available_time": 6.0, "status": "ok", "inference": {
            "estimated_stage": "fit", "completion_state": "completed",
            "risk_assessments": [{"rule_id": "missing", "status": "triggered"}],
        }},
        {"decision_time": 7.0, "available_time": 8.0, "status": "failed", "inference": {
            "risk_assessments": [{"rule_id": "missing", "status": "triggered"}],
        }},
        {"decision_time": 10.0, "available_time": 11.0, "status": "ok", "inference": {
            "estimated_stage": "check", "completion_state": "in_progress",
            "risk_assessments": [{"rule_id": "missing", "status": "triggered"}],
        }},
    ]
    (run_dir / "estimates.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    predictions, _ = predictions_from_run_dir(_truth(), run_dir, job_id="j1", method_id="P")
    assert [row["timestamp_s"] for row in predictions["risk_events"]] == [6.0, 11.0]
