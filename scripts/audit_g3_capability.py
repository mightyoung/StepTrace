from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from display_project.g3_capability import (  # noqa: E402
    build_g3_capability_artifact,
    write_g3_capability,
)


def _result_paths(values: list[str]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for value in values:
        task, separator, raw_path = value.partition("=")
        if not separator or not task or not raw_path or task in paths:
            raise ValueError(
                "--mechanism-result must be a unique TASK_ID=JSON_PATH assignment"
            )
        paths[task] = Path(raw_path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a fail-closed, non-authorizing G3 precheck"
    )
    parser.add_argument("--mechanism-result", action="append", default=[])
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--joint-gate", "--joint-gate-path", dest="joint_gate_path")
    parser.add_argument("--thresholds-manifest")
    parser.add_argument("--operating-point-manifest")
    parser.add_argument("--method-manifest")
    parser.add_argument("--paired-support-manifest")
    parser.add_argument("--ledger-path")
    parser.add_argument("--ledger-anchor-path")
    parser.add_argument("--expected-anchor-hash")
    parser.add_argument("--split-manifest-path")
    parser.add_argument("--paired-metrics-freeze-path")
    parser.add_argument("--scorer-config-hash")
    parser.add_argument("--task-config-hash")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        result_paths = _result_paths(args.mechanism_result)
    except ValueError as exc:
        parser.error(str(exc))
    task_ids = tuple(args.task_id) if len(args.task_id) == 2 else None

    capability = build_g3_capability_artifact(
        result_paths,
        thresholds_manifest_path=args.thresholds_manifest,
        operating_point_manifest_path=args.operating_point_manifest,
        method_manifest_path=args.method_manifest,
        paired_support_manifest_path=args.paired_support_manifest,
        ledger_path=args.ledger_path,
        ledger_anchor_path=args.ledger_anchor_path,
        expected_anchor_hash=args.expected_anchor_hash,
        split_manifest_path=args.split_manifest_path,
        paired_metrics_freeze_path=args.paired_metrics_freeze_path,
        scorer_config_hash=args.scorer_config_hash,
        task_config_hash=args.task_config_hash,
        task_ids=task_ids,
        fixture=args.fixture,
        joint_gate_path=args.joint_gate_path,
    )
    write_g3_capability(args.output, capability)
    print(
        json.dumps(
            {
                "output": str(Path(args.output).resolve()),
                "decision_type": capability["decision_type"],
                "status": capability["status"],
                "artifact_sha256": capability["artifact_sha256"],
                "errors": capability["errors"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
