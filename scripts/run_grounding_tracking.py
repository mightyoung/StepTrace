from __future__ import annotations

import argparse
import json

from display_project.detection import GroundingDinoDetector
from display_project.tracking import render_tracked_video


def main() -> int:
    parser = argparse.ArgumentParser(description="Grounding DINO + supervision tracking demo")
    parser.add_argument("--video", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--classes", nargs="+", required=True)
    parser.add_argument("--box-threshold", type=float, default=0.35)
    parser.add_argument("--text-threshold", type=float, default=0.25)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    detector = GroundingDinoDetector(args.model, device=args.device)
    result = render_tracked_video(args.video, args.output, detector, args.classes, box_threshold=args.box_threshold, text_threshold=args.text_threshold)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
