from __future__ import annotations

import hashlib
import json
from pathlib import Path

from display_project.g3_statistics import validate_statistical_result
from display_project.g3_statistical_input import validate_g3_statistical_input


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "research/audits/g3-full-producer.task-specific.synthetic-input.json"
OUTPUT = ROOT / "research/audits/g3-full-producer.task-specific.synthetic-output.json"


def test_task_specific_complete_job_fallback_remote_replay_is_pinned_and_valid():
    raw = json.loads(INPUT.read_text(encoding="utf-8"))
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    valid, reasons = validate_g3_statistical_input(raw)
    assert valid, reasons
    assert hashlib.sha256(OUTPUT.read_bytes()).hexdigest() == (
        "970c631303cddf62d901bfe157a14c07d305aae6fee624ffb174b29caf929693"
    )
    valid, reasons = validate_statistical_result(
        result,
        allow_fixture=False,
        expected_producer_id="g3-primary-r-real-candidate",
        expected_producer_version="0.2.0",
        expected_runtime="R 4.6.1",
        expected_code_sha256=raw["producer"]["code_sha256"],
        expected_environment_sha256=raw["producer"]["environment_sha256"],
        expected_receipt_id=raw["binding"]["admission_receipt_id"],
        expected_receipt_sha256=raw["binding"]["admission_receipt_sha256"],
        expected_evidence_bundle_sha256=raw["binding"]["evidence_bundle_sha256"],
        expected_runtime_asset_sha256=raw["producer"]["runtime_asset_sha256"],
    )
    assert valid, reasons
    assert raw["cluster_level_by_task"] == {
        "meccano": "operator",
        "industreal": "complete_job",
    }
    assert result["resampling"]["cluster_level"] == "task_specific"
    assert result["branches"]["A"]["confirmatory_latency_model"]["random_effects"] == [
        "cluster",
        "cluster:complete_job",
    ]
