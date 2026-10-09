from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from display_project.data.industreal_videos import (
    action_recordings,
    audit_industreal_videos,
    canonical_sha256,
    official_recording_ids_sha256,
)

RECORDING_IDS = {"01_assy_0_1", "02_main_1_1"}


def _make_video(path: Path) -> bytes:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore[attr-defined]
        5.0,
        (64, 48),
    )
    assert writer.isOpened()
    for index in range(5):
        frame = np.full((48, 64, 3), index * 40, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert path.is_file() and path.stat().st_size > 0
    return path.read_bytes()


def _make_labels(root: Path, *, malformed: bool = False) -> Path:
    root.mkdir()
    train = (
        "bad-id,0,action,000001.jpg,000002.jpg\n"
        if malformed
        else ("01_assy_0_1,0,action,000001.jpg,000002.jpg\n")
    )
    (root / "train.csv").write_text(train, encoding="utf-8", newline="\n")
    (root / "val.csv").write_text("", encoding="utf-8", newline="\n")
    (root / "test.csv").write_text(
        "02_main_1_1,0,action,000001.jpg,000002.jpg\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def _make_archive(path: Path, video: bytes) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for recording_id in sorted(RECORDING_IDS):
            archive.writestr(f"{recording_id}.mp4", video)
    return path


def _audit(archive: Path, labels: Path, extracted: Path | None):
    return audit_industreal_videos(
        archive,
        labels,
        expected_size=archive.stat().st_size,
        expected_md5=hashlib.md5(archive.read_bytes()).hexdigest(),
        expected_count=2,
        expected_recording_ids=RECORDING_IDS,
        extracted_dir=extracted,
    )


def test_action_recordings_derives_only_official_splits(tmp_path):
    labels = _make_labels(tmp_path / "labels")
    assert action_recordings(
        labels, expected_count=2, expected_recording_ids=RECORDING_IDS
    ) == {"01_assy_0_1": "train", "02_main_1_1": "test"}


def test_action_recordings_emits_split_disjoint_recording_prefix_proxy(tmp_path):
    labels = _make_labels(tmp_path / "labels")
    report = _audit(
        _make_archive(tmp_path / "videos.zip", _make_video(tmp_path / "source.mp4")),
        labels,
        None,
    )
    assert report["participant_group_proxy"] == {
        "kind": "recording_prefix",
        "source": "official_action_label_recording_id",
        "group_count": 2,
        "recording_prefixes": {"01": "train", "02": "test"},
        "operator_id": "UNKNOWN",
    }
    assert {r["recording_prefix"] for r in report["records"]} == {"01", "02"}
    assert all(
        record["participant_group_proxy"] == record["recording_prefix"]
        for record in report["records"]
    )


def test_cross_split_recording_prefix_blocks(tmp_path):
    labels = _make_labels(tmp_path / "labels")
    (labels / "val.csv").write_text(
        "01_main_0_2,0,action\n", encoding="utf-8"
    )
    report = _audit(
        _make_archive(tmp_path / "videos.zip", _make_video(tmp_path / "source.mp4")),
        labels,
        None,
    )
    assert report["status"] == "BLOCKED"
    assert any(
        "recording prefix appears in multiple action splits" in error
        for error in report["errors"]
    )


def test_complete_archive_extract_and_video_audit_passes(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    labels = _make_labels(tmp_path / "labels")
    extracted = tmp_path / "dedicated-videos"

    report = _audit(archive, labels, extracted)

    assert report["status"] == "PASS"
    assert report["errors"] == []
    assert report["canonical_sha256"] == canonical_sha256(report)
    assert report["verified_on"] == __import__("datetime").date.today().isoformat()
    assert report["extracted_root"] == str(extracted.resolve())
    assert report["official_recording_ids"] == sorted(RECORDING_IDS)
    assert report["official_recording_ids_sha256"] == official_recording_ids_sha256(
        sorted(RECORDING_IDS)
    )
    assert len(report["records"]) == 2
    for record in report["records"]:
        assert record["operator_id"] == "UNKNOWN"
        assert record["complete_job_id"] == (
            f"INDUSTREAL-RECORDING-{record['source_recording_id']}"
        )
        assert record["project_analysis_unit"] == "source_recording_id"
        assert record["unit_status"] == "AUTHORITATIVE_COMPLETE_RECORDING_UNIT"
        assert record["project_split"] == "UNASSIGNED_BLOCKED_IDENTITY"
        assert record["formal_eligible"] is False
        info = record["video"]
        assert len(info["sha256"]) == 64 and info["bytes"] > 0
        assert info["codec"] != "UNKNOWN" and info["fps"] > 0
        assert info["resolution"] == {"width": 64, "height": 48}
        assert info["frame_count"] == 5 and info["duration_seconds"] > 0
        assert info["decode"] == {"first": True, "mid": True, "last": True}

    mtimes = {path.name: path.stat().st_mtime_ns for path in extracted.glob("*.mp4")}
    reused = _audit(archive, labels, extracted)
    assert reused["status"] == "PASS"
    assert mtimes == {
        path.name: path.stat().st_mtime_ns for path in extracted.glob("*.mp4")
    }


def test_archive_only_can_never_pass(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    labels = _make_labels(tmp_path / "labels")
    report = _audit(archive, labels, None)
    assert report["status"] == "BLOCKED"
    assert "extracted_dir is required for complete video audit" in report["errors"]
    assert all(record["video"] is None for record in report["records"])


def test_existing_extraction_is_not_overwritten_and_extra_mp4_blocks(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    labels = _make_labels(tmp_path / "labels")
    extracted = tmp_path / "videos"
    assert _audit(archive, labels, extracted)["status"] == "PASS"
    extra = extracted / "03_assy_0_1.mp4"
    extra.write_bytes(video)
    report = _audit(archive, labels, extracted)
    assert report["status"] == "BLOCKED"
    assert any("unexpected extracted MP4" in error for error in report["errors"])
    assert extra.read_bytes() == video


@pytest.mark.parametrize(
    ("member", "message"),
    [
        ("../evil.mp4", "unsafe archive member"),
        ("/evil.mp4", "unsafe archive member"),
        ("C:/evil.mp4", "unsafe archive member"),
        ("nested/01_assy_0_1.mp4", "not at root"),
        ("notes.txt", "unexpected regular archive member"),
    ],
)
def test_hostile_or_extra_regular_members_block(tmp_path, member, message):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    with zipfile.ZipFile(archive, "a") as source:
        source.writestr(member, b"payload")
    labels = _make_labels(tmp_path / "labels")
    report = _audit(archive, labels, tmp_path / "extract")
    assert report["status"] == "BLOCKED"
    assert any(message in error for error in report["errors"])
    assert not (tmp_path / "extract").exists()


def test_duplicate_raw_and_normalized_members_block(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = tmp_path / "videos.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr("01_assy_0_1.mp4", video)
            source.writestr("01_assy_0_1.mp4", video)
    labels = _make_labels(tmp_path / "labels")
    report = _audit(archive, labels, tmp_path / "extract")
    assert report["status"] == "BLOCKED"
    assert any("duplicate raw" in error for error in report["errors"])
    assert any("duplicate normalized" in error for error in report["errors"])


def test_symlink_and_zero_size_members_block(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    with zipfile.ZipFile(archive, "a") as source:
        source.writestr("03_assy_0_1.mp4", b"")
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        source.writestr(link, "01_assy_0_1.mp4")
    labels = _make_labels(tmp_path / "labels")
    report = _audit(archive, labels, tmp_path / "extract")
    assert report["status"] == "BLOCKED"
    assert any("zero-size" in error for error in report["errors"])
    assert any("symlink" in error for error in report["errors"])


def _set_first_member_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    local = data.index(b"PK\x03\x04")
    flags = struct.unpack_from("<H", data, local + 6)[0] | 0x1
    struct.pack_into("<H", data, local + 6, flags)
    central = data.index(b"PK\x01\x02")
    flags = struct.unpack_from("<H", data, central + 8)[0] | 0x1
    struct.pack_into("<H", data, central + 8, flags)
    path.write_bytes(data)


def test_encrypted_member_blocks_without_extraction(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    _set_first_member_encrypted(archive)
    labels = _make_labels(tmp_path / "labels")
    report = _audit(archive, labels, tmp_path / "extract")
    assert report["status"] == "BLOCKED"
    assert any("encrypted archive member" in error for error in report["errors"])
    assert not (tmp_path / "extract").exists()


def _corrupt_first_member(path: Path) -> None:
    data = bytearray(path.read_bytes())
    local = data.index(b"PK\x03\x04")
    name_length, extra_length = struct.unpack_from("<HH", data, local + 26)
    payload = local + 30 + name_length + extra_length
    data[payload] ^= 0xFF
    path.write_bytes(data)


def test_crc_failure_blocks(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    _corrupt_first_member(archive)
    labels = _make_labels(tmp_path / "labels")
    report = _audit(archive, labels, tmp_path / "extract")
    assert report["status"] == "BLOCKED"
    assert any("CRC failure" in error for error in report["errors"])


def test_malformed_action_label_recording_is_not_ignored(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    labels = _make_labels(tmp_path / "labels", malformed=True)
    report = _audit(archive, labels, tmp_path / "extract")
    assert report["status"] == "BLOCKED"
    assert any("malformed action-label recording ID" in e for e in report["errors"])


def test_cli_blocked_output_is_deterministic_utf8_lf_json(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    labels = _make_labels(tmp_path / "labels")
    output = tmp_path / "audit.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/audit_industreal_videos.py",
            str(archive),
            str(labels),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 2
    raw = output.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    payload = json.loads(raw)
    assert payload["status"] == "BLOCKED"
    assert payload["canonical_sha256"] == canonical_sha256(payload)


def test_invalid_verified_on_blocks_and_preserves_canonical_hash(tmp_path):
    video = _make_video(tmp_path / "source.mp4")
    archive = _make_archive(tmp_path / "videos.zip", video)
    labels = _make_labels(tmp_path / "labels")
    report = audit_industreal_videos(
        archive,
        labels,
        expected_size=archive.stat().st_size,
        expected_md5=hashlib.md5(archive.read_bytes()).hexdigest(),
        expected_count=2,
        expected_recording_ids=RECORDING_IDS,
        extracted_dir=tmp_path / "extract",
        verified_on="2026-02-30",
    )
    assert report["status"] == "BLOCKED"
    assert any("verified_on" in error for error in report["errors"])
    assert report["canonical_sha256"] == canonical_sha256(report)
