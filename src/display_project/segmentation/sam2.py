from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from display_project.detection.grounding import resolve_device


@dataclass(frozen=True)
class SegmentationResult:
    masks: np.ndarray
    scores: np.ndarray

    def __post_init__(self) -> None:
        masks = np.asarray(self.masks, dtype=bool)
        scores = np.asarray(self.scores, dtype=np.float32)
        if masks.ndim != 3:
            raise ValueError("masks must have shape (N, H, W)")
        if scores.ndim != 1:
            raise ValueError("scores must have shape (N,)")
        if len(masks) != len(scores):
            raise ValueError("masks and scores must have matching length")
        object.__setattr__(self, "masks", masks)
        object.__setattr__(self, "scores", scores)

    @classmethod
    def empty(cls, *, height: int, width: int) -> "SegmentationResult":
        return cls(
            masks=np.empty((0, height, width), dtype=bool),
            scores=np.empty(0, dtype=np.float32),
        )


def normalize_boxes(boxes: np.ndarray, *, width: int, height: int) -> np.ndarray:
    if width <= 0 or height <= 0:
        raise ValueError("frame dimensions must be positive")
    result = np.asarray(boxes, dtype=np.float32)
    if result.ndim != 2 or result.shape[1:] != (4,):
        raise ValueError("boxes must have shape (N, 4)")
    if not np.isfinite(result).all():
        raise ValueError("boxes must contain only finite values")
    result = result.copy()
    result[:, (0, 2)] = np.clip(result[:, (0, 2)], 0, width - 1)
    result[:, (1, 3)] = np.clip(result[:, (1, 3)], 0, height - 1)
    if np.any(result[:, 2] <= result[:, 0]) or np.any(result[:, 3] <= result[:, 1]):
        raise ValueError("boxes must have positive area after clipping")
    return result


class Sam2BoxSegmenter:
    """Generate one SAM2 mask per detection box without changing SOP state."""

    def __init__(self, model_path: str | Path, *, device: str = "cuda") -> None:
        import torch
        from transformers import Sam2Model, Sam2Processor

        resolved_device = resolve_device(device, torch)
        if resolved_device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for SAM2 video-frame segmentation")
        path = str(Path(model_path).expanduser().resolve())
        self.torch = torch
        self.device = resolved_device
        self.processor = Sam2Processor.from_pretrained(path, local_files_only=True)
        self.model = Sam2Model.from_pretrained(path, local_files_only=True).to(self.device)
        self.model.eval()

    def segment(self, frame_bgr: np.ndarray, boxes: np.ndarray) -> SegmentationResult:
        from PIL import Image

        frame = np.asarray(frame_bgr)
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("frame_bgr must have shape (H, W, 3)")
        height, width = frame.shape[:2]
        prompt_boxes = normalize_boxes(boxes, width=width, height=height)
        if len(prompt_boxes) == 0:
            return SegmentationResult.empty(height=height, width=width)

        image = Image.fromarray(frame[:, :, ::-1])
        inputs = self.processor(
            images=image,
            input_boxes=[prompt_boxes.tolist()],
            return_tensors="pt",
        ).to(self.device)
        autocast = (
            self.torch.autocast("cuda", dtype=self.torch.bfloat16)
            if self.device.startswith("cuda")
            else nullcontext()
        )
        with self.torch.inference_mode(), autocast:
            outputs = self.model(**inputs, multimask_output=False)

        original_sizes = inputs["original_sizes"].detach().cpu()
        masks = self.processor.post_process_masks(
            outputs.pred_masks.detach().cpu(),
            original_sizes,
        )[0]
        masks = np.asarray(masks)
        if masks.ndim == 4 and masks.shape[1] == 1:
            masks = masks[:, 0]
        if masks.ndim != 3 or len(masks) != len(prompt_boxes):
            raise RuntimeError(
                f"unexpected SAM2 mask shape {masks.shape} for {len(prompt_boxes)} boxes"
            )

        scores = np.asarray(outputs.iou_scores.detach().float().cpu())[0]
        if scores.ndim == 2 and scores.shape[1] == 1:
            scores = scores[:, 0]
        elif scores.ndim == 2:
            scores = scores.max(axis=1)
        scores = np.asarray(scores, dtype=np.float32).reshape(-1)
        return SegmentationResult(masks=masks > 0, scores=scores)
