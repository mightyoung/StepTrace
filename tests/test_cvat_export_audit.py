import os
from pathlib import Path
import zipfile

from PIL import Image

from display_project.data.cvat import YOLO_CLASSES, audit_cvat_export


def _img(path: Path, color=(10, 20, 30), size=(100, 80)):
    Image.new("RGB", size, color).save(path)


def test_cvat_audit_accepts_six_classes_and_paired_labels(tmp_path):
    for i, _name in enumerate(YOLO_CLASSES):
        _img(tmp_path / f"f{i}.jpg")
        (tmp_path / f"f{i}.txt").write_text(f"{i} .5 .5 .2 .2\n", encoding="utf-8")
    (tmp_path / "manifest.csv").write_text(
        "frame,source\n0,video.mp4\n", encoding="utf-8"
    )
    report = audit_cvat_export(tmp_path)
    assert report["images"] == report["labels"] == 6
    assert report["invalid_labels"] == report["unknown_classes"] == 0
    assert report["missing_labels"] == 0 and report["source_manifest_present"]


def test_cvat_audit_reports_decode_size_hash_and_pairing_failures(tmp_path):
    _img(tmp_path / "ok.jpg", size=(100, 80))
    (tmp_path / "ok.txt").write_text("0 .5 .5 .2 .2\n", encoding="utf-8")
    (tmp_path / "dupe.jpg").write_bytes((tmp_path / "ok.jpg").read_bytes())
    (tmp_path / "bad.jpg").write_bytes(b"not-an-image")
    (tmp_path / "orphan.txt").write_text("0 .5 .5 .2 .2\n", encoding="utf-8")
    report = audit_cvat_export(tmp_path)
    assert report["duplicate_hashes"] == 1
    assert report["decode_failures"] == 1
    assert report["missing_labels"] == 1
    assert report["unpaired_labels"] >= 1


def test_cvat_audit_rejects_out_of_bounds_duplicate_and_tiny_boxes(tmp_path):
    _img(tmp_path / "f.jpg")
    (tmp_path / "f.txt").write_text(
        "0 .5 .5 .2 .2\n0 .5 .5 .2 .2\n1 1.1 .5 .2 .2\n2 .5 .5 .01 .01\n",
        encoding="utf-8",
    )
    report = audit_cvat_export(tmp_path)
    assert report["duplicate_boxes"] == 1
    assert report["invalid_labels"] >= 1
    assert report["tiny_boxes"] == 1


def test_cvat_audit_ignores_preview_readme_and_flat_labels_metadata(tmp_path):
    _img(tmp_path / "preview-20.jpg")
    _img(tmp_path / "frame.jpg")
    (tmp_path / "labels.txt").write_text("hand\n", encoding="utf-8")
    (tmp_path / "README.txt").write_text("metadata", encoding="utf-8")
    (tmp_path / "orphan.txt").write_text("0 .5 .5 .2 .2\n", encoding="utf-8")
    report = audit_cvat_export(
        tmp_path, expected_count=1, expected_width=100, expected_height=80
    )
    assert report["images"] == 1
    assert report["labels"] == 1
    assert report["unpaired_labels"] == 1
    assert report["status"] == "BLOCKED"


def test_cvat_audit_supports_ultralytics_zip_and_dimension_mismatch(tmp_path):
    root = tmp_path / "export"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    _img(root / "images" / "a.jpg", size=(100, 80))
    _img(root / "images" / "b.jpg", size=(120, 80))
    (root / "labels" / "a.txt").write_text("0 .5 .5 .2 .2\n", encoding="utf-8")
    (root / "labels" / "b.txt").write_text("", encoding="utf-8")
    (root / "data.yaml").write_text(
        "names: [hand, metal bar, partial assembly, screw, bolt, screwdriver]\n",
        encoding="utf-8",
    )
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for f in root.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(root))
    report = audit_cvat_export(
        archive, expected_count=2, expected_width=100, expected_height=80
    )
    assert report["images"] == report["labels"] == 2
    assert report["empty_labels"] == 1
    assert report["dimension_mismatches"] == 1
    assert report["status"] == "FAIL"


def test_cvat_cli_returns_nonzero_for_blocked_and_writes_lf_canonical(tmp_path):
    _img(tmp_path / "frame.jpg")
    out = tmp_path / "audit.json"
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "scripts/export_audit_cvat.py",
            str(tmp_path),
            "--output",
            str(out),
        ],
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    raw = out.read_bytes()
    assert b"\xef\xbb\xbf" not in raw and b"\r" not in raw
    import json

    payload = json.loads(raw)
    assert payload["status"] == "BLOCKED" and len(payload["canonical_sha256"]) == 64


def test_ultralytics_train_txt_is_not_a_label_and_yaml_map_parses(tmp_path):
    root = tmp_path / "export"
    (root / "images" / "Train").mkdir(parents=True)
    _img(root / "images" / "Train" / "f.jpg")
    (root / "Train.txt").write_text("f.jpg\n", encoding="utf-8")
    (root / "data.yaml").write_text(
        "names:\n  0: hand\n  1: metal bar\n  2: partial assembly\n  3: screw\n  4: bolt\n  5: screwdriver\n",
        encoding="utf-8",
    )
    report = audit_cvat_export(root)
    assert report["labels"] == 0
    assert report["unpaired_labels"] == 0
    assert report["missing_labels"] == 1
    assert report["declared_classes"] == list(YOLO_CLASSES)
    assert report["status"] == "BLOCKED"


def test_cvat_audit_rejects_zip_slip_and_duplicate_members(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../escape.txt", "bad")
    import pytest

    with pytest.raises(ValueError):
        audit_cvat_export(archive)
