import json
from pathlib import Path

from scripts.audit_g1_local_inputs import _audit_hmm


def test_hmm_readiness_accepts_frozen_truth_free_episode_manifest(
    tmp_path: Path,
):
    observations = tmp_path / "observations.jsonl"
    observations.write_text(
        json.dumps(
            {
                "group_hash": "a" * 64,
                "partition": "DEVELOPMENT",
                "state": "pick_components",
                "features": [1.0, 2.0, 3.0],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "status": "FROZEN_DEVELOPMENT_CALIBRATION",
                "selection_policy": {
                    "tombstone_0009_excluded": True,
                    "runtime_receives_action_labels": False,
                    "runtime_truth_store_allowed": False,
                },
                "records": [
                    {
                        "video_id": "0014",
                        "formal_test": False,
                        "runtime_truth_store_allowed": False,
                        "project_split": "DEVELOPMENT",
                        "group_hash": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = _audit_hmm(observations, manifest)
    assert result["status"] == "READY"
