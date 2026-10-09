from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2

from display_project.render.events import BoxEvent, load_events
from display_project.video.io import VideoError, probe_video


@dataclass(frozen=True)
class RenderResult:
    output: Path
    frames_written: int
    events_rendered: int


def _draw(frame, event: BoxEvent) -> None:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = event.bbox
    x1, x2 = max(0, min(x1, width - 1)), max(0, min(x2, width - 1))
    y1, y2 = max(0, min(y1, height - 1)), max(0, min(y2, height - 1))
    if x2 <= x1 or y2 <= y1:
        return
    color = (40, 220, 40)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    text = event.label if event.score is None else f"{event.label} {event.score:.2f}"
    cv2.putText(frame, text, (x1, max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def render_baseline(
    source: str | Path,
    events: str | Path,
    output: str | Path,
) -> RenderResult:
    metadata = probe_video(source)
    grouped: dict[int, list[BoxEvent]] = defaultdict(list)
    for event in load_events(events):
        grouped[event.frame].append(event)
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(metadata.path))
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        metadata.fps,
        (metadata.width, metadata.height),
    )
    if not writer.isOpened():
        capture.release()
        raise VideoError(f"cannot create video: {output_path}")
    frames_written = 0
    events_rendered = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            for event in grouped.get(frames_written, []):
                _draw(frame, event)
                events_rendered += 1
            writer.write(frame)
            frames_written += 1
    finally:
        capture.release()
        writer.release()
    if frames_written == 0:
        raise VideoError("render produced no frames")
    return RenderResult(output_path, frames_written, events_rendered)
