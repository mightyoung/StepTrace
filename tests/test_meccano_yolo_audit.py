import csv
import hashlib
import json
from pathlib import Path

from PIL import Image

from display_project.data.meccano_yolo_audit import (
    audit_meccano_yolo_dataset,
    build_meccano_candidate_manifest,
)
from display_project.data.yolo_dataset import MECCANO_YOLO_NAMES


def _pair(root, split, stem, color, label):
    image = root / "images" / split / f"{stem}.jpg"
    annotation = root / "labels" / split / f"{stem}.txt"
    image.parent.mkdir(parents=True, exist_ok=True)
    annotation.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 50), color=color).save(image)
    annotation.write_text(label + "\n", encoding="utf-8")


def _box_counts(**values):
    result = {name: 0 for name in MECCANO_YOLO_NAMES}
    result.update(values)
    return result


def _source_entry(path, *, family, split, used_videos):
    return {
        "family": family,
        "split": split,
        "path": path.resolve().as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema_status": "PASS",
        "used_videos": used_videos,
    }


def _write_annotation_sources(root, split_videos):
    source_root = root / "official-annotations"
    entries = []
    for split, videos in split_videos.items():
        coco = source_root / f"instances_meccano_{split}.json"
        coco.parent.mkdir(parents=True, exist_ok=True)
        coco.write_text(
            json.dumps({"images": [], "annotations": [], "categories": []}),
            encoding="utf-8",
        )
        entries.append(
            _source_entry(
                coco,
                family="ACTIVE_OBJECT_COCO",
                split=split,
                used_videos=videos,
            )
        )
        for video in videos:
            hand = source_root / split / f"{int(video):04d}.csv"
            hand.parent.mkdir(parents=True, exist_ok=True)
            with hand.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=[
                        "filename",
                        "region_shape_attributes",
                        "region_attributes",
                    ],
                )
                writer.writeheader()
            entries.append(
                _source_entry(
                    hand,
                    family="HAND_VGG_CSV",
                    split=split,
                    used_videos=[video],
                )
            )
    body = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return {
        "status": "PASS",
        "source_count": len(entries),
        "sources_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "sources": entries,
        "held_out_videos_excluded": ["09"],
    }


