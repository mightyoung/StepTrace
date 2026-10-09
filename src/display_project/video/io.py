from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2


class VideoError(RuntimeError):
    """Raised when a video cannot be read or written."""


@dataclass(frozen=True)
class VideoMetadata:
    path: Path
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.fps


def _open_capture(path: str | Path) -> tuple[Path, cv2.VideoCapture]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise VideoError(f"video does not exist: {resolved}")
    capture = cv2.VideoCapture(str(resolved))
    if not capture.isOpened():
        capture.release()
        raise VideoError(f"cannot open video: {resolved}")
    return resolved, capture


def _metadata(path: Path, capture: cv2.VideoCapture) -> VideoMetadata:
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or width <= 0 or height <= 0 or frame_count <= 0:
        raise VideoError(f"video has invalid metadata: {path}")
    return VideoMetadata(path, width, height, fps, frame_count)


def probe_video(path: str | Path) -> VideoMetadata:
    resolved, capture = _open_capture(path)
    try:
        return _metadata(resolved, capture)
    finally:
        capture.release()


def crop_video(
    source: str | Path,
    output: str | Path,
    *,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> VideoMetadata:
    if start_seconds < 0 or (duration_seconds is not None and duration_seconds <= 0):
        raise VideoError("start_seconds must be non-negative and duration_seconds positive")
    source_path, capture = _open_capture(source)
    try:
        metadata = _metadata(source_path, capture)
        start_frame = round(start_seconds * metadata.fps)
        if start_frame >= metadata.frame_count:
            raise VideoError("crop starts after the end of the video")
        available = metadata.frame_count - start_frame
        wanted = available if duration_seconds is None else round(duration_seconds * metadata.fps)
        frames_to_write = min(available, wanted)
        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            metadata.fps,
            (metadata.width, metadata.height),
        )
        if not writer.isOpened():
            raise VideoError(f"cannot create video: {output_path}")
        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        written = 0
        try:
            while written < frames_to_write:
                ok, frame = capture.read()
                if not ok:
                    break
                writer.write(frame)
                written += 1
        finally:
            writer.release()
        if written == 0:
            raise VideoError("crop produced no frames")
        return probe_video(output_path)
    finally:
        capture.release()
