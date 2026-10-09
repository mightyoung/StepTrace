from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from display_project.experiments.hmm_sequence import (
    HMMSequenceAdapter,
    canonical_sha256,
    load_hmm_artifact,
)


def _artifact(**overrides):
    a = {
        "schema_version": 1,
        "baseline_kind": "hmm",
        "task_id": "task",
        "task_config_hash": "a" * 64,
        "states": ["s0", "s1"],
        "observation_schema": {
            "feature_names": ["luma_mean", "luma_std", "frame_motion"],
            "feature_types": ["float", "float", "float"],
            "extractor_code_hash": "b" * 64,
            "model": "diagonal_gaussian",
            "tracker": "NOT_APPLICABLE",
        },
        "initial_log_probs": [math.log(0.5), math.log(0.5)],
        "transition_log_probs": [[math.log(0.8), math.log(0.2)], [None, 0.0]],
        "emission": {
            "means": [[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            "variances": [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
        },
        "min_posterior": 0.75,
        "fit_provenance": {
            "project_split": "DEVELOPMENT",
            "partition_manifest_hash": "c" * 64,
            "fit_code_hash": "d" * 64,
            "canonical_group_commitment": "e" * 64,
            "count": 2,
        },
    }
    a["fit_provenance"]["fit_group_hashes"] = ["e" * 64]
    a["fit_provenance"]["canonical_group_commitment"] = canonical_sha256(
        a["fit_provenance"]["fit_group_hashes"]
    )
    a.update(overrides)
    a["artifact_hash"] = canonical_sha256(a)
    return a


def _write(path: Path, artifact):
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


def _video(path: Path, values=(0, 5)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (16, 12))
    for value in values:
        writer.write(np.full((12, 16, 3), value, dtype=np.uint8))
    writer.release()


def _adapter(path, artifact):
    return HMMSequenceAdapter(
        fit_artifact=path,
        expected_fit_artifact_hash=artifact["artifact_hash"],
        expected_fit_partition_hash="c" * 64,
        task_config_hash="a" * 64,
        task_id="task",
        states=["s0", "s1"],
    )


def test_manifest_hmm_factory_ready_and_runs_synthetic_video(tmp_path: Path):
    from dataclasses import asdict

    from display_project.experiments.adapter_manifest import compile_adapter_manifest
    from display_project.experiments.hmm_sequence import HMMSequenceAdapter
    from display_project.experiments.paired_runner import METHOD_NAMES
    from display_project.sop.task_config import compile_task_config

    task_raw = {
        "schema_version": 1,
        "task_id": "task",
        "steps": [{"id": "s0"}, {"id": "s1"}],
    }
    compiled_task = compile_task_config(task_raw)
    artifact = _artifact(task_config_hash=canonical_sha256(asdict(compiled_task)))
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    value = {
        "schema_version": 1,
        "profile": "SOFTWARE_FIXTURE",
        "task_config": task_raw,
        "fixed_streak": {"confirm_frames": 4, "cooldown_frames": 12},
        "methods": [
            {"name": name, "factory": "unavailable", "config": {}}
            for name in METHOD_NAMES
        ],
    }
    value["methods"] = [
        {
            "name": item["name"],
            "factory": "hmm_sequence",
            "config": {
                "fit_artifact": str(fit),
                "expected_fit_artifact_hash": artifact["artifact_hash"],
                "expected_fit_partition_hash": "c" * 64,
            },
        }
        if item["name"] == "hmm_or_crf"
        else item
        for item in value["methods"]
    ]
    compiled = compile_adapter_manifest(value)
    check = next(item for item in compiled.preflight if item.name == "hmm_or_crf")
    assert check.status == "READY"
    assert isinstance(compiled.adapters["hmm_or_crf"], HMMSequenceAdapter)
    video = tmp_path / "video.avi"
    _video(video, (0, 5))
    result = compiled.adapters["hmm_or_crf"](video, input_canonical_group="f" * 64)
    assert result["status"] == "success"
    assert result["frames_processed"] == 2


def test_valid_artifact_hash_and_normalization(tmp_path: Path):
    artifact = _artifact()
    path = tmp_path / "fit.json"
    _write(path, artifact)
    loaded = load_hmm_artifact(
        path,
        expected_hash=artifact["artifact_hash"],
        expected_partition_hash="c" * 64,
        expected_task_id="task",
        expected_task_config_hash="a" * 64,
    )
    assert loaded["artifact_hash"] == artifact["artifact_hash"]


@pytest.mark.parametrize(
    "field,value",
    [("task_id", "other"), ("task_config_hash", "f" * 64), ("artifact_hash", "0" * 64)],
)
def test_binding_hash_and_task_mismatch_fail_closed(tmp_path: Path, field, value):
    if isinstance(value, float) and math.isnan(value):
        with pytest.raises(ValueError):
            _artifact(**{field: value})
        return
    artifact = _artifact(**{field: value})
    path = tmp_path / "fit.json"
    _write(path, artifact)
    with pytest.raises(ValueError):
        load_hmm_artifact(
            path,
            expected_hash=artifact["artifact_hash"],
            expected_partition_hash="c" * 64,
            expected_task_id="task",
            expected_task_config_hash="a" * 64,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("states", ["s0"]),
        ("min_posterior", float("nan")),
        ("min_posterior", True),
    ],
)
def test_shape_and_scalar_validation_fail_closed(tmp_path: Path, field, value):
    if isinstance(value, float) and math.isnan(value):
        with pytest.raises(ValueError):
            _artifact(**{field: value})
        return
    artifact = _artifact(**{field: value})
    path = tmp_path / "fit.json"
    _write(path, artifact)
    with pytest.raises(ValueError):
        load_hmm_artifact(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a["transition_log_probs"][0].__setitem__(0, 0.0),
        lambda a: a["transition_log_probs"][0].__setitem__(1, math.log(0.9)),
        lambda a: a["transition_log_probs"][0].__setitem__(0, float("nan")),
        lambda a: a["transition_log_probs"][0].__setitem__(0, None),
    ],
)
def test_transition_normalization_and_self_forward_constraints(tmp_path: Path, mutate):
    artifact = _artifact()
    mutate(artifact)
    path = tmp_path / "fit.json"
    _write(path, artifact)
    with pytest.raises(ValueError):
        load_hmm_artifact(path)


def test_backward_transition_is_rejected_even_when_row_is_normalized(tmp_path: Path):
    artifact = _artifact()
    artifact["transition_log_probs"][1] = [math.log(0.25), math.log(0.75)]
    path = tmp_path / "fit.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="illegal HMM transition"):
        load_hmm_artifact(path)


def test_skip_transition_is_rejected_for_three_states(tmp_path: Path):
    artifact = _artifact()
    artifact["states"] = ["s0", "s1", "s2"]
    artifact["initial_log_probs"] = [math.log(1 / 3)] * 3
    artifact["transition_log_probs"] = [
        [math.log(0.5), None, math.log(0.5)],
        [None, 0.5 * math.log(0.5), 0.5 * math.log(0.5)],
        [None, None, 0.0],
    ]
    artifact["emission"]["means"].append([0.0, 0.0, 0.0])
    artifact["emission"]["variances"].append([1.0, 1.0, 1.0])
    artifact["artifact_hash"] = canonical_sha256(
        {k: artifact[k] for k in artifact if k != "artifact_hash"}
    )
    path = tmp_path / "fit.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="illegal HMM transition"):
        load_hmm_artifact(path)


@pytest.mark.parametrize(
    "key",
    ["annotation_used", "ground_truth", "truth_labels", "video_path", "frame_labels"],
)
def test_truth_annotation_and_video_specific_fields_are_rejected(tmp_path: Path, key):
    artifact = _artifact()
    artifact[key] = False
    path = tmp_path / "fit.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="forbidden"):
        load_hmm_artifact(path)


def test_partition_hash_mismatch_fails_closed(tmp_path: Path):
    artifact = _artifact()
    path = tmp_path / "fit.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="partition"):
        load_hmm_artifact(path, expected_partition_hash="f" * 64)


def test_artifact_is_standard_json_and_hash_fields_are_lowercase(tmp_path: Path):
    artifact = _artifact()
    path = tmp_path / "fit.json"
    _write(path, artifact)
    raw = path.read_text(encoding="utf-8")
    assert "Infinity" not in raw and "NaN" not in raw
    assert json.loads(raw)["transition_log_probs"][1][0] is None
    for mutate in (
        lambda value: value["observation_schema"].__setitem__(
            "extractor_code_hash", "B" * 64
        ),
        lambda value: value["fit_provenance"].__setitem__("fit_code_hash", "G" * 64),
    ):
        hostile = _artifact()
        mutate(hostile)
        hostile["artifact_hash"] = canonical_sha256(
            {key: hostile[key] for key in hostile if key != "artifact_hash"}
        )
        _write(path, hostile)
        with pytest.raises(ValueError):
            load_hmm_artifact(path)


def test_online_filter_uses_sum_product_not_viterbi(tmp_path: Path):
    artifact = _artifact()
    artifact["emission"]["means"] = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    artifact["min_posterior"] = 0.0
    artifact["artifact_hash"] = canonical_sha256(
        {key: artifact[key] for key in artifact if key != "artifact_hash"}
    )
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "filter.avi"
    _video(video, (0, 0))
    result = _adapter(fit, artifact)(video, input_canonical_group="f" * 64)
    assert result["events"][1]["posterior"] == pytest.approx([0.4, 0.6])


def test_any_fit_group_member_is_rejected(tmp_path: Path):
    groups = sorted(
        [hashlib.sha256(b"fit-a").hexdigest(), hashlib.sha256(b"fit-b").hexdigest()]
    )
    artifact = _artifact()
    artifact["fit_provenance"]["fit_group_hashes"] = groups
    artifact["fit_provenance"]["canonical_group_commitment"] = canonical_sha256(groups)
    artifact["artifact_hash"] = canonical_sha256(
        {key: artifact[key] for key in artifact if key != "artifact_hash"}
    )
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "video.avi"
    _video(video, (0,))
    assert (
        _adapter(fit, artifact)(video, input_canonical_group=groups[1])["error_code"]
        == "INPUT_GROUP_OVERLAPS_FIT"
    )


def test_synthetic_video_emits_definitive_and_abstain_with_consistent_coverage(
    tmp_path: Path,
):
    artifact = _artifact()
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "video.avi"
    _video(video)
    result = _adapter(fit, artifact)(video, input_canonical_group="f" * 64)
    assert result["status"] == "success"
    assert {e["decision"] for e in result["events"]} == {"abstain", "definitive"}
    assert result["coverage"]["definitive"] == len(result["method_decisions"])
    assert result["coverage"]["opportunities"] == result["frames_processed"]
    assert result["state_writes"] == 0
    assert result["authority_scope"] == "experiment_prediction_only"
    assert result["coverage"]["definitive"] == len(result["method_decisions"])


def test_frame_trace_is_exact_prefix_of_event_hashes(tmp_path: Path):
    artifact = _artifact()
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "video.avi"
    _video(video, (0,))
    result = _adapter(fit, artifact)(video, input_canonical_group="f" * 64)
    assert result["frame_trace"] == [
        {"frame": e["frame"], "source_frame_sha256": e["source_frame_sha256"]}
        for e in result["events"]
    ]
    assert len(result["events"][0]["source_frame_sha256"]) == 64


def test_frame_hash_matches_decoded_pixels(tmp_path: Path):
    artifact = _artifact()
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "video.avi"
    _video(video, (0,))
    result = _adapter(fit, artifact)(video, input_canonical_group="f" * 64)
    cap = cv2.VideoCapture(str(video))
    ok, image = cap.read()
    cap.release()
    assert ok
    assert (
        result["events"][0]["source_frame_sha256"]
        == hashlib.sha256(image.tobytes()).hexdigest()
    )


def test_online_prefix_is_invariant_to_future_frames(tmp_path: Path):
    artifact = _artifact()
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    short = tmp_path / "short.avi"
    long = tmp_path / "long.avi"
    _video(short, (0, 5))
    _video(long, (0, 5, 255))
    a = _adapter(fit, artifact)(short, input_canonical_group="f" * 64)
    b = _adapter(fit, artifact)(long, input_canonical_group="f" * 64)
    assert a["events"] == b["events"][:2]


def test_missing_and_invalid_group_fail_closed(tmp_path: Path):
    artifact = _artifact()
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "video.avi"
    _video(video, (0,))
    adapter = _adapter(fit, artifact)
    assert adapter(video)["error_code"] == "INPUT_GROUP_COMMITMENT_MISSING"
    assert (
        adapter(video, input_canonical_group=True)["error_code"]
        == "INPUT_GROUP_COMMITMENT_MISSING"
    )
    assert (
        adapter(video, input_canonical_group="not-a-hash")["error_code"]
        == "INPUT_GROUP_COMMITMENT_MISSING"
    )


def test_run_context_group_commitment_is_sha256_and_nonoverlap_runs(tmp_path: Path):
    fit_group = hashlib.sha256(b"fit-group").hexdigest()
    artifact = _artifact()
    artifact["fit_provenance"]["fit_group_hashes"] = [fit_group]
    artifact["fit_provenance"]["canonical_group_commitment"] = canonical_sha256(
        [fit_group]
    )
    artifact["artifact_hash"] = canonical_sha256(
        {k: artifact[k] for k in artifact if k != "artifact_hash"}
    )
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "video.avi"
    _video(video, (0,))
    adapter = _adapter(fit, artifact)
    assert (
        adapter(
            video,
            run_context={
                "input_canonical_group": hashlib.sha256(b"new-group").hexdigest()
            },
        )["status"]
        == "success"
    )
    assert (
        adapter(video, run_context={"input_canonical_group": fit_group})["error_code"]
        == "INPUT_GROUP_OVERLAPS_FIT"
    )


def test_group_overlap_is_unavailable(tmp_path: Path):
    artifact = _artifact()
    fit = tmp_path / "fit.json"
    _write(fit, artifact)
    video = tmp_path / "video.avi"
    _video(video, (0,))
    assert (
        _adapter(fit, artifact)(video, input_canonical_group="e" * 64)["error_code"]
        == "INPUT_GROUP_OVERLAPS_FIT"
    )
