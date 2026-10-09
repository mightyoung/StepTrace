"""MECCANO-to-YOLO mapping and dataset leakage checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


MECCANO_YOLO_NAMES = (
    "hand",
    "metal bar",
    "partial assembly",
    "screw",
    "bolt",
    "screwdriver",
)

_BAR_CATEGORIES = {
    "gray_angled_perforated_bar",
    "white_angled_perforated_bar",
    "gray_perforated_bar",
    "red_angled_perforated_bar",
    "red_perforated_bar",
    "red_perforated_junction_bar",
    "red_4_perforated_junction_bar",
}


def coco_category_to_yolo(category: str) -> str | None:
    """Map official MECCANO active-object names to the demo's closed set."""

    normalized = category.strip().casefold()
    if normalized in _BAR_CATEGORIES:
        return "metal bar"
    return {
        "partial_model": "partial assembly",
        "screw": "screw",
        "bolt": "bolt",
        "screwdriver": "screwdriver",
    }.get(normalized)


def to_yolo_line(
    class_id: int,
    bbox: Iterable[float],
    *,
    image_width: int,
    image_height: int,
) -> str:
    """Convert a clipped COCO ``x, y, width, height`` box to a YOLO line."""

    x, y, width, height = (float(value) for value in bbox)
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")
    if width <= 0 or height <= 0:
        raise ValueError("box dimensions must be positive")
    center_x = (x + width / 2) / image_width
    center_y = (y + height / 2) / image_height
    normalized_width = width / image_width
    normalized_height = height / image_height
    values = (center_x, center_y, normalized_width, normalized_height)
    if any(value < 0 or value > 1 for value in values):
        raise ValueError(f"box lies outside image after normalization: {values!r}")
    return f"{class_id} " + " ".join(f"{value:.6f}" for value in values)


def validate_video_splits(
    splits: Mapping[str, Iterable[str]],
    *,
    held_out_videos: Iterable[str] = (),
) -> None:
    """Reject cross-split overlap and held-out demo leakage."""

    owners: dict[str, str] = {}
    normalized = {
        split: {str(video).lstrip("0").zfill(2) for video in videos}
        for split, videos in splits.items()
    }
    for split in ("train", "val", "test"):
        for video in sorted(normalized.get(split, set())):
            previous = owners.get(video)
            if previous is not None:
                raise ValueError(f"video {video} appears in both {previous} and {split}")
            owners[video] = split

    for raw_video in held_out_videos:
        video = str(raw_video).lstrip("0").zfill(2)
        owner = owners.get(video)
        if owner in {"train", "val"}:
            raise ValueError(f"held-out video {video} appears in {owner}")
