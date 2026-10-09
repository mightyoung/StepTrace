import csv
import json
import sys
import pytest

sys.path.insert(0, "src")
from display_project.data.industreal_ledger import (
    integrate,
    IndustRealLedgerError,
    _canonical_hash,
    LEDGER_FIELDS,
)


def _audit(path, root, n=86):
    records = []
    for i in range(n):
        rid = (
            f"01_assy_{i // 10}_{i % 10}"
            if i < 84
            else ("15_main_0_1" if i == 84 else "21_assy_1_1")
        )
        video = root / (rid + ".mp4")
        video.write_bytes(b"video")
        records.append(
            {
                "source_recording_id": rid,
                "official_split": "train"
                if i < 36
                else (
                    "val"
                    if i < 52
                    else ("test" if i < 84 else "UNKNOWN_OFFICIAL_UNLABELED")
                ),
                "video": {
                    "path": str(video),
                    "bytes": 5,
                    "sha256": f"{i + 1:064x}",
                    "codec": "avc1",
                    "fps": 25.0,
                    "frame_count": 10,
                    "width": 640,
                    "height": 480,
                    "duration_seconds": 0.4,
                    "decode": {"first": True, "mid": True, "last": True},
                },
            }
        )
    payload = {
        "status": "PASS",
        "verified_on": "2026-07-18",
        "extracted_root": str(root),
        "official_recording_ids": [r["source_recording_id"] for r in records],
        "records": records,
    }
    payload["canonical_sha256"] = _canonical_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _dest(tmp, tombstone=False):
    ledger_path = tmp / "ledger.csv"
    with ledger_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        if tombstone:
            row = {k: "" for k in LEDGER_FIELDS}
            row.update(
                schema_version="1.0.0",
                asset_id="MECCANO-0009",
                record_type="CANONICAL_ROOT_VIDEO",
                parent_asset_id="NONE",
                canonical_root_id="MECCANO-0009",
                canonical_group_id="MECCANO::0009",
                sha256="a" * 64,
                tombstone_id="TOMBSTONE-MECCANO-0009",
                tombstone_irreversible="YES",
                source_dataset="MECCANO",
                project_split="DEVELOPMENT_ONLY_CONTAMINATED",
                formal_use_allowed="NO",
                split_locked="YES",
            )
            w.writerow(row)
    m = tmp / "manifest.json"
    m.write_text(
        json.dumps(
            {
                "schema_version": "1.1.0",
                "status": "BLOCKED_MISSING_EXTERNAL_IDENTITY",
                "records": [],
            }
        ),
        encoding="utf-8",
    )
    return ledger_path, m


def _fixture(tmp):
    root = tmp / "extracted"
    root.mkdir(exist_ok=True)
    a = tmp / "audit.json"
    _audit(a, root)
    ledger_path, m = _dest(tmp)
    return a, ledger_path, m


def test_preflight_and_idempotence(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    assert integrate(a, ledger_path, m)["added"] == 86
    integrate(a, ledger_path, m, apply=True)
    rows = list(csv.DictReader(ledger_path.open(encoding="utf-8")))
    labeled = [r for r in rows if r["source_split"] != "NOT_APPLICABLE"]
    archive_only = [r for r in rows if r["source_split"] == "NOT_APPLICABLE"]
    assert len(labeled) == 84
    assert len(archive_only) == 2
    assert all(
        "OFFICIAL_ACTION_ANNOTATIONS_AVAILABLE" in r["annotation_status"]
        for r in labeled
    )
    assert all(
        "OFFICIAL_ACTION_ANNOTATIONS_UNAVAILABLE" in r["annotation_status"]
        for r in archive_only
    )
    assert all(
        r["complete_job_id"]
        == f"INDUSTREAL-RECORDING-{r['asset_id'].removeprefix('INDUSTREAL-')}"
        for r in labeled + archive_only
    )
    before = (ledger_path.read_bytes(), m.read_bytes())
    assert integrate(a, ledger_path, m)["added"] == 0
    integrate(a, ledger_path, m, apply=True)
    assert before == (ledger_path.read_bytes(), m.read_bytes())


def test_legacy_industreal_rows_migrate_metadata_and_manifest(tmp_path):
    a, ledger_path, manifest_path = _fixture(tmp_path)
    integrate(a, ledger_path, manifest_path, apply=True)
    rows = list(csv.DictReader(ledger_path.open(encoding="utf-8")))
    for row in rows:
        if row["asset_id"].startswith("INDUSTREAL-"):
            row["annotation_status"] = "OFFICIAL_RGB_VIDEO_AUDIT_PASS"
            row["contamination_status"] = "OFFICIAL_SOURCE"
            row["source_split"] = (
                "UNKNOWN_OFFICIAL_UNLABELED"
                if row["asset_id"].endswith(("15_main_0_1", "21_assy_1_1"))
                else row["source_split"]
            )
    with ledger_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    manifest_path.write_text(json.dumps({"records": []}), encoding="utf-8")
    result = integrate(a, ledger_path, manifest_path, apply=True)
    assert result["added"] == 86
    migrated = list(csv.DictReader(ledger_path.open(encoding="utf-8")))
    assert all(
        "OFFICIAL_RGB_VIDEO" not in r["annotation_status"]
        for r in migrated
        if r["asset_id"].startswith("INDUSTREAL-")
    )
    assert len(json.loads(manifest_path.read_text(encoding="utf-8"))["records"]) == 86
    before = (ledger_path.read_bytes(), manifest_path.read_bytes())
    assert integrate(a, ledger_path, manifest_path)["added"] == 0
    assert before == (ledger_path.read_bytes(), manifest_path.read_bytes())


def test_legacy_industreal_immutable_mismatch_rejected(tmp_path):
    a, ledger_path, manifest_path = _fixture(tmp_path)
    integrate(a, ledger_path, manifest_path, apply=True)
    rows = list(csv.DictReader(ledger_path.open(encoding="utf-8")))
    next(r for r in rows if r["asset_id"].startswith("INDUSTREAL-"))["source_path"] = (
        "datasets/IndustReal/other.mp4"
    )
    with ledger_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, manifest_path)


