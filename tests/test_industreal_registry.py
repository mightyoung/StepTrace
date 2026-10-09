from pathlib import Path
import csv
import pytest
from display_project.data.industreal import register_industreal_records


def _csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


def test_industreal_registry_requires_recording_operator_job_task_and_official_split(
    tmp_path,
):
    p = tmp_path / "records.csv"
    _csv(
        p,
        [
            {
                "recording": "r1",
                "operator": "op1",
                "job": "j1",
                "task": "t1",
                "official_split": "train",
                "error_type": "skip",
            }
        ],
    )
    row = register_industreal_records(p)[0]
    assert row["status"] == "REGISTERED"
    assert row["official_split"] == "train"
    assert row["error_type"] == "skip"


def test_industreal_registry_rejects_unknown_psr_error(tmp_path):
    p = tmp_path / "records.csv"
    _csv(
        p,
        [
            {
                "recording": "r1",
                "operator": "op1",
                "job": "j1",
                "task": "t1",
                "official_split": "train",
                "error_type": "mystery",
            }
        ],
    )
    with pytest.raises(ValueError, match="unknown PSR"):
        register_industreal_records(p)


def test_industreal_registry_blocks_missing_file(tmp_path):
    assert (
        register_industreal_records(tmp_path / "none.csv")[0]["status"]
        == "BLOCKED_MISSING_EXTERNAL_DATA"
    )


def test_industreal_registry_does_not_infer_job_or_error(tmp_path):
    p = tmp_path / "records.csv"
    _csv(
        p,
        [
            {
                "recording": "r1",
                "operator": "op1",
                "task": "t1",
                "official_split": "train",
            }
        ],
    )
    row = register_industreal_records(p)[0]
    assert row["complete_job_id"] == "UNKNOWN"
    assert row["error_type"] == "UNKNOWN"
    assert row["status"].startswith("BLOCKED_")


def test_industreal_official_metadata_is_blocked_without_assets(tmp_path):
    split = tmp_path / "split.csv"
    _csv(split, [{"recording": "r1", "split": "train"}])
    rows = register_industreal_records(
        tmp_path / "missing.csv", official_split_csv=split
    )
    assert rows[0]["status"] == "BLOCKED_METADATA_ONLY_VIDEO_OR_ERROR_UNAVAILABLE"
    assert rows[0]["video_asset_status"] == "UNAVAILABLE"
    assert rows[0]["psr_error_asset_status"] == "UNAVAILABLE"


def test_industreal_official_files_inventory_is_complete_and_blocked():
    import json

    root = Path(__file__).parents[1]
    inventory = json.loads(
        (root / "datasets/IndustReal/official/inventory.json").read_text(
            encoding="utf-8"
        )
    )
    files_path = root / inventory["official_files_api"]["path"]
    files = json.loads(files_path.read_text(encoding="utf-8"))
    assert inventory["official_files_api"]["api_url"].endswith("/files")
    assert inventory["official_files_api"]["retrieved_date"] == "2026-07-18"
    assert len(files) == 18
    assert inventory["official_files_api"]["file_count"] == 18
    for item in files:
        assert {"name", "bytes", "md5", "uuid", "download_url"} <= item.keys()

    psr = inventory["recording_archive_acquisition"]["psr"]
    assert psr["status"] == "FULL_RECORDING_ARCHIVES_REQUIRED"
    assert len(psr["full_recording_archives"]) == 9
    assert psr["total_bytes"] == 54154922299
    assert psr["total_gib"] == 50.436
    train_p4 = next(
        x for x in psr["full_recording_archives"] if x["archive"] == "train_p4"
    )
    assert train_p4["bytes"] == 4837382030
    assert train_p4["md5"] == "40193a8fee14fad551ff6ed7b140c29f"
    assert train_p4["uuid"] == "3ff5409e-165f-4354-9fa4-a4fde639a7a6"
    assert inventory["rgb_video_asset"]["expected_bytes"] == 4960644152
    assert (
        inventory["rgb_video_asset"]["expected_md5"]
        == "c5b8901dba179d2eb10e9348f8163c79"
    )
    rgb = inventory["rgb_video_asset"]
    assert rgb["range_resume_supported"] is False
    assert rgb["status"] == "AVAILABLE_VERIFIED"
    assert rgb["actual_bytes"] == 4960644152
    assert rgb["actual_md5"] == "c5b8901dba179d2eb10e9348f8163c79"
    assert rgb["extracted_recording_count"] == 86
    assert rgb["unique_video_hash_count"] == 86
    assert rgb["decoded_recording_count"] == 86
    assert (
        rgb["audit_sha256"]
        == "0db1bce20db799bb9337c0f4b09ce60ca17e43fb0fefcbd87b4b956df7747ee9"
    )
    assert (
        inventory["labels"]["psr_error_labels"] == "MISSING_FROM_DOWNLOADED_LABEL_ASSET"
    )
    assert inventory["labels"]["action_labeled_recording_count"] == 84
    assert inventory["labels"]["participant_count"] == 27
    assert inventory["labels"]["operator_mapping_status"] == "UNKNOWN"
    assert inventory["labels"]["complete_job_id_status"] == "UNKNOWN"
    psr = inventory["recording_archive_acquisition"]["psr"]
    assert psr["official_archive_recording_count"] == 86
    assert psr["action_labeled_recording_count"] == 84
    assert psr["archive_only_recording_ids"] == ["15_main_0_1", "21_assy_1_1"]
    assert inventory["status"] == "BLOCKED"
    assert inventory["blockers"] == []
    assert any(
        event["event"] == "VERIFIED_COMPLETE_RGB_ARCHIVE"
        for event in inventory["acquisition_events"]
    )


