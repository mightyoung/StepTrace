import hashlib
import json
from pathlib import Path

import pytest

from display_project.g3_statistics import validate_statistical_result


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "research" / "audits" / "g3-full-producer.synthetic-input.json"
OUTPUT = ROOT / "research" / "audits" / "g3-full-producer.synthetic-output.json"
AFT_DLL_SHA256 = "afa2dc1e7a9136668351308ec688ccbcf404533f5af1acc53eea46b0c0863dc5"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_remote_full_producer_development_replay_is_pinned_and_complete():
    raw_input = _load(INPUT)
    result = _load(OUTPUT)
    assert len(raw_input["opportunities"]) == 6480
    assert hashlib.sha256(OUTPUT.read_bytes()).hexdigest() == (
        "171dac0a2f8ce5d5853a101d30bc7622dcd01a01fd2d1a9ebd3ced34745e59d5"
    )
    valid, reasons = validate_statistical_result(
        result,
        allow_fixture=False,
        expected_producer_id="g3-primary-r-real-candidate",
        expected_producer_version="0.2.0",
        expected_runtime="R 4.6.1",
        expected_code_sha256=raw_input["producer"]["code_sha256"],
        expected_environment_sha256=raw_input["producer"]["environment_sha256"],
        expected_receipt_id=raw_input["binding"]["admission_receipt_id"],
        expected_receipt_sha256=raw_input["binding"]["admission_receipt_sha256"],
        expected_evidence_bundle_sha256=raw_input["binding"][
            "evidence_bundle_sha256"
        ],
        expected_runtime_asset_sha256={"aft_dll": AFT_DLL_SHA256},
    )
    assert valid is True, reasons
    assert result["producer"]["fixture"] is False
    assert result["binding"]["admission_receipt_id"].startswith("development:")
    assert result["resampling"]["completed_replicates"] == 10_000
    assert result["model"]["fallback_triggered"] is True


@pytest.mark.parametrize("branch", ["A", "B"])
def test_remote_full_producer_aft_is_complete(branch):
    aft = _load(OUTPUT)["branches"][branch]["confirmatory_latency_model"]
    assert aft["complete"] is True
    assert aft["dll_sha256"] == AFT_DLL_SHA256
    assert aft["convergence"] == 0
    assert aft["diagnostics"] == {
        "positive_definite_hessian": True,
        "random_effect_variance_boundary": False,
    }
    effects = {item["term"]: item["estimate"] for item in aft["fixed_effects"]}
    assert effects["methodfixed_streak"] == pytest.approx(0.405465, abs=0.013)
    assert effects["methodhmm_or_crf"] == pytest.approx(0.262364, abs=0.013)
    assert effects["taskindustreal"] == pytest.approx(0.05, abs=0.002)


def test_remote_full_producer_recovers_both_preregistered_branches():
    result = _load(OUTPUT)
    branch_a = result["branches"]["A"]
    branch_b = result["branches"]["B"]
    assert branch_a["bootstrap_effects"]["fixed_streak"]["mean"] == pytest.approx(-0.2)
    assert branch_a["bootstrap_effects"]["hmm_or_crf"]["mean"] == pytest.approx(-0.2)
    assert branch_b["bootstrap_effects"]["fixed_streak"]["mean"] == pytest.approx(0.2)
    assert branch_b["bootstrap_effects"]["hmm_or_crf"]["mean"] == pytest.approx(0.1)
    assert all(branch_a["operating_point_gates"].values())
    assert all(branch_b["operating_point_gates"].values())
    assert result["task_directions"] == {
        "A": {"meccano": "favorable", "industreal": "favorable"},
        "B": {"meccano": "favorable", "industreal": "favorable"},
    }
