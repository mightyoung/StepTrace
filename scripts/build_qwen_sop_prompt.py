from __future__ import annotations

import argparse
import json
from pathlib import Path

from display_project.vlm_sop import (
    build_qwen_sop_review_request,
    load_sop_knowledge,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a truth-free, audit-only Qwen SOP review request"
    )
    parser.add_argument("--knowledge-pack", required=True)
    parser.add_argument("--clip-frame", required=True, type=int)
    parser.add_argument("--expected-step", required=True)
    parser.add_argument("--current-step")
    parser.add_argument(
        "--perception-summary",
        help="optional JSON object containing detector/track observations only",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    perception = None
    if args.perception_summary:
        perception = json.loads(
            Path(args.perception_summary).read_text(encoding="utf-8")
        )
        if not isinstance(perception, dict):
            parser.error("--perception-summary must contain a JSON object")
    request = build_qwen_sop_review_request(
        load_sop_knowledge(args.knowledge_pack),
        clip_frame=args.clip_frame,
        expected_step=args.expected_step,
        current_step=args.current_step,
        perception_summary=perception,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
