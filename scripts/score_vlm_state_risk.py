from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

def main() -> int:
    parser = argparse.ArgumentParser(description="Offline DEVELOPMENT_ONLY VLM state-risk scorer")
    parser.add_argument("--truth", required=True, type=Path, help="isolated scoring truth JSON; never runtime input")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--predictions", type=Path)
    input_group.add_argument("--run-dir", type=Path,
                             help="causal runner output directory; adapted offline")
    parser.add_argument("--job-id", help="fixed truth job represented by --run-dir")
    parser.add_argument("--method-id", help="must equal manifest terminal method_id for --run-dir")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    from display_project.metrics.state_risk import (
        DEVELOPMENT_ONLY,
        canonical_json_bytes,
        predictions_from_run_dir,
        score_state_risk,
        sha256_file,
    )

    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    run_metadata = None
    if args.run_dir is not None:
        if not args.job_id or not args.method_id:
            parser.error("--run-dir requires --job-id and --method-id")
        predictions, run_metadata = predictions_from_run_dir(
            truth, args.run_dir, job_id=args.job_id, method_id=args.method_id
        )
    else:
        if args.job_id or args.method_id:
            parser.error("--job-id and --method-id require --run-dir")
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    result = score_state_risk(truth, predictions)
    result["input_hashes"] = {"truth_sha256": sha256_file(args.truth)}
    if run_metadata is None:
        result["input_hashes"]["predictions_sha256"] = sha256_file(args.predictions)
    else:
        result["input_hashes"].update(run_metadata)
    result["output_payload_sha256"] = sha256(canonical_json_bytes(result)).hexdigest()
    result["status"] = DEVELOPMENT_ONLY
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
