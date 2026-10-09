from __future__ import annotations

import argparse
import json
from pathlib import Path

from display_project.data.meccano import (
    load_coco_annotations,
    load_hand_annotations,
    load_temporal_annotations,
)
from display_project.video import crop_video, probe_video


def _active_action(actions: list[dict[str, object]], frame: int) -> str:
    for action in actions:
        if int(action["start_frame"]) <= frame <= int(action["end_frame"]):
            return str(action["action_name"])
    return "transition"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare an annotated MECCANO demo clip")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-dir", default="data/meccano/official-demo")
    parser.add_argument("--start-frame", type=int, default=150)
    parser.add_argument("--end-frame", type=int, default=500)
    parser.add_argument("--dataset-root", default="datasets/MECCANO")
    args = parser.parse_args()
    if args.start_frame < 1 or args.end_frame < args.start_frame:
        raise ValueError("MECCANO frame range must be positive and ordered")

    root = Path(args.dataset_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clip_path = output_dir / "video.mp4"
    events_path = output_dir / "events.json"
    metadata = probe_video(args.source)
    crop_video(
        args.source,
        clip_path,
        start_seconds=(args.start_frame - 1) / metadata.fps,
        duration_seconds=(args.end_frame - args.start_frame + 1) / metadata.fps,
    )

    actions = [
        row
        for row in load_temporal_annotations(
            root / "MECCANO_action_annotations/MECCANO_test_actions.csv", kind="action"
        )
        if row["video_id"] == "0009"
    ]
    objects = [
        row
        for row in load_coco_annotations(
            root / "MECCANO_active_objects_annotations/instances_meccano_test.json"
        )
        if row["video_id"] == "09" and args.start_frame <= row["frame"] <= args.end_frame
    ]
    hands = [
        row
        for row in load_hand_annotations(
            root / "MECCANO_hands_bounding_box_annotations/Test/0009.csv",
            image_size=(metadata.width, metadata.height),
        )
        if args.start_frame <= row["frame"] <= args.end_frame and row["is_hand"]
    ]

    events: list[dict[str, object]] = []
    for row in objects:
        x, y, width, height = row["bbox"]
        scale_x = metadata.width / int(row["image_width"])
        scale_y = metadata.height / int(row["image_height"])
        events.append(
            {
                "frame": int(row["frame"]) - args.start_frame,
                "label": f"{row['category_name']} | {_active_action(actions, int(row['frame']))}",
                "bbox": [x * scale_x, y * scale_y, (x + width) * scale_x, (y + height) * scale_y],
                "score": 1.0,
            }
        )
    for row in hands:
        x, y, width, height = row["bbox"]
        events.append(
            {
                "frame": int(row["frame"]) - args.start_frame,
                "label": f"{row['category_name']} | {_active_action(actions, int(row['frame']))}",
                "bbox": [x, y, x + width, y + height],
                "score": 1.0,
            }
        )
    events.sort(key=lambda event: (int(event["frame"]), str(event["label"])))
    events_path.write_text(json.dumps({"events": events}, indent=2), encoding="utf-8")
    print(json.dumps({"video": str(clip_path), "events": str(events_path),
                      "event_count": len(events)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
