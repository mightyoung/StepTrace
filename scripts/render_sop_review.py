from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from display_project.data.meccano import load_temporal_annotations
from display_project.sop.demo_mapping import DEMO_STEPS
from display_project.sop.state_machine import Evidence, SOPStateMachine


def active_action(actions: list[dict[str, object]], frame: int) -> str | None:
    for action in actions:
        if int(action["start_frame"]) <= frame <= int(action["end_frame"]):
            return str(action["action_name"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render SOP state and VLM review overlay"
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--actions", required=True)
    parser.add_argument("--vlm-reviews", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--events-output", required=True)
    parser.add_argument("--source-start-frame", type=int, default=150)
    args = parser.parse_args()

    import supervision as sv

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
    fps = capture.get(cv2.CAP_PROP_FPS)
    width, height = (
        int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )
    writer = cv2.VideoWriter(
        args.output, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    machine = SOPStateMachine(DEMO_STEPS)
    box_annotator = sv.BoxAnnotator(thickness=4)
    label_annotator = sv.LabelAnnotator(text_scale=0.75, text_thickness=2)
    events: list[dict[str, object]] = []
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            original_frame = args.source_start_frame + frame_index
            action = active_action(actions, original_frame)
            # Action annotations are development-only and have no runtime authority.\n            step = None
            review = reviews.get(frame_index)
            transition = machine.observe(
                Evidence(
                    deterministic_step=None,
                    vlm_step=review.get("observed_action") if review else None,
                )
            )
            decision_id = {"normal": 0, "deviation": 1, "unknown": 2}[
                transition.decision
            ]
            panel = sv.Detections(
                xyxy=np.asarray([[20, 20, 900, 165]], dtype=np.float32),
                class_id=np.asarray([decision_id]),
            )
            label = f"SOP {transition.decision.upper()} | current: {transition.current_step or 'not_started'} | next: {transition.expected_step or 'complete'}"
            annotated = box_annotator.annotate(frame.copy(), panel)
            annotated = label_annotator.annotate(annotated, panel, labels=[label])
            cv2.putText(
                annotated,
                f"Action: {action or 'insufficient evidence'}",
                (35, 115),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.72,
                (255, 255, 255),
                2,
            )
            if review:
                cv2.putText(
                    annotated,
                    f"Qwen review: {review['verdict']} ({review['confidence']:.2f})",
                    (35, 150),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.68,
                    (80, 230, 255),
                    2,
                )
            writer.write(annotated)
            events.append(
                {
                    "frame": frame_index,
                    "original_frame": original_frame,
                    "action": action,
                    "decision": transition.decision,
                    "current_step": transition.current_step,
                    "expected_step": transition.expected_step,
                    "reason": transition.reason,
                    "vlm_review": review,
                }
            )
            frame_index += 1
    finally:
        capture.release()
        writer.release()
    Path(args.events_output).write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": args.output,
                "frames": frame_index,
                "events": len(events),
                "complete": machine.is_complete,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
