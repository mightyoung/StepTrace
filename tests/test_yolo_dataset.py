from display_project.data.yolo_dataset import (
    MECCANO_YOLO_NAMES,
    coco_category_to_yolo,
    to_yolo_line,
    validate_video_splits,
)


def test_official_categories_collapse_into_demo_classes():
    assert MECCANO_YOLO_NAMES == (
        "hand",
        "metal bar",
        "partial assembly",
        "screw",
        "bolt",
        "screwdriver",
    )
    assert coco_category_to_yolo("gray_angled_perforated_bar") == "metal bar"
    assert coco_category_to_yolo("red_perforated_junction_bar") == "metal bar"
    assert coco_category_to_yolo("partial_model") == "partial assembly"
    assert coco_category_to_yolo("washer") is None


def test_coco_box_is_written_as_normalized_yolo_center_box():
    assert to_yolo_line(4, [10, 20, 20, 10], image_width=100, image_height=50) == (
        "4 0.200000 0.500000 0.200000 0.200000"
    )


def test_demo_video_cannot_leak_into_training_or_validation():
    validate_video_splits(
        {"train": {"01", "02"}, "val": {"07"}, "test": {"09", "10"}},
        held_out_videos={"09"},
    )

    try:
        validate_video_splits(
            {"train": {"01", "09"}, "val": {"07"}, "test": {"10"}},
            held_out_videos={"09"},
        )
    except ValueError as exc:
        assert "held-out video 09" in str(exc)
    else:
        raise AssertionError("expected held-out video leakage to be rejected")


def test_a_video_cannot_appear_in_multiple_splits():
    try:
        validate_video_splits(
            {"train": {"01"}, "val": {"01"}, "test": {"09"}},
            held_out_videos={"09"},
        )
    except ValueError as exc:
        assert "video 01 appears in both train and val" in str(exc)
    else:
        raise AssertionError("expected cross-split leakage to be rejected")


def test_prepare_meccano_excludes_heldout_video_before_output(monkeypatch, tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    import sys

    module_path = (
        Path(__file__).parents[1] / "scripts" / "prepare_meccano_yolo_dataset.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prepare_meccano_yolo_dataset_e2e", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "OFFICIAL_SPLITS",
        {"train": {"01"}, "val": {"07"}, "test": {"09", "10"}},
    )
    source = tmp_path / "videos"
    source.mkdir()
    (source / "0009.mp4").touch()
    (source / "0010.mp4").touch()
    (source / "0001.mp4").touch()
    (source / "0007.mp4").touch()
    monkeypatch.setattr(
        module,
        "_video_index",
        lambda root: {
            "01": source / "0001.mp4",
            "07": source / "0007.mp4",
            "09": source / "0009.mp4",
            "10": source / "0010.mp4",
        },
    )

    def fake_coco(path):
        if path.name.endswith("train.json"):
            videos = ["01"]
        elif path.name.endswith("val.json"):
            videos = ["07"]
        else:
            videos = ["09", "10"]
        return [
            {
                "video_id": video,
                "frame": 1,
                "image_width": 100,
                "image_height": 50,
                "category_name": "screw",
                "bbox": [10, 10, 20, 10],
            }
            for video in videos
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
    for folder, videos in {"Train": ["01"], "Val": ["07"], "Test": ["10"]}.items():
        for video in videos:
            hand = hands_root / folder / f"{int(video):04d}.csv"
            hand.parent.mkdir(parents=True, exist_ok=True)
            hand.write_text(
                "filename,region_shape_attributes,region_attributes\n",
                encoding="utf-8",
            )
    monkeypatch.setattr(module, "load_hand_annotations", lambda path: [])
    monkeypatch.setattr(
        module,
        "_extract_frames_batch",
        lambda video, requests: [target.write_bytes(b"jpg") for _, target in requests],
    )
    output = tmp_path / "out"
    stale_image = output / "images" / "test" / "09_00001.jpg"
    stale_label = output / "labels" / "test" / "09_00001.txt"
    stale_image.parent.mkdir(parents=True)
    stale_label.parent.mkdir(parents=True)
    stale_image.write_bytes(b"stale")
    stale_label.write_text("stale", encoding="utf-8")
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
    report = json.loads((output / "dataset-audit.json").read_text(encoding="utf-8"))
    assert "09" not in report["splits"]["test"]
    assert report["splits"]["test"] == ["10"]
    assert report["dataset_profile"] == "DEVELOPMENT_CANDIDATE"
    assert report["formal_admission"] == "BLOCKED_UNTIL_G2"
    assert report["annotation_source_provenance"]["status"] == "PASS"
    assert report["annotation_source_provenance"]["source_count"] == 6
    assert all(
        "09" not in source["used_videos"]
        for source in report["annotation_source_provenance"]["sources"]
    )
    assert not stale_image.exists() and not stale_label.exists()
    assert list((output / "images" / "test").glob("10_*.jpg"))
    assert not list((output / "images" / "test").glob("09_*.jpg"))


def test_batch_extractor_reads_requested_frames_once(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    import sys
    import types

    module_path = (
        Path(__file__).parents[1] / "scripts" / "prepare_meccano_yolo_dataset.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prepare_meccano_yolo_dataset_batch", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class Capture:
        def __init__(self, path):
            self.index = 0
            self.reads = 0
            self.grabs = 0

        def set(self, prop, value):
            self.index = int(value)

        def grab(self):
            self.grabs += 1
            self.index += 1
            return True

        def read(self):
            self.reads += 1
            frame = self.index
            self.index += 1
            return True, frame

        def release(self):
            pass

    captures = []
    fake_cv2 = types.SimpleNamespace(
        CAP_PROP_POS_FRAMES=1,
        VideoCapture=lambda path: captures.append(Capture(path)) or captures[-1],
        imwrite=lambda path, frame: Path(path).write_text(str(frame)) or True,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    targets = [tmp_path / "f2.jpg", tmp_path / "f4.jpg"]
    module._extract_frames_batch(
        tmp_path / "video.mp4", [(4, targets[1]), (2, targets[0])]
    )
    assert [target.read_text() for target in targets] == ["1", "3"]
    assert len(captures) == 1 and captures[0].reads == 2 and captures[0].grabs == 1
