from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from display_project.data.protocol_amendment import (  # noqa: E402
    ratify_industreal_identity_amendment,
    validate_ratified_amendment,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--applied-summary", type=Path, required=True)
    parser.add_argument("--ratified-on", default="2026-08-21")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    draft = json.loads(args.draft.read_text(encoding="utf-8"))
    summary = json.loads(args.applied_summary.read_text(encoding="utf-8"))
    artifact = ratify_industreal_identity_amendment(
        draft,
        ratified_on=args.ratified_on,
        applied_outputs=summary,
    )
    valid, reasons = validate_ratified_amendment(artifact)
    if not valid:
        raise SystemExit("invalid ratified amendment: " + ",".join(reasons))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": str(args.output), "status": artifact["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
