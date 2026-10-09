"""Executable paired-runner integration fixtures (spawn-safe on Windows)."""

from __future__ import annotations

import hashlib
import json
import shutil
import pytest
import time
from pathlib import Path

import cv2
import numpy as np

from display_project.experiments import (
    PairedExperimentRunner,
    append_jsonl_atomic,
    fixed_streak_adapter,
)
from scripts.freeze_paired_metrics import main as freeze_main
from scripts.validate_experiment_ledger import validate
from scripts.ledger_anchor import write_anchor


def _frame_trace(path: str | Path) -> list[dict[str, str | int]]:
    capture = cv2.VideoCapture(str(path))
    trace = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            trace.append(
                {
                    "frame": len(trace),
                    "source_frame_sha256": __import__("hashlib")
                    .sha256(frame.tobytes())
                    .hexdigest(),
                }
            )
    finally:
        capture.release()
    return trace


def _video_frame_count(path: str | Path) -> int:
    capture = cv2.VideoCapture(str(path))
    count = 0
    while True:
        ok, _ = capture.read()
        if not ok:
            break
        count += 1
    capture.release()
    return count


def fixture_proposed(video: str | Path, *, seed: int = 0, retry: int = 0):
    """Top-level, pickleable adapter used by the Windows spawn fixture."""
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": _video_frame_count(video),
        "frame_trace": _frame_trace(video),
        "events": [{"method": "proposed", "seed": seed, "retry": retry}],
    }


def fixture_success(video: str | Path, *, seed: int = 0, retry: int = 0):
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": _video_frame_count(video),
        "frame_trace": _frame_trace(video),
    }


def fixture_fixed_streak(
    video: str | Path,
    *,
    seed: int = 0,
    retry: int = 0,
    confirm_frames: int,
    cooldown_frames: int,
):
    assert (confirm_frames, cooldown_frames) == (4, 12)
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": _video_frame_count(video),
        "frame_trace": _frame_trace(video),
        "events": [
            {"confirm_frames": confirm_frames, "cooldown_frames": cooldown_frames}
        ],
    }


def fixture_crash(video: str | Path, *, seed: int = 0, retry: int = 0):
    raise ValueError("fixture crash")


def fixture_oom(video: str | Path, *, seed: int = 0, retry: int = 0):
    raise MemoryError("CUDA out of memory fixture")


def fixture_timeout(video: str | Path, *, seed: int = 0, retry: int = 0):
    time.sleep(3.0)
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": _video_frame_count(video),
        "frame_trace": _frame_trace(video),
    }


def fixture_missing_frames(video: str | Path, *, seed: int = 0, retry: int = 0):
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": 1,
        "frame_trace": [{"frame": 0}],
    }


def fixture_retry_then_success(video: str | Path, *, seed: int = 0, retry: int = 0):
    if retry == 0:
        raise RuntimeError("transient fixture crash")
    return {
        "status": "success",
        "annotation_used": False,
        "complete_video": True,
        "frames_processed": _video_frame_count(video),
        "frame_trace": _frame_trace(video),
    }


