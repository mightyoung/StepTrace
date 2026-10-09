from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, TypedDict

from display_project.data.meccano import load_coco_annotations, load_hand_annotations
from display_project.provenance import portable_path
from display_project.data.yolo_dataset import (
    MECCANO_YOLO_NAMES,
    coco_category_to_yolo,
    to_yolo_line,
    validate_video_splits,
)


OFFICIAL_SPLITS = {
    "train": {"01", "02", "03", "04", "05", "06", "13", "14", "15", "16", "18"},
    "val": {"07", "17"},
    "test": {"08", "09", "10", "11", "12", "19", "20"},
}


class SelectedFrame(TypedDict):
    video: str
    frame: int
    width: int
    height: int
    labels: list[str]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_entry(
    path: Path, *, family: str, split: str, used_videos: Iterable[str]
) -> dict[str, object]:
    resolved = path.resolve()
    return {
        "family": family,
        "split": split,
        # Repository-relative when possible so frozen QA artifacts verify on
        # any machine; absolute only for sources outside the repository.
        "path": portable_path(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
        "schema_status": "PASS",
        "used_videos": sorted(set(used_videos)),
    }


def _sources_sha256(sources: list[dict[str, object]]) -> str:
    payload = json.dumps(
        sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a leakage-safe six-class YOLO dataset from MECCANO videos"
    )
    parser.add_argument("--dataset-root", default="datasets/MECCANO")
    parser.add_argument(
        "--video-root", help="directory containing the 20 source videos"
    )
    parser.add_argument("--output", default="data/yolo/meccano-small-objects")
    parser.add_argument(
        "--report-output",
        help="write the deterministic conversion report even in --audit-only mode",
    )
    parser.add_argument("--held-out-video", action="append", default=["09"])
    parser.add_argument("--audit-only", action="store_true")
    return parser.parse_args()


def _video_index(root: Path | None) -> dict[str, Path]:
    if root is None or not root.exists():
        return {}
    result: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in {
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
        }:
            continue
        digits = "".join(character for character in path.stem if character.isdigit())
        if digits:
            result.setdefault(digits.lstrip("0").zfill(2), path)
    return result


def _hand_index(path: Path) -> dict[int, list[dict[str, object]]]:
    if not path.exists():
        return {}
    rows = load_hand_annotations(path)
    result: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row["is_hand"]:
            result[int(row["frame"])].append(row)
    return result


def _box_values(value: object) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must contain four coordinates")
    return [float(item) for item in value]


def _clipped_hand_line(row: dict[str, object], width: int, height: int) -> str | None:
    x, y, box_width, box_height = _box_values(row.get("bbox"))
    x1, y1 = max(0.0, x), max(0.0, y)
    x2, y2 = min(float(width), x + box_width), min(float(height), y + box_height)
    if x2 <= x1 or y2 <= y1:
        return None
    return to_yolo_line(
        0, [x1, y1, x2 - x1, y2 - y1], image_width=width, image_height=height
    )


def _extract_frame(video: Path, frame_number: int, target: Path) -> None:
    import cv2

    capture = cv2.VideoCapture(str(video))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_number - 1))
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise RuntimeError(f"cannot decode frame {frame_number} from {video}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(target), frame):
        raise RuntimeError(f"cannot write extracted frame: {target}")


def _extract_frames_batch(video: Path, requests: list[tuple[int, Path]]) -> None:
    """Extract ascending 1-based frames with one VideoCapture per source video."""
    if not requests:
        return
    import cv2

    ordered = sorted(requests, key=lambda item: item[0])
    capture = cv2.VideoCapture(str(video))
    try:
        first_frame = ordered[0][0]
        if first_frame < 1:
            raise ValueError(f"frame numbers must be 1-based: {first_frame}")
        capture.set(cv2.CAP_PROP_POS_FRAMES, first_frame - 1)
        current_index = first_frame - 1
        for frame_number, target in ordered:
            if frame_number < current_index + 1:
                raise ValueError(f"frame requests must be ascending: {frame_number}")
            while current_index < frame_number - 1:
                # Skipped frames do not need a decoded NumPy array. `grab`.
                # advances the decoder without allocating a full 1080p frame.
                ok = capture.grab()
                if not ok:
                    raise RuntimeError(
                        f"cannot decode frame {frame_number} from {video}"
                    )
                current_index += 1
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"cannot decode frame {frame_number} from {video}")
            current_index += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(target), frame):
                raise RuntimeError(f"cannot write extracted frame: {target}")
            del frame
    finally:
        capture.release()


