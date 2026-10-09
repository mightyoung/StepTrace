from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

import display_project.data.industreal_videos as videos


def _report() -> dict[str, object]:
    payload: dict[str, object] = {"status": "PASS", "message": "中文"}
    payload["canonical_sha256"] = videos.canonical_sha256(payload)
    return payload


def _make_video(path: Path) -> bytes:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (32, 24))
    assert writer.isOpened()
    for value in range(3):
        writer.write(np.full((24, 32, 3), value * 60, dtype=np.uint8))
    writer.release()
    return path.read_bytes()


def _make_labels(root: Path, *, recording_ids: tuple[str, ...]) -> Path:
    root.mkdir()
    rows = "".join(f"{rid},0,action,000001.jpg,000002.jpg\n" for rid in recording_ids)
    (root / "train.csv").write_text(rows, encoding="utf-8", newline="\n")
    (root / "val.csv").write_text("", encoding="utf-8", newline="\n")
    (root / "test.csv").write_text("", encoding="utf-8", newline="\n")
    return root


def _make_archive(path: Path, video: bytes, recording_ids: tuple[str, ...]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for recording_id in recording_ids:
            archive.writestr(f"{recording_id}.mp4", video)
    return path


def test_write_inventory_rejects_bad_canonical_hash(tmp_path):
    payload = _report()
    payload["canonical_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="canonical_sha256 does not verify"):
        videos.write_inventory(tmp_path / "inventory.json", payload)


def test_write_inventory_is_utf8_lf_and_parseable_json(tmp_path):
    target = tmp_path / "inventory.json"

    videos.write_inventory(target, _report())

    raw = target.read_bytes()
    assert raw.endswith(b"\n")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["message"] == "中文"
    assert parsed["canonical_sha256"] == videos.canonical_sha256(parsed)


@pytest.mark.parametrize("existing", [False, True])
def test_write_inventory_replace_failure_preserves_target_and_cleans_temp(
    tmp_path, monkeypatch, existing
):
    target = tmp_path / "inventory.json"
    original = b"existing bytes\x00\xff\n" if existing else None
    if original is not None:
        target.write_bytes(original)

    def fail_replace(source, destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(videos.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        videos.write_inventory(target, _report())

    if original is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == original
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


def test_extraction_failure_is_blocked_and_cleans_temp_directory(tmp_path, monkeypatch):
    recording_ids = ("01_assy_0_1", "02_main_1_1")
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video, recording_ids)
    labels = _make_labels(tmp_path / "labels", recording_ids=recording_ids)
    extracted = tmp_path / "extract"

    def fail_extractall(self, path, *args, **kwargs):
        raise RuntimeError("injected extraction failure")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", fail_extractall)
    report = videos.audit_industreal_videos(
        archive,
        labels,
        expected_size=archive.stat().st_size,
        expected_md5=hashlib.md5(archive.read_bytes()).hexdigest(),
        expected_count=2,
        expected_recording_ids=recording_ids,
        extracted_dir=extracted,
    )

    assert report["status"] == "BLOCKED"
    assert any("safe extraction failed" in error for error in report["errors"])
    assert not extracted.exists()
    assert not list(tmp_path.glob(f".{extracted.name}.*"))


def test_official_84_86_warning_marks_two_archive_only_splits_unknown(
    tmp_path, monkeypatch
):
    archive_only = ("15_main_0_1", "21_assy_1_1")
    monkeypatch.setattr(videos, "OFFICIAL_COUNT", 2)
    monkeypatch.setattr(videos, "OFFICIAL_ARCHIVE_ONLY", set(archive_only))
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video, archive_only)
    labels = _make_labels(tmp_path / "labels", recording_ids=())

    report = videos.audit_industreal_videos(
        archive,
        labels,
        expected_size=archive.stat().st_size,
        expected_md5=hashlib.md5(archive.read_bytes()).hexdigest(),
        expected_count=2,
        expected_recording_ids=archive_only,
        extracted_dir=tmp_path / "extract",
    )

    assert report["status"] == "PASS"
    assert any(
        "84 recordings" in warning and "86" in warning for warning in report["warnings"]
    )
    assert report["archive_only_recording_ids"] == list(archive_only)
    assert {record["official_split"] for record in report["records"]} == {
        "UNKNOWN_OFFICIAL_UNLABELED"
    }
