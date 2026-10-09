from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from display_project.detection.grounding import GroundedDetections, normalize_prompt


DEFAULT_SMALL_OBJECT_CLASSES = (
    "hand",
    "metal bar",
    "partial assembly",
    "screw",
    "bolt",
    "screwdriver",
)


def _as_numpy(value: Any, *, dtype: np.dtype | type | None = None) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value, dtype=dtype)


def _lookup_name(names: Mapping[int, str] | Sequence[str] | None, class_id: int) -> str:
    if names is None:
        return str(class_id)
    if isinstance(names, Mapping):
        return str(names.get(class_id, class_id))
    if 0 <= class_id < len(names):
        return str(names[class_id])
    return str(class_id)


class UltralyticsYoloDetector:
    """Closed-set YOLO detector adapter for the SOP geometry engine."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "cuda",
        image_size: int = 1280,
        iou_threshold: float = 0.50,
        model: Any | None = None,
    ) -> None:
        if image_size < 32:
            raise ValueError("image_size must be at least 32")
        if not 0.0 <= iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be between 0 and 1")
        self.device = device
        self.image_size = image_size
        self.iou_threshold = iou_threshold
        if model is None:
            from ultralytics import YOLO

            model = YOLO(str(Path(model_path).expanduser()))
        self.model = model

    def detect(
        self,
        frame_bgr: np.ndarray,
        classes: Sequence[str],
        *,
        box_threshold: float = 0.25,
        text_threshold: float = 0.0,
    ) -> GroundedDetections:
        del text_threshold
        requested = {label.casefold() for label in normalize_prompt(classes)}
        result = self.model.predict(
            frame_bgr,
            device=self.device,
            imgsz=self.image_size,
            conf=box_threshold,
            iou=self.iou_threshold,
            verbose=False,
        )[0]
        boxes_obj = result.boxes
        if boxes_obj is None or len(boxes_obj.xyxy) == 0:
            return GroundedDetections.empty()

        boxes = _as_numpy(boxes_obj.xyxy, dtype=np.float32)
        scores = _as_numpy(boxes_obj.conf, dtype=np.float32)
        class_ids = _as_numpy(boxes_obj.cls, dtype=np.int64)
        names = getattr(result, "names", getattr(self.model, "names", None))

        keep_boxes: list[np.ndarray] = []
        keep_scores: list[float] = []
        keep_labels: list[str] = []
        for box, score, class_id in zip(boxes, scores, class_ids):
            label = _lookup_name(names, int(class_id))
            if label.casefold() not in requested:
                continue
            if float(score) < box_threshold:
                continue
            keep_boxes.append(box)
            keep_scores.append(float(score))
            keep_labels.append(label)

        if not keep_boxes:
            return GroundedDetections.empty()
        return GroundedDetections(
            np.stack(keep_boxes).astype(np.float32),
            np.asarray(keep_scores, dtype=np.float32),
            keep_labels,
        )
