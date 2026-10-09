from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from display_project.g3_statistics import validate_statistical_result
from display_project.g3_statistical_input import (
    GATE_MANIFEST_SCHEMA,
    INPUT_SCHEMA,
    canonical_sha256,
    validate_g3_statistical_input,
)


ROOT = Path(__file__).parents[1]
PRODUCER = ROOT / "statistics" / "g3-r-producer" / "produce_g3_statistics.R"
REAL_PRODUCER = (
    ROOT / "statistics" / "g3-r-producer" / "produce_g3_statistics_real.R"
)


def _rscript() -> Path:
    configured = os.environ.get("DISPLAY_PROJECT_RSCRIPT")
    local = ROOT / ".runtime" / "R-4.6.1" / "bin" / "Rscript.exe"
    candidate = Path(configured) if configured else local
    if candidate.is_file():
        return candidate
    found = shutil.which("Rscript")
    if found:
        return Path(found)
    pytest.skip("isolated Rscript is unavailable")


def _fixture() -> dict:
    events = []
    methods = ("proposed", "fixed_streak", "hmm_or_crf")
    for operator in range(1, 13):
        for task in ("meccano", "industreal"):
            for job in range(1, 4):
                for method in methods:
                    for observation in range(1, 11):
                        threshold = {
                            "proposed": 2,
                            "hmm_or_crf": 3,
                            "fixed_streak": 4,
                        }[method]
                        threshold += (operator % 3) - 1
                        score = (
                            observation + job + int(task == "industreal")
                        ) % 10
                        events.append(
                            {
                                "false_advance": int(score < threshold),
                                "method": method,
                                "task": task,
                                "operator_id": f"op-{operator}",
                                "complete_job_id": f"op-{operator}-{task}-{job}",
                            }
                        )
    return {
        "producer": {"fixture": True, "environment_sha256": "b" * 64},
        "binding": {
            "admission_receipt_id": "fixture-receipt",
            "admission_receipt_sha256": "d" * 64,
            "evidence_bundle_sha256": "c" * 64,
        },
        "resampling": {"requested_replicates": 10000, "seed": 20260812},
        "events": events,
        "task_directions": {
            "A": {"meccano": "favorable", "industreal": "favorable"},
            "B": {"meccano": "favorable", "industreal": "favorable"},
        },
        "branch_B": {
            "complete": True,
            "adjusted_p_values": {"fixed_streak": 0.01, "hmm_or_crf": 0.02},
        },
        "common_support": {
            "complete": True,
            "lower": 0.4,
            "upper": 0.8,
            "minimum_width": 0.1,
        },
        "endpoints": {
            "fsar": {},
            "coverage": {},
            "latency": {},
            "failures": {},
            "abstention": {},
            "exclusions": {},
        },
    }


