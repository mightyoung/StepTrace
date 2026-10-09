from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from display_project.data.yolo_training_views import build_training_view


def _source_candidate(root: Path) -> Path:
    for split in ("train", "val", "test"):
        video = "01" if split == "train" else "07" if split == "val" else "10"
        image_root = root / "images" / split
        label_root = root / "labels" / split
        image_root.mkdir(parents=True)
        label_root.mkdir(parents=True)
        (image_root / f"{video}_00001.jpg").write_bytes(f"{split}-image".encode())
        (label_root / f"{video}_00001.txt").write_text(
            "\n".join((
                "0 0.5 0.5 0.4 0.4",
                "1 0.5 0.5 0.2 0.2",
                "3 0.2 0.2 0.1 0.1",
                "4 0.3 0.3 0.1 0.1",
                "5 0.4 0.4 0.1 0.1",
            )) + "\n",
            encoding="utf-8",
        )
    qa = root / "dataset-qa.json"
    qa.write_text(json.dumps({
        "status": "PASS",
        "passed": True,
        "dataset_profile": "DEVELOPMENT_CANDIDATE",
        "held_out_videos": ["09"],
        "held_out_test_assets": [],
        "content_sha256": hashlib.sha256(b"source").hexdigest(),
        "videos": {"train": ["01"], "val": ["07"], "test": ["10"]},
    }), encoding="utf-8")
    return qa


def test_hand_view_preserves_images_and_keeps_filtered_frames_as_negatives(tmp_path: Path):
    source = tmp_path / "source"
    qa = _source_candidate(source)
    output = tmp_path / "hand"
    report = build_training_view(
        source, output, source_qa_path=qa, profile="hand", link_mode="hardlink"
    )

    assert report["status"] == "PASS"
    assert report["task_profile"] == "hand"
    assert report["names"] == ["hand"]
    assert report["boxes"] == {
        "train": {"hand": 1}, "val": {"hand": 1}, "test": {"hand": 1}
    }
    assert (output / "labels/train/01_00001.txt").read_text() == "0 0.5 0.5 0.4 0.4\n"
    assert (output / "images/train/01_00001.jpg").samefile(
        source / "images/train/01_00001.jpg"
    )

    (source / "images/train/01_00002.jpg").write_bytes(b"negative")
    (source / "labels/train/01_00002.txt").write_text(
        "3 0.2 0.2 0.1 0.1\n", encoding="utf-8"
    )
    output2 = tmp_path / "hand-with-negative"
    report2 = build_training_view(
        source, output2, source_qa_path=qa, profile="hand", link_mode="hardlink"
    )
    assert (output2 / "images/train/01_00002.jpg").is_file()
    assert (output2 / "labels/train/01_00002.txt").read_text() == ""
    assert report2["negative_images"]["train"] == 1


def test_fastener_view_remaps_only_screw_bolt_and_screwdriver(tmp_path: Path):
    source = tmp_path / "source"
    qa = _source_candidate(source)
    output = tmp_path / "fasteners"
    report = build_training_view(
        source, output, source_qa_path=qa, profile="fasteners", link_mode="copy"
    )

    assert report["names"] == ["screw", "bolt", "screwdriver"]
    assert (output / "labels/train/01_00001.txt").read_text() == (
        "0 0.2 0.2 0.1 0.1\n"
        "1 0.3 0.3 0.1 0.1\n"
        "2 0.4 0.4 0.1 0.1\n"
    )
    assert report["boxes"]["train"] == {
        "screw": 1, "bolt": 1, "screwdriver": 1
    }
    assert report["held_out_test_assets"] == []
    assert len(report["content_sha256"]) == 64
    assert (output / "dataset.yaml").is_file()


def test_training_view_rejects_contaminated_source_qa(tmp_path: Path):
    source = tmp_path / "source"
    qa = _source_candidate(source)
    payload = json.loads(qa.read_text(encoding="utf-8"))
    payload["held_out_test_assets"] = ["images/test/09_00001.jpg"]
    qa.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="uncontaminated"):
        build_training_view(source, tmp_path / "view", source_qa_path=qa, profile="hand")