def _normalise_videos(values: Iterable[object]) -> set[str]:
    return {str(value).lstrip("0").zfill(2) for value in values}


def _remove_stale_held_out_outputs(
    output: Path, held_out_videos: set[str], *, split: str = "test"
) -> None:
    """Remove only previously generated files for held-out test videos."""

    for kind, suffix in (("images", ".jpg"), ("labels", ".txt")):
        directory = output / kind / split
        if not directory.exists():
            continue
        for path in directory.rglob(f"*{suffix}"):
            match = re.match(r"^(\d+)_", path.stem)
            if match and str(int(match.group(1))).zfill(2) in held_out_videos:
                path.unlink()


def _held_out_derivatives(
    output: Path, held_out_videos: set[str], *, split: str = "test"
) -> list[str]:
    found: list[str] = []
    for kind, suffix in (("images", ".jpg"), ("labels", ".txt")):
        directory = output / kind / split
        if not directory.exists():
            continue
        for path in directory.rglob(f"*{suffix}"):
            match = re.match(r"^(\d+)_", path.stem)
            if match and str(int(match.group(1))).zfill(2) in held_out_videos:
                found.append(path.relative_to(output).as_posix())
    return sorted(found)


def main() -> int:
    args = _arguments()
    dataset_root = Path(args.dataset_root)
    output = Path(args.output)
    video_root = None if args.video_root is None else Path(args.video_root)
    videos = _video_index(video_root)
    held_out_videos = _normalise_videos(args.held_out_video)
    validate_video_splits(OFFICIAL_SPLITS, held_out_videos=held_out_videos)
    generated_splits = {
        split: set(video_ids) - held_out_videos
        for split, video_ids in OFFICIAL_SPLITS.items()
    }

    coco_root = dataset_root / "MECCANO_active_objects_annotations"
    hands_root = dataset_root / "MECCANO_hands_bounding_box_annotations"
    split_source_names = {"train": "train", "val": "val", "test": "test"}
    hand_source_names = {"train": "Train", "val": "Val", "test": "Test"}
    counts: Counter[tuple[str, str]] = Counter()
    selected: dict[str, list[SelectedFrame]] = defaultdict(list)
    annotation_sources: list[dict[str, object]] = []

    for split, source_name in split_source_names.items():
        coco_path = coco_root / f"instances_meccano_{source_name}.json"
        rows = load_coco_annotations(coco_path)
        annotation_sources.append(
            _source_entry(
                coco_path,
                family="ACTIVE_OBJECT_COCO",
                split=split,
                used_videos=generated_splits[split],
            )
        )
        grouped: dict[tuple[str, int, int, int], list[dict[str, object]]] = defaultdict(
            list
        )
        for row in rows:
            video = str(row["video_id"]).lstrip("0").zfill(2)
            # Filter before grouping so held-out videos cannot reach any artifact.
            if video in held_out_videos:
                continue
            if video not in generated_splits[split]:
                raise ValueError(
                    f"official {split} annotation unexpectedly contains video {video}"
                )
            grouped[
                (
                    video,
                    int(row["frame"]),
                    int(row["image_width"]),
                    int(row["image_height"]),
                )
            ].append(row)

        hand_cache: dict[str, dict[int, list[dict[str, object]]]] = {}
        for (video, frame, width, height), annotation_rows in sorted(grouped.items()):
            mapped: list[str] = []
            for annotation in annotation_rows:
                class_name = coco_category_to_yolo(str(annotation["category_name"]))
                if class_name is None:
                    continue
                class_id = MECCANO_YOLO_NAMES.index(class_name)
                mapped.append(
                    to_yolo_line(
                        class_id,
                        _box_values(annotation.get("bbox")),
                        image_width=width,
                        image_height=height,
                    )
                )
                counts[(split, class_name)] += 1

            if video not in hand_cache:
                hand_path = (
                    hands_root / hand_source_names[split] / f"{int(video):04d}.csv"
                )
                hand_cache[video] = _hand_index(hand_path)
            for hand in hand_cache[video].get(frame, []):
                line = _clipped_hand_line(hand, width, height)
                if line is not None:
                    mapped.append(line)
                    counts[(split, "hand")] += 1

            if mapped:
                selected[split].append(
                    {
                        "video": video,
                        "frame": frame,
                        "width": width,
                        "height": height,
                        "labels": mapped,
                    }
                )

    for split, source_name in hand_source_names.items():
        for video in sorted(generated_splits[split]):
            hand_path = hands_root / source_name / f"{int(video):04d}.csv"
            # Reopen every declared VGG source so schema/parser failures are fatal.
            load_hand_annotations(hand_path)
            annotation_sources.append(
                _source_entry(
                    hand_path,
                    family="HAND_VGG_CSV",
                    split=split,
                    used_videos=[video],
                )
            )

    missing_source_videos = sorted(
        set().union(*generated_splits.values()) - set(videos)
    )
    report = {
        "splits": {split: sorted(videos) for split, videos in generated_splits.items()},
        "held_out_videos": sorted(held_out_videos),
        "dataset_profile": "DEVELOPMENT_CANDIDATE",
        "formal_admission": "BLOCKED_UNTIL_G2",
        "available_source_videos": sorted(videos),
        "missing_source_videos": missing_source_videos,
        "selected_images": {
            split: len(selected.get(split, [])) for split in generated_splits
        },
        "boxes": {
            split: {name: counts[(split, name)] for name in MECCANO_YOLO_NAMES}
            for split in ("train", "val", "test")
        },
        "annotation_source_provenance": {
            "status": "PASS",
            "source_count": len(annotation_sources),
            "sources_sha256": _sources_sha256(annotation_sources),
            "sources": annotation_sources,
            "held_out_videos_excluded": sorted(held_out_videos),
        },
    }
    report_output = (
        Path(args.report_output)
        if args.report_output
        else output / "dataset-audit.json"
    )
    if args.audit_only:
        if args.report_output:
            report_output.parent.mkdir(parents=True, exist_ok=True)
            report_output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if video_root is None:
        raise ValueError("--video-root is required unless --audit-only is used")
    if missing_source_videos:
        raise FileNotFoundError(
            f"missing MECCANO source videos: {missing_source_videos}"
        )

    _remove_stale_held_out_outputs(output, held_out_videos)
    requests_by_video: dict[str, list[tuple[int, Path]]] = defaultdict(list)
    for split, items in selected.items():
        image_dir, label_dir = output / "images" / split, output / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        for item in items:
            stem = f"{item['video']}_{int(item['frame']):05d}"
            image_path = image_dir / f"{stem}.jpg"
            label_path = label_dir / f"{stem}.txt"
            pair_valid = (
                image_path.is_file()
                and image_path.stat().st_size > 0
                and label_path.is_file()
                and label_path.stat().st_size > 0
            )
            if not pair_valid:
                if image_path.exists():
                    image_path.unlink()
                if label_path.exists():
                    label_path.unlink()
                # Persist labels before extraction so completed pairs survive interruption.
                label_path.write_text(
                    "\n".join(item["labels"]) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                requests_by_video[str(item["video"])].append(
                    (int(item["frame"]), image_path)
                )
    for video, requests in requests_by_video.items():
        _extract_frames_batch(videos[video], requests)

    residual_held_out = _held_out_derivatives(output, held_out_videos)
    if residual_held_out:
        raise RuntimeError(
            "held-out derivatives remain after MECCANO candidate build: "
            + ", ".join(residual_held_out)
        )

    output.mkdir(parents=True, exist_ok=True)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    shutil.copy2("configs/yolo_meccano_small_objects.yaml", output / "dataset.yaml")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
