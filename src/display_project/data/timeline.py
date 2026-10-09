"""Frame-based timeline conversion helpers."""

from __future__ import annotations


def frame_to_seconds(frame: int, *, fps: float) -> float:
    if frame < 0:
        raise ValueError("frame must be non-negative")
    if fps <= 0:
        raise ValueError("fps must be positive")
    return frame / fps


def clip_interval(
    start_frame: int,
    end_frame: int,
    *,
    clip_start: int,
    clip_end: int,
) -> dict[str, int] | None:
    """Intersect an interval with a clip and rebase it to clip frame zero."""

    if min(start_frame, end_frame, clip_start, clip_end) < 0:
        raise ValueError("frame indexes must be non-negative")
    if end_frame < start_frame:
        raise ValueError("end_frame must not precede start_frame")
    if clip_end < clip_start:
        raise ValueError("clip_end must not precede clip_start")
    overlap_start = max(start_frame, clip_start)
    overlap_end = min(end_frame, clip_end)
    if overlap_end < overlap_start:
        return None
    return {
        "start_frame": overlap_start - clip_start,
        "end_frame": overlap_end - clip_start,
    }
