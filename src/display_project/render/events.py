from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EventError(ValueError):
    """Raised when the baseline event file violates its public schema."""


@dataclass(frozen=True)
class BoxEvent:
    frame: int
    label: str
    bbox: tuple[int, int, int, int]
    score: float | None = None


def _event(item: Any, index: int) -> BoxEvent:
    if not isinstance(item, dict):
        raise EventError(f"event {index} must be an object")
    try:
        frame = int(item["frame"])
        label = str(item["label"]).strip()
        bbox_raw = item["bbox"]
    except (KeyError, TypeError, ValueError) as exc:
        raise EventError(f"event {index} requires frame, label and bbox") from exc
    if frame < 0 or not label:
        raise EventError(f"event {index} has invalid frame or label")
    if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
        raise EventError(f"event {index} bbox must be [x1, y1, x2, y2]")
    try:
        bbox = tuple(int(value) for value in bbox_raw)
    except (TypeError, ValueError) as exc:
        raise EventError(f"event {index} bbox must contain numbers") from exc
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise EventError(f"event {index} bbox must have positive area")
    score_raw = item.get("score")
    score = None if score_raw is None else float(score_raw)
    return BoxEvent(frame=frame, label=label, bbox=bbox, score=score)


def load_events(path: str | Path) -> list[BoxEvent]:
    event_path = Path(path).expanduser().resolve()
    if not event_path.is_file():
        raise EventError(f"event file does not exist: {event_path}")
    try:
        raw = json.loads(event_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EventError(f"invalid event JSON: {exc}") from exc
    items = raw.get("events") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise EventError("event JSON must be a list or an object containing an events list")
    events = [_event(item, index) for index, item in enumerate(items)]
    return sorted(events, key=lambda item: item.frame)
