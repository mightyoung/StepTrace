from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from display_project.data.meccano import load_temporal_annotations
from display_project.localization import action_zh, decision_zh, step_zh, verdict_zh
from display_project.sop.demo_mapping import DEMO_STEPS
from display_project.sop.state_machine import Evidence, SOPStateMachine


def active_action(actions: list[dict[str, object]], frame: int) -> str | None:
    return next(
        (
            str(item["action_name"])
            for item in actions
            if int(item["start_frame"]) <= frame <= int(item["end_frame"])
        ),
        None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SOP review overlay (audit-only)",
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--actions", required=True)
    parser.add_argument("--vlm-reviews", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--font", required=True)
    args = parser.parse_args()
    import supervision as sv

    font_title, font_body = (
        ImageFont.truetype(args.font, 32),
        ImageFont.truetype(args.font, 26),
    )
    actions = [
        row
        for row in load_temporal_annotations(args.actions, kind="action")
        if row["video_id"] == "0009"
    ]
    reviews = {
        int(item["clip_frame"]): item
        for item in json.loads(Path(args.vlm_reviews).read_text(encoding="utf-8"))[
            "reviews"
        ]
    }
    capture = cv2.VideoCapture(args.video)
    fps, width, height = (
        capture.get(cv2.CAP_PROP_FPS),
        int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )
    writer = cv2.VideoWriter(
        args.output, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    machine, box_annotator = SOPStateMachine(DEMO_STEPS), sv.BoxAnnotator(thickness=4)
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            original_frame = 150 + frame_index
            action = active_action(actions, original_frame)
            review = reviews.get(frame_index)
            # Annotation and VLM outputs are audit-only; neither can advance state.
            transition = machine.observe(Evidence())
            decision_id = {"normal": 0, "deviation": 1, "unknown": 2}[
                transition.decision
            ]
            panel = sv.Detections(
                xyxy=np.asarray([[20, 20, 960, 180]], dtype=np.float32),
                class_id=np.asarray([decision_id]),
            )
            annotated = box_annotator.annotate(frame.copy(), panel)
            image = Image.fromarray(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(image, "RGBA")
            draw.rectangle((24, 24, 956, 176), fill=(10, 10, 10, 205))
            next_step = (
                "complete"
                if transition.expected_step is None
                else step_zh(transition.expected_step)
            )
            draw.text(
                (40, 32),
                f"SOP: {decision_zh(transition.decision)}",
                font=font_title,
                fill=(255, 220, 80, 255),
            )
            draw.text(
                (40, 78),
                f"Current: {step_zh(transition.current_step)}  Next: {next_step}",
                font=font_body,
                fill=(255, 255, 255, 255),
            )
            draw.text(
                (40, 118),
                f"Action: {action_zh(action)}",
                font=font_body,
                fill=(220, 240, 255, 255),
            )
            if review:
                draw.text(
                    (540, 118),
                    f"Qwen review: {verdict_zh(review['verdict'])} ({review['confidence']:.2f})",
                    font=font_body,
                    fill=(100, 240, 255, 255),
                )
            writer.write(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))
            frame_index += 1
    finally:
        capture.release()
        writer.release()
    print(
        json.dumps(
            {
                "output": args.output,
                "frames": frame_index,
                "complete": machine.is_complete,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
