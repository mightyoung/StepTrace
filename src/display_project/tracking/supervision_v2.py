from __future__ import annotations

from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from display_project.detection import GroundingDinoDetector, normalize_prompt


def class_index(label: str, classes: Sequence[str]) -> int:
    normalized = label.casefold()
    for index, candidate in enumerate(classes):
        if candidate.casefold() in normalized:
            return index
    return 0


def render_tracked_video_v2(source: str | Path, output: str | Path, detector: GroundingDinoDetector, classes: Sequence[str], *, box_threshold: float = 0.35, text_threshold: float = 0.25) -> dict[str, object]:
    import supervision as sv

    class_names = normalize_prompt(classes)
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_info = sv.VideoInfo.from_video_path(str(source_path))
    tracker = sv.ByteTrack(frame_rate=round(video_info.fps))
    box_annotator = sv.BoxAnnotator(color_lookup=sv.ColorLookup.TRACK, thickness=3)
    label_annotator = sv.LabelAnnotator(color_lookup=sv.ColorLookup.TRACK)
    trace_annotator = sv.TraceAnnotator(color_lookup=sv.ColorLookup.TRACK, trace_length=40)
    capture = cv2.VideoCapture(str(source_path))
    frames = detections_total = tracked_total = 0
    try:
        with sv.VideoSink(str(output_path), video_info=video_info) as sink:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                grounded = detector.detect(frame, class_names, box_threshold=box_threshold, text_threshold=text_threshold)
                detections = sv.Detections(
                    xyxy=grounded.xyxy,
                    confidence=grounded.confidence,
                    class_id=np.asarray([class_index(label, class_names) for label in grounded.labels], dtype=int),
                )
                detections = tracker.update_with_detections(detections)
                labels = [f"#{track_id} {class_names[class_id]} {confidence:.2f}" for track_id, class_id, confidence in zip(detections.tracker_id, detections.class_id, detections.confidence)]
                annotated = trace_annotator.annotate(frame.copy(), detections)
                annotated = box_annotator.annotate(annotated, detections)
                annotated = label_annotator.annotate(annotated, detections, labels=labels)
                sink.write_frame(annotated)
                frames += 1
                detections_total += len(grounded.labels)
                tracked_total += len(detections)
    finally:
        capture.release()
    if frames == 0:
        raise RuntimeError("tracking pipeline produced no frames")
    return {"output": str(output_path), "frames": frames, "detections": detections_total, "tracked_instances": tracked_total}