def test_real_candidate_qa_passes_while_unknown_identity_stays_blocked(tmp_path):
    root = tmp_path / "dataset"
    _pair(root, "train", "01_00001", (255, 0, 0), "0 0.5 0.5 0.2 0.2")
    _pair(root, "val", "07_00002", (0, 255, 0), "3 0.5 0.5 0.1 0.1")
    _pair(root, "test", "10_00003", (0, 0, 255), "5 0.5 0.5 0.3 0.3")
    conversion = {
        "dataset_profile": "DEVELOPMENT_CANDIDATE",
        "formal_admission": "BLOCKED_UNTIL_G2",
        "selected_images": {"train": 1, "val": 1, "test": 1},
        "boxes": {
            "train": _box_counts(hand=1),
            "val": _box_counts(screw=1),
            "test": _box_counts(screwdriver=1),
        },
        "annotation_source_provenance": _write_annotation_sources(
            root, {"train": ["01"], "val": ["07"], "test": ["10"]}
        ),
    }
    (root / "dataset-audit.json").write_text(
        json.dumps(conversion),
        encoding="utf-8",
    )
    identity = {
        "schema_version": "1.1.0",
        "status": "BLOCKED_MISSING_EXTERNAL_IDENTITY",
        "records": [
            {
                "asset_id": "MECCANO-0001",
                "source_dataset": "MECCANO",
                "video_id": "0001",
                "canonical_root_id": "MECCANO-0001",
                "operator_id": "UNKNOWN",
                "complete_job_id": "UNKNOWN",
                "tombstone_id": "NONE",
            },
            {
                "asset_id": "MECCANO-0007",
                "source_dataset": "MECCANO",
                "video_id": "0007",
                "canonical_root_id": "MECCANO-0007",
                "operator_id": "UNKNOWN",
                "complete_job_id": "MECCANO-0007-COMPLETE-JOB",
                "tombstone_id": "NONE",
            },
            {
                "asset_id": "MECCANO-0010",
                "source_dataset": "MECCANO",
                "video_id": "0010",
                "canonical_root_id": "MECCANO-0010",
                "operator_id": "UNKNOWN",
                "complete_job_id": "MECCANO-0010-COMPLETE-JOB",
                "tombstone_id": "NONE",
            },
            {
                "asset_id": "MECCANO-0009",
                "source_dataset": "MECCANO",
                "video_id": "0009",
                "canonical_root_id": "MECCANO-0009",
                "operator_id": "UNKNOWN",
                "complete_job_id": "MECCANO-0009-COMPLETE-JOB",
                "tombstone_id": "TOMBSTONE-MECCANO-0009",
            },
        ],
    }
    identity_path = tmp_path / "split-manifest.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    file_manifest = tmp_path / "files.jsonl"

    first = audit_meccano_yolo_dataset(
        root,
        file_manifest_path=file_manifest,
    )
    second = audit_meccano_yolo_dataset(
        root,
        file_manifest_path=tmp_path / "files-second.jsonl",
    )

    assert first["status"] == "PASS"
    assert first["images"] == {"train": 1, "val": 1, "test": 1}
    assert first["boxes"]["test"]["screwdriver"] == 1
    assert first["held_out_test_assets"] == []
    assert first["content_sha256"] == second["content_sha256"]
    assert first["file_manifest_sha256"] == second["file_manifest_sha256"]
    assert first["annotation_source_provenance"]["status"] == "PASS"
    candidate = build_meccano_candidate_manifest(first, identity_path)
    assert candidate["status"] == "BLOCKED_UNKNOWN_IDENTITY"
    assert candidate["formal_admission"] == "BLOCKED_UNTIL_G2"
    assert candidate["checks"]["video_isolation"] == "PASS"
    assert candidate["checks"]["adjacent_frame_isolation"] == "PASS"
    assert candidate["checks"]["identity_values_invented"] is False
    record_01 = next(
        record for record in candidate["records"] if record["dataset_video_id"] == "01"
    )
    assert record_01["complete_job_id"] == "UNKNOWN"
    assert candidate["excluded_held_out_records"][0]["tombstone_id"] == (
        "TOMBSTONE-MECCANO-0009"
    )


def test_qa_rejects_heldout_test_invalid_box_and_missing_pair(tmp_path):
    root = tmp_path / "dataset"
    _pair(root, "train", "01_00001", (1, 2, 3), "3 0.99 0.5 0.2 0.2")
    val_image = root / "images" / "val" / "07_00001.jpg"
    val_image.parent.mkdir(parents=True)
    Image.new("RGB", (100, 50), color=(4, 5, 6)).save(val_image)
    _pair(root, "test", "09_00001", (7, 8, 9), "0 0.5 0.5 0.2 0.2")
    conversion = {
        "selected_images": {"train": 1, "val": 1, "test": 1},
        "boxes": {split: _box_counts() for split in ("train", "val", "test")},
        "annotation_source_provenance": _write_annotation_sources(
            root, {"train": ["01"], "val": ["07"], "test": []}
        ),
    }
    (root / "dataset-audit.json").write_text(
        json.dumps(conversion),
        encoding="utf-8",
    )

    report = audit_meccano_yolo_dataset(root)

    assert report["status"] == "FAIL"
    assert report["held_out_test_assets"] == ["images/test/09_00001.jpg"]
    assert report["invalid_labels"] == 1
    assert report["missing_label_pairs"] == 1
    assert any("outside normalized bounds" in error for error in report["errors"])


