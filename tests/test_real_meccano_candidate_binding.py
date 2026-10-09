import json
from pathlib import Path

from display_project.gate_audit import (
    _candidate_manifest_valid,
    _embedded_hash_valid,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_real_candidate_is_hash_bound_to_full_source_identity_records():
    qa_path = REPO_ROOT / "data/yolo/meccano-small-objects/dataset-qa.json"
    candidate_path = (
        REPO_ROOT / "research/audits/meccano-yolo-candidate-split-manifest.json"
    )
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

    assert _embedded_hash_valid(candidate, "candidate_payload_sha256")
    assert _candidate_manifest_valid(candidate, qa, candidate_path)
    assert len(candidate["records"]) == 19
    assert all(record["canonical_group_id"] for record in candidate["records"])
    assert all(
        record["operator_id"]
        == f"MECCANO-P-{int(record['dataset_video_id']):02d}"
        for record in candidate["records"]
    )
    assert all(
        record["complete_job_id"]
        == f"MECCANO-ASSEMBLY-{int(record['dataset_video_id']):02d}"
        for record in candidate["records"]
    )
    assert not any(
        record["dataset_video_id"] == "09" for record in candidate["records"]
    )
