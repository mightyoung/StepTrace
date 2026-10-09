from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from display_project.config import load_config
from display_project.render.baseline import render_baseline
from display_project.video.io import VideoError, crop_video, probe_video


def _write_video(path: Path, *, frames: int = 12, fps: float = 6.0) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (96, 64),
    )
    assert writer.isOpened()
    for index in range(frames):
        frame = np.full((64, 96, 3), (index * 13) % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_load_config_resolves_paths_relative_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / "demo.yaml"
    config_path.write_text(
        "video:\n  input: media/input.mp4\n  output: results/demo.mp4\n"
        "events:\n  input: annotations/events.json\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.video.input == (tmp_path / "media/input.mp4").resolve()
    assert config.video.output == (tmp_path / "results/demo.mp4").resolve()
    assert config.events.input == (tmp_path / "annotations/events.json").resolve()


def test_probe_and_crop_video(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "clip.mp4"
    _write_video(source)

    metadata = probe_video(source)
    assert metadata.width == 96
    assert metadata.height == 64
    assert metadata.frame_count == 12
    assert metadata.fps == pytest.approx(6.0, rel=0.05)
    assert metadata.duration_seconds == pytest.approx(2.0, rel=0.05)

    cropped = crop_video(source, output, start_seconds=0.5, duration_seconds=1.0)
    assert output.exists() and output.stat().st_size > 0
    assert cropped.frame_count == 6
    assert cropped.duration_seconds == pytest.approx(1.0, rel=0.1)


def test_probe_rejects_missing_video(tmp_path: Path) -> None:
    with pytest.raises(VideoError, match="does not exist"):
        probe_video(tmp_path / "missing.mp4")


def test_render_baseline_writes_playable_annotated_video(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "rendered.mp4"
    events = tmp_path / "events.json"
    _write_video(source)
    events.write_text(
        json.dumps(
            {
                "events": [
                    {"frame": 2, "label": "left_hand", "bbox": [10, 8, 45, 38]},
                    {"frame": 3, "label": "part", "bbox": [50, 15, 90, 55]},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = render_baseline(source, events, output)

    assert result.output == output.resolve()
    assert result.frames_written == 12
    assert result.events_rendered == 2
    rendered = probe_video(output)
    assert rendered.frame_count == 12
    capture = cv2.VideoCapture(str(output))
    capture.set(cv2.CAP_PROP_POS_FRAMES, 2)
    ok, frame = capture.read()
    capture.release()
    assert ok
    assert frame[8:39, 10:46].std() > 0
