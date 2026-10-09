from __future__ import annotations

import argparse
import json
from display_project.provenance import composite_model_hash
from display_project.provenance import compiled_task_hash
from display_project.risk import RiskCalibrator

from display_project.detection import GroundingDinoDetector
from display_project.segmentation import Sam2BoxSegmenter
from display_project.tracking.real_sop_zh import render_real_sop_video_zh
from display_project.sop.task_config import load_task_config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grounding DINO + SAM2.1 real SOP detection"
    )
    parser.add_argument("--video", required=True)
    parser.add_argument(
        "--task-config", required=True, help="compiled task configuration path"
    )
    parser.add_argument("--dino-model", required=True)
    parser.add_argument("--sam-model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--events-output", required=True)
    parser.add_argument("--font", required=True)
    parser.add_argument("--vlm-reviews")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--confirm-frames", type=int, default=4)
    parser.add_argument("--cooldown-frames", type=int, default=12)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument(
        "--decision-policy",
        choices=("fixed_streak", "proposed", "uncalibrated_certificate"),
        default="fixed_streak",
    )
    parser.add_argument("--calibration-artifact")
    parser.add_argument("--raw-risk-threshold", type=float)
    args = parser.parse_args()

    if args.decision_policy == "proposed" and not args.calibration_artifact:
        parser.error("--calibration-artifact is required for proposed")
    if (
        args.decision_policy == "uncalibrated_certificate"
        and args.raw_risk_threshold is None
    ):
        parser.error(
            "--raw-risk-threshold is required for uncalibrated_certificate"
        )

    detector = GroundingDinoDetector(args.dino_model, device=args.device)
    segmenter = Sam2BoxSegmenter(args.sam_model, device=args.device)
    task = load_task_config(args.task_config)
    calibrator = (
        RiskCalibrator.from_artifact(args.calibration_artifact)
        if args.calibration_artifact
        else None
    )
    result = render_real_sop_video_zh(
        args.video,
        args.output,
        args.events_output,
        detector,
        segmenter,
        font_path=args.font,
        vlm_reviews=args.vlm_reviews,
        confirm_frames=args.confirm_frames,
        cooldown_frames=args.cooldown_frames,
        max_frames=args.max_frames,
        task_config=task,
        risk_calibrator=calibrator,
        calibration_version=(
            calibrator.calibration_version if calibrator is not None else None
        ),
        risk_threshold=calibrator.risk_threshold if calibrator is not None else None,
        model_hash=composite_model_hash(args.dino_model, args.sam_model),
        config_hash=compiled_task_hash(task),
        decision_policy=args.decision_policy,
        raw_risk_threshold=args.raw_risk_threshold,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