def test_industreal_registry_reflects_verified_rgb_source_but_blocks_identity():
    import hashlib
    import json

    root = Path(__file__).parents[1]
    path = root / "research/audits/industreal-registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    canonical = dict(registry)
    canonical["canonical_sha256"] = ""
    expected = hashlib.sha256(
        (
            json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    ).hexdigest()
    assert registry["canonical_sha256"] == expected
    assert registry["video_asset_status"] == "AVAILABLE"
    assert registry["video_audit"]["status"] == "PASS"
    assert registry["video_audit"]["extracted_recording_count"] == 86
    assert registry["video_count"] == registry["source_recording_count"] == 86
    assert registry["action_labeled_source_recording_count"] == 84
    assert registry["participant_identity_status"] == "UNVERIFIED_NAME_DERIVED"
    assert registry["operator_mapping_status"] == "UNKNOWN"
    assert registry["complete_job_id_status"] == "COMPLETE_RECORDING_BOUND"
    assert all(
        record["complete_job_id"]
        == f"INDUSTREAL-RECORDING-{record['recording_id']}"
        for record in registry["records"]
    )
    assert registry["status"] == "BLOCKED"


def test_industreal_split_crossing_is_fail_closed(tmp_path):
    p = tmp_path / "records.csv"
    _csv(
        p,
        [
            {
                "recording": "r1",
                "operator": "op1",
                "job": "job1",
                "task": "t1",
                "official_split": "train",
                "error_type": "skip",
            },
            {
                "recording": "r2",
                "operator": "op1",
                "job": "job1",
                "task": "t1",
                "official_split": "test",
                "error_type": "skip",
            },
        ],
    )
    from display_project.data.industreal import audit_industreal_records

    report = audit_industreal_records(p, expected_videos=2, expected_operators=1)
    assert report["status"] == "BLOCKED"
    assert any("crosses official splits" in e for e in report["errors"])


def test_industreal_cli_returns_nonzero_for_missing_video_and_psr(tmp_path):
    split = tmp_path / "split.csv"
    _csv(split, [{"recording": "r1", "split": "train"}])
    out = tmp_path / "registry.json"
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "scripts/register_industreal.py",
            str(tmp_path / "missing.csv"),
            "--split-csv",
            str(split),
            "--output",
            str(out),
            "--expected-videos",
            "1",
            "--expected-operators",
            "1",
        ],
        env={"PYTHONPATH": "src"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    raw = out.read_bytes()
    assert b"\xef\xbb\xbf" not in raw and b"\r" not in raw
    import json

    payload = json.loads(raw)
    assert payload["status"] == "BLOCKED" and len(payload["canonical_sha256"]) == 64


def test_industreal_json_artifacts_are_utf8_lf_and_parseable():
    root = Path(__file__).parents[1]
    paths = sorted((root / "datasets" / "IndustReal").rglob("*.json"))
    paths += [root / "research" / "audits" / "industreal-registry.json"]
    assert paths
    import json

    for path in paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        assert b"\r" not in raw, path
        assert not raw.endswith(b"\\n"), path
        json.loads(raw.decode("utf-8"))
