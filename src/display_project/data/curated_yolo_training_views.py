"""Positive-only YOLO views for MECCANO active fasteners."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from display_project.data.yolo_training_views import (
    IMAGE_SUFFIXES,
    SPLITS,
    _load_source_qa,
    _materialize_image,
    _remap_label,
    _sha256_bytes,
    _sha256_file,
    _write_exact,
)

MODES: dict[str, dict[str, Any]] = {
    "multiclass": {
        "names": ("screw", "bolt", "screwdriver"),
        "mapping": {0: 0, 1: 1, 2: 2},
    },
    "binary": {"names": ("fastener",), "mapping": {0: 0, 1: 0, 2: 0}},
}


def _video_frame(path: Path) -> tuple[str, int]:
    try:
        video, frame = path.stem.split("_", 1)
        return video.lstrip("0").zfill(2), int(frame)
    except ValueError as exc:
        raise ValueError(
            f"expected <video>_<frame> image name: {path.name}"
        ) from exc


def build_curated_fastener_view(
    source_root: str | Path,
    output_root: str | Path,
    *,
    source_qa_path: str | Path | None = None,
    mode: str,
    link_mode: str = "hardlink",
    train_min_frame_gap: int = 3,
) -> dict[str, Any]:
    """Build a positive-only active-fastener development view."""

    if mode not in MODES:
        raise ValueError(f"unknown curated fastener mode: {mode}")
    if train_min_frame_gap < 1:
        raise ValueError("train_min_frame_gap must be at least one")

    source, output = Path(source_root), Path(output_root)
    qa_path = Path(source_qa_path) if source_qa_path else source / "dataset-qa.json"
    source_qa = _load_source_qa(qa_path)
    if source_qa.get("task_profile") != "fasteners":
        raise ValueError("curated view requires a fasteners training view")
    names = tuple(MODES[mode]["names"])
    mapping = dict(MODES[mode]["mapping"])
    rows: list[dict[str, Any]] = []
    payloads: dict[tuple[str, str], bytes] = {}
    images = {split: 0 for split in SPLITS}
    boxes = {split: Counter() for split in SPLITS}
    positives = {split: 0 for split in SPLITS}
    excluded = {
        split: Counter(
            unverified_negative=0,
            duplicate_target_label=0,
            temporal_redundancy=0,
        )
        for split in SPLITS
    }
    held_out: list[str] = []

    manifest_path = source / "file-manifest.jsonl"
    expected_manifest = source_qa.get("view_manifest_sha256")
    if (
        not manifest_path.is_file()
        or not isinstance(expected_manifest, str)
        or _sha256_file(manifest_path) != expected_manifest
    ):
        raise ValueError("source fasteners manifest hash mismatch")

    candidates: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    for line_number, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            source_row = json.loads(raw_line)
            split = str(source_row["split"])
            box_count = int(source_row["target_box_count"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid source manifest row {line_number}"
            ) from exc
        if split not in SPLITS:
            raise ValueError(f"invalid source manifest split: {split}")
        if box_count <= 0:
            excluded[split]["unverified_negative"] += 1
            continue
        candidates[split].append(source_row)

    for split in SPLITS:
        last_frame: dict[str, int] = {}
        last_classes: dict[str, set[int]] = {}
        for source_row in candidates[split]:
            source_image = source / str(source_row["source_image"])
            relative = source_image.relative_to(source / "images" / split)
            label_path = source / "labels" / split / relative.with_suffix(".txt")
            if not source_image.is_file() or not label_path.is_file():
                raise ValueError(f"missing source image/label pair: {relative}")
            if split == "test" and source_image.stem.startswith("09_"):
                held_out.append(source_image.relative_to(source).as_posix())
            payload, counts = _remap_label(label_path, mapping)
            if not counts:
                raise ValueError(f"positive manifest row has no target labels: {relative}")
            positives[split] += 1
            lines = payload.decode("utf-8").splitlines()
            if len(lines) != len(set(lines)):
                excluded[split]["duplicate_target_label"] += 1
                continue

            video, frame = _video_frame(source_image)
            classes = set(counts)
            if (
                split == "train"
                and video in last_frame
                and frame - last_frame[video] < train_min_frame_gap
                and classes <= last_classes[video]
            ):
                excluded[split]["temporal_redundancy"] += 1
                continue
            last_frame[video], last_classes[video] = frame, classes
            key = relative.with_suffix("").as_posix()
            payloads[(split, key)] = payload
            images[split] += 1
            boxes[split].update(
                {names[class_id]: count for class_id, count in counts.items()}
            )
            rows.append(
                {
                    "split": split,
                    "video": video,
                    "frame": frame,
                    "source_image": source_image.relative_to(source).as_posix(),
                    "target_label_sha256": _sha256_bytes(payload),
                    "target_box_count": sum(counts.values()),
                }
            )

    if held_out:
        raise ValueError(f"held-out video 09 derivatives reached view: {held_out[:3]}")
    for row in rows:
        split = str(row["split"])
        source_image = source / str(row["source_image"])
        relative = source_image.relative_to(source / "images" / split)
        _materialize_image(
            source_image, output / "images" / split / relative, link_mode
        )
        _write_exact(
            output / "labels" / split / relative.with_suffix(".txt"),
            payloads[(split, relative.with_suffix("").as_posix())],
        )

    manifest = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    manifest_hash = _sha256_bytes(manifest.encode("utf-8"))
    commitment = {
        "source": source_qa["content_sha256"],
        "mode": mode,
        "mapping": mapping,
        "train_min_frame_gap": train_min_frame_gap,
        "manifest": manifest_hash,
    }
    report = {
        "schema_version": "display_project.curated_yolo_training_view-1.0.0",
        "status": "PASS",
        "passed": True,
        "dataset_profile": "DEVELOPMENT_CANDIDATE",
        "formal_admission": "DEVELOPMENT_ONLY",
        "task_profile": "fasteners",
        "selection_profile": "positive_active_fasteners",
        "mode": mode,
        "label_semantics": "ACTIVE_FASTENER_ONLY",
        "exhaustive_visible_instance_labels": False,
        "manual_annotation_count": 0,
        "negative_policy": "UNVERIFIED_NEGATIVES_EXCLUDED",
        "names": list(names),
        "class_mapping": {str(key): value for key, value in sorted(mapping.items())},
        "source_dataset_content_sha256": source_qa["content_sha256"],
        "held_out_videos": ["09"],
        "held_out_test_assets": [],
        "images": images,
        "labels": dict(images),
        "boxes": {
            split: {name: int(boxes[split][name]) for name in names}
            for split in SPLITS
        },
        "negative_images": {split: 0 for split in SPLITS},
        "source_positive_images": positives,
        "excluded_images": {split: dict(excluded[split]) for split in SPLITS},
        "temporal_thinning": {
            "train_min_frame_gap": train_min_frame_gap,
            "new_class_within_gap_is_retained": True,
            "val_test_thinned": False,
        },
        "videos": source_qa.get("videos", {}),
        "view_manifest_sha256": manifest_hash,
        "content_sha256": _sha256_bytes(
            json.dumps(commitment, sort_keys=True, separators=(",", ":")).encode()
        ),
        "link_mode": link_mode,
    }
    yaml = [
        f"path: {output.resolve().as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        "names:",
        *(f"  {index}: {name}" for index, name in enumerate(names)),
        "",
    ]
    _write_exact(output / "file-manifest.jsonl", manifest.encode("utf-8"))
    _write_exact(output / "dataset.yaml", "\n".join(yaml).encode("utf-8"))
    _write_exact(
        output / "dataset-qa.json",
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(),
    )
    return report
