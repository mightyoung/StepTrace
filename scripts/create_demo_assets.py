from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description="Create deterministic SOP demo assets")
    parser.add_argument("--output-dir", default="data/meccano/subset")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / "video.mp4"
    events_path = output_dir / "events.json"

    width, height, fps = 1280, 720, 15
    total_frames = args.seconds * fps
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot create {video_path}")

    events: list[dict[str, object]] = []
    steps = [
        ("STEP 1/3  Pick bracket", "bracket", (480, 300, 650, 430)),
        ("STEP 2/3  Align bolt", "bolt", (650, 320, 735, 405)),
        ("STEP 3/3  Tighten", "screwdriver", (720, 210, 930, 390)),
    ]
    segment = max(1, total_frames // len(steps))

    try:
        for frame_index in range(total_frames):
            step_index = min(frame_index // segment, len(steps) - 1)
            step_text, label, box = steps[step_index]
            frame = np.full((height, width, 3), (34, 39, 45), dtype=np.uint8)
            cv2.rectangle(frame, (120, 120), (1160, 650), (75, 82, 88), -1)
            cv2.rectangle(frame, (380, 260), (900, 500), (115, 120, 125), -1)
            cv2.putText(frame, "MECCANO SOP COMPLIANCE - BASELINE", (70, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.15, (235, 235, 235), 2)
            cv2.putText(frame, step_text, (70, 690), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (80, 230, 255), 2)
            cv2.circle(frame, (600, 380), 55, (75, 170, 230), -1)
            cv2.rectangle(frame, (700, 345), (840, 415), (170, 170, 180), -1)

            x1, y1, x2, y2 = box
            drift = int(12 * np.sin(frame_index / 8.0))
            tracked_box = [x1 + drift, y1, x2 + drift, y2]
            events.append(
                {
                    "frame": frame_index,
                    "label": f"{label} | compliant",
                    "bbox": tracked_box,
                    "score": round(0.91 + 0.06 * (frame_index % 7) / 6, 2),
                }
            )
            writer.write(frame)
    finally:
        writer.release()

    events_path.write_text(
        json.dumps({"events": events}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"video": str(video_path), "events": str(events_path),
                      "frames": total_frames}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
