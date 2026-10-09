from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def test_prepare_meccano_writes_lf_and_physically_excludes_heldout(
    monkeypatch, tmp_path
):
    module_path = (
        Path(__file__).parents[1] / "scripts" / "prepare_meccano_yolo_dataset.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prepare_meccano_yolo_dataset_lf", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    source = tmp_path / "videos"
    source.mkdir()
    videos = {video: source / f"{video}.mp4" for video in ("01", "09", "10")}
    for path in videos.values():
        path.touch()
    monkeypatch.setattr(
        module,
        "OFFICIAL_SPLITS",
        {"train": {"01"}, "val": set(), "test": {"09", "10"}},
    )
    monkeypatch.setattr(module, "_video_index", lambda _root: videos)
    monkeypatch.setattr(module, "_hand_index", lambda _path: {})

    def fake_coco(path):
        split_videos = (
            ["01"]
            if path.name.endswith("train.json")
            else []
            if path.name.endswith("val.json")
            else ["09", "10"]
        )
        return [
            {
                "video_id": video,
                "frame": 1,
                "image_width": 100,
                "image_height": 50,
                "category_name": "screw",
                "bbox": [10, 10, 20, 10],
            }
            for video in split_videos
        ]

    monkeypatch.setattr(module, "load_coco_annotations", fake_coco)
    coco_root = tmp_path / "MECCANO_active_objects_annotations"
    coco_root.mkdir()
    for split in ("train", "val", "test"):
        (coco_root / f"instances_meccano_{split}.json").write_text(
            json.dumps({"images": [], "annotations": [], "categories": []}),
            encoding="utf-8",
        )
    hands_root = tmp_path / "MECCANO_hands_bounding_box_annotations"
    for folder, video_ids in {
        "Train": ["01"],
        "Val": [],
        "Test": ["10"],
    }.items():
        for video_id in video_ids:
            hand = hands_root / folder / f"{int(video_id):04d}.csv"
            hand.parent.mkdir(parents=True, exist_ok=True)
            hand.write_text(
                "filename,region_shape_attributes,region_attributes\n",
                encoding="utf-8",
            )
    monkeypatch.setattr(module, "load_hand_annotations", lambda _path: [])
    monkeypatch.setattr(
        module,
        "_extract_frames_batch",
        lambda _video, requests: [target.write_bytes(b"jpg") for _, target in requests],
    )
    output = tmp_path / "candidate"
    # Pre-existing aliases must all be removed before the post-build scan.
    for kind in ("images", "labels"):
        stale = output / kind / "test"
        stale.mkdir(parents=True, exist_ok=True)
        for stem in ("09_00000", "0009_00000", "9_00000"):
            (stale / (stem + (".jpg" if kind == "images" else ".txt"))).write_bytes(b"stale")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare",
            "--dataset-root",
            str(tmp_path),
            "--video-root",
            str(source),
            "--output",
            str(output),
        ],
    )

    assert module.main() == 0

    label_bytes = (output / "labels/test/10_00001.txt").read_bytes()
    report_bytes = (output / "dataset-audit.json").read_bytes()
    assert label_bytes.endswith(b"\n") and b"\r\n" not in label_bytes
    assert report_bytes.endswith(b"\n") and b"\r\n" not in report_bytes
    assert not list((output / "images/test").glob("09_*.jpg"))
    assert not list((output / "images/test").glob("0009_*.jpg"))
    assert not list((output / "images/test").glob("9_*.jpg"))
    assert not list((output / "labels/test").glob("09_*.txt"))
    assert not list((output / "labels/test").glob("0009_*.txt"))
    assert not list((output / "labels/test").glob("9_*.txt"))
