from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import yaml

from scripts.freeze_preflight_anchors import freeze_preflight_anchors


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture_repo(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    config = root / "configs/experiments/example.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """schema_version: 1
profile: DEVELOPMENT_ONLY
anchors:
  code_hash: "0000000000000000000000000000000000000000000000000000000000000000"
  environment_hash: "0000000000000000000000000000000000000000000000000000000000000000"
  annotation_hash: "0000000000000000000000000000000000000000000000000000000000000000"
  partition_hash: "0000000000000000000000000000000000000000000000000000000000000000"
""",
        encoding="utf-8",
    )
    active = root / "datasets/MECCANO/MECCANO_active_objects_annotations"
    hands = root / "datasets/MECCANO/MECCANO_hands_bounding_box_annotations"
    active.mkdir(parents=True)
    hands.mkdir(parents=True)
    (active / "instances.json").write_text("{}", encoding="utf-8")
    (hands / "0001.csv").write_text("frame,x1,y1,x2,y2\n", encoding="utf-8")
    audit = root / "data/yolo/dataset-audit.json"
    qa = root / "data/yolo/dataset-qa.json"
    _write_json(audit, {"dataset_profile": "DEVELOPMENT_CANDIDATE"})
    _write_json(
        qa,
        {
            "status": "PASS",
            "passed": True,
            "dataset_profile": "DEVELOPMENT_CANDIDATE",
            "held_out_videos": ["09"],
            "held_out_test_assets": [],
            "conversion_report_sha256": hashlib.sha256(audit.read_bytes()).hexdigest(),
        },
    )
    split = root / "research/split-manifest.json"
    _write_json(split, {"status": "BLOCKED_MISSING_EXTERNAL_IDENTITY"})
    candidate = root / "research/audits/candidate.json"
    _write_json(
        candidate,
        {
            "status": "BLOCKED_UNKNOWN_IDENTITY",
            "dataset_profile": "DEVELOPMENT_CANDIDATE",
            "source_identity_manifest_sha256": hashlib.sha256(split.read_bytes()).hexdigest(),
            "excluded_held_out_records": [
                {
                    "dataset_video_id": "09",
                    "tombstone_id": "TOMBSTONE-MECCANO-0009",
                    "present_in_candidate_dataset": False,
                }
            ],
        },
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    return root, {
        "config": config,
        "active": active,
        "hands": hands,
        "audit": audit,
        "qa": qa,
        "split": split,
        "candidate": candidate,
    }


def test_freeze_is_reproducible_and_updates_all_four_anchors(tmp_path: Path):
    root, paths = _fixture_repo(tmp_path)
    output = root / "research/audits/g1-preflight-anchors.json"
    kwargs = {
        "root": root,
        "manifest": paths["config"],
        "output": output,
        "active_annotations": paths["active"],
        "hand_annotations": paths["hands"],
        "dataset_audit": paths["audit"],
        "dataset_qa": paths["qa"],
        "candidate_manifest": paths["candidate"],
        "split_manifest": paths["split"],
        "packages": (("numpy", "2.0.0"), ("PyYAML", "6.0")),
        "update_manifest": True,
    }
    first = freeze_preflight_anchors(**kwargs)
    second = freeze_preflight_anchors(**kwargs)

    assert first == second
    assert first["status"] == "FROZEN_DEVELOPMENT_ONLY"
    assert first["formal_admission"] == "DOES_NOT_CLOSE_G1_OR_G2"
    assert first["provenance"]["code"]["git_commit"]
    assert first["provenance"]["code"]["git_tree"]
    assert first["provenance"]["partition"]["candidate_status"] == "BLOCKED_UNKNOWN_IDENTITY"
    assert first["artifact_sha256"] == hashlib.sha256(
        json.dumps(
            {key: value for key, value in first.items() if key != "artifact_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    loaded = yaml.safe_load(paths["config"].read_text(encoding="utf-8"))
    assert loaded["anchors"] == first["anchors"]
    assert all(value != "0" * 64 for value in loaded["anchors"].values())


def test_freeze_rejects_missing_0009_tombstone(tmp_path: Path):
    root, paths = _fixture_repo(tmp_path)
    _write_json(
        paths["candidate"],
        {
            "status": "BLOCKED_UNKNOWN_IDENTITY",
            "source_identity_manifest_sha256": hashlib.sha256(
                paths["split"].read_bytes()
            ).hexdigest(),
        },
    )

    try:
        freeze_preflight_anchors(
            root=root,
            manifest=paths["config"],
            output=root / "anchors.json",
            active_annotations=paths["active"],
            hand_annotations=paths["hands"],
            dataset_audit=paths["audit"],
            dataset_qa=paths["qa"],
            candidate_manifest=paths["candidate"],
            split_manifest=paths["split"],
            packages=(("PyYAML", "6.0"),),
            update_manifest=False,
        )
    except ValueError as error:
        assert "TOMBSTONE-MECCANO-0009" in str(error)
    else:
        raise AssertionError("partition anchor accepted a missing 0009 tombstone")
