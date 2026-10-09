from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from display_project.experiments.paired_runner import METHOD_NAMES
from display_project.risk import RiskCalibrator
from scripts import run_paired_experiment


TASK = {"schema_version": 1, "task_id": "cli-fixture", "steps": [{"id": "a"}]}


def blocked_manifest(path: Path) -> Path:
    payload = {
        "schema_version": 1,
        "profile": "SOFTWARE_FIXTURE",
        "task_config": TASK,
        "fixed_streak": {"confirm_frames": 4, "cooldown_frames": 12},
        "methods": [
            {"name": name, "factory": "unavailable", "config": {}}
            for name in METHOD_NAMES
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_list_methods_does_not_require_manifest(capsys):
    run_paired_experiment.main(["--list-methods"])
    assert capsys.readouterr().out.splitlines() == list(METHOD_NAMES)


def test_adapter_manifest_is_required():
    with pytest.raises(SystemExit) as caught:
        run_paired_experiment.main(["--preflight-only"])
    assert caught.value.code == 2


def test_admission_metadata_binds_result_ledger_id(tmp_path: Path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "result_destination": {
                    "ledger_id": "ledger:g3-development-paired",
                    "ledger_path_sha256": "a" * 64,
                }
            }
        ),
        encoding="utf-8",
    )
    assert run_paired_experiment._admission_metadata(receipt) == {
        "paired_ledger_id": "ledger:g3-development-paired"
    }


def test_blocked_preflight_writes_utf8_no_bom_report_and_exits_two(tmp_path: Path):
    manifest = blocked_manifest(tmp_path / "manifest.json")
    report = tmp_path / "preflight.json"
    with pytest.raises(SystemExit) as caught:
        run_paired_experiment.main(
            [
                "--adapter-manifest",
                str(manifest),
                "--preflight-only",
                "--preflight-report",
                str(report),
            ]
        )
    assert caught.value.code == 2
    raw = report.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    payload = json.loads(raw)
    assert payload["status"] == "BLOCKED"
    assert payload["blocked"] is True
    assert len(payload["methods"]) == 7
    assert payload["frozen_anchors"]["fixed_streak"] == {
        "confirm_frames": 4,
        "cooldown_frames": 12,
    }


def test_blocked_manifest_never_constructs_runner_or_writes_ledger(
    tmp_path: Path, monkeypatch
):
    manifest = blocked_manifest(tmp_path / "manifest.json")
    ledger = tmp_path / "ledger.jsonl"

    class ForbiddenRunner:
        def __init__(self, *args, **kwargs):
            raise AssertionError("blocked manifest was scheduled")

    monkeypatch.setattr(
        run_paired_experiment, "PairedExperimentRunner", ForbiddenRunner
    )
    with pytest.raises(SystemExit) as caught:
        run_paired_experiment.main(
            [
                str(tmp_path / "video.avi"),
                "--adapter-manifest",
                str(manifest),
                "--ledger",
                str(ledger),
            ]
        )
    assert caught.value.code == 2
    assert not ledger.exists()
    assert Path(f"{manifest}.preflight.json").is_file()


def test_invalid_manifest_writes_fail_closed_report(tmp_path: Path):
    manifest = tmp_path / "invalid.json"
    manifest.write_text("{}", encoding="utf-8")
    report = tmp_path / "invalid-report.json"
    with pytest.raises(SystemExit) as caught:
        run_paired_experiment.main(
            [
                "--adapter-manifest",
                str(manifest),
                "--preflight-only",
                "--preflight-report",
                str(report),
            ]
        )
    assert caught.value.code == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["error_code"] == "MANIFEST_INVALID"
    assert payload["status"] == "BLOCKED"


def _fixture_video(path: Path, frames: int = 3) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (32, 24))
    assert writer.isOpened()
    for index in range(frames):
        writer.write(np.full((24, 32, 3), index * 20, dtype=np.uint8))
    writer.release()


