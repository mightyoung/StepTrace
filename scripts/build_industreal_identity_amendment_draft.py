from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from display_project.data.protocol_amendment import (  # noqa: E402
    build_industreal_identity_amendment,
    validate_amendment_draft,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim-freeze", type=Path, default=ROOT / "research/claim-freeze.yaml")
    parser.add_argument("--analysis-plan", type=Path, default=ROOT / "research/analysis-plan.md")
    parser.add_argument("--partition", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--capability", type=Path, default=ROOT / "research/audits/industreal-capability.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = build_industreal_identity_amendment(
        claim_freeze_path=args.claim_freeze,
        analysis_plan_path=args.analysis_plan,
        partition_candidate_path=args.partition,
        revision_candidate_path=args.revision,
        capability_path=args.capability,
    )
    valid, reasons = validate_amendment_draft(artifact)
    if not valid:
        raise SystemExit("invalid amendment draft: " + ",".join(reasons))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(args.output), "status": artifact["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
