from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.audit_g1_local_inputs import build_report
from scripts.audit_g1_local_inputs import DEFAULT_HMM_PARTITION_MANIFEST


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_default_hmm_partition_uses_frozen_episode_manifest():
    assert DEFAULT_HMM_PARTITION_MANIFEST.as_posix() == (
        "data/g1/meccano-g1-episode-manifest.json"
    )


def test_missing_observations_and_runtime_fail_closed(tmp_path: Path, monkeypatch):
    dataset_qa = tmp_path / "qa.json"
    dataset_qa.write_text(
        json.dumps(
            {
                "status": "PASS",
                "passed": True,
                "dataset_profile": "DEVELOPMENT_CANDIDATE",
                "held_out_videos": ["09"],
                "held_out_test_assets": [],
                "content_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    base_model = tmp_path / "base.pt"
    base_model.write_bytes(b"official base")
    provenance = tmp_path / "base.provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "artifact_role": "PRETRAINED_BASE_MODEL",
                "training_status": "NOT_FINE_TUNED",
                "formal_admission": "PROHIBITED_AS_MECCANO_BEST_PT",
                "sha256": _hash(base_model.read_bytes()),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.audit_g1_local_inputs.importlib.util.find_spec", lambda name: None
    )

    report = build_report(
        calibration_input=tmp_path / "missing-calibration.json",
        hmm_input=tmp_path / "missing-hmm.jsonl",
        partition_manifest=tmp_path / "missing-manifest.json",
        dataset_qa=dataset_qa,
        base_model=base_model,
        base_provenance=provenance,
    )

    assert report["status"] == "BLOCKED"
    assert report["synthetic_or_tombstoned_substitution_allowed"] is False
    assert report["chains"]["calibration"]["error_code"] == "CALIBRATION_INPUT_MISSING"
    assert report["chains"]["hmm_or_crf"]["error_code"] == "HMM_FIT_INPUT_MISSING"
    assert report["chains"]["yolo"]["error_code"] == "ULTRALYTICS_MISSING"


def test_calibration_rejects_video_identity(tmp_path: Path):
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {
                "split_role": "CALIBRATION",
                "truth_store_used": False,
                "partition_hash": "a" * 64,
                "source_digest": "b" * 64,
                "probabilities": [0.2, 0.8],
                "labels": [0, 1],
                "video_id": "0009",
            }
        ),
        encoding="utf-8",
    )

    from scripts.audit_g1_local_inputs import _audit_calibration

    result = _audit_calibration(calibration)
    assert result["error_code"] == "CALIBRATION_IDENTITY_FORBIDDEN"


def test_hmm_readiness_rejects_non_numeric_or_wrong_width_features(tmp_path: Path):
    observations = tmp_path / "observations.jsonl"
    observations.write_text(
        json.dumps(
            {
                "group_hash": "a" * 64,
                "partition": "DEVELOPMENT",
                "state": "start",
                "features": [0.1, True],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    partition = tmp_path / "partition.json"
    partition.write_text(
        json.dumps(
            {
                "dataset_identity_status": {"MECCANO": "READY"},
                "records": [
                    {
                        "tombstone_id": "TOMBSTONE-MECCANO-0009",
                        "formal_use_allowed": "NO",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    from scripts.audit_g1_local_inputs import _audit_hmm

    result = _audit_hmm(observations, partition)
    assert result["error_code"] == "HMM_FIT_INPUT_INVALID"
    assert "features are invalid" in result["reason"]
