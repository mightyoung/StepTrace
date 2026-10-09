from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from display_project.g3_statistical_input import (
    GATE_MANIFEST_SCHEMA,
    INPUT_SCHEMA,
    canonical_sha256,
    validate_g3_statistical_input,
)


METHODS = ("proposed", "fixed_streak", "hmm_or_crf")
TASKS = ("meccano", "industreal")
BRANCHES = ("A", "B")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate_hash(paths: list[Path]) -> str:
    hashes = sorted({_sha256(path) for path in paths})
    return hashlib.sha256(
        json.dumps(hashes, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_fixture(root: Path, *, dll_sha256: str) -> dict:
    producer = root / "statistics" / "g3-r-producer"
    sources = [
        producer / "produce_g3_statistics_real.R",
        producer / "latency_aft_tmb.R",
        producer / "src" / "lognormal_aft.cpp",
        producer / "build_tmb_model.R",
    ]
    thresholds = [
        {"branch": branch, "task": task, "method": method, "threshold": 0.5}
        for branch in BRANCHES
        for task in TASKS
        for method in METHODS
    ]
    manifest = {
        "schema_version": GATE_MANIFEST_SCHEMA,
        "c_star": 0.7,
        "r_star": 0.2,
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
    for operator in range(1, 19):
        operator_effect = 0.025 * operator
        for task in TASKS:
            task_effect = 0.05 if task == "industreal" else 0.0
            for job in range(1, 4):
                job_effect = 0.09 * (job - 2)
                for branch in BRANCHES:
                    for observation in range(1, 11):
                        for method in METHODS:
                            if branch == "A":
                                definitive_limit = 7
                            else:
                                definitive_limit = {
                                    "proposed": 9,
                                    "fixed_streak": 7,
                                    "hmm_or_crf": 8,
                                }[method]
                            definitive = observation <= definitive_limit
                            advanced = definitive and observation <= 5
                            if branch == "A":
                                false_limit = {
                                    "proposed": 1,
                                    "fixed_streak": 2,
                                    "hmm_or_crf": 2,
                                }[method]
                            else:
                                false_limit = 1
                            false_advance = advanced and observation <= false_limit
                            method_scale = {
                                "proposed": 1.0,
                                "fixed_streak": 1.5,
                                "hmm_or_crf": 1.3,
                            }[method]
                            event_time = method_scale * math.exp(
                                operator_effect
                                + job_effect
                                + task_effect
                                + 0.015 * observation
                            )
                            rows.append(
                                {
                                    "opportunity_id": (
                                        f"opp-{branch}-{task}-{operator}-{job}-{observation}"
                                    ),
                                    "unit_id": f"unit-{task}-{operator}-{job}",
                                    "task": task,
                                    "method": method,
                                    "branch": branch,
                                    "operator_id": f"operator-{operator}",
                                    "complete_job_id": f"job-{task}-{operator}-{job}",
                                    "threshold": 0.5,
                                    "scorable": True,
                                    "definitive": definitive,
                                    "advanced": advanced,
                                    "false_advance": false_advance,
                                    "abstained": not definitive,
                                    "latency_seconds": event_time if definitive else None,
                                    "censoring_seconds": None if definitive else event_time * 0.9,
                                    "right_censored": not definitive,
                                    "run_failure": False,
                                }
                            )
    value = {
        "schema_version": INPUT_SCHEMA,
        "producer": {
            "fixture": False,
            "real_input_enabled": True,
            "code_sha256": _aggregate_hash(sources),
            "environment_sha256": _sha256(producer / "environment-lock.json"),
            "runtime_asset_sha256": {"aft_dll": dll_sha256},
        },
        "binding": {
            "admission_receipt_id": "development:synthetic-full-producer-replay",
            "admission_receipt_sha256": "a" * 64,
            "evidence_bundle_sha256": "b" * 64,
        },
        "resampling": {
            "requested_replicates": 10_000,
            "seed": 20260821,
            "cluster_level": "operator",
        },
        "gate_manifest": manifest,
        "opportunities": rows,
    }
    valid, reasons = validate_g3_statistical_input(value)
    if not valid:
        raise ValueError(f"fixture failed its own contract: {reasons}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).parents[1]
    value = build_fixture(root, dll_sha256=args.dll_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"rows": len(value["opportunities"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
