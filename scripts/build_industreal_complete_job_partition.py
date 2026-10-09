from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from display_project.data.industreal_complete_job_partition import (  # noqa: E402
    build_candidate_partition,
    validate_candidate_partition,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260821)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8-sig"))
    artifact = build_candidate_partition(registry, seed=args.seed)
    valid, reasons = validate_candidate_partition(artifact)
    if not valid:
        raise SystemExit("invalid candidate partition: " + ",".join(reasons))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": str(args.output), "counts": artifact["counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
