"""Normalize the small set of MECCANO annotation formats used by the demo."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


_FRAME_SUFFIX = re.compile(r"(?:^|_)(\d+)$")
_HAND_CLASSES = {"sx_hand", "dx_hand"}


def parse_frame_number(value: str | int) -> int:
    """Return a frame integer from a padded filename or integer.

    Both five- and six-digit MECCANO names are accepted.  For COCO names such
    as ``06_00952.jpg``, the numeric suffix is the frame and the prefix remains
    available to the caller as the video id.
    """

    if isinstance(value, bool):
        raise ValueError("a boolean is not a frame number")
    if isinstance(value, int):
        frame = value
    else:
        stem = Path(str(value).strip()).stem
        match = _FRAME_SUFFIX.search(stem)
        if not match:
            raise ValueError(f"cannot parse frame number from {value!r}")
        frame = int(match.group(1))
    if frame < 0:
        raise ValueError("frame number must be non-negative")
    return frame


def _deduplicate(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        marker = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            result.append(row)
    return result


def load_temporal_annotations(
    path: str | Path,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    """Load action or verb CSV rows, normalize frames, and remove duplicates."""

    if kind not in {"action", "verb"}:
        raise ValueError("kind must be 'action' or 'verb'")
    id_field = f"{kind}_id"
    source_name_field = "action_name" if kind == "action" else "verb_class"
    output_name_field = f"{kind}_name"
    normalized: list[dict[str, Any]] = []

    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for line_number, source in enumerate(csv.DictReader(stream), start=2):
            try:
                start = parse_frame_number(source["start_frame"])
                end = parse_frame_number(source["end_frame"])
                if end < start:
                    raise ValueError("end frame precedes start frame")
                row = {
                    "video_id": source["video_id"].strip(),
                    id_field: int(source[id_field]),
                    output_name_field: source[source_name_field].strip(),
                    "start_frame": start,
                    "end_frame": end,
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid {kind} row at {path}:{line_number}: {exc}") from exc
            normalized.append(row)

    return _deduplicate(normalized)


def join_action_verbs(
    actions: Iterable[Mapping[str, Any]],
    verbs: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Associate actions and verbs by video and exact temporal interval.

    IDs are deliberately not used as a cross-file key because the MECCANO
    annotation families contain id/name conflicts.  Ambiguous or missing
    interval matches are rejected instead of guessing.
    """

    index: dict[tuple[str, int, int], list[Mapping[str, Any]]] = {}
    for verb in verbs:
        key = (
            str(verb["video_id"]),
            int(verb["start_frame"]),
            int(verb["end_frame"]),
        )
        index.setdefault(key, []).append(verb)

    joined: list[dict[str, Any]] = []
    for action in actions:
        key = (
            str(action["video_id"]),
            int(action["start_frame"]),
            int(action["end_frame"]),
        )
        matches = index.get(key, [])
        unique_matches = _deduplicate([dict(match) for match in matches])
        if len(unique_matches) != 1:
            raise ValueError(
                f"expected one verb for interval {key}, found {len(unique_matches)}"
            )
        joined.append({**dict(action), **unique_matches[0]})
    return joined


def _load_json(source: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    with Path(source).open(encoding="utf-8") as stream:
        loaded = json.load(stream)
    if not isinstance(loaded, Mapping):
        raise ValueError("COCO document must be an object")
    return loaded


def _clip_bbox(
    bbox: Iterable[Any], image_width: int, image_height: int
) -> list[float] | None:
    values = list(bbox)
    if len(values) != 4:
        raise ValueError(f"bbox must contain four values, got {values!r}")
    x, y, width, height = (float(value) for value in values)
    if width < 0 or height < 0:
        raise ValueError(f"bbox width and height must be non-negative: {values!r}")
    x1 = min(max(x, 0.0), float(image_width))
    y1 = min(max(y, 0.0), float(image_height))
    x2 = min(max(x + width, 0.0), float(image_width))
    y2 = min(max(y + height, 0.0), float(image_height))
    clipped_width = x2 - x1
    clipped_height = y2 - y1
    if clipped_width <= 0 or clipped_height <= 0:
        return None
    return [x1, y1, clipped_width, clipped_height]


def _video_and_frame(file_name: str) -> tuple[str, int]:
    stem = Path(file_name).stem
    if "_" not in stem:
        return "", parse_frame_number(stem)
    video_id, _ = stem.rsplit("_", 1)
    return video_id, parse_frame_number(stem)


def load_coco_annotations(
    source: str | Path | Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Normalize COCO boxes while retaining category identity by name."""

    document = _load_json(source)
    images = {int(image["id"]): image for image in document.get("images", [])}
    categories = {
        int(category["id"]): str(category["name"])
        for category in document.get("categories", [])
    }
    normalized: list[dict[str, Any]] = []

    for annotation in document.get("annotations", []):
        image_id = int(annotation["image_id"])
        category_id = int(annotation["category_id"])
        if image_id not in images:
            raise ValueError(f"annotation {annotation.get('id')} has dangling image_id {image_id}")
        if category_id not in categories:
            raise ValueError(
                f"annotation {annotation.get('id')} has dangling category_id {category_id}"
            )
        image = images[image_id]
        width, height = int(image["width"]), int(image["height"])
        bbox = _clip_bbox(annotation["bbox"], width, height)
        if bbox is None:
            continue
        video_id, frame = _video_and_frame(str(image["file_name"]))
        normalized.append(
            {
                "annotation_id": int(annotation["id"]),
                "image_id": image_id,
                "video_id": video_id,
                "frame": frame,
                "category_name": categories[category_id],
                "bbox": bbox,
                "image_width": width,
                "image_height": height,
            }
        )
    return _deduplicate(normalized)


def load_hand_annotations(
    path: str | Path,
    *,
    image_size: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Parse VGG hand CSV records, retaining non-hand extra classes."""

    normalized: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for line_number, source in enumerate(csv.DictReader(stream), start=2):
            try:
                shape = json.loads(source["region_shape_attributes"] or "{}")
                attributes = json.loads(source["region_attributes"] or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid VGG JSON at {path}:{line_number}: {exc}") from exc
            if not shape:
                continue
            if shape.get("name") != "rect":
                raise ValueError(f"unsupported VGG shape at {path}:{line_number}: {shape!r}")
            category = attributes.get("class")
            if not category:
                raise ValueError(f"VGG region has no class at {path}:{line_number}")
            raw_bbox = [shape[key] for key in ("x", "y", "width", "height")]
            bbox = (
                _clip_bbox(raw_bbox, *image_size)
                if image_size is not None
                else [float(value) for value in raw_bbox]
            )
            if bbox is None:
                continue
            is_hand = str(category).casefold() in _HAND_CLASSES
            normalized.append(
                {
                    "frame": parse_frame_number(source["filename"]),
                    "category_name": str(category),
                    "bbox": bbox,
                    "is_hand": is_hand,
                    "is_extra_class": not is_hand,
                }
            )
    return _deduplicate(normalized)
