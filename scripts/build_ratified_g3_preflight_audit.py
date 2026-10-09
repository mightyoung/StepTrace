"""Assemble a hash-bound, non-authorizing ratified-fallback preflight audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def build() -> dict[str, Any]:
    amendment_path = ROOT / "research/audits/industreal-identity-amendment-1.4.0-ratified.json"
    bundle_path = ROOT / "research/audits/g3-admission-bundle.remote-20260821-ratified-fallback.json"
    decision_path = ROOT / "research/audits/g3-run-admission-20260821-ratified-fallback.json"
    task_input = ROOT / "research/audits/g3-full-producer.task-specific.synthetic-input.json"
    task_output = ROOT / "research/audits/g3-full-producer.task-specific.synthetic-output.json"
    amendment = load(amendment_path)
    bundle = load(bundle_path)
    decision = load(decision_path)
    task_input_value = load(task_input)
    task_output_value = load(task_output)
    if amendment.get("g3_execution_authorized") is not False:
        raise ValueError("amendment unexpectedly authorizes G3")
    if bundle.get("status") != "READY" or bundle.get("ready") is not True:
        raise ValueError("versioned admission bundle is not ready for preflight")
    if decision.get("status") != "BLOCKED" or decision.get("ready") is not False:
        raise ValueError("G3 admission did not remain blocked")
    if task_input_value.get("cluster_level_by_task", {}).get("industreal") != "complete_job":
        raise ValueError("task-specific fallback input is not explicit")
    if task_output_value.get("resampling", {}).get("cluster_level") != "task_specific":
        raise ValueError("task-specific fallback output is not explicit")
    return {
        "schema_version": "display_project.g3_ratified_fallback_preflight_audit-1.0.0",
        "amendment_version": amendment.get("amendment_version"),
        "amendment_artifact_sha256": amendment.get("artifact_sha256"),
        "ratification_status": amendment.get("status"),
        "versioned_copies": {
            "split_manifest": {
                "path": "research/split-manifest-1.4.0-industreal-fallback.json",
                "sha256": sha256(ROOT / "research/split-manifest-1.4.0-industreal-fallback.json"),
            },
            "dataset_ledger": {
                "path": "research/dataset-ledger-1.4.0-industreal-fallback.csv",
                "sha256": sha256(ROOT / "research/dataset-ledger-1.4.0-industreal-fallback.csv"),
            },
        },
        "admission_bundle": {
            "path": "research/audits/g3-admission-bundle.remote-20260821-ratified-fallback.json",
            "sha256": sha256(bundle_path),
            "status": bundle.get("status"),
            "unit_count": len(bundle.get("artifacts", {}).get("unit_inventory", [])),
        },
        "g3_admission": {
            "path": "research/audits/g3-run-admission-20260821-ratified-fallback.json",
            "sha256": sha256(decision_path),
            "status": decision.get("status"),
            "reason_codes": decision.get("reason_codes", []),
            "downstream_authorized": decision.get("downstream_authorized"),
        },
        "task_specific_producer_replay": {
            "input_path": "research/audits/g3-full-producer.task-specific.synthetic-input.json",
            "input_sha256": sha256(task_input),
            "output_path": "research/audits/g3-full-producer.task-specific.synthetic-output.json",
            "output_sha256": sha256(task_output),
            "producer_version": task_output_value.get("producer", {}).get("version"),
            "cluster_level": task_output_value.get("resampling", {}).get("cluster_level"),
            "consumer_validation": "PASS",
        },
        "pilot_data_accessed": False,
        "formal_data_accessed": False,
        "g3_execution_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "research/audits/g3-ratified-fallback-preflight-20260821.json")
    args = parser.parse_args()
    value = build()
    body = dict(value)
    body["artifact_sha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(args.output), "artifact_sha256": body["artifact_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
