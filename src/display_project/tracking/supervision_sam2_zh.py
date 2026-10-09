from __future__ import annotations

from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from display_project.detection import GroundingDinoDetector, normalize_prompt
from display_project.localization import class_zh
from display_project.segmentation import Sam2BoxSegmenter
from display_project.tracking.supervision_v2 import class_index


def render_segmented_tracked_video_zh(
    source: str | Path,
    output: str | Path,
    detector: GroundingDinoDetector,
    segmenter: Sam2BoxSegmenter,
    classes: Sequence[str],
    *,
    font_path: str,
    box_threshold: float = 0.28,
    text_threshold: float = 0.20,
) -> dict[str, object]:
    import supervision as sv

    class_names = normalize_prompt(classes)
    font = ImageFont.truetype(font_path, 28)
    source_path, output_path = Path(source).resolve(), Path(output).resolve()
    video_info = sv.VideoInfo.from_video_path(str(source_path))
    tracker = sv.ByteTrack(frame_rate=round(video_info.fps))
    mask_annotator = sv.MaskAnnotator(color_lookup=sv.ColorLookup.TRACK)
    box_annotator = sv.BoxAnnotator(color_lookup=sv.ColorLookup.TRACK, thickness=3)
    trace_annotator = sv.TraceAnnotator(
        color_lookup=sv.ColorLookup.TRACK,
        trace_length=40,
    )
    capture = cv2.VideoCapture(str(source_path))
    frames = detections_total = tracked_total = masks_total = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sv.VideoSink(str(output_path), video_info=video_info) as sink:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                grounded = detector.detect(
                    frame,
                    class_names,
                    box_threshold=box_threshold,
                    text_threshold=text_threshold,
                )
                segmented = segmenter.segment(frame, grounded.xyxy)
                detections = sv.Detections(
                    xyxy=grounded.xyxy,
                    confidence=grounded.confidence,
                    class_id=np.asarray(
                        [class_index(label, class_names) for label in grounded.labels],
                        dtype=int,
                    ),
                    mask=segmented.masks,
                )
                detections = tracker.update_with_detections(detections)
                annotated = trace_annotator.annotate(frame.copy(), detections)
                annotated = mask_annotator.annotate(annotated, detections)
                annotated = box_annotator.annotate(annotated, detections)
                image = Image.fromarray(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(image)
                for box, track_id, class_id, confidence in zip(
                    detections.xyxy,
                    detections.tracker_id,
                    detections.class_id,
                    detections.confidence,
                ):
                    x1, y1 = int(box[0]), max(0, int(box[1]) - 36)
                    text = f"#{track_id} {class_zh(class_names[class_id])} {confidence:.2f}"
                    bounds = draw.textbbox((x1, y1), text, font=font)
                    draw.rectangle(bounds, fill=(15, 15, 15))
                    draw.text((x1, y1), text, font=font, fill=(255, 255, 255))
                sink.write_frame(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))
                frames += 1
                detections_total += len(grounded.labels)
                tracked_total += len(detections)
                masks_total += len(segmented.masks)
    finally:
        capture.release()
    if frames == 0:
        raise RuntimeError("SAM2 tracking pipeline produced no frames")
    return {
        "output": str(output_path),
        "frames": frames,
        "detections": detections_total,
        "tracked_instances": tracked_total,
        "masks": masks_total,
    }
