"""Deterministic class-filtered YOLO training views for MECCANO."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
VIEW_PROFILES: dict[str, dict[str, Any]] = {
    "hand": {"names": ("hand",), "mapping": {0: 0}},
    "fasteners": {
        "names": ("screw", "bolt", "screwdriver"),
        "mapping": {3: 0, 4: 1, 5: 2},
    },
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_source_qa(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    digest = payload.get("content_sha256")
    if (
        payload.get("status") != "PASS"
        or payload.get("passed") is not True
        or payload.get("dataset_profile") != "DEVELOPMENT_CANDIDATE"
        or payload.get("held_out_videos") != ["09"]
        or payload.get("held_out_test_assets") != []
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ValueError("source dataset QA is not an uncontaminated development candidate")
    return payload


def _remap_label(path: Path, mapping: dict[int, int]) -> tuple[bytes, Counter[int]]:
    output: list[str] = []
    counts: Counter[int] = Counter()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"{path}:{line_number}: expected five YOLO fields")
        try:
            class_value = float(fields[0])
            coordinates = [float(value) for value in fields[1:]]
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: non-numeric label") from exc
        if not class_value.is_integer() or int(class_value) not in range(6):
            raise ValueError(f"{path}:{line_number}: source class id is invalid")
        if any(value < 0 or value > 1 for value in coordinates):
            raise ValueError(f"{path}:{line_number}: box value outside 0..1")
        source_class = int(class_value)
        if source_class not in mapping:
            continue
        target_class = mapping[source_class]
        output.append(" ".join((str(target_class), *fields[1:])))
        counts[target_class] += 1
    encoded = (("\n".join(output) + "\n") if output else "").encode("utf-8")
    return encoded, counts


def _materialize_image(source: Path, target: Path, mode: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if mode == "hardlink" and target.samefile(source):
            return
        if target.is_file() and _sha256_file(target) == _sha256_file(source):
            return
        raise FileExistsError(f"refusing to overwrite image view target: {target}")
    if mode == "hardlink":
        os.link(source, target)
    elif mode == "symlink":
        target.symlink_to(source.resolve())
    elif mode == "copy":
        shutil.copy2(source, target)
    else:
        raise ValueError(f"unsupported link mode: {mode}")


def _write_exact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == payload:
            return
        raise FileExistsError(f"refusing to overwrite training-view file: {path}")
    path.write_bytes(payload)


def build_training_view(
    source_root: str | Path,
    output_root: str | Path,
    *,
    source_qa_path: str | Path | None = None,
    profile: str,
    link_mode: str = "hardlink",
) -> dict[str, Any]:
    """Build an immutable image-linked view with remapped labels and QA."""

    if profile not in VIEW_PROFILES:
        raise ValueError(f"unknown training view profile: {profile}")
    source = Path(source_root)
    output = Path(output_root)
    qa_path = Path(source_qa_path) if source_qa_path else source / "dataset-qa.json"
    source_qa = _load_source_qa(qa_path)
    specification = VIEW_PROFILES[profile]
    names = tuple(specification["names"])
    mapping = dict(specification["mapping"])
    rows: list[dict[str, Any]] = []
    transformed: dict[tuple[str, str], bytes] = {}
    boxes = {split: Counter() for split in SPLITS}
    images = {split: 0 for split in SPLITS}
    negatives = {split: 0 for split in SPLITS}
    held_out_assets: list[str] = []

    # Validate the complete source view before materializing any destination file.
    for split in SPLITS:
        image_root = source / "images" / split
        label_root = source / "labels" / split
        image_paths = sorted(
            path
            for path in image_root.rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
        )
        if not image_paths:
            raise ValueError(f"source view has no images in split: {split}")
        for image_path in image_paths:
            relative_image = image_path.relative_to(image_root)
            label_path = label_root / relative_image.with_suffix(".txt")
            if not label_path.is_file():
                raise ValueError(f"missing source label for {image_path}")
            if split == "test" and image_path.stem.startswith("09_"):
                held_out_assets.append(image_path.relative_to(source).as_posix())
            label_payload, label_counts = _remap_label(label_path, mapping)
            key = relative_image.with_suffix("").as_posix()
            transformed[(split, key)] = label_payload
            images[split] += 1
            if not label_counts:
                negatives[split] += 1
            for target_class, count in label_counts.items():
                boxes[split][names[target_class]] += count
            rows.append(
                {
                    "split": split,
                    "key": key,
                    "source_image": image_path.relative_to(source).as_posix(),
                    "target_label_sha256": _sha256_bytes(label_payload),
                    "target_box_count": sum(label_counts.values()),
                }
            )
        label_keys = {
            path.relative_to(label_root).with_suffix("").as_posix()
            for path in label_root.rglob("*.txt")
            if path.is_file()
        }
        image_keys = {
            path.relative_to(image_root).with_suffix("").as_posix()
            for path in image_paths
        }
        if label_keys != image_keys:
            raise ValueError(f"source image/label pairing mismatch in split: {split}")
    if held_out_assets:
        raise ValueError(f"held-out video 09 derivatives reached view: {held_out_assets[:3]}")

    for row in rows:
        split, key = str(row["split"]), str(row["key"])
        source_image = source / str(row["source_image"])
        target_image = output / "images" / split / source_image.name
        # Preserve any nested relative layout, not only the common flat dataset.
        source_relative = source_image.relative_to(source / "images" / split)
        target_image = output / "images" / split / source_relative
        target_label = output / "labels" / split / source_relative.with_suffix(".txt")
        _materialize_image(source_image, target_image, link_mode)
        _write_exact(target_label, transformed[(split, key)])

    manifest_text = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    manifest_sha256 = _sha256_bytes(manifest_text.encode("utf-8"))
    commitment_payload = {
        "source_dataset_content_sha256": source_qa["content_sha256"],
        "task_profile": profile,
        "names": names,
        "mapping": mapping,
        "view_manifest_sha256": manifest_sha256,
    }
    content_sha256 = _sha256_bytes(
        json.dumps(commitment_payload, sort_keys=True, separators=(",", ":")).encode()
    )
    report = {
        "schema_version": "display_project.yolo_training_view-1.0.0",
        "status": "PASS",
        "passed": True,
        "dataset_profile": "DEVELOPMENT_CANDIDATE",
        "formal_admission": "DEVELOPMENT_ONLY",
        "task_profile": profile,
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
        "negative_images": negatives,
        "videos": source_qa.get("videos", {}),
        "view_manifest_sha256": manifest_sha256,
        "content_sha256": content_sha256,
        "link_mode": link_mode,
    }
    yaml_lines = [
        f"path: {output.resolve().as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        "names:",
        *(f"  {index}: {name}" for index, name in enumerate(names)),
        "",
    ]
    _write_exact(output / "file-manifest.jsonl", manifest_text.encode("utf-8"))
    _write_exact(output / "dataset.yaml", "\n".join(yaml_lines).encode("utf-8"))
    _write_exact(
        output / "dataset-qa.json",
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return report