def test_bad_hash_writes_nothing(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    p = json.loads(a.read_text())
    p["records"][0]["video"]["sha256"] = "f" * 64
    a.write_text(json.dumps(p))
    old = (ledger_path.read_bytes(), m.read_bytes())
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m, apply=True)
    assert old == (ledger_path.read_bytes(), m.read_bytes())


def test_invalid_id_and_anchor(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    p = json.loads(a.read_text())
    p["records"][0]["source_recording_id"] = "bad"
    p["official_recording_ids"][0] = "bad"
    p["canonical_sha256"] = _canonical_hash(p)
    a.write_text(json.dumps(p))
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m)


def test_bad_metadata_and_path_escape(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    p = json.loads(a.read_text())
    p["records"][0]["video"]["fps"] = "nan"
    p["canonical_sha256"] = _canonical_hash(p)
    a.write_text(json.dumps(p))
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m)
    p = json.loads(a.read_text())
    p["records"][0]["video"]["fps"] = 25
    p["records"][0]["video"]["path"] = str(tmp_path / "outside.mp4")
    p["canonical_sha256"] = _canonical_hash(p)
    a.write_text(json.dumps(p))
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m)


def test_ledger_duplicate_and_collision(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    with ledger_path.open("a", encoding="utf-8", newline="") as f:
        f.write(",".join(["x"] * len(LEDGER_FIELDS)) + "\n")
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m)
    a, ledger_path, m = _fixture(tmp_path)
    integrate(a, ledger_path, m, apply=True)
    rows = list(csv.DictReader(ledger_path.open(encoding="utf-8")))
    rows[0]["sha256"] = "b" * 64
    with ledger_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m)


def test_manifest_differing_collision_and_split(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    m.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "asset_id": "INDUSTREAL-01_assy_0_0",
                        "canonical_root_id": "X",
                        "video_id": "v",
                        "source_split": "train",
                        "project_split": "a",
                    }
                ]
            }
        )
    )
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m)


def test_tombstone_fields_preserved(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    ledger_path, m = _dest(tmp_path, tombstone=True)
    integrate(a, ledger_path, m, apply=True)
    row = next(
        r
        for r in csv.DictReader(ledger_path.open(encoding="utf-8"))
        if r["asset_id"] == "MECCANO-0009"
    )
    assert (
        row["tombstone_id"] == "TOMBSTONE-MECCANO-0009"
        and row["tombstone_irreversible"] == "YES"
    )


def test_second_replace_failure_rolls_back_byte_identical(tmp_path, monkeypatch):
    a, ledger_path, m = _fixture(tmp_path)
    old = (ledger_path.read_bytes(), m.read_bytes())
    import display_project.data.industreal_ledger as mod

    real = mod.os.replace
    count = [0]

    def fail_second(src, dst):
        count[0] += 1
        if count[0] == 2:
            raise OSError("injected")
        return real(src, dst)

    monkeypatch.setattr(mod.os, "replace", fail_second)
    with pytest.raises(OSError):
        integrate(a, ledger_path, m, apply=True)
    assert (ledger_path.read_bytes(), m.read_bytes()) == old
    assert not list(tmp_path.glob("*.journal")) and not list(tmp_path.glob(".*.tmp"))


def test_overwrite_flag_rejected(tmp_path):
    a, ledger_path, m = _fixture(tmp_path)
    with pytest.raises(IndustRealLedgerError):
        integrate(a, ledger_path, m, overwrite=True)
