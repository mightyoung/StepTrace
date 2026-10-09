from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
AUDITS = ROOT / "research" / "audits"
PRODUCER = ROOT / "statistics" / "g3-r-producer"
IMAGE_DIGEST = (
    "sha256:555a0e7734b17f3901f01c8e379f87d797a0e6344a4cc3b246329ed3f0689809"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_remote_aft_build_is_bound_to_frozen_source_environment_and_image():
    build = json.loads(
        (AUDITS / "lognormal_aft.build.json").read_text(encoding="utf-8")
    )
    assert build["schema_version"] == "g3-aft-tmb-build-1.0.0"
    assert build["source_sha256"] == _sha256(
        PRODUCER / "src" / "lognormal_aft.cpp"
    )
    assert build["renv_lock_sha256"] == _sha256(PRODUCER / "renv.lock")
    assert build["container_image_digest"] == IMAGE_DIGEST
    assert build["r_version"] == "R 4.6.1"
    assert build["tmb_version"] == "1.9.23"
    assert build["platform"] == "x86_64-pc-linux-gnu"
    assert build["compiler"] == "g++ -std=gnu++20"
    assert len(build["dll_sha256"]) == 64


def test_remote_aft_smoke_recovers_truth_without_singular_random_effects():
    build = json.loads(
        (AUDITS / "lognormal_aft.build.json").read_text(encoding="utf-8")
    )
    smoke = json.loads(
        (AUDITS / "lognormal_aft.smoke.json").read_text(encoding="utf-8")
    )
    assert smoke["schema_version"] == "g3-aft-tmb-smoke-1.0.0"
    assert smoke["numerical_truth_checks"]["passed"] is True
    assert smoke["numerical_truth_checks"]["tolerance"] == 0.02
    for branch in ("A", "B"):
        result = smoke[branch]
        assert result["complete"] is True
        assert result["dll_sha256"] == build["dll_sha256"]
        assert result["distribution"] == "lognormal"
        assert result["censoring"] == "right"
        assert result["random_effects"] == ["operator", "operator:complete_job"]
        assert result["convergence"] == 0
        assert result["diagnostics"] == {
            "positive_definite_hessian": True,
            "random_effect_variance_boundary": False,
        }
        assert result["sd_operator"] > 0.05
        assert result["sd_job"] > 0.03
