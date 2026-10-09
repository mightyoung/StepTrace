from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from display_project.data.industreal_partition_revision import (  # noqa: E402
    apply_revision_to_copies,
    build_revision_candidate,
    validate_revision_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--partition", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply-ratified-to-copies", action="store_true")
    parser.add_argument("--split-output", type=Path)
    parser.add_argument("--ledger-output", type=Path)
    parser.add_argument("--applied-summary-output", type=Path)
    args = parser.parse_args()
    partition = json.loads(args.partition.read_text(encoding="utf-8"))
    artifact = build_revision_candidate(partition, args.split, args.ledger)
    valid, reasons = validate_revision_candidate(artifact)
    if not valid:
        raise SystemExit("invalid revision candidate: " + ",".join(reasons))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if args.apply_ratified_to_copies:
        if args.split_output is None or args.ledger_output is None:
            raise SystemExit("--split-output and --ledger-output are required")
        summary = apply_revision_to_copies(
            artifact, args.split, args.ledger, args.split_output, args.ledger_output
        )
        if args.applied_summary_output is not None:
            args.applied_summary_output.parent.mkdir(parents=True, exist_ok=True)
            args.applied_summary_output.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        print(json.dumps(summary, sort_keys=True))
    print(json.dumps({"output": str(args.output), "patches": len(artifact["patches"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
