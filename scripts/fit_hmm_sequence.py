"""Fit the development-only HMM artifact, or emit a machine-readable blocker."""

from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from display_project.experiments.hmm_fit import fit_hmm_jsonl


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocker-output", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-config-hash", required=True)
    parser.add_argument("--partition-manifest", type=Path, required=True)
    parser.add_argument("--states", nargs="+", required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.blocker_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        if args.input_jsonl is None or not args.input_jsonl.is_file():
            raise ValueError(
                "HMM_FIT_INPUT_MISSING: pre-extracted DEVELOPMENT observations are required"
            )
        fit_hmm_jsonl(
            args.input_jsonl,
            args.output,
            task_id=args.task_id,
            task_config_hash=args.task_config_hash,
            partition_manifest_hash=_hash(args.partition_manifest),
            states=args.states,
            fit_code_hash=_hash(
                Path(__file__).resolve().parents[1]
                / "src/display_project/experiments/hmm_fit.py"
            ),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        args.blocker_output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "BLOCKED",
                    "error_code": str(exc).split(":", 1)[0],
                    "reason": str(exc),
                    "project_split": "DEVELOPMENT",
                    "formal_use_allowed": False,
                    "artifact_created": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        args.output.unlink(missing_ok=True)
        return 2
    payload = json.loads(args.output.read_text(encoding="utf-8"))
    args.blocker_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "READY",
                "artifact_hash": payload["artifact_hash"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
