from display_project.data.meccano_registry import register_meccano_video


def test_meccano_registry_records_sha256_and_video_metadata(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"video-bytes")
    row = register_meccano_video(p, video_id="0001", source_split="official-test")
    assert row["sha256"] != "UNKNOWN"
    assert row["status"] == "REGISTERED"
    assert row["source_split"] == "official-test"


def test_meccano_registry_blocks_missing_external_video(tmp_path):
    row = register_meccano_video(tmp_path / "missing.mp4", video_id="0002")
    assert row["status"] == "BLOCKED_MISSING_EXTERNAL_DATA"
    assert row["sha256"] == "UNKNOWN"


def test_meccano_0009_is_irreversible_tombstone(tmp_path):
    p = tmp_path / "0009.mp4"
    p.write_bytes(b"video")
    row = register_meccano_video(p, video_id="0009")
    assert row["tombstone_id"] == "TOMBSTONE-MECCANO-0009"
    assert row["tombstone_irreversible"] is True
    assert row["status"] in {"REGISTERED", "TOMBSTONED"}