def ready_fixture_manifest(path: Path, tmp_path: Path) -> Path:
    partition_hash = "c" * 64
    artifact_path = tmp_path / "calibration.json"
    artifact = (
        RiskCalibrator()
        .fit([0.2, 0.8], [0, 1])
        .write_artifact(artifact_path, partition_hash)
    )
    processor = "display_project.experiments.adapters:software_fixture_processor"
    common = {
        "detector_kind": "grounding_dino",
        "device": "cpu",
        "processor_name": processor,
    }
    model_hash = "d" * 64
    method_configs = {
        "fixed_streak": {**common, "policy": "fixed_streak"},
        "hmm_or_crf": {**common, "policy": "fixed_streak"},
        "vlm_only": {**common, "policy": "fixed_streak"},
        "yolo": {
            "policy": "fixed_streak",
            "detector_kind": "yolo",
            "yolo_image_size": 1280,
            "yolo_iou_threshold": 0.5,
            "device": "cpu",
            "processor_name": processor,
        },
        "grounding_dino": {**common, "policy": "fixed_streak"},
        "no_calibration_certificate": {
            **common,
            "policy": "uncalibrated_certificate",
            "raw_risk_threshold": 0.5,
        },
        "proposed": {
            **common,
            "policy": "proposed",
            "calibration_artifact": str(artifact_path),
            "calibration_artifact_hash": artifact["artifact_hash"],
            "calibration_partition_hash": partition_hash,
        },
    }
    payload = {
        "schema_version": 1,
        "profile": "SOFTWARE_FIXTURE",
        "task_config": TASK,
        "fixed_streak": {"confirm_frames": 4, "cooldown_frames": 12},
        "schedule": list(METHOD_NAMES),
        "methods": [
            {
                "name": name,
                "factory": "render_sop",
                "model_hash": model_hash,
                "config": method_configs[name],
            }
            for name in METHOD_NAMES
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cli_rejects_task_id_override_mismatch(tmp_path: Path, monkeypatch):
    manifest = ready_fixture_manifest(tmp_path / "manifest.json", tmp_path)
    report = tmp_path / "task-mismatch.json"

    class ForbiddenRunner:
        def __init__(self, *args, **kwargs):
            raise AssertionError("task mismatch was scheduled")

    monkeypatch.setattr(
        run_paired_experiment, "PairedExperimentRunner", ForbiddenRunner
    )
    with pytest.raises(SystemExit) as caught:
        run_paired_experiment.main(
            [
                "--adapter-manifest",
                str(manifest),
                "--preflight-only",
                "--task-id",
                "wrong-task",
                "--preflight-report",
                str(report),
            ]
        )
    assert caught.value.code == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["error_code"] == "TASK_ID_MISMATCH"


def test_full_seven_software_fixture_cli_smoke(tmp_path: Path):
    manifest = ready_fixture_manifest(tmp_path / "ready.json", tmp_path)
    video = tmp_path / "fixture.avi"
    ledger = tmp_path / "ledger.jsonl"
    report = tmp_path / "preflight.json"
    _fixture_video(video)
    run_paired_experiment.main(
        [
            str(video),
            "--adapter-manifest",
            str(manifest),
            "--ledger",
            str(ledger),
            "--expected-frames",
            "3",
            "--preflight-report",
            str(report),
            "--timeout",
            "5",
        ]
    )
    preflight = json.loads(report.read_text(encoding="utf-8"))
    assert preflight["status"] == "READY"
    rows = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 7
    assert {row["method"] for row in rows} == set(METHOD_NAMES)
    assert all(row["status"] == "success" for row in rows)
    assert all(row["complete_video"] is True for row in rows)
    assert all(row["experiment_profile"] == "SOFTWARE_FIXTURE" for row in rows)
    assert all(
        row["manifest_hash"] == preflight["frozen_anchors"]["manifest_hash"]
        for row in rows
    )
    for row in rows:
        assert len(row["events"]) == 3
        assert all(event["authorization"] is False for event in row["events"])
        assert all(
            event["experiment_profile"] == "SOFTWARE_FIXTURE" for event in row["events"]
        )
