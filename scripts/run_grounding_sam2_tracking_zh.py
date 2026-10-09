from __future__ import annotations

import argparse
import json

from display_project.detection import GroundingDinoDetector
from display_project.segmentation import Sam2BoxSegmenter
from display_project.tracking.supervision_sam2_zh import (
    render_segmented_tracked_video_zh,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="中文 Grounding DINO + SAM2.1 + ByteTrack 分割跟踪演示"
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--dino-model", required=True)
    parser.add_argument("--sam-model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--font", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--box-threshold", type=float, default=0.28)
    parser.add_argument("--text-threshold", type=float, default=0.20)
    args = parser.parse_args()

    detector = GroundingDinoDetector(args.dino_model, device=args.device)
    segmenter = Sam2BoxSegmenter(args.sam_model, device=args.device)
    result = render_segmented_tracked_video_zh(
        args.video,
        args.output,
        detector,
        segmenter,
        ["hand", "screwdriver", "bolt", "metal bar"],
        font_path=args.font,
        box_threshold=args.box_threshold,
        text_threshold=args.text_threshold,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
