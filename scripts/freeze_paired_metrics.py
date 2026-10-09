"""Freeze development paired metrics from a runner ledger and support grid."""

from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from display_project.experiments.paired_runner import verify_ledger_integrity
from display_project.gate_audit import verify_joint_gate_permission
from scripts.ledger_anchor import validate_anchor
from display_project.metrics.paired_freeze import (
    freeze_paired_metrics,
    write_freeze_artifact,
)

if __package__:
    from scripts.validate_experiment_ledger import validate as validate_ledger
else:
    from validate_experiment_ledger import validate as validate_ledger  # type: ignore[no-redef]


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("ledger row must be an object")
            rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", required=True, type=Path)
    p.add_argument("--operating-points", required=True, type=Path)
    p.add_argument("--input-ledger-hash", required=True)
    p.add_argument("--anchor", required=True, type=Path)
    p.add_argument("--expected-anchor-hash", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--profile", default="development")
    p.add_argument("--branch", default="A_FSAR_AT_MATCHED_COVERAGE")
    p.add_argument("--target", type=float, default=0.5)
    p.add_argument("--delta-c", type=float, default=0.0)
    p.add_argument("--delta-r", type=float, default=0.0)
    p.add_argument("--min-support-width", type=float, default=0.0)
    p.add_argument("--min-coverage", type=float, default=0.5)
    p.add_argument("--min-units-per-task", type=int, default=1)
    p.add_argument(
        "--joint-gate",
        type=Path,
        default=Path("research/audits/g1-g2-joint-gate.json"),
    )
    args = p.parse_args(argv)
    try:
        raw_ledger = args.ledger.read_bytes()
        actual_ledger_hash = hashlib.sha256(raw_ledger).hexdigest()
        if args.input_ledger_hash != actual_ledger_hash:
            raise ValueError("input ledger hash does not match ledger bytes")
        anchor_errors = validate_anchor(
            args.ledger,
            args.anchor,
            expected_anchor_hash=args.expected_anchor_hash,
        )
        if anchor_errors:
            raise ValueError(
                "ledger anchor validation failed: " + "; ".join(anchor_errors[:3])
            )
        integrity_errors, _tail = verify_ledger_integrity(raw_ledger)
        if integrity_errors:
            raise ValueError(
                "ledger integrity failed: " + "; ".join(integrity_errors[:3])
            )
        semantic_errors = validate_ledger(args.ledger)
        if semantic_errors:
            raise ValueError(
                "ledger semantic validation failed: " + "; ".join(semantic_errors[:3])
            )
        points = json.loads(args.operating_points.read_text(encoding="utf-8"))
        if not isinstance(points, dict):
            raise ValueError("operating points must be an object")
        verify_joint_gate_permission(
            args.joint_gate,
            permission="freeze_paired_metrics_allowed",
        )
        artifact = freeze_paired_metrics(
            _jsonl(args.ledger),
            input_ledger_hash=actual_ledger_hash,
            operating_points=points,
            profile=args.profile,
            branch=args.branch,
            target=args.target,
            delta_c=args.delta_c,
            delta_r=args.delta_r,
            min_support_width=args.min_support_width,
            min_coverage=args.min_coverage,
            min_units_per_task=args.min_units_per_task,
        )
        write_freeze_artifact(artifact, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "FROZEN_DEVELOPMENT_ONLY", "sha256": artifact.sha256},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
