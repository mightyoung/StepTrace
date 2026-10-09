"""Auditing CVAT Ultralytics YOLO Detection exports."""

from __future__ import annotations
import hashlib
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

YOLO_CLASSES = ("hand", "metal bar", "partial assembly", "screw", "bolt", "screwdriver")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass
class CVATAudit:
    images: int = 0
    labels: int = 0
    empty_labels: int = 0
    duplicate_hashes: int = 0
    invalid_labels: int = 0
    unknown_classes: int = 0
    tiny_boxes: int = 0
    decode_failures: int = 0
    missing_labels: int = 0
    source_manifest_present: bool = False
    errors: list[str] | None = None
    unpaired_labels: int = 0
    duplicate_boxes: int = 0
    dimension_mismatches: int = 0
    declared_classes: list[str] | None = None
    audit_status: str = "PASS"

    def to_dict(self):
        return asdict(self)


def _metadata_file(path: Path) -> bool:
    n = path.name.lower()
    return n in {"labels.txt", "classes.txt", "readme.txt"} or n.startswith("preview")


def _split_key(path: Path, root_name: str) -> tuple[str, str]:
    parts = [p.lower() for p in path.parts]
    try:
        idx = parts.index(root_name)
        split = path.parts[idx + 1] if idx + 2 < len(path.parts) else ""
    except ValueError:
        split = ""
    return split.lower(), path.stem


def _find_files(base: Path) -> tuple[list[Path], dict[tuple[str, str], Path]]:
    all_files = [x for x in base.rglob("*") if x.is_file()]
    images = [
        x
        for x in all_files
        if x.suffix.lower() in IMAGE_SUFFIXES and not _metadata_file(x)
    ]
    image_dirs = [
        x for x in base.rglob("*") if x.is_dir() and x.name.lower() == "images"
    ]
    if image_dirs:
        scoped = [x for x in images if any(d in x.parents for d in image_dirs)]
        if scoped:
            images = scoped
    label_dirs = [
        x
        for x in base.rglob("*")
        if x.is_dir() and x.name.lower() in {"labels", "annotations"}
    ]
    labels: dict[tuple[str, str], Path] = {}
    for x in all_files:
        if x.suffix.lower() != ".txt" or _metadata_file(x):
            continue
        if label_dirs and not any(d in x.parents for d in label_dirs):
            continue
        if image_dirs and not any(d in x.parents for d in label_dirs):
            continue
        labels[_split_key(x, "labels")] = x
    return sorted(images), labels


def _declared_names(base: Path) -> list[str] | None:
    flat = next((p for p in base.rglob("labels.txt") if p.is_file()), None)
    if flat is not None:
        return [
            line.strip()
            for line in flat.read_text(
                encoding="utf-8-sig", errors="replace"
            ).splitlines()
            if line.strip()
        ]

    yaml_path = next((p for p in base.rglob("data.yaml") if p.is_file()), None) or next(
        (p for p in base.rglob("data.yml") if p.is_file()), None
    )
    if yaml_path is None:
        return None
    try:
        payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError:
        return None
    names = payload.get("names") if isinstance(payload, dict) else None
    if isinstance(names, list):
        return [str(v) for v in names]
    if isinstance(names, dict):
        try:
            return [str(names[k]) for k in sorted(names, key=lambda x: int(x))]
        except (ValueError, TypeError):
            return None
    return None