def test_qa_fails_closed_when_bound_coco_or_vgg_source_is_tampered(tmp_path):
    root = tmp_path / "dataset"
    _pair(root, "train", "01_00001", (1, 2, 3), "0 0.5 0.5 0.2 0.2")
    _pair(root, "val", "07_00001", (4, 5, 6), "0 0.5 0.5 0.2 0.2")
    _pair(root, "test", "10_00001", (7, 8, 9), "0 0.5 0.5 0.2 0.2")
    provenance = _write_annotation_sources(
        root, {"train": ["01"], "val": ["07"], "test": ["10"]}
    )
    conversion = {
        "splits": {"train": ["01"], "val": ["07"], "test": ["10"]},
        "held_out_videos": ["09"],
        "selected_images": {"train": 1, "val": 1, "test": 1},
        "boxes": {split: _box_counts(hand=1) for split in ("train", "val", "test")},
        "annotation_source_provenance": provenance,
    }
    (root / "dataset-audit.json").write_text(json.dumps(conversion), encoding="utf-8")
    Path(provenance["sources"][0]["path"]).write_text("{}", encoding="utf-8")

    report = audit_meccano_yolo_dataset(root)

    assert report["status"] == "FAIL"
    assert report["annotation_source_provenance"]["status"] == "FAIL"
    assert any("annotation source" in error for error in report["errors"])


def test_candidate_manifest_rejects_video_and_known_identity_crossing(tmp_path):
    identity = {
        "status": "PASS",
        "records": [
            {
                "asset_id": "MECCANO-0001",
                "source_dataset": "MECCANO",
                "video_id": "0001",
                "canonical_root_id": "MECCANO-0001",
                "operator_id": "operator-a",
                "complete_job_id": "job-a",
                "tombstone_id": "NONE",
            }
        ],
    }
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    qa_report = {
        "passed": True,
        "video_frame_counts": {
            "train": {"01": 2},
            "val": {},
            "test": {"01": 1},
        },
        "video_cross_split": {"01": ["test", "train"]},
        "held_out_test_assets": [],
        "content_sha256": "a" * 64,
        "file_manifest_sha256": "b" * 64,
        "conversion_report_sha256": "c" * 64,
    }

    candidate = build_meccano_candidate_manifest(
        qa_report,
        identity_path,
        held_out_videos=(),
    )

    assert candidate["status"] == "FAIL_SPLIT_LEAKAGE"
    assert candidate["checks"]["video_isolation"] == "FAIL"
    assert candidate["checks"]["known_operator_isolation"] == "FAIL"
    assert candidate["checks"]["known_complete_job_isolation"] == "FAIL"


def test_candidate_manifest_admits_complete_anonymous_sequence_identities(tmp_path):
    records = []
    for video in ("0001", "0007", "0010", "0009"):
        sequence = int(video)
        records.append(
            {
                "asset_id": f"MECCANO-{video}",
                "source_dataset": "MECCANO",
                "video_id": video,
                "canonical_root_id": f"MECCANO-{video}",
                "canonical_group_id": f"MECCANO::MECCANO-P-{sequence:02d}",
                "operator_id": f"MECCANO-P-{sequence:02d}",
                "complete_job_id": f"MECCANO-ASSEMBLY-{sequence:02d}",
                "tombstone_id": (
                    "TOMBSTONE-MECCANO-0009" if video == "0009" else "NONE"
                ),
            }
        )
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(
        json.dumps({"status": "BLOCKED_OTHER_DATASET", "records": records}),
        encoding="utf-8",
    )
    qa_report = {
        "passed": True,
        "video_frame_counts": {
            "train": {"01": 2},
            "val": {"07": 2},
            "test": {"10": 2},
        },
        "video_cross_split": {},
        "held_out_test_assets": [],
        "content_sha256": "a" * 64,
        "file_manifest_sha256": "b" * 64,
        "conversion_report_sha256": "c" * 64,
    }

    candidate = build_meccano_candidate_manifest(qa_report, identity_path)

    assert candidate["status"] == "DEVELOPMENT_CANDIDATE"
    assert candidate["formal_admission"] == "ADMITTED"
    assert candidate["formal_admission_scope"] == (
        "ELIGIBLE_FOR_FUTURE_FORMAL_PARTITION_NOT_ASSIGNED"
    )
    assert candidate["formal_use_currently_allowed"] is False
    assert candidate["source_identity_manifest_status"] == "READY"
    assert candidate["unknown_operator_video_ids"] == []
    assert candidate["unknown_complete_job_video_ids"] == []
    assert candidate["excluded_held_out_records"] == [
        {
            "dataset_video_id": "09",
            "asset_id": "MECCANO-0009",
            "video_id": "0009",
            "tombstone_id": "TOMBSTONE-MECCANO-0009",
            "present_in_candidate_dataset": False,
        }
    ]
