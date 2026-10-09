from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

import numpy as np


def normalize_prompt(classes: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in classes:
        label = value.strip()
        if label and label.casefold() not in seen:
            result.append(label)
            seen.add(label.casefold())
    if not result:
        raise ValueError("at least one detection class is required")
    return result


def resolve_device(device: str, torch_module) -> str:
    value = str(device).strip().lower()
    if value == "auto":
        return "cuda" if bool(torch_module.cuda.is_available()) else "cpu"
    if not value:
        raise ValueError("device must be non-empty")
    return str(device)


def result_text_labels(
    result: Mapping[str, object], *, detection_count: int
) -> list[str]:
    """Normalize Grounding DINO labels across Transformers versions."""

    if detection_count == 0:
        return []
    raw = result.get("text_labels")
    if raw is None:
        raw = result.get("labels", [])
    labels = [str(value) for value in cast(Sequence[object], raw)]
    if len(labels) != detection_count:
        raise ValueError("detection boxes and text labels must have matching length")
    return labels


@dataclass(frozen=True)
class GroundedDetections:
    xyxy: np.ndarray
    confidence: np.ndarray
    labels: list[str]

    def __post_init__(self) -> None:
        boxes = np.asarray(self.xyxy, dtype=np.float32)
        confidence = np.asarray(self.confidence, dtype=np.float32)
        if boxes.ndim != 2 or boxes.shape[1:] != (4,):
            raise ValueError("xyxy must have shape (N, 4)")
        if confidence.ndim != 1:
            raise ValueError("confidence must have shape (N,)")
        if not (len(boxes) == len(confidence) == len(self.labels)):
            raise ValueError("detection arrays and labels must have matching length")
        object.__setattr__(self, "xyxy", boxes)
        object.__setattr__(self, "confidence", confidence)

    @classmethod
    def empty(cls) -> "GroundedDetections":
        return cls(
            np.empty((0, 4), dtype=np.float32), np.empty(0, dtype=np.float32), []
        )


class GroundingDinoDetector:
    def __init__(self, model_path: str | Path, *, device: str = "cuda") -> None:
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self.torch = torch
        self.device = resolve_device(device, torch)
        path = str(Path(model_path).expanduser().resolve())
        self.processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(
            path, local_files_only=True
        ).to(self.device)
        self.model.eval()

    def detect(
        self,
        frame_bgr: np.ndarray,
        classes: Sequence[str],
        *,
        box_threshold: float = 0.35,
        text_threshold: float = 0.25,
    ) -> GroundedDetections:
        from PIL import Image

        labels = normalize_prompt(classes)
        image = Image.fromarray(frame_bgr[:, :, ::-1])
        inputs = self.processor(images=image, text=[labels], return_tensors="pt").to(
            self.device
        )
        with self.torch.inference_mode():
            outputs = self.model(**inputs)
        kwargs = {
            "threshold": box_threshold,
            "text_threshold": text_threshold,
            "target_sizes": [(image.height, image.width)],
        }
        try:
            result = self.processor.post_process_grounded_object_detection(
                outputs, **kwargs
            )[0]
        except TypeError:
            result = self.processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, **kwargs
            )[0]
        boxes = result["boxes"].detach().cpu().numpy()
        scores = result["scores"].detach().cpu().numpy()
        text_labels = result_text_labels(result, detection_count=len(boxes))
        return GroundedDetections(boxes, scores, text_labels)
