import json
import pytest

from display_project.metrics.paired_freeze import (
    compute_paired_metrics,
    freeze_paired_metrics,
    verify_freeze_artifact,
)


HASH = "a" * 64
UNITS = ("job-a", "job-b")
METHODS = ("proposed", "fixed_streak", "hmm_or_crf")


def rows():
    out = []
    for unit in UNITS:
        for method in METHODS:
            out.append(
                {
                    "video_id": unit,
                    "source_dataset": "SOFTWARE_FIXTURE",
                    "canonical_root_id": unit,
                    "operator_id": "operator-fixture",
                    "complete_job_id": unit,
                    "task_id": "task-a" if unit == "job-a" else "task-b",
                    "method": method,
                    "status": "success",
                    "primary": True,
                    "complete_video": True,
                    "video_hash": "c" * 64,
                    "task_config_hash": "d" * 64,
                    "manifest_hash": "e" * 64,
                    "task_source_hash": "f" * 64,
                    "profile": "DEVELOPMENT",
                    "expected_frames": 10,
                    "frames_processed": 10,
                    "source_fps": 10,
                    "annotation_used": False,
                    "scoring": {
                        "schema_version": "paired-scoring-1.0.0",
                        "scorer_enriched": True,
                        "ground_truth_hash": "b" * 64,
                        "runtime_annotation_used": False,
                        "total_scorable_opportunities": 2,
                    },
                    "events": [
                        {
                            "scorable": True,
                            "decision": "confirmed",
                            "emitted_advance": True,
                            "valid_advance": True,
                            "candidate_frame": 2,
                            "confirmed_frame": 4,
                        },
                        {
                            "scorable": True,
                            "decision": "unknown",
                            "emitted_advance": False,
                        },
                    ],
                }
            )
    return out


def points():
    return {
        f"{m}::{t}": [
            {
                "threshold": 0.5,
                "threshold_semantic": "risk_max_allowed"
                if m == "proposed"
                else "confidence",
                "conservative_direction": "lower" if m == "proposed" else "higher",
                "coverage": 0.5,
                "fsar": 0.1,
                "emitted_advances": 1,
            }
        ]
        for m in METHODS
        for t in ("task-a", "task-b")
    }


def test_metrics_keep_failure_attempts_and_compute_denominators():
    data = rows() + [
        {
            "video_id": "job-a",
            "source_dataset": "SOFTWARE_FIXTURE",
            "canonical_root_id": "job-a",
            "operator_id": "operator-fixture",
            "complete_job_id": "job-a",
            "task_id": "task-a",
            "method": "proposed",
            "status": "timeout",
            "primary": False,
            "attempt": 2,
        }
    ]
    result = compute_paired_metrics(data, allow_methods=METHODS)
    assert result["all_attempt_count"] == 7
    assert result["methods"]["proposed"][0]["fsar"] == 0.0
    assert result["methods"]["proposed"][0]["coverage"] == 0.5
    assert result["methods"]["proposed"][0]["confirmation_delay"]["mean"] == 0.2


