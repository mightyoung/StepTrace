import json

import pytest

from display_project.experiments.g1_derivation import derive_inputs


def _row(oid, vid, group, *, index=0, frame=3, candidate_step="pick"):
    return {
        "observation_id": oid,
        "video_id": vid,
        "group_hash": group,
        "sequence_index": index,
        "frame_start": frame,
        "frame_end": frame,
        "probability": 0.8,
        "candidate_step": candidate_step,
        "luma_mean": 1.0,
        "luma_std": 2.0,
        "frame_motion": 3.0,
    }


def _fixture(tmp_path, trace_rows, *, records=None, actions_text=None):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"records": records if records is not None else [
            {"video_id": "0007", "project_split": "CALIBRATION", "group_hash": "a" * 64},
            {"video_id": "0001", "project_split": "DEVELOPMENT", "group_hash": "b" * 64},
        ]}),
        encoding="utf-8",
    )
    actions = tmp_path / "actions.csv"
    actions.write_text(
        actions_text
        if actions_text is not None
        else "video_id,action_id,action_name,start_frame,end_frame\n"
        "0007,1,pick,2,4\n0001,1,pick,2,4\n",
        encoding="utf-8",
    )
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "".join(json.dumps(row) + "\n" for row in trace_rows), encoding="utf-8"
    )
    action_state_map = tmp_path / "action-state-map.json"
    action_state_map.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "task",
                "states": ["pick"],
                "action_to_state": {"pick": "pick"},
                "proxy_labels": {},
            }
        ),
        encoding="utf-8",
    )
    return manifest, actions, trace, action_state_map


def _derive(manifest, actions, trace, action_state_map):
    return derive_inputs(
        manifest, actions, trace,
        code_hash="code", model_hash="model", config_hash="config",
        action_state_map=action_state_map,
    )


def test_derivation_separates_offline_labels_and_runtime_truth(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("a1", "0007", "a" * 64, index=0, frame=3),
        _row("a2", "0007", "a" * 64, index=1, frame=10),
        _row("b1", "0001", "b" * 64, index=0, frame=3),
    ])
    result = _derive(manifest, actions, trace, action_state_map)
    assert result["calibration"]["labels"] == [0, 1]
    assert result["calibration"]["truth_store_used"] is False
    assert result["sidecar"]["runtime_truth_store_used"] is False
    assert set(result["hmm_records"][0]) == {
        "group_hash", "partition", "state", "features"
    }


def test_empty_trace_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [])
    with pytest.raises(ValueError, match="empty runtime inference trace"):
        _derive(manifest, actions, trace, action_state_map)


def test_missing_calibration_partition_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("b1", "0001", "b" * 64, index=0, frame=3),
    ])
    with pytest.raises(ValueError, match="calibration partition produced no"):
        _derive(manifest, actions, trace, action_state_map)


def test_single_class_calibration_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("a1", "0007", "a" * 64, index=0, frame=3),
        _row("b1", "0001", "b" * 64, index=0, frame=3),
    ])
    with pytest.raises(ValueError, match="single-class"):
        _derive(manifest, actions, trace, action_state_map)


def test_missing_development_partition_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("a1", "0007", "a" * 64, index=0, frame=3),
        _row("a2", "0007", "a" * 64, index=1, frame=10),
    ])
    with pytest.raises(ValueError, match="development partition produced no"):
        _derive(manifest, actions, trace, action_state_map)


def test_duplicate_observation_id_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("a1", "0007", "a" * 64, index=0, frame=3),
        _row("a1", "0007", "a" * 64, index=1, frame=10),
    ])
    with pytest.raises(ValueError, match="duplicate observation_id"):
        _derive(manifest, actions, trace, action_state_map)


def test_non_monotonic_sequence_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("a1", "0007", "a" * 64, index=1, frame=3),
        _row("a2", "0007", "a" * 64, index=1, frame=10),
    ])
    with pytest.raises(ValueError, match="non-monotonic"):
        _derive(manifest, actions, trace, action_state_map)


def test_group_hash_disagreement_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("a1", "0007", "c" * 64, index=0, frame=3),
    ])
    with pytest.raises(ValueError, match="group_hash disagrees"):
        _derive(manifest, actions, trace, action_state_map)


def test_trace_video_outside_manifest_is_blocked(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(tmp_path, [
        _row("a1", "0002", "a" * 64, index=0, frame=3),
    ])
    with pytest.raises(ValueError, match="not in frozen manifest"):
        _derive(manifest, actions, trace, action_state_map)


def test_derivation_rejects_test_video(tmp_path):
    manifest, actions, trace, action_state_map = _fixture(
        tmp_path,
        [],
        records=[{
            "video_id": "0009",
            "project_split": "DEVELOPMENT",
            "group_hash": "a" * 64,
        }],
        actions_text=(
            "video_id,action_id,action_name,start_frame,end_frame\n"
            "0009,1,pick,0,1\n"
        ),
    )
    with pytest.raises(ValueError, match="forbidden"):
        _derive(manifest, actions, trace, action_state_map)