def _real_input() -> dict:
    methods = ("proposed", "fixed_streak", "hmm_or_crf")
    tasks = ("meccano", "industreal")
    branches = ("A", "B")
    thresholds = [
        {"branch": branch, "task": task, "method": method, "threshold": 0.5}
        for branch in branches
        for task in tasks
        for method in methods
    ]
    manifest = {
        "schema_version": GATE_MANIFEST_SCHEMA,
        "c_star": 0.7,
        "r_star": 0.1,
        "delta_c": 0.05,
        "delta_r": 0.05,
        "minimum_coverage": 0.5,
        "maximum_latency_seconds": 5.0,
        "common_support_minimum_width": 0.1,
        "common_support_lower": 0.5,
        "common_support_upper": 0.9,
        "maximum_differential_failure": 0.05,
        "selected_thresholds": thresholds,
    }
    manifest["artifact_sha256"] = canonical_sha256(manifest)
    rows = []
    for operator in range(1, 13):
        for task in tasks:
            for branch in branches:
                for observation in range(1, 11):
                    for method in methods:
                        definitive_limit = 7 if branch == "A" else {
                            "proposed": 9,
                            "fixed_streak": 7,
                            "hmm_or_crf": 8,
                        }[method]
                        definitive = observation <= definitive_limit
                        advanced = definitive and observation <= 5
                        false_limit = {
                            "proposed": 1,
                            "fixed_streak": 2,
                            "hmm_or_crf": 2,
                        }[method]
                        false_advance = advanced and observation <= false_limit
                        rows.append(
                            {
                                "opportunity_id": f"opp-{branch}-{task}-{operator}-{observation}",
                                "unit_id": f"unit-{task}-{operator}",
                                "task": task,
                                "method": method,
                                "branch": branch,
                                "operator_id": f"operator-{operator}",
                                "complete_job_id": f"job-{task}-{operator}",
                                "threshold": 0.5,
                                "scorable": True,
                                "definitive": definitive,
                                "advanced": advanced,
                                "false_advance": false_advance,
                                "abstained": not definitive,
                                "latency_seconds": 1.0 if definitive else None,
                                "censoring_seconds": None if definitive else 5.0,
                                "right_censored": not definitive,
                                "run_failure": False,
                            }
                        )
    return {
        "schema_version": INPUT_SCHEMA,
        "producer": {
            "fixture": False,
            "real_input_enabled": True,
            "code_sha256": hashlib.sha256(REAL_PRODUCER.read_bytes()).hexdigest(),
            "environment_sha256": "b" * 64,
            "runtime_asset_sha256": {"aft_dll": "f" * 64},
        },
        "binding": {
            "admission_receipt_id": "candidate-receipt",
            "admission_receipt_sha256": "d" * 64,
            "evidence_bundle_sha256": "c" * 64,
        },
        "resampling": {
            "requested_replicates": 10000,
            "seed": 20260820,
            "cluster_level": "operator",
        },
        "gate_manifest": manifest,
        "opportunities": rows,
    }


def test_isolated_r_producer_emits_consumer_valid_fixture(tmp_path):
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(_fixture()), encoding="utf-8", newline="\n")
    result = subprocess.run(
        [
            str(_rscript()),
            "--vanilla",
            str(PRODUCER),
            "--input",
            str(input_path),
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    valid, reasons = validate_statistical_result(
        payload,
        allow_fixture=True,
        expected_producer_id="g3-primary-r",
        expected_producer_version="1.0.0",
        expected_runtime="R 4.6.1",
        expected_code_sha256=hashlib.sha256(PRODUCER.read_bytes()).hexdigest(),
        expected_environment_sha256="b" * 64,
        expected_receipt_id="fixture-receipt",
        expected_receipt_sha256="d" * 64,
        expected_evidence_bundle_sha256="c" * 64,
    )
    assert valid is True, reasons


def test_isolated_r_producer_refuses_real_input(tmp_path):
    fixture = _fixture()
    fixture["producer"]["fixture"] = False
    input_path = tmp_path / "real-input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8", newline="\n")
    result = subprocess.run(
        [
            str(_rscript()),
            "--vanilla",
            str(PRODUCER),
            "--input",
            str(input_path),
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "STATISTICAL_ENGINE_MISSING" in result.stderr
    assert not output_path.exists()


def test_real_candidate_derives_both_branches_from_raw_opportunities(tmp_path):
    payload = _real_input()
    input_valid, input_reasons = validate_g3_statistical_input(payload)
    assert input_valid, input_reasons
    input_path = tmp_path / "real-statistical-input.json"
    output_path = tmp_path / "real-statistical-output.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    result = subprocess.run(
        [
            str(_rscript()),
            "--vanilla",
            str(REAL_PRODUCER),
            "--input",
            str(input_path),
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    produced = json.loads(output_path.read_text(encoding="utf-8"))
    valid, reasons = validate_statistical_result(
        produced,
        allow_fixture=False,
        expected_producer_id="g3-primary-r-real-candidate",
        expected_producer_version="0.2.0",
        expected_runtime="R 4.6.1",
        expected_code_sha256=hashlib.sha256(REAL_PRODUCER.read_bytes()).hexdigest(),
        expected_environment_sha256="b" * 64,
        expected_receipt_id="candidate-receipt",
        expected_receipt_sha256="d" * 64,
        expected_evidence_bundle_sha256="c" * 64,
        expected_runtime_asset_sha256={"aft_dll": "f" * 64},
    )
    assert valid is False
    assert reasons == ["confirmatory_latency_model"]
    assert produced["branches"]["A"]["confirmatory_latency_model_complete"] is False
    assert produced["branches"]["B"]["confirmatory_latency_model_complete"] is False
