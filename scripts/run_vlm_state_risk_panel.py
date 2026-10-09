"""Register and run a separate development method panel with complete terminals."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def append_record(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def run_panel(spec_path: Path, output: Path) -> dict:
    from display_project.provenance import sha256_file as sha256
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if spec.get("profile") != "development" or spec.get("formal_admission") is not False:
        raise ValueError("only explicit non-formal development panels are supported")
    methods, jobs = spec["methods"], spec["jobs"]
    if not methods or len(set(methods)) != len(methods) or not jobs:
        raise ValueError("nonempty unique methods and jobs required")
    from display_project.vlm_sop.state_risk_prompt import METHOD_IDS
    if set(methods) - (set(METHOD_IDS) | {"B0-fixed-streak-v1"}):
        raise ValueError("unsupported panel method")
    job_ids = [job["job_id"] for job in jobs]
    if len(set(job_ids)) != len(job_ids) or any(
        not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", value)
        for value in job_ids
    ):
        raise ValueError("job IDs must be unique opaque path-safe identifiers")
    budget = spec["budget"]
    required_budget = {"job_timeout_seconds", "sample_every", "window_frames", "call_timeout_seconds",
                       "max_attempts", "max_backlog_seconds"}
    if set(budget) != required_budget:
        raise ValueError("panel must freeze all runtime budgets")
    for key, value in budget.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 86400:
            raise ValueError(f"invalid bounded budget: {key}")
    if any(not isinstance(budget[key], int) for key in ("sample_every", "window_frames", "max_attempts")):
        raise ValueError("sample/frame/attempt budgets must be integers")
    if budget["window_frames"] > 8 or budget["max_attempts"] > 3:
        raise ValueError("frame/attempt budget exceeds runtime bounds")
    timeout = budget["job_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
        raise ValueError("job timeout must be bounded positive seconds")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("panel output must be empty")
    (output / "spec.json").write_bytes(spec_path.read_bytes())
    registrations = []
    for job in jobs:
        identity = {"job_id": job["job_id"], "model_sha256": job["model_hash"],
                    "source_sha256": None, "knowledge_sha256": None, "perception_sha256": None}
        try:
            identity.update(source_sha256=sha256(Path(job["video"])),
                            knowledge_sha256=sha256(Path(job["knowledge"])),
                            perception_sha256=sha256(Path(job["perception_cache"])) if job.get("perception_cache") else None)
        except OSError as error:
            identity["input_error"] = str(error)
        for method in methods:
            row = dict(identity, method_id=method, status="registered")
            registrations.append(row)
            append_record(output / "ledger.jsonl", row)
    config_hash = hashlib.sha256(json.dumps(budget, sort_keys=True).encode()).hexdigest()
    terminals = []
    for registration in registrations:
        job = next(item for item in jobs if item["job_id"] == registration["job_id"])
        method = registration["method_id"]
        run_dir = output / job["job_id"] / method
        command = [sys.executable, str(Path(__file__).with_name("run_vlm_state_risk.py")),
                   "--knowledge", job["knowledge"], "--video", job["video"],
                   "--model-hash", job["model_hash"], "--config-hash", config_hash,
                   "--output-dir", str(run_dir), "--profile", "development",
                   "--method-id", method, "--sample-every", str(budget["sample_every"]),
                   "--window-frames", str(budget["window_frames"]),
                   "--timeout-seconds", str(budget["call_timeout_seconds"]),
                   "--max-attempts", str(budget["max_attempts"]),
                   "--max-backlog-seconds", str(budget["max_backlog_seconds"]),
                   "--model-name", spec.get("model_name", "Qwen3.5-9B-Q4_K_M")]
        if "endpoint" in spec:
            command += ["--endpoint", spec["endpoint"]]
        if job.get("perception_cache"):
            command += ["--perception-cache", job["perception_cache"]]
        started = time.perf_counter()
        terminal = dict(registration, status="failed")
        log_path = output / f"{job['job_id']}-{method}.log"
        try:
            if registration.get("input_error"):
                raise FileNotFoundError(registration["input_error"])
            if sha256(Path(job["video"])) != registration["source_sha256"] or \
                    sha256(Path(job["knowledge"])) != registration["knowledge_sha256"]:
                raise ValueError("paired input changed after registration")
            if job.get("perception_cache") and sha256(Path(job["perception_cache"])) != registration["perception_sha256"]:
                raise ValueError("paired perception cache changed after registration")
            with log_path.open("wb") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=timeout, check=False)
            terminal.update(status="completed" if result.returncode == 0 else "failed",
                            exit_code=result.returncode)
            receipt_path = run_dir / "manifest.json"
            if result.returncode == 0:
                if not receipt_path.exists():
                    raise ValueError("successful process has no run receipt")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))["terminal"]
                if receipt.get("status") != "completed" or not receipt.get("input_integrity_verified") \
                        or receipt.get("source_sha256") != registration["source_sha256"] \
                        or receipt.get("method_id") != method or receipt.get("model_sha256") != job["model_hash"]:
                    raise ValueError("run receipt does not prove complete stable inputs")
                effective = json.loads((run_dir / "configuration.json").read_text(encoding="utf-8"))
                required_effective = {"method_id": method, "model_sha256": job["model_hash"],
                    "model_name": spec.get("model_name", "Qwen3.5-9B-Q4_K_M"),
                    "knowledge_sha256": registration["knowledge_sha256"],
                    "perception_sha256": registration["perception_sha256"],
                    "declared_config_sha256": config_hash, "sample_every": budget["sample_every"],
                    "window_frames": budget["window_frames"], "timeout_seconds": budget["call_timeout_seconds"],
                    "max_attempts": budget["max_attempts"], "max_backlog_seconds": budget["max_backlog_seconds"]}
                if any(effective.get(key) != value for key, value in required_effective.items()):
                    raise ValueError("effective configuration differs from registered panel")
            effective_config = run_dir / "configuration.json"
            terminal["effective_config_sha256"] = sha256(effective_config) if effective_config.exists() else None
        except subprocess.TimeoutExpired:
            terminal["status"] = "timeout"
        except FileNotFoundError as error:
            terminal.update(status="unavailable", error=str(error))
        except (ValueError, OSError, KeyError) as error:
            terminal.update(status="failed", error=str(error), error_type=type(error).__name__)
        terminal["elapsed_seconds"] = time.perf_counter() - started
        append_record(output / "ledger.jsonl", terminal)
        terminals.append(terminal)
    report = {"profile": "DEVELOPMENT_ONLY", "registered": len(registrations),
              "terminal": len(terminals), "results": terminals,
              "complete_pairing": all(row["status"] == "completed" for row in terminals),
              "mechanism_decision": "NOT_RUN", "formal_admission": False}
    (output / "panel.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_panel(args.spec, args.output_dir)
    print(json.dumps(report))
    return 0 if report["complete_pairing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
