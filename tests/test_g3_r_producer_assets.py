from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
PRODUCER = ROOT / "statistics" / "g3-r-producer"


def test_r_producer_environment_is_exactly_locked():
    lock = json.loads((PRODUCER / "environment-lock.json").read_text(encoding="utf-8"))
    assert lock["schema_version"] == "g3-r-environment-lock-1.0.0"
    assert lock["r_version"] == "4.6.1"
    assert lock["repository"] == "https://cloud.r-project.org"
    assert lock["packages"] == {
        "DHARMa": "0.5.0",
        "boot": "1.3-32",
        "brglm2": "1.1.0",
        "clubSandwich": "0.7.0",
        "coxme": "2.2-22",
        "digest": "0.6.39",
        "emmeans": "2.0.4",
        "glmmTMB": "1.1.14",
        "jsonlite": "2.0.0",
        "lme4": "2.0-6",
        "renv": "1.2.4",
        "survival": "3.8-9",
        "TMB": "1.9.23",
    }
    canonical = dict(lock)
    supplied = canonical.pop("lock_sha256")
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert supplied == hashlib.sha256(raw).hexdigest()
    renv_lock = json.loads((PRODUCER / "renv.lock").read_text(encoding="utf-8"))
    assert renv_lock["R"]["Version"] == "4.6.1"
    for package, version in lock["packages"].items():
        assert renv_lock["Packages"][package]["Version"] == version


def test_r_producer_is_out_of_process_and_fail_closed():
    script = (PRODUCER / "produce_g3_statistics.R").read_text(encoding="utf-8")
    assert 'RNGkind("L\'Ecuyer-CMRG")' in script
    assert "requested_replicates != 10000" in script
    assert "lme4::glmer" in script
    assert "brglm2::brglmFit" in script
    assert "p.adjust" in script and 'method = "holm"' in script
    assert "fixture" in script
    assert "real production is not admitted by the fixture producer" in script
    assert "operator_id" in script and "complete_job_id" in script
    assert "artifact_sha256" in script
    assert "STATISTICAL_ENGINE_MISSING" in script
    verifier = (PRODUCER / "verify_environment.R").read_text(encoding="utf-8")
    assert "packageVersion" in verifier and "renv.lock" in verifier


def test_real_candidate_uses_only_raw_rows_and_keeps_latency_fail_closed():
    script = (PRODUCER / "produce_g3_statistics_real.R").read_text(encoding="utf-8")
    assert '"display_project.g3_statistical_input-1.0.0"' in script
    assert "payload$opportunities" in script
    assert "payload$task_directions" not in script
    assert "payload$branches" not in script
    assert "payload$endpoints" not in script
    assert "payload$common_support" not in script
    assert "requested_replicates != 10000L" in script
    assert "lme4::glmer" in script and "brglm2::brglmFit" in script
    assert 'method = "holm"' in script
    assert "bootstrap_branch" in script and "operator_id" in script
    assert 'confirmatory_aft_status = "PINNED_TMB_MODEL_REQUIRED"' in script
    assert "fit_confirmatory_latency_aft" in script
    aft = (PRODUCER / "latency_aft_tmb.R").read_text(encoding="utf-8")
    assert "TMB::MakeADFun" in aft
    assert 'random = c("operator_effect", "job_effect")' in aft
    assert "selected$censoring_seconds" in aft
    model = (PRODUCER / "src" / "lognormal_aft.cpp").read_text(encoding="utf-8")
    assert "dnorm(log_time, mu, sigma, true) - log_time" in model
    assert "log(pnorm(-standardized))" in model
    assert "operator_effect(operator_index(i))" in model
    assert "job_effect(job_index(i))" in model
    build = (PRODUCER / "build_tmb_model.R").read_text(encoding="utf-8")
    assert 'packageVersion("TMB")) != "1.9.23"' in build
    assert 'TMB::compile("lognormal_aft.cpp", flags = "-O2")' in build
    assert "dll_sha256" in build and "source_sha256" in build


def test_python_project_does_not_gain_in_process_statistical_dependencies():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "scipy" not in pyproject.lower()
    assert "statsmodels" not in pyproject.lower()