def test_freeze_is_development_only_and_verifiable():
    artifact = freeze_paired_metrics(
        rows(), input_ledger_hash=HASH, operating_points=points(), allow_methods=METHODS
    )
    assert artifact.payload["fixed_streak_baseline"] == {
        "confirm_frames": 4,
        "cooldown_frames": 12,
    }
    assert artifact.payload["ground_truth_hashes"] == ["b" * 64]
    assert len(artifact.payload["operating_points_canonical_hash"]) == 64
    assert "scorer-enriched" in artifact.payload["scorer_truth_boundary"]
    blob = (
        json.dumps(
            {**artifact.payload, "sha256": artifact.sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert verify_freeze_artifact(blob, expected_hash=artifact.sha256)
    altered = json.loads(blob)
    altered["metrics"]["proposed"][0]["coverage"] = 0.0
    altered["sha256"] = artifact.sha256
    assert not verify_freeze_artifact(altered, expected_hash=artifact.sha256)
    with pytest.raises(ValueError, match="development-only"):
        freeze_paired_metrics(
            rows(),
            input_ledger_hash=HASH,
            operating_points=points(),
            allow_methods=METHODS,
            profile="pilot",
        )


@pytest.mark.parametrize("mutation", ["missing", "zero_support"])
def test_freeze_fails_closed(mutation):
    record_rows = rows()
    ops = points()
    if mutation == "missing":
        ops.pop("hmm_or_crf::task-b")
    if mutation == "zero_support":
        ops["proposed::task-a"] = [
            {"threshold": 0.5, "coverage": 0.0, "fsar": 0.1, "emitted_advances": 1}
        ]
    if mutation == "incomplete":
        record_rows[0]["complete_video"] = False
    with pytest.raises(ValueError):
        freeze_paired_metrics(
            record_rows,
            input_ledger_hash=HASH,
            operating_points=ops,
            allow_methods=METHODS,
        )


def test_composite_identity_prevents_cross_dataset_collision():
    record_rows = rows()
    record_rows[0]["source_dataset"] = "OTHER_DATASET"
    with pytest.raises(ValueError, match="missing paired methods"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


def test_composite_identity_requires_operator_and_root_fields():
    record_rows = rows()
    record_rows[0].pop("operator_id")
    with pytest.raises(ValueError, match="operator_id"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


def test_same_independent_unit_cannot_cross_tasks():
    record_rows = rows()
    record_rows[1]["task_id"] = "task-b"
    with pytest.raises(ValueError, match="multiple tasks"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


def test_operating_points_must_be_keyed_by_tasks_not_units():
    wrong = {f"{m}::{u}": points()[f"{m}::task-a"] for m in METHODS for u in UNITS}
    with pytest.raises(ValueError, match="method/task"):
        freeze_paired_metrics(
            rows(),
            input_ledger_hash=HASH,
            operating_points=wrong,
            allow_methods=METHODS,
        )


def test_emission_without_ground_truth_is_rejected():
    record_rows = rows()
    record_rows[0]["events"][0].pop("valid_advance")
    with pytest.raises(ValueError, match="explicit boolean validity"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


def test_failed_primary_scores_pre_failure_events_and_freezes_with_failure_rate():
    record_rows = rows()
    record_rows[0]["status"] = "timeout"
    record_rows[0]["complete_video"] = False
    record_rows[0]["scoring"]["scorable_opportunities"] = 10
    record_rows[0]["scoring"]["total_scorable_opportunities"] = 10
    metrics = compute_paired_metrics(record_rows, allow_methods=METHODS)
    failed = next(x for x in metrics["methods"]["proposed"] if x["task_id"] == "task-a")
    assert failed["scorable_opportunities"] == 10
    assert failed["coverage"] == 0.1
    assert failed["fsar"] == 0.0
    assert failed["run_failure_rate"] == 1.0
    artifact = freeze_paired_metrics(
        record_rows,
        input_ledger_hash=HASH,
        operating_points=points(),
        allow_methods=METHODS,
    )
    assert artifact.payload["attempt_accounting"]["primary_failures"] == 1


def test_failed_primary_without_scorer_truth_is_ineligible():
    record_rows = rows()
    record_rows[0]["status"] = "timeout"
    record_rows[0]["complete_video"] = False
    record_rows[0].pop("scoring")
    metrics = compute_paired_metrics(record_rows, allow_methods=METHODS)
    assert metrics["endpoint_ineligible"]
    with pytest.raises(ValueError, match="cannot freeze metrics"):
        freeze_paired_metrics(
            record_rows,
            input_ledger_hash=HASH,
            operating_points=points(),
            allow_methods=METHODS,
        )


def test_primary_failure_retry_does_not_dilute_failure_rate():
    record_rows = rows()
    record_rows[0]["status"] = "timeout"
    record_rows[0]["complete_video"] = False
    record_rows[0]["scoring"]["scorable_opportunities"] = 10
    retry = dict(record_rows[0])
    retry.update(
        {
            "primary": False,
            "attempt": 2,
            "retry_of": 1,
            "status": "success",
            "complete_video": True,
            "frames_processed": 10,
        }
    )
    record_rows.append(retry)
    metrics = compute_paired_metrics(record_rows, allow_methods=METHODS)
    proposed = next(
        x for x in metrics["methods"]["proposed"] if x["task_id"] == "task-a"
    )
    assert proposed["run_failure_rate"] == 1.0
    assert proposed["scheduled_runs"] == 1
    assert metrics["all_attempt_count"] == 7


def test_zero_frame_failed_primary_keeps_failure_and_requires_frozen_denominator():
    record_rows = rows()
    record_rows[0]["status"] = "timeout"
    record_rows[0]["complete_video"] = False
    record_rows[0]["expected_frames"] = 0
    record_rows[0]["frames_processed"] = 0
    record_rows[0]["scoring"]["scorable_opportunities"] = 0
    record_rows[0]["events"] = []
    metrics = compute_paired_metrics(record_rows, allow_methods=METHODS)
    proposed = next(
        x for x in metrics["methods"]["proposed"] if x["task_id"] == "task-a"
    )
    assert proposed["run_failure_rate"] == 1.0
    assert proposed["scorable_opportunities"] == 0
    assert proposed["fsar"] is None


def test_confirmation_delay_uses_observation_weighted_sum():
    record_rows = rows()
    for row in record_rows:
        if row["task_id"] == "task-a" and row["method"] == "proposed":
            row["events"] = [
                {
                    "scorable": True,
                    "decision": "confirmed",
                    "emitted_advance": True,
                    "valid_advance": True,
                    "candidate_frame": 0,
                    "confirmed_frame": 10,
                }
            ]
    metrics = compute_paired_metrics(record_rows, allow_methods=METHODS)
    delay = next(x for x in metrics["methods"]["proposed"] if x["task_id"] == "task-a")[
        "confirmation_delay"
    ]
    assert delay["mean"] == 1.0 and delay["observed"] == 1


def test_default_requires_and_retains_all_seven_runner_methods():
    seven = (
        "fixed_streak",
        "hmm_or_crf",
        "vlm_only",
        "yolo",
        "grounding_dino",
        "no_calibration_certificate",
        "proposed",
    )
    records = list(rows())
    for row in rows():
        for method in seven[2:]:
            clone = dict(row)
            clone["method"] = method
            records.append(clone)
    # rows() already contains proposed/fixed/hmm; add the four absent methods.
    for row in rows():
        for method in seven[2:6]:
            clone = dict(row)
            clone["method"] = method
            records.append(clone)
    # remove accidental duplicates and retain one row per unit/method.
    unique = {(r["complete_job_id"], r["method"]): r for r in records}
    result = compute_paired_metrics(unique.values())
    assert set(result["methods"]) == set(seven)


def test_raw_runner_success_without_scoring_is_rejected():
    record_rows = rows()
    record_rows[0].pop("scoring")
    with pytest.raises(ValueError, match="scoring metadata"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


def test_runtime_annotation_true_is_rejected():
    record_rows = rows()
    record_rows[0]["scoring"]["runtime_annotation_used"] = True
    with pytest.raises(ValueError, match="runtime_annotation_used"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


@pytest.mark.parametrize("mutation", ["missing_hash", "bad_hash", "missing_emitted"])
def test_scorer_boundary_is_fail_closed(mutation):
    record_rows = rows()
    if mutation == "missing_hash":
        record_rows[0]["scoring"].pop("ground_truth_hash")
    elif mutation == "bad_hash":
        record_rows[0]["scoring"]["ground_truth_hash"] = "A" * 64
    else:
        record_rows[0]["events"][1].pop("emitted_advance")
    with pytest.raises(ValueError):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


@pytest.mark.parametrize(
    "field",
    ["video_hash", "profile", "task_config_hash", "manifest_hash", "task_source_hash"],
)
def test_successful_primary_requires_provenance_anchors(field):
    record_rows = rows()
    for row in record_rows:
        row.pop(field, None)
    with pytest.raises(ValueError, match="successful primary requires"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


def test_software_fixture_explicitly_relaxes_task_source_anchors():
    record_rows = rows()
    for row in record_rows:
        row["profile"] = "SOFTWARE_FIXTURE"
        row.pop("task_config_hash", None)
        row.pop("manifest_hash", None)
        row.pop("task_source_hash", None)
    result = compute_paired_metrics(record_rows, allow_methods=METHODS)
    assert result["tasks"] == ("task-a", "task-b")


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", 1),
        ("decision", ["confirmed"]),
        ("scorable", "yes"),
        ("definitive", 1),
        ("candidate_frame", []),
        ("confirmed_frame", "3"),
    ],
)
def test_metric_scoring_rejects_malformed_event_types(field, value):
    data = rows()
    data[0]["events"][0][field] = value
    with pytest.raises(ValueError):
        compute_paired_metrics(data, allow_methods=METHODS)


def test_metric_scoring_rejects_boolean_fps():
    data = rows()
    data[0]["source_fps"] = True
    with pytest.raises(ValueError, match="fps"):
        compute_paired_metrics(data, allow_methods=METHODS)


@pytest.mark.parametrize(
    "placeholder",
    [
        "UNASSIGNED",
        "UNASSIGNED_BLOCKED_IDENTITY",
        "UNKNOWN_EXTERNAL_IDENTITY",
        "UNKNOWN_IDENTITY",
        "UNKNOWN_ASSET",
        "UNREGISTERED",
        "UNREGISTERED_ASSET",
        "PLACEHOLDER",
        "TBD",
        "TBC",
        "NOT_APPLICABLE",
        "N/A",
        "-",
        "?",
    ],
)
def test_nonfixture_placeholder_identity_is_ineligible(placeholder):
    data = rows()
    for row in data:
        row["source_dataset"] = "IndustReal"
        row["operator_id"] = placeholder
    with pytest.raises(ValueError, match="identity"):
        compute_paired_metrics(data, allow_methods=METHODS)
