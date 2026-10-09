from __future__ import annotations

import hashlib
import json
from pathlib import Path

from display_project.data.curated_yolo_training_views import (
    build_curated_fastener_view,
)


def _write_source(root: Path) -> Path:
    rows = {
        "train": [
            ("01_00001", "0 0.2 0.2 0.1 0.1\n"),
            ("01_00002", "0 0.2 0.2 0.1 0.1\n"),
            ("01_00003", "1 0.3 0.3 0.1 0.1\n"),
            ("01_00006", "2 0.4 0.4 0.1 0.1\n"),
            ("01_00007", "3 0.5 0.5 0.2 0.2\n"),
        ],
        "val": [
            ("07_00001", "0 0.2 0.2 0.1 0.1\n"),
            ("07_00002", "3 0.5 0.5 0.2 0.2\n"),
        ],
        "test": [
            ("10_00001", "1 0.3 0.3 0.1 0.1\n"),
            ("10_00002", "2 0.4 0.4 0.1 0.1\n"),
        ],
    }
    for split, samples in rows.items():
        image_root, label_root = root / "images" / split, root / "labels" / split
        image_root.mkdir(parents=True)
        label_root.mkdir(parents=True)
        for stem, label in samples:
            (image_root / f"{stem}.jpg").write_bytes(stem.encode())
            (label_root / f"{stem}.txt").write_text(label, encoding="utf-8")
    manifest_rows = []
    for split, samples in rows.items():
        for stem, label in samples:
            target_count = int(label.split()[0]) in {0, 1, 2}
            manifest_rows.append(
                {
                    "key": stem,
                    "source_image": f"images/{split}/{stem}.jpg",
                    "split": split,
                    "target_box_count": int(target_count),
                    "target_label_sha256": hashlib.sha256(
                        label.encode("utf-8") if target_count else b""
                    ).hexdigest(),
                }
            )
    manifest = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in manifest_rows
    )
    (root / "file-manifest.jsonl").write_bytes(manifest.encode("utf-8"))
    qa = root / "dataset-qa.json"
    qa.write_text(
        json.dumps(
            {
                "status": "PASS",
                "passed": True,
                "dataset_profile": "DEVELOPMENT_CANDIDATE",
                "task_profile": "fasteners",
                "held_out_videos": ["09"],
                "held_out_test_assets": [],
                "content_sha256": hashlib.sha256(b"source").hexdigest(),
                "view_manifest_sha256": hashlib.sha256(
                    manifest.encode("utf-8")
                ).hexdigest(),
                "videos": {"train": ["01"], "val": ["07"], "test": ["10"]},
            }
        ),
        encoding="utf-8",
    )
    return qa


def test_curated_multiclass_excludes_unverified_negatives_and_thins_train(
    tmp_path: Path,
):
    source, output = tmp_path / "source", tmp_path / "curated"
    report = build_curated_fastener_view(
        source,
        output,
        source_qa_path=_write_source(source),
        mode="multiclass",
        link_mode="copy",
        train_min_frame_gap=3,
    )
    assert report["images"] == {"train": 3, "val": 1, "test": 2}
    assert report["negative_images"] == {"train": 0, "val": 0, "test": 0}
    assert report["excluded_images"]["train"] == {
        "unverified_negative": 1,
        "duplicate_target_label": 0,
        "temporal_redundancy": 1,
    }
    assert report["label_semantics"] == "ACTIVE_FASTENER_ONLY"
    assert report["exhaustive_visible_instance_labels"] is False
    assert not (output / "images/train/01_00002.jpg").exists()
    assert not (output / "images/train/01_00007.jpg").exists()
    assert (output / "labels/train/01_00003.txt").read_text() == (
        "1 0.3 0.3 0.1 0.1\n"
    )


def test_curated_binary_merges_all_fastener_classes(tmp_path: Path):
    source, output = tmp_path / "source", tmp_path / "binary"
    report = build_curated_fastener_view(
        source,
        output,
        source_qa_path=_write_source(source),
        mode="binary",
        link_mode="hardlink",
        train_min_frame_gap=1,
    )
    assert report["names"] == ["fastener"]
    assert report["boxes"]["train"] == {"fastener": 4}
    assert (output / "labels/train/01_00003.txt").read_text() == (
        "0 0.3 0.3 0.1 0.1\n"
    )
    assert report["manual_annotation_count"] == 0
    assert report["formal_admission"] == "DEVELOPMENT_ONLY"