def audit_cvat_export(
    path: str | Path,
    *,
    expected_count: int | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> dict[str, Any]:
    p = Path(path)
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cvat-audit-") as temp:
        base = p
        if p.suffix.lower() == ".zip":
            base = Path(temp) / "export"
            base.mkdir()
            with zipfile.ZipFile(p) as z:
                names = [i.filename for i in z.infolist()]
                if len(names) != len(set(names)):
                    raise ValueError("duplicate ZIP members")
                for name in names:
                    candidate = Path(name)
                    if candidate.is_absolute() or ".." in candidate.parts:
                        raise ValueError(f"unsafe ZIP member: {name}")
                z.extractall(base)
        if not base.is_dir():
            raise ValueError(f"unsupported CVAT export: {p}")
        images, labels = _find_files(base)
        dimensions: set[tuple[int, int]] = set()
        r = CVATAudit(
            images=len(images),
            labels=len(labels),
            errors=errors,
            declared_classes=_declared_names(base),
        )
        hashes = {}
        for image in images:
            try:
                from PIL import Image

                with Image.open(image) as im:
                    width, height = im.size
                    im.verify()
                dimensions.add((width, height))
                if (
                    expected_width is not None
                    and expected_height is not None
                    and (width, height) != (expected_width, expected_height)
                ):
                    r.dimension_mismatches += 1
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
                if digest in hashes:
                    r.duplicate_hashes += 1
                    errors.append(f"duplicate image hash: {image}")
                hashes[digest] = image
            except Exception as exc:
                r.decode_failures += 1
                errors.append(f"decode failure: {image}: {exc}")
                continue
            label = labels.get(_split_key(image, "images"))
            if label is None:
                r.missing_labels += 1
                continue
            try:
                lines = [
                    z.strip()
                    for z in label.read_text(encoding="utf-8-sig").splitlines()
                    if z.strip()
                ]
            except Exception as exc:
                r.invalid_labels += 1
                errors.append(f"label read failure: {label}: {exc}")
                continue
            if not lines:
                r.empty_labels += 1
            boxes_seen = set()
            for line in lines:
                vals = line.split()
                if len(vals) != 5:
                    r.invalid_labels += 1
                    continue
                try:
                    cls_float, *box = [float(v) for v in vals]
                    cls = int(cls_float)
                    if cls_float != cls:
                        raise ValueError("non-integral class")
                except ValueError:
                    r.invalid_labels += 1
                    continue
                if cls < 0 or cls >= len(YOLO_CLASSES):
                    r.unknown_classes += 1
                if (
                    any(v < 0 or v > 1 for v in box)
                    or box[2] <= 0
                    or box[3] <= 0
                    or box[0] - box[2] / 2 < 0
                    or box[0] + box[2] / 2 > 1
                    or box[1] - box[3] / 2 < 0
                    or box[1] + box[3] / 2 > 1
                ):
                    r.invalid_labels += 1
                if box[2] * width < 4 or box[3] * height < 4:
                    r.tiny_boxes += 1
                key = (cls, *[round(v, 6) for v in box])
                if key in boxes_seen:
                    r.duplicate_boxes += 1
                boxes_seen.add(key)
        r.unpaired_labels = len(set(labels) - {_split_key(x, "images") for x in images})
        r.source_manifest_present = any(
            x.name.lower() in {"manifest.csv", "manifest.json", "data.yaml", "data.yml"}
            for x in base.rglob("*")
        )
        if len(dimensions) > 1 and expected_width is None:
            r.dimension_mismatches = len(dimensions) - 1
        if expected_count is not None and r.images != expected_count:
            errors.append(f"image count {r.images} != expected {expected_count}")
        names_mismatch = (
            r.declared_classes is not None and tuple(r.declared_classes) != YOLO_CLASSES
        )
        if names_mismatch:
            errors.append("data.yaml names do not match canonical YOLO classes")
        if (
            r.images == 0
            or r.labels == 0
            or r.missing_labels
            or r.invalid_labels
            or r.unknown_classes
            or r.decode_failures
            or r.unpaired_labels
            or r.dimension_mismatches
            or names_mismatch
        ):
            r.audit_status = "BLOCKED" if r.labels == 0 or r.missing_labels else "FAIL"
        if r.missing_labels:
            errors.append(f"missing labels: {r.missing_labels}")
        if r.invalid_labels:
            errors.append(f"invalid labels: {r.invalid_labels}")
        if r.unknown_classes:
            errors.append(f"unknown classes: {r.unknown_classes}")
        result = r.to_dict()
        result["status"] = r.audit_status
        return result
