from __future__ import annotations

import argparse
import json

from display_project.detection import GroundingDinoDetector
from display_project.tracking.supervision_zh import render_tracked_video_zh


def main() -> int:
    parser = argparse.ArgumentParser(description="中文 Grounding DINO + supervision 跟踪演示")
    parser.add_argument("--video", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--font", required=True)
    args = parser.parse_args()
    detector = GroundingDinoDetector(args.model, device="cuda")
    result = render_tracked_video_zh(args.video, args.output, detector, ["hand", "screwdriver", "bolt", "metal bar"], font_path=args.font)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