def _make_fixture_video(tmp_path: Path, frames: int = 5) -> Path:
    path = tmp_path / "paired-fixture.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (32, 32))
    assert writer.isOpened()
    for i in range(frames):
        frame = np.full((32, 32, 3), i * 20, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert path.stat().st_size > 0
    assert _video_frame_count(path) == frames
    return path


def test_fixed_streak_and_proposed_run_same_complete_video(tmp_path):
    video = _make_fixture_video(tmp_path)
    ledger = tmp_path / "paired.jsonl"
    runner = PairedExperimentRunner(
        {
            "fixed_streak": fixed_streak_adapter(processor=fixture_fixed_streak),
            "proposed": fixture_proposed,
        },
        frozen_schedule=["fixed_streak", "proposed"],
        ledger_path=ledger,
        timeout_s=2,
        metadata={
            "code_hash": "a" * 64,
            "config_hash": "b" * 64,
            "model_hash": "c" * 64,
        },
    )

    run = runner.run_video(
        video,
        video_id="fixture-video",
        expected_frames=5,
        task_id="task-fixture",
        operator_id="operator-fixture",
        complete_job_id="job-fixture",
    )

    assert [row.status for row in run.attempts] == ["success", "success"]
    assert len({row.video_hash for row in run.attempts}) == 1
    assert all(row.expected_frames == 5 for row in run.attempts)
    assert all(row.complete_video for row in run.attempts)
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2
    assert run.attempts[0].events == [{"confirm_frames": 4, "cooldown_frames": 12}]


def test_all_seven_schedule_retains_failures_and_retry_lineage(tmp_path, capsys):
    video = _make_fixture_video(tmp_path)
    ledger = tmp_path / "all-seven.jsonl"
    names = [
        "fixed_streak",
        "hmm_or_crf",
        "vlm_only",
        "yolo",
        "grounding_dino",
        "no_calibration_certificate",
        "proposed",
    ]
    methods = {
        "fixed_streak": fixture_success,
        "hmm_or_crf": fixture_crash,
        "vlm_only": fixture_oom,
        "yolo": fixture_timeout,
        "grounding_dino": fixture_missing_frames,
        "no_calibration_certificate": fixture_crash,
        "proposed": fixture_retry_then_success,
    }
    runner = PairedExperimentRunner(
        methods,
        frozen_schedule=names,
        retries=1,
        # Leave enough room for Windows spawn startup while the timeout fixture still exceeds the budget deterministically.
        timeout_s=3.0,
        ledger_path=ledger,
        metadata={
            "code_hash": "a" * 64,
            "config_hash": "b" * 64,
            "model_hash": "c" * 64,
            "environment_hash": "d" * 64,
            "annotation_hash": "e" * 64,
            "partition_hash": "f" * 64,
        },
    )
    run = runner.run_video(video, video_id="all-seven", expected_frames=5)
    by_method = {
        name: [row for row in run.attempts if row.method == name] for name in names
    }

    assert by_method["fixed_streak"][0].status == "success"
    assert all(row.status == "crash" for row in by_method["hmm_or_crf"])
    assert all(row.status == "oom" for row in by_method["vlm_only"])
    assert all(row.status == "timeout" for row in by_method["yolo"])
    assert all(row.status == "missing_frames" for row in by_method["grounding_dino"])
    assert all(row.status == "crash" for row in by_method["no_calibration_certificate"])
    assert [row.status for row in by_method["proposed"]] == ["crash", "success"]
    assert by_method["proposed"][0].primary is True
    assert by_method["proposed"][1].primary is False
    assert by_method["proposed"][1].retry_of == 1
    assert by_method["proposed"][0].lineage_id == by_method["proposed"][1].lineage_id
    assert len(run.attempts) == 13
    rows = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == len(run.attempts)
    assert validate(ledger, expected_methods=names, require_hashes=True) == []

    # Rebuilding the hash chain after deleting a retry must not make the
    # semantically incomplete runner ledger eligible for metric freezing.
    tampered = tmp_path / "deleted-retry.jsonl"
    tampered_rows = [
        row for row in rows if not (row["method"] == "proposed" and row["attempt"] == 2)
    ]
    for row in tampered_rows:
        row.pop("ledger_integrity", None)
    append_jsonl_atomic(tampered, tampered_rows)
    shutil.copyfile(
        ledger.with_suffix(ledger.suffix + ".registration.jsonl"),
        tampered.with_suffix(tampered.suffix + ".registration.jsonl"),
    )
    operating_points = tmp_path / "points.json"
    operating_points.write_text("{}", encoding="utf-8")
    anchor = tmp_path / "anchor.json"
    write_anchor(tampered, anchor)
    anchor_hash = hashlib.sha256(anchor.read_bytes()).hexdigest()
    output = tmp_path / "freeze.json"
    result = freeze_main(
        [
            "--ledger",
            str(tampered),
            "--operating-points",
            str(operating_points),
            "--input-ledger-hash",
            hashlib.sha256(tampered.read_bytes()).hexdigest(),
            "--anchor",
            str(anchor),
            "--expected-anchor-hash",
            anchor_hash,
            "--output",
            str(output),
        ]
    )
    assert result == 2
    assert "ledger semantic validation failed" in capsys.readouterr().err
    assert not output.exists()


def test_manual_intervention_is_recorded_for_every_scheduled_method(tmp_path):
    video = _make_fixture_video(tmp_path)
    names = [
        "fixed_streak",
        "hmm_or_crf",
        "vlm_only",
        "yolo",
        "grounding_dino",
        "no_calibration_certificate",
        "proposed",
    ]
    runner = PairedExperimentRunner(
        {name: fixture_success for name in names}, frozen_schedule=names, retries=0
    )
    run = runner.run_video(
        video, video_id="manual", expected_frames=5, manual_intervention=True
    )
    assert len(run.attempts) == 7
    assert all(row.status == "manual_intervention" for row in run.attempts)
    assert all(not row.complete_video for row in run.attempts)


def test_registration_chain_continues_across_videos_and_duplicate_is_noop(tmp_path):
    video = _make_fixture_video(tmp_path)
    ledger = tmp_path / "multi.jsonl"
    runner = PairedExperimentRunner(
        {"fixed_streak": fixture_success},
        frozen_schedule=["fixed_streak"],
        timeout_s=5,
        ledger_path=ledger,
    )
    runner.run_video(
        video,
        video_id="video-a",
        expected_frames=5,
        source_dataset="d",
        canonical_root_id="r1",
        canonical_group_id="g1",
    )
    runner.run_video(
        video,
        video_id="video-b",
        expected_frames=5,
        source_dataset="d",
        canonical_root_id="r2",
        canonical_group_id="g2",
    )
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    registrations = [
        json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["sequence"] for row in registrations] == [0, 1]
    assert registrations[1]["prev_hash"] == registrations[0]["record_hash"]
    ledger_before, sidecar_before = ledger.read_bytes(), sidecar.read_bytes()
    with pytest.raises(ValueError, match="already registered"):
        runner.run_video(
            video,
            video_id="video-a",
            expected_frames=5,
            source_dataset="d",
            canonical_root_id="r1",
            canonical_group_id="g1",
        )
    assert ledger.read_bytes() == ledger_before
    assert sidecar.read_bytes() == sidecar_before


def test_invalid_registration_hash_anchor_writes_nothing(tmp_path):
    video = _make_fixture_video(tmp_path)
    ledger = tmp_path / "invalid-anchor.jsonl"
    runner = PairedExperimentRunner(
        {"fixed_streak": fixture_success},
        frozen_schedule=["fixed_streak"],
        ledger_path=ledger,
        metadata={"model_hash": "not-a-hash"},
    )
    with pytest.raises(ValueError, match="model_hash"):
        runner.run_video(
            video,
            expected_frames=5,
            source_dataset="dataset",
            canonical_root_id="root",
            canonical_group_id="group",
            operator_id="operator",
            complete_job_id="job",
        )
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    assert not ledger.exists()
    assert not sidecar.exists()
