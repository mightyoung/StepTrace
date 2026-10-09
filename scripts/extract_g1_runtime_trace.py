"""Extract a truth-free runtime trace for G1 calibration and HMM fitting."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from display_project.detection import GroundingDinoDetector  # noqa: E402
from display_project.provenance import (  # noqa: E402
    compiled_task_hash,
    sha256_asset,
    sha256_file,
)
from display_project.sop.task_config import load_task_config  # noqa: E402
from display_project.sop.visual_evidence import (  # noqa: E402
    ObjectObservation,
    VisualEvidenceEngine,
)


def _iou(left: np.ndarray, right: np.ndarray) -> float:
    x1 = max(float(left[0]), float(right[0]))
    y1 = max(float(left[1]), float(right[1]))
    x2 = min(float(left[2]), float(right[2]))
    y2 = min(float(left[3]), float(right[3]))
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, float(left[2] - left[0])) * max(
        0.0, float(left[3] - left[1])
    )
    right_area = max(0.0, float(right[2] - right[0])) * max(
        0.0, float(right[3] - right[1])
    )
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _classwise_nms(
    boxes: np.ndarray,
    confidence: np.ndarray,
    labels: list[str],
    *,
    threshold: float = 0.55,
) -> list[int]:
    kept: list[int] = []
    for label in sorted(set(labels)):
        pending = sorted(
            (index for index, value in enumerate(labels) if value == label),
            key=lambda index: float(confidence[index]),
            reverse=True,
        )
        while pending:
            selected = pending.pop(0)
            kept.append(selected)
            pending = [
                index
                for index in pending
                if _iou(boxes[selected], boxes[index]) <= threshold
            ]
    return sorted(kept)


def _normalize_observations(
    *,
    task: Any,
    boxes: np.ndarray,
    confidence: np.ndarray,
    labels: list[str],
) -> list[ObjectObservation]:
    normalized: list[tuple[np.ndarray, float, str]] = []
    for box, score, raw_label in zip(boxes, confidence, labels):
        matched = next(
            (
                name
                for name in task.allowed_classes
                if any(
                    alias.casefold() in raw_label.casefold()
                    for alias in task.aliases_for(name)
                )
            ),
            None,
        )
        if matched is not None:
            normalized.append((box, float(score), matched))
    if not normalized:
        return []
    normalized_boxes = np.asarray([item[0] for item in normalized])
    normalized_confidence = np.asarray([item[1] for item in normalized])
    normalized_labels = [item[2] for item in normalized]
    indices = _classwise_nms(
        normalized_boxes, normalized_confidence, normalized_labels
    )
    return [
        ObjectObservation(
            label=normalized_labels[index],
            confidence=float(normalized_confidence[index]),
            xyxy=tuple(map(float, normalized_boxes[index])),
        )
        for index in indices
    ]


def extract(
    *,
    manifest_path: Path,
    video_root: Path,
    task_config_path: Path,
    dino_model_path: Path,
    output_path: Path,
    provenance_path: Path,
    device: str,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not (
        isinstance(manifest, dict)
        and manifest.get("status") == "FROZEN_DEVELOPMENT_CALIBRATION"
        and manifest.get("selection_policy", {}).get(
            "runtime_receives_action_labels"
        )
        is False
        and manifest.get("selection_policy", {}).get(
            "runtime_truth_store_allowed"
        )
        is False
        and isinstance(manifest.get("records"), list)
    ):
        raise ValueError("runtime manifest is not a frozen truth-free schedule")
    task = load_task_config(task_config_path)
    if list(task.steps) != manifest.get("states"):
        raise ValueError("runtime task steps disagree with frozen manifest")
    detector = GroundingDinoDetector(dino_model_path, device=device)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    written = 0
    video_hashes: dict[str, str] = {}
    with temporary.open("w", encoding="utf-8") as stream:
        for record in manifest["records"]:
            video_id = str(record.get("video_id", ""))
            if (
                video_id == "0009"
                or record.get("project_split")
                not in {"DEVELOPMENT", "CALIBRATION"}
                or record.get("runtime_truth_store_allowed") is not False
            ):
                raise ValueError(f"forbidden runtime manifest record: {video_id}")
            video_path = video_root / f"{video_id}.mp4"
            if not video_path.is_file():
                raise FileNotFoundError(video_path)
            video_hashes[video_id] = sha256_file(video_path)
            start = int(record["episode_start"])
            end = int(record["episode_end"])
            stride = int(record["trace_stride"])
            group_hash = str(record["group_hash"])
            capture = cv2.VideoCapture(str(video_path))
            if not capture.isOpened():
                raise RuntimeError(f"cannot open video: {video_path}")
            capture.set(cv2.CAP_PROP_POS_FRAMES, start - 1)
            engine = VisualEvidenceEngine(
                confirm_frames=4,
                cooldown_frames=12,
                task_config=task,
            )
            step_index = 0
            previous_gray: np.ndarray | None = None
            sequence_index = 0
            try:
                for original_frame in range(start, end + 1, stride):
                    capture.set(cv2.CAP_PROP_POS_FRAMES, original_frame - 1)
                    ok, frame = capture.read()
                    if not ok:
                        raise RuntimeError(
                            f"failed to decode {video_id} frame {original_frame}"
                        )
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    motion = (
                        0.0
                        if previous_gray is None
                        else float(cv2.absdiff(gray, previous_gray).mean())
                    )
                    previous_gray = gray
                    expected = (
                        task.steps[step_index]
                        if step_index < len(task.steps)
                        else None
                    )
                    detections = detector.detect(
                        frame,
                        list(task.detector_prompts),
                        box_threshold=0.22,
                        text_threshold=0.16,
                    )
                    observations = _normalize_observations(
                        task=task,
                        boxes=detections.xyxy,
                        confidence=detections.confidence,
                        labels=detections.labels,
                    )
                    visual = engine.observe(
                        expected,
                        observations,
                        frame_size=(frame.shape[1], frame.shape[0]),
                    )
                    candidate = visual.candidate_step
                    probability = (
                        1.0
                        if not observations
                        else 1.0
                        - min(observation.confidence for observation in observations)
                    )
                    payload = {
                        "observation_id": (
                            f"{video_id}-{original_frame:06d}"
                        ),
                        "video_id": video_id,
                        "group_hash": group_hash,
                        "sequence_index": sequence_index,
                        "frame_start": original_frame,
                        "frame_end": original_frame,
                        "candidate_step": candidate,
                        "probability": probability,
                        "luma_mean": float(gray.mean()),
                        "luma_std": float(gray.std()),
                        "frame_motion": motion,
                    }
                    stream.write(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    sequence_index += 1
                    written += 1
                    if visual.confirmed_step is not None:
                        step_index += 1
            finally:
                capture.release()
    temporary.replace(output_path)
    provenance = {
        "schema_version": 1,
        "status": "REAL_RUNTIME_TRACE",
        "annotation_used": False,
        "truth_store_used": False,
        "manifest_sha256": sha256_file(manifest_path),
        "task_config_sha256": sha256_file(task_config_path),
        "compiled_task_hash": compiled_task_hash(task),
        "model_asset_hash": sha256_asset(dino_model_path),
        "trace_sha256": sha256_file(output_path),
        "trace_records": written,
        "video_sha256": video_hashes,
        "device": device,
    }
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--video-root", type=Path, required=True)
    parser.add_argument("--task-config", type=Path, required=True)
    parser.add_argument("--dino-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance-output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    result = extract(
        manifest_path=args.manifest,
        video_root=args.video_root,
        task_config_path=args.task_config,
        dino_model_path=args.dino_model,
        output_path=args.output,
        provenance_path=args.provenance_output,
        device=args.device,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
