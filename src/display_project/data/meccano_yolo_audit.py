"""Deterministic QA and identity audit for a MECCANO YOLO candidate dataset."""

from __future__ import annotations

import hashlib
import json
import math
import re
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image

from .yolo_dataset import MECCANO_YOLO_NAMES

SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
UNKNOWN_VALUES = {"", "UNKNOWN", "NONE", "NOT_APPLICABLE"}
_FRAME_RE = re.compile(r"^(?P<video>\d{2,4})_(?P<frame>\d+)$")
_SOURCE_FAMILIES = {"ACTIVE_OBJECT_COCO", "HAND_VGG_CSV"}
_VGG_REQUIRED_COLUMNS = {
    "filename",
    "region_shape_attributes",
    "region_attributes",
}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_video(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    if not digits:
        return "UNKNOWN"
    return str(int(digits)).zfill(2)


def _frame_identity(stem: str) -> tuple[str, int] | None:
    match = _FRAME_RE.fullmatch(stem)
    if match is None:
        return None
    return _canonical_video(match.group("video")), int(match.group("frame"))


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _source_manifest_sha256(sources: list[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_meccano_annotation_sources(
    provenance: object,
    *,
    splits: Mapping[str, Any],
    held_out_videos: Iterable[str] = ("09",),
    repository_root: str | Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Reopen every COCO/VGG source and validate its immutable binding.

    Source paths may be repository-relative (portable, preferred) or absolute
    (legacy artifacts). Relative paths resolve against ``repository_root``
    (default: the current working directory) and must not traverse upward.
    """

    errors: list[str] = []
    held_out = {_canonical_video(value) for value in held_out_videos}
    expected = {
        split: sorted({_canonical_video(value) for value in splits.get(split, [])})
        for split in SPLITS
    }
    if not isinstance(provenance, Mapping):
        return {
            "status": "FAIL",
            "source_count": 0,
            "sources_sha256": None,
            "sources": [],
            "held_out_videos_excluded": sorted(held_out),
        }, ["annotation source provenance is missing"]
    raw_sources = provenance.get("sources")
    sources = list(raw_sources) if isinstance(raw_sources, list) else []
    if not sources:
        errors.append("annotation source provenance has no sources")
    if provenance.get("source_count") != len(sources):
        errors.append("annotation source count mismatch")
    if provenance.get("sources_sha256") != _source_manifest_sha256(sources):
        errors.append("annotation source manifest hash mismatch")
    if provenance.get("held_out_videos_excluded") != sorted(held_out):
        errors.append("annotation source held-out declaration mismatch")

    seen_paths: set[str] = set()
    coco_splits: set[str] = set()
    vgg_videos: dict[str, set[str]] = {split: set() for split in SPLITS}
    for index, source in enumerate(sources):
        prefix = f"annotation source {index}"
        if not isinstance(source, Mapping):
            errors.append(f"{prefix}: record is not an object")
            continue
        if set(source) != {
            "family", "split", "path", "bytes", "sha256", "schema_status", "used_videos"
        }:
            errors.append(f"{prefix}: schema mismatch")
            continue
        family = source.get("family")
        split = source.get("split")
        raw_path = source.get("path")
        used = source.get("used_videos")
        if family not in _SOURCE_FAMILIES or split not in SPLITS:
            errors.append(f"{prefix}: family or split is invalid")
            continue
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{prefix}: path is invalid")
            continue
        declared = Path(raw_path)
        if not declared.is_absolute() and ".." in declared.parts:
            errors.append(f"{prefix}: relative path must not traverse upward")
            continue
        root_base = (
            Path(repository_root).resolve()
            if repository_root is not None
            else Path.cwd().resolve()
        )
        path = declared if declared.is_absolute() else root_base / declared
        canonical_path = path.resolve().as_posix()
        if canonical_path in seen_paths:
            errors.append(f"{prefix}: duplicate path")
        seen_paths.add(canonical_path)
        if not path.is_file():
            errors.append(f"{prefix}: file is missing")
            continue
        if source.get("bytes") != path.stat().st_size:
            errors.append(f"{prefix}: byte count mismatch")
        if source.get("sha256") != sha256_file(path):
            errors.append(f"{prefix}: sha256 mismatch")
        if source.get("schema_status") != "PASS":
            errors.append(f"{prefix}: schema status is not PASS")
        canonical_used = (
            sorted({_canonical_video(value) for value in used})
            if isinstance(used, list)
            else []
        )
        if canonical_used != used or any(video in held_out for video in canonical_used):
            errors.append(f"{prefix}: used_videos is invalid")
        try:
            if family == "ACTIVE_OBJECT_COCO":
                document = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(document, Mapping) or not all(
                    isinstance(document.get(key), list)
                    for key in ("images", "annotations", "categories")
                ):
                    raise ValueError("COCO object/list schema mismatch")
                if canonical_used != expected[split] or split in coco_splits:
                    raise ValueError("COCO used_videos or split uniqueness mismatch")
                coco_splits.add(split)
            else:
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    columns = set(csv.DictReader(stream).fieldnames or [])
                if not _VGG_REQUIRED_COLUMNS.issubset(columns):
                    raise ValueError("VGG CSV columns missing")
                if len(canonical_used) != 1 or canonical_used[0] not in expected[split]:
                    raise ValueError("VGG used_videos mismatch")
                if canonical_used:
                    vgg_videos[split].add(canonical_used[0])
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{prefix}: schema validation failed: {exc}")

    if coco_splits != set(SPLITS):
        errors.append("annotation source COCO split coverage mismatch")
    for split in SPLITS:
        if vgg_videos[split] != set(expected[split]):
            errors.append(f"annotation source VGG coverage mismatch: {split}")
    summary = {
        "status": "PASS" if not errors else "FAIL",
        "source_count": len(sources),
        "sources_sha256": provenance.get("sources_sha256"),
        "sources": sources,
        "held_out_videos_excluded": sorted(held_out),
    }
    return summary, errors


def _parse_label(
    path: Path,
    *,
    width: int | None,
    height: int | None,
) -> tuple[Counter[str], int, int, list[str]]:
    counts: Counter[str] = Counter()
    tiny_boxes = 0
    box_count = 0
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception as exc:
        return counts, tiny_boxes, box_count, [f"cannot read label: {exc}"]
    nonempty = [
        (index, line.strip()) for index, line in enumerate(lines, 1) if line.strip()
    ]
    if not nonempty:
        return counts, tiny_boxes, box_count, ["empty label"]
    for line_number, line in nonempty:
        fields = line.split()
        if len(fields) != 5:
            errors.append(f"line {line_number}: expected 5 fields")
            continue
        try:
            class_value = float(fields[0])
            coordinates = [float(value) for value in fields[1:]]
        except ValueError:
            errors.append(f"line {line_number}: non-numeric value")
            continue
        if not class_value.is_integer():
            errors.append(f"line {line_number}: class id is not integral")
            continue
        class_id = int(class_value)
        if class_id < 0 or class_id >= len(MECCANO_YOLO_NAMES):
            errors.append(f"line {line_number}: class id is outside 0..5")
            continue
        if not all(math.isfinite(value) for value in coordinates):
            errors.append(f"line {line_number}: non-finite box value")
            continue
        center_x, center_y, box_width, box_height = coordinates
        edge_epsilon = 1e-6
        x1 = center_x - box_width / 2
        x2 = center_x + box_width / 2
        y1 = center_y - box_height / 2
        y2 = center_y + box_height / 2
        if (
            box_width <= 0
            or box_height <= 0
            or x1 < -edge_epsilon
            or y1 < -edge_epsilon
            or x2 > 1 + edge_epsilon
            or y2 > 1 + edge_epsilon
        ):
            errors.append(f"line {line_number}: box is outside normalized bounds")
            continue
        name = MECCANO_YOLO_NAMES[class_id]
        counts[name] += 1
        box_count += 1
        if width is not None and height is not None:
            if box_width * width < 4 or box_height * height < 4:
                tiny_boxes += 1
    return counts, tiny_boxes, box_count, errors


def audit_meccano_yolo_dataset(
    dataset_root: str | Path,
    *,
    held_out_videos: Iterable[str] = ("09",),
    file_manifest_path: str | Path | None = None,
    conversion_report_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(dataset_root)
    held_out = {_canonical_video(value) for value in held_out_videos}
    errors: list[str] = []
    images: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    labels: dict[str, dict[str, Path]] = defaultdict(dict)
    label_rows: dict[tuple[str, str], dict[str, Any]] = {}
    file_rows: list[dict[str, Any]] = []
    class_counts: dict[str, Counter[str]] = {split: Counter() for split in SPLITS}
    tiny_boxes = {split: 0 for split in SPLITS}
    decode_failures = 0
    invalid_labels = 0
    video_splits: defaultdict[str, set[str]] = defaultdict(set)
    frame_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    held_out_test_assets: list[str] = []

    for split in SPLITS:
        image_root = root / "images" / split
        label_root = root / "labels" / split
        image_paths = (
            sorted(
                path
                for path in image_root.rglob("*")
                if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
            )
            if image_root.exists()
            else []
        )
        label_paths = (
            sorted(path for path in label_root.rglob("*.txt") if path.is_file())
            if label_root.exists()
            else []
        )
        for path in image_paths:
            key = path.relative_to(image_root).with_suffix("").as_posix()
            relative = path.relative_to(root).as_posix()
            identity = _frame_identity(path.stem)
            video_id: str | None = None
            frame_id: int | None = None
            if identity is None:
                errors.append(f"unparseable image identity: {relative}")
            else:
                video_id, frame_id = identity
                video_splits[video_id].add(split)
                frame_counts[(split, video_id)] += 1
                if split == "test" and video_id in held_out:
                    held_out_test_assets.append(relative)
                    errors.append(f"held-out video derivative in test: {relative}")
            width: int | None = None
            height: int | None = None
            try:
                with Image.open(path) as image:
                    image.load()
                    width, height = image.size
            except Exception as exc:
                decode_failures += 1
                errors.append(f"image decode failure {relative}: {exc}")
            row = {
                "path": relative,
                "kind": "image",
                "split": split,
                "video_id": video_id,
                "frame_id": frame_id,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "width": width,
                "height": height,
            }
            images[split][key] = row
            file_rows.append(row)
        for path in label_paths:
            key = path.relative_to(label_root).with_suffix("").as_posix()
            relative = path.relative_to(root).as_posix()
            row = {
                "path": relative,
                "kind": "label",
                "split": split,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "box_count": None,
            }
            labels[split][key] = path
            label_rows[(split, key)] = row
            file_rows.append(row)

        missing_labels = sorted(set(images[split]) - set(labels[split]))
        missing_images = sorted(set(labels[split]) - set(images[split]))
        errors.extend(f"missing label pair {split}/{key}" for key in missing_labels)
        errors.extend(f"missing image pair {split}/{key}" for key in missing_images)
        for key in sorted(set(images[split]) & set(labels[split])):
            image_row = images[split][key]
            counts, tiny, boxes, label_errors = _parse_label(
                labels[split][key],
                width=image_row["width"],
                height=image_row["height"],
            )
            class_counts[split].update(counts)
            tiny_boxes[split] += tiny
            label_rows[(split, key)]["box_count"] = boxes
            if label_errors:
                invalid_labels += len(label_errors)
                relative = labels[split][key].relative_to(root).as_posix()
                errors.extend(
                    f"invalid label {relative}: {error}" for error in label_errors
                )

        if not image_paths:
            errors.append(f"no images in split: {split}")
        if not label_paths:
            errors.append(f"no labels in split: {split}")

    image_hash_paths: defaultdict[str, list[str]] = defaultdict(list)
    for row in file_rows:
        if row["kind"] == "image":
            image_hash_paths[str(row["sha256"])].append(str(row["path"]))
    duplicate_image_hashes = {
        digest: sorted(paths)
        for digest, paths in sorted(image_hash_paths.items())
        if len(paths) > 1
    }

    sorted_rows = sorted(file_rows, key=lambda row: str(row["path"]))
    manifest_text = "".join(_canonical_json(row) + "\n" for row in sorted_rows)
    manifest_sha256 = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    if file_manifest_path is not None:
        _write_text(file_manifest_path, manifest_text)
    content_digest = hashlib.sha256()
    for row in sorted_rows:
        content_digest.update(str(row["path"]).encode("utf-8"))
        content_digest.update(b"\0")
        content_digest.update(str(row["sha256"]).encode("ascii"))
        content_digest.update(b"\0")
        content_digest.update(str(row["bytes"]).encode("ascii"))
        content_digest.update(b"\n")

    conversion_path = (
        Path(conversion_report_path)
        if conversion_report_path is not None
        else root / "dataset-audit.json"
    )
    conversion_mismatches: list[str] = []
    conversion_sha256: str | None = None
    annotation_source_provenance: dict[str, Any] = {
        "status": "FAIL",
        "source_count": 0,
        "sources_sha256": None,
        "sources": [],
        "held_out_videos_excluded": sorted(held_out),
    }
    if conversion_path.is_file():
        conversion_sha256 = sha256_file(conversion_path)
        try:
            conversion = json.loads(conversion_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            conversion_mismatches.append(f"cannot read conversion report: {exc}")
            conversion = {}
        expected_images = conversion.get("selected_images", {})
        expected_boxes = conversion.get("boxes", {})
        actual_source_splits = {
            split: sorted(
                video for video, assigned in video_splits.items() if split in assigned
            )
            for split in SPLITS
        }
        annotation_source_provenance, source_errors = (
            validate_meccano_annotation_sources(
                conversion.get("annotation_source_provenance"),
                splits=actual_source_splits,
                held_out_videos=held_out,
            )
        )
        conversion_mismatches.extend(source_errors)
        for split in SPLITS:
            actual_images = len(images[split])
            expected_image_count = expected_images.get(split)
            if expected_image_count != actual_images:
                conversion_mismatches.append(
                    f"{split} images {actual_images} != {expected_image_count}"
                )
            for name in MECCANO_YOLO_NAMES:
                actual_box_count = class_counts[split][name]
                expected_box_count = expected_boxes.get(split, {}).get(name)
                if expected_box_count != actual_box_count:
                    conversion_mismatches.append(
                        f"{split}/{name} boxes {actual_box_count} != "
                        f"{expected_box_count}"
                    )
    else:
        conversion_mismatches.append(f"missing conversion report: {conversion_path}")
    errors.extend(
        f"conversion cross-check: {mismatch}" for mismatch in conversion_mismatches
    )

    video_cross_split = {
        video: sorted(splits)
        for video, splits in sorted(video_splits.items())
        if len(splits) > 1
    }
    if video_cross_split:
        errors.append(f"videos cross splits: {video_cross_split}")

    images_by_split = {split: len(images[split]) for split in SPLITS}
    labels_by_split = {split: len(labels[split]) for split in SPLITS}
    boxes_by_split = {
        split: {name: int(class_counts[split][name]) for name in MECCANO_YOLO_NAMES}
        for split in SPLITS
    }
    videos_by_split = {
        split: sorted(
            video for video, splits in video_splits.items() if split in splits
        )
        for split in SPLITS
    }
    frame_counts_by_split = {
        split: {video: frame_counts[(split, video)] for video in videos_by_split[split]}
        for split in SPLITS
    }
    passed = not errors
    return {
        "schema_version": "1.0.0",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "dataset_profile": "DEVELOPMENT_CANDIDATE",
        "formal_admission": "BLOCKED_UNTIL_G2",
        "held_out_videos": sorted(held_out),
        "held_out_test_assets": sorted(held_out_test_assets),
        "images": images_by_split,
        "labels": labels_by_split,
        "boxes": boxes_by_split,
        "box_total": sum(sum(values.values()) for values in boxes_by_split.values()),
        "tiny_boxes": tiny_boxes,
        "decode_failures": decode_failures,
        "invalid_labels": invalid_labels,
        "missing_label_pairs": sum(
            len(set(images[split]) - set(labels[split])) for split in SPLITS
        ),
        "missing_image_pairs": sum(
            len(set(labels[split]) - set(images[split])) for split in SPLITS
        ),
        "videos": videos_by_split,
        "video_frame_counts": frame_counts_by_split,
        "video_cross_split": video_cross_split,
        "duplicate_image_hash_count": len(duplicate_image_hashes),
        "duplicate_image_hashes": duplicate_image_hashes,
        "file_count": len(sorted_rows),
        "file_manifest_sha256": manifest_sha256,
        "content_sha256": content_digest.hexdigest(),
        "conversion_report": conversion_path.as_posix(),
        "conversion_report_sha256": conversion_sha256,
        "conversion_crosscheck": {
            "status": "PASS" if not conversion_mismatches else "FAIL",
            "mismatches": conversion_mismatches,
        },
        "annotation_source_provenance": annotation_source_provenance,
        "errors": errors,
    }


def _cross_split(
    records: Iterable[Mapping[str, Any]],
    field: str,
) -> dict[str, list[str]]:
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        value = str(record.get(field, "UNKNOWN"))
        if value.upper() not in UNKNOWN_VALUES:
            groups[value].add(str(record["dataset_split"]))
    return {
        value: sorted(splits)
        for value, splits in sorted(groups.items())
        if len(splits) > 1
    }


def build_meccano_candidate_manifest(
    qa_report: Mapping[str, Any],
    identity_manifest_path: str | Path,
    *,
    held_out_videos: Iterable[str] = ("09",),
) -> dict[str, Any]:
    source_path = Path(identity_manifest_path)
    source_payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    source_records = (
        source_payload.get("records", [])
        if isinstance(source_payload, Mapping)
        else source_payload
    )
    meccano_records = [
        dict(record)
        for record in source_records
        if str(record.get("source_dataset", "")).casefold() == "meccano"
    ]
    source_by_video: dict[str, dict[str, Any]] = {}
    duplicate_source_videos: list[str] = []
    for record in meccano_records:
        video = _canonical_video(record.get("video_id", "UNKNOWN"))
        if video in source_by_video:
            duplicate_source_videos.append(video)
        source_by_video[video] = record

    candidate_records: list[dict[str, Any]] = []
    missing_source_identity: list[str] = []
    frame_counts = qa_report.get("video_frame_counts", {})
    for split in SPLITS:
        split_counts = frame_counts.get(split, {})
        for video, count in sorted(split_counts.items()):
            canonical_video = _canonical_video(video)
            source = source_by_video.get(canonical_video)
            if source is None:
                missing_source_identity.append(canonical_video)
                source = {}
            candidate_records.append(
                {
                    "asset_id": str(source.get("asset_id", "UNKNOWN")),
                    "canonical_root_id": str(
                        source.get("canonical_root_id", "UNKNOWN")
                    ),
                    "canonical_group_id": str(
                        source.get("canonical_group_id", "UNKNOWN")
                    ),
                    "video_id": str(source.get("video_id", canonical_video)),
                    "dataset_video_id": canonical_video,
                    "dataset_split": split,
                    "frame_count": int(count),
                    "operator_id": str(source.get("operator_id", "UNKNOWN")),
                    "complete_job_id": str(source.get("complete_job_id", "UNKNOWN")),
                    "tombstone_id": str(source.get("tombstone_id", "NONE")),
                    "identity_values_invented": False,
                    "adjacent_frames_inherit_video_split": True,
                }
            )

    unknown_operator = sorted(
        record["dataset_video_id"]
        for record in candidate_records
        if str(record["operator_id"]).upper() in UNKNOWN_VALUES
    )
    unknown_complete_job = sorted(
        record["dataset_video_id"]
        for record in candidate_records
        if str(record["complete_job_id"]).upper() in UNKNOWN_VALUES
    )
    video_cross_split = dict(qa_report.get("video_cross_split", {}))
    operator_cross_split = _cross_split(candidate_records, "operator_id")
    complete_job_cross_split = _cross_split(candidate_records, "complete_job_id")
    held_out = {_canonical_video(value) for value in held_out_videos}
    held_out_test_assets = list(qa_report.get("held_out_test_assets", []))
    excluded_held_out = []
    for video in sorted(held_out):
        source = source_by_video.get(video, {})
        excluded_held_out.append(
            {
                "dataset_video_id": video,
                "asset_id": str(source.get("asset_id", "UNKNOWN")),
                "video_id": str(source.get("video_id", video)),
                "tombstone_id": str(source.get("tombstone_id", "NONE")),
                "present_in_candidate_dataset": False,
            }
        )

    leakage = bool(
        video_cross_split
        or operator_cross_split
        or complete_job_cross_split
        or held_out_test_assets
    )
    identity_blocked = bool(
        unknown_operator
        or unknown_complete_job
        or missing_source_identity
        or duplicate_source_videos
    )
    qa_passed = qa_report.get("passed") is True
    if not qa_passed:
        status = "BLOCKED_DATASET_QA"
    elif leakage:
        status = "FAIL_SPLIT_LEAKAGE"
    elif identity_blocked:
        status = "BLOCKED_UNKNOWN_IDENTITY"
    else:
        status = "DEVELOPMENT_CANDIDATE"

    source_identity_ready = bool(meccano_records) and not any(
        str(record.get("operator_id", "UNKNOWN")).upper() in UNKNOWN_VALUES
        or str(record.get("complete_job_id", "UNKNOWN")).upper() in UNKNOWN_VALUES
        for record in meccano_records
        if _canonical_video(record.get("video_id", "UNKNOWN")) not in held_out
    )
    formal_admission = (
        "ADMITTED" if status == "DEVELOPMENT_CANDIDATE" and source_identity_ready
        else "BLOCKED_UNTIL_G2"
    )
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "status": status,
        "dataset_profile": "DEVELOPMENT_CANDIDATE",
        "formal_admission": formal_admission,
        "formal_admission_scope": (
            "ELIGIBLE_FOR_FUTURE_FORMAL_PARTITION_NOT_ASSIGNED"
        ),
        "formal_use_currently_allowed": False,
        "source_identity_manifest": source_path.as_posix(),
        "source_identity_manifest_sha256": sha256_file(source_path),
        "source_identity_manifest_status": "READY" if source_identity_ready else "BLOCKED",
        "dataset_content_sha256": qa_report.get("content_sha256"),
        "file_manifest_sha256": qa_report.get("file_manifest_sha256"),
        "conversion_report_sha256": qa_report.get("conversion_report_sha256"),
        "records": sorted(
            candidate_records,
            key=lambda record: (
                str(record["dataset_split"]),
                str(record["dataset_video_id"]),
            ),
        ),
        "excluded_held_out_records": excluded_held_out,
        "unknown_operator_video_ids": unknown_operator,
        "unknown_complete_job_video_ids": unknown_complete_job,
        "missing_source_identity_video_ids": sorted(missing_source_identity),
        "duplicate_source_identity_video_ids": sorted(set(duplicate_source_videos)),
        "checks": {
            "dataset_qa": "PASS" if qa_passed else "FAIL",
            "held_out_test_absence": ("PASS" if not held_out_test_assets else "FAIL"),
            "video_isolation": "PASS" if not video_cross_split else "FAIL",
            "adjacent_frame_isolation": ("PASS" if not video_cross_split else "FAIL"),
            "known_operator_isolation": (
                "PASS" if not operator_cross_split else "FAIL"
            ),
            "known_complete_job_isolation": (
                "PASS" if not complete_job_cross_split else "FAIL"
            ),
            "operator_identity_completeness": (
                "BLOCKED_UNKNOWN_IDENTITY" if unknown_operator else "PASS"
            ),
            "complete_job_identity_completeness": (
                "BLOCKED_UNKNOWN_IDENTITY" if unknown_complete_job else "PASS"
            ),
            "identity_values_invented": False,
        },
        "cross_split": {
            "video_id": video_cross_split,
            "operator_id": operator_cross_split,
            "complete_job_id": complete_job_cross_split,
            "adjacent_frame_lineage": video_cross_split,
        },
    }
    result["candidate_payload_sha256"] = hashlib.sha256(
        _canonical_json(result).encode("utf-8")
    ).hexdigest()
    return result


def write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    _write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
