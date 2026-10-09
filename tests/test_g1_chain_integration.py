"""End-to-end G1 chain: derive frozen inputs -> calibrator + HMM artifacts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from display_project.experiments.g1_derivation import atomic_write, derive_inputs
from display_project.experiments.hmm_fit import fit_hmm_jsonl
from display_project.experiments.hmm_sequence import load_hmm_artifact
from display_project.risk import RiskCalibrator
from scripts.build_risk_calibration_artifact import build


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"records": [
        {"video_id": "0007", "project_split": "CALIBRATION", "group_hash": "a" * 64},
        {"video_id": "0001", "project_split": "DEVELOPMENT", "group_hash": "b" * 64},
        {"video_id": "0002", "project_split": "DEVELOPMENT", "group_hash": "c" * 64},
    ]}), encoding="utf-8")
    actions = tmp_path / "actions.csv"
    actions.write_text(
        "video_id,action_id,action_name,start_frame,end_frame\n"
        "0007,1,pick,2,4\n0001,1,pick,2,4\n0002,1,pick,2,4\n",
        encoding="utf-8",
    )
    rows = []
    for oid, vid, group, index, frame in (
        ("a1", "0007", "a" * 64, 0, 3),
        ("a2", "0007", "a" * 64, 1, 10),
        ("b1", "0001", "b" * 64, 0, 3),
        ("b2", "0001", "b" * 64, 1, 10),
        ("c1", "0002", "c" * 64, 0, 4),
        ("c2", "0002", "c" * 64, 1, 11),
    ):
        rows.append({
            "observation_id": oid,
            "video_id": vid,
            "group_hash": group,
            "sequence_index": index,
            "frame_start": frame,
            "frame_end": frame,
            "candidate_step": "pick",
            "probability": 0.8 if frame < 5 else 0.2,
            "luma_mean": 1.0 + frame,
            "luma_std": 2.0,
            "frame_motion": 3.0,
        })
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    action_state_map = tmp_path / "action-state-map.json"
    action_state_map.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "meccano",
                "states": ["pick"],
                "action_to_state": {"pick": "pick"},
                "proxy_labels": {},
            }
        ),
        encoding="utf-8",
    )
    return manifest, actions, trace, action_state_map


def test_derived_inputs_feed_calibrator_and_hmm(tmp_path):
    manifest, actions, trace, action_state_map = _write_fixture(tmp_path)
    result = derive_inputs(
        manifest, actions, trace,
        action_state_map=action_state_map,
        code_hash="code", model_hash="model", config_hash="config",
    )

    # Calibration branch: the derived export is a valid frozen-calibrator input.
    calibration_input = tmp_path / "calibration-input.json"
    atomic_write(calibration_input, result["calibration"])
    built = build(
        calibration_input,
        tmp_path / "frozen-calibrator.json",
        tmp_path / "replay.json",
    )
    assert built["artifact"]["fitted"] is True
    assert built["artifact"]["frozen"] is True
    loaded = RiskCalibrator.from_artifact(
        json.loads(
            (tmp_path / "frozen-calibrator.json").read_text(encoding="utf-8")
        )
    )
    assert loaded is not None

    # HMM branch: the derived JSONL is a valid development-only fit input.
    hmm_input = tmp_path / "hmm-input.jsonl"
    hmm_input.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in result["hmm_records"]
        ),
        encoding="utf-8",
    )
    fitted = fit_hmm_jsonl(
        hmm_input,
        tmp_path / "fitted.json",
        task_id="meccano",
        task_config_hash="c" * 64,
        partition_manifest_hash="d" * 64,
        states=["pick"],
        fit_code_hash="e" * 64,
    )
    reloaded = load_hmm_artifact(tmp_path / "fitted.json")
    assert reloaded["artifact_hash"] == fitted["artifact_hash"]

    # Provenance binding survives the chain.
    sidecar = result["sidecar"]
    assert sidecar["runtime_truth_store_used"] is False
    assert built["replay"]["source_digest"] == sidecar["source_digest"]


def test_derive_cli_writes_all_three_artifacts(tmp_path):
    manifest, actions, trace, action_state_map = _write_fixture(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/derive_g1_inputs.py",
            "--manifest", str(manifest),
            "--actions", str(actions),
            "--trace", str(trace),
            "--action-state-map", str(action_state_map),
            "--calibration-output", str(tmp_path / "out" / "calibration.json"),
            "--hmm-output", str(tmp_path / "out" / "hmm.jsonl"),
            "--sidecar-output", str(tmp_path / "out" / "sidecar.json"),
            "--code-hash", "code",
            "--model-hash", "model",
            "--config-hash", "config",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calibration = json.loads(
        (tmp_path / "out" / "calibration.json").read_text(encoding="utf-8")
    )
    assert calibration["split_role"] == "CALIBRATION"
    assert calibration["truth_store_used"] is False
    lines = (
        (tmp_path / "out" / "hmm.jsonl").read_text(encoding="utf-8").splitlines()
    )
    # Development-only mapped states; calibration and background are excluded.
    assert len(lines) == 2
    assert all(
        set(json.loads(line)) == {"group_hash", "partition", "state", "features"}
        for line in lines
    )
    sidecar = json.loads(
        (tmp_path / "out" / "sidecar.json").read_text(encoding="utf-8")
    )
    assert sidecar["schema"] == "display_project.g1_derivation_sidecar"
