from __future__ import annotations
import json
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Mapping, cast
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from display_project.detection import GroundingDinoDetector
from display_project.segmentation import Sam2BoxSegmenter
from display_project.sop.task_config import (
    CompiledTask,
    compile_task_config,
    load_task_config,
)
from display_project.sop.state_machine import (Evidence, issue_model_geometry_evidence, issue_certificate_evidence)
from display_project.sop.visual_evidence import ObjectObservation, VisualEvidenceEngine
from display_project.evidence import (
    ProcessEvidenceGraph,
    EvidenceCertificate,
    verify_certificate,
)
from display_project.risk import RiskCalibrator
from display_project.fusion.event_schema import validate_event
from display_project.provenance import compiled_task_hash, sha256_file, sidecar_record
from display_project.localization import decision_zh, step_zh, task_zh


def _sha256_path(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _target_prompts(step: str | None, task=None) -> tuple[str, ...]:
    if task is not None:
        for spec in task.step_specs:
            if spec.id == step and spec.prompts:
                return spec.prompts
        labels = []
        for spec in task.step_specs:
            if spec.id == step:
                for predicate in spec.evidence:
                    if predicate.get("predicate") == "count" and isinstance(
                        predicate.get("label"), str
                    ):
                        labels.append(predicate["label"])
        return tuple(dict.fromkeys(labels or task.detector_prompts))
    return ()


def _load_reviews(path: str | Path | None) -> dict[int, dict[str, object]]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(item["clip_frame"]): item for item in payload.get("reviews", [])}


def _certificate_event_fields(
    certificate: EvidenceCertificate | None, artifact_path: Path
) -> dict[str, str | None]:
    if certificate is None:
        return {
            "certificate_hash": None,
            "graph_digest": None,
            "certificate_artifact_ref": None,
        }
    return {
        "certificate_hash": certificate.certificate_hash,
        "graph_digest": certificate.graph_digest,
        "certificate_artifact_ref": str(artifact_path),
    }


def evaluate_and_commit_frame(
    *,
    machine,
    task,
    visual,
    review=None,
    risk_calibrator=None,
    graph=None,
    frame=0,
    timestamp_ms=0,
    risk_threshold=None,
    calibration_version=None,
    input_hash=None,
    observations=(),
    model_hash=None,
    config_hash=None,
    decision_policy="proposed",
    raw_risk_threshold=None,
):
    """Build a typed PEG, verify one candidate, and commit only after the certificate passes."""
    if decision_policy == "no_calibration":
        decision_policy = "uncalibrated_certificate"
    if decision_policy == "uncalibrated_certificate":
        if risk_calibrator is not None:
            raise ValueError("uncalibrated certificate policy forbids a calibrator")
        if (
            not isinstance(raw_risk_threshold, float)
            or not 0.0 <= float(raw_risk_threshold) <= 1.0
        ):
            raise ValueError("raw_risk_threshold must be an explicit float in [0,1]")
    graph = graph or ProcessEvidenceGraph()
    expected = machine.expected_step
    pre = machine.current_step
    candidate = visual.candidate_step or visual.confirmed_step
    context_id = f"context-{task.task_id}-{frame}"
    pre_id = f"state-pre-{task.task_id}-{frame}"
    cand_id = f"state-candidate-{task.task_id}-{frame}"
    graph = graph.add_node(
        {
            "id": context_id,
            "kind": "context",
            "frame": frame,
            "task_id": task.task_id,
            "expected_step": expected,
            "current_step": pre,
        }
    )
    graph = graph.add_node(
        {
            "id": pre_id,
            "kind": "step_state",
            "step": pre or "START",
            "frame": frame,
            "task_id": task.task_id,
        }
    )
    if candidate is not None:
        graph = graph.add_node(
            {
                "id": cand_id,
                "kind": "step_state",
                "step": candidate,
                "frame": frame,
                "task_id": task.task_id,
            }
        )
        graph = graph.add_edge(
            {
                "source": pre_id,
                "target": cand_id,
                "kind": "transition",
                "task_steps": ["START", *list(task.steps)],
            }
        )
    evidence_id = f"relation-{task.task_id}-{frame}"
    object_id = f"object-{task.task_id}-{frame}"
    object_ids = []
    for obs_index, obs in enumerate(observations or ()):
        object_id = f"object-{task.task_id}-{frame}-{obs_index}"
        object_ids.append(object_id)
        graph = graph.add_node(
            {
                "id": object_id,
                "kind": "object",
                "frame": frame,
                "entity": getattr(obs, "label", "unknown"),
                "predicate": "detected_object",
                "source": "model_geometry",
                "value": {
                    "label": getattr(obs, "label", "unknown"),
                    "confidence": float(getattr(obs, "confidence", 0.0)),
                    "xyxy": list(getattr(obs, "xyxy", ())),
                },
            }
        )
        graph = graph.add_edge(
            {"source": context_id, "target": object_id, "kind": "observation"}
        )
    if visual.confirmed_step is not None:
        graph = graph.add_node(
            {
                "id": evidence_id,
                "kind": "relation",
                "frame": frame,
                "entity": task.task_id,
                "predicate": "confirmed_step",
                "source": "model_geometry",
                "value": visual.confirmed_step,
            }
        )
        for oid in object_ids:
            graph = graph.add_edge(
                {"source": oid, "target": evidence_id, "kind": "dependency"}
            )
        graph = graph.add_edge(
            {"source": evidence_id, "target": cand_id, "kind": "dependency"}
        )
    has_negative = any(
        k in r
        for r in visual.reasons
        for k in ("missing", "unsupported", "insufficient")
    )
    if has_negative:
        neg_id = f"negative-{task.task_id}-{frame}"
        graph = graph.add_node(
            {
                "id": neg_id,
                "kind": "negative",
                "frame": frame,
                "context_id": context_id,
                "reasons": list(visual.reasons),
            }
        )
        if candidate is not None:
            graph = graph.add_edge(
                {"source": neg_id, "target": cand_id, "kind": "negative"}
            )
    hash_ready = all(
        isinstance(x, str)
        and len(x) == 64
        and x == x.lower()
        and all(c in "0123456789abcdef" for c in x)
        for x in (input_hash, model_hash, config_hash)
    )
    calibration_ready = risk_calibrator is not None and risk_calibrator.fitted
    cert = cert_result = None
    committed = False
    fixed_authorized = (
        decision_policy == "fixed_streak"
        and visual.confirmed_step is not None
        and candidate == expected
        and bool(object_ids)
        and not has_negative
    )
    if fixed_authorized:
        transition = machine.observe(
            Evidence(deterministic_step=issue_model_geometry_evidence(
                visual=visual, graph=graph, expected_step=expected
            ))
        )
        committed = (
            transition.decision == "normal"
            and transition.current_step == visual.confirmed_step
        )
    elif (
        decision_policy in {"proposed", "uncalibrated_certificate"}
        and visual.confirmed_step is not None
        and candidate == expected
        and bool(object_ids)
        and (decision_policy == "uncalibrated_certificate" or calibration_ready)
        and hash_ready
        and not has_negative
    ):
        # Conservative frame risk is derived from actual authorizing boxes.
        raw_false_advance_risk = (
            1.0 - min(float(getattr(obs, "confidence", 0.0)) for obs in observations)
            if observations
            else 1.0
        )
        if decision_policy == "proposed":
            evaluation = risk_calibrator.evaluate(raw_false_advance_risk)
            effective_method = "Proposed"
            effective_version = evaluation.calibration_version
            effective_threshold = evaluation.risk_threshold
            risk_score = evaluation.risk_score
            risk_ok = evaluation.coverage_eligible
            metadata_mismatch = (
                risk_threshold is not None
                and float(risk_threshold) != effective_threshold
                or calibration_version is not None
                and calibration_version != effective_version
            )
        else:
            effective_method = "NoCalibrationCertificate"
            effective_version = "UNCALIBRATED_RAW_V1"
            effective_threshold = float(raw_risk_threshold)
            risk_score = raw_false_advance_risk
            risk_ok = risk_score <= effective_threshold
            metadata_mismatch = False
        if metadata_mismatch:
            graph = graph.add_node(
                {
                    "id": f"negative-{task.task_id}-{frame}-metadata",
                    "kind": "negative",
                    "frame": frame,
                    "context_id": context_id,
                    "reasons": ["risk_metadata_mismatch"],
                }
            )
        else:
            from display_project.evidence.certificate import VerificationContext

            vc = VerificationContext(
                task.task_id,
                expected,
                pre,
                candidate,
                context_id,
                cand_id,
                frame,
                frame,
            )
            authorizing_evidence = tuple(
                {
                    "source": "model_geometry",
                    "id": oid,
                    "type": "detected_object",
                    "value": next(
                        n.get("value") for n in graph.nodes if n.get("id") == oid
                    ),
                }
                for oid in object_ids
            ) + (
                (
                    {
                        "source": "model_geometry",
                        "id": evidence_id,
                        "type": "confirmed_step",
                        "value": visual.confirmed_step,
                    }
                ),
            )
            cert = EvidenceCertificate.issue(
                graph,
                authorizing_evidence,
                risk_ok=risk_ok,
                risk_score=risk_score,
                risk_threshold=effective_threshold,
                coverage=1.0,
                frame=frame,
                pre_state=pre,
                candidate=candidate,
                task_id=task.task_id,
                expected_step=expected,
                current_step=pre,
                method=effective_method,
                calibration_version=effective_version,
                verification_context=vc,
                input_hash=input_hash,
                model_hash=model_hash,
                config_hash=config_hash,
                authorizing_evidence_refs=tuple((*object_ids, evidence_id)),
                confirmed_transition=True,
            )
            cert_result = verify_certificate(
                cert,
                graph,
                context=vc,
                task_id=task.task_id,
                expected_step=expected,
                current_step=pre,
            )
            if cert_result.valid:
                transition = machine.observe(
                    Evidence(deterministic_step=issue_certificate_evidence(
                        certificate=cert, graph=graph, context=vc,
                        task_id=task.task_id, expected_step=expected, current_step=pre,
                    ))
                )
                committed = (
                    transition.decision == "normal"
                    and transition.current_step == visual.confirmed_step
                )
    if not committed:
        transition = machine.observe(Evidence())
        if cert_result is not None and not cert_result.valid:
            transition = replace(transition, reason=cert_result.reason)
        elif has_negative:
            transition = replace(transition, reason="negative_evidence")
    event = {
        "schema_version": "1.0",
        "event_id": f"{task.task_id}-frame-{frame:06d}",
        "timestamp_ms": int(timestamp_ms),
        "decision": transition.decision,
        "expected_step": transition.expected_step,
        "observed_step": transition.observed_step,
        "previous_step": transition.previous_step,
        "current_step": transition.current_step,
        "reason": transition.reason,
        "evidence_source": transition.evidence_source,
        "evidence": [
            {
                "source": "deterministic",
                "kind": "visual_evidence",
                "confidence": 1.0 if visual.confirmed_step else 0.0,
                "value": {"coverage_eligible": committed},
            }
        ],
        "certificate_hash": None,
        "graph_digest": None,
        "certificate_artifact_ref": None,
    }
    validate_event(event)
    return {
        "transition": transition,
        "event": event,
        "graph": graph,
        "certificate": cert if committed else None,
        "certificate_result": cert_result,
        "committed": committed,
    }


def render_real_sop_video_zh(
    source: str | Path,
    output: str | Path,
    events_output: str | Path,
    detector: GroundingDinoDetector,
    segmenter: Sam2BoxSegmenter,
    *,
    font_path: str,
    vlm_reviews: str | Path | None = None,
    confirm_frames: int = 4,
    cooldown_frames: int = 12,
    max_frames: int | None = None,
    task_config: CompiledTask | Mapping[str, object] | str | Path | None = None,
    risk_calibrator: RiskCalibrator | None = None,
    calibration_version: str | None = None,
    risk_threshold: float | None = None,
    model_hash: str | None = None,
    config_hash: str | None = None,
    decision_policy: str = "proposed",
    raw_risk_threshold: float | None = None,
) -> dict[str, object]:
    if decision_policy == "no_calibration":
        decision_policy = "uncalibrated_certificate"
    if decision_policy not in {"fixed_streak", "proposed", "uncalibrated_certificate"}:
        raise ValueError("unsupported decision policy")
    if decision_policy == "fixed_streak" and (confirm_frames, cooldown_frames) != (
        4,
        12,
    ):
        raise ValueError(
            "fixed_streak baseline is frozen at confirm_frames=4, cooldown_frames=12"
        )
    if decision_policy == "proposed" and (
        risk_calibrator is None or not risk_calibrator.fitted
    ):
        raise ValueError("proposed policy requires a fitted risk calibrator")
    if decision_policy == "uncalibrated_certificate":
        if risk_calibrator is not None:
            raise ValueError("uncalibrated certificate policy forbids a calibrator")
        if (
            not isinstance(raw_risk_threshold, float)
            or not 0.0 <= float(raw_risk_threshold) <= 1.0
        ):
            raise ValueError("raw_risk_threshold must be an explicit float in [0,1]")

    source_path, output_path, events_path = (
        Path(source).resolve(),
        Path(output).resolve(),
        Path(events_output).resolve(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    reviews = _load_reviews(vlm_reviews)
    if task_config is None:
        raise ValueError(
            "task_config is required for production rendering; "
            "pass a CompiledTask or an explicit task config path"
        )
    if isinstance(task_config, CompiledTask):
        task = task_config
    elif isinstance(task_config, (str, Path)):
        task = load_task_config(task_config)
    else:
        task = compile_task_config(task_config)
    source_hash = sha256_file(source_path)
    task_config_hash = compiled_task_hash(task)
    if decision_policy in {"proposed", "uncalibrated_certificate"} and (
        not model_hash or not config_hash
    ):
        raise ValueError("certificate provenance anchors are required")
    certificate_config_hash = config_hash or task_config_hash
    if not task.allowed_classes or not task.detector_prompts:
        raise ValueError(
            "production task config requires allowed_classes and detector_prompts"
        )
    import supervision as sv

    info = sv.VideoInfo.from_video_path(str(source_path))
    tracker = sv.ByteTrack(frame_rate=round(info.fps))
    machine = task.state_machine()
    graph = ProcessEvidenceGraph()
    engine = VisualEvidenceEngine(
        confirm_frames=4 if decision_policy == "fixed_streak" else confirm_frames,
        cooldown_frames=12 if decision_policy == "fixed_streak" else cooldown_frames,
        task_config=task,
    )
    mask_annotator = sv.MaskAnnotator(color_lookup=sv.ColorLookup.TRACK)
    box_annotator = sv.BoxAnnotator(color_lookup=sv.ColorLookup.TRACK)
    trace_annotator = sv.TraceAnnotator(
        color_lookup=sv.ColorLookup.TRACK, trace_length=40
    )
    font_title, font_body = (
        ImageFont.truetype(font_path, 31),
        ImageFont.truetype(font_path, 24),
    )
    capture = cv2.VideoCapture(str(source_path))
    frames = detections_total = masks_total = confirmed_total = 0
    certificate_path = (
        events_path.parent / (events_path.stem + ".certificates") / "records.jsonl"
    )
    certificate_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        events_path.open("w", encoding="utf-8") as stream,
        certificate_path.open("w", encoding="utf-8") as certificate_stream,
    ):
        try:
            with sv.VideoSink(str(output_path), video_info=info) as sink:
                while max_frames is None or frames < max_frames:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    expected = machine.expected_step
                    base_prompts = tuple(task.detector_prompts)
                    results = [
                        detector.detect(
                            frame,
                            list(base_prompts),
                            box_threshold=0.22,
                            text_threshold=0.16,
                        )
                    ]
                    for prompt in _target_prompts(expected, task):
                        results.append(
                            detector.detect(
                                frame, [prompt], box_threshold=0.28, text_threshold=0.16
                            )
                        )
                    boxes = (
                        np.concatenate([r.xyxy for r in results], axis=0)
                        if results
                        else np.empty((0, 4))
                    )
                    conf = (
                        np.concatenate([r.confidence for r in results], axis=0)
                        if results
                        else np.empty((0,))
                    )
                    labels = [label for r in results for label in r.labels]
                    class_names = task.allowed_classes
                    ids = np.asarray(
                        [
                            next(
                                (
                                    i
                                    for i, n in enumerate(class_names)
                                    if any(
                                        alias.casefold() in label.casefold()
                                        for alias in task.aliases_for(n)
                                    )
                                ),
                                0,
                            )
                            for label in labels
                        ],
                        dtype=int,
                    )
                    raw = (
                        sv.Detections(
                            xyxy=boxes, confidence=conf, class_id=ids
                        ).with_nms(threshold=0.55, class_agnostic=False)
                        if len(boxes)
                        else sv.Detections.empty()
                    )
                    labels = (
                        [task.allowed_classes[int(i)] for i in raw.class_id]
                        if len(raw)
                        else []
                    )
                    segmented = segmenter.segment(frame, raw.xyxy)
                    tracked = tracker.update_with_detections(
                        sv.Detections(
                            xyxy=raw.xyxy,
                            confidence=raw.confidence,
                            class_id=raw.class_id,
                            mask=segmented.masks,
                        )
                    )
                    observations = [
                        ObjectObservation(
                            label=label_name,
                            confidence=float(c),
                            xyxy=cast(
                                tuple[float, float, float, float],
                                tuple(map(float, b))[:4],
                            ),
                        )
                        for label_name, c, b in zip(labels, raw.confidence, raw.xyxy)
                    ]
                    visual = engine.observe(
                        expected,
                        observations,
                        frame_size=(frame.shape[1], frame.shape[0]),
                    )
                    review = reviews.get(frames)
                    frame_result = evaluate_and_commit_frame(
                        machine=machine,
                        task=task,
                        visual=visual,
                        review=review,
                        risk_calibrator=risk_calibrator,
                        graph=graph,
                        frame=frames,
                        timestamp_ms=int(frames * 1000 / info.fps),
                        risk_threshold=risk_threshold,
                        calibration_version=calibration_version,
                        input_hash=source_hash,
                        model_hash=model_hash,
                        config_hash=certificate_config_hash,
                        observations=observations,
                        decision_policy=decision_policy,
                        raw_risk_threshold=raw_risk_threshold,
                    )
                    transition = frame_result["transition"]
                    graph = frame_result["graph"]
                    if frame_result["committed"]:
                        confirmed_total += 1
                    annotated = trace_annotator.annotate(frame.copy(), tracked)
                    annotated = mask_annotator.annotate(annotated, tracked)
                    annotated = box_annotator.annotate(annotated, tracked)
                    image = Image.fromarray(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB))
                    draw = ImageDraw.Draw(image, "RGBA")
                    draw.rectangle((20, 20, 1100, 130), fill=(10, 10, 10, 215))
                    draw.text(
                        (35, 30),
                        f"{task_zh(task.task_id)}｜工序状态：{decision_zh(transition.decision)}",
                        font=font_title,
                        fill=(255, 220, 80, 255),
                    )
                    draw.text(
                        (35, 78),
                        f"当前工序：{step_zh(machine.current_step)}    目标工序：{step_zh(machine.expected_step)}",
                        font=font_body,
                        fill=(255, 255, 255, 255),
                    )
                    sink.write_frame(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))
                    record = frame_result["event"]
                    record["frame"] = frames
                    record["source_frame_sha256"] = hashlib.sha256(
                        frame.tobytes()
                    ).hexdigest()
                    cert = frame_result.get("certificate")
                    record.update(_certificate_event_fields(cert, certificate_path))
                    if cert:
                        certificate_stream.write(
                            json.dumps(sidecar_record(cert, graph), ensure_ascii=False)
                            + "\n"
                        )
                        certificate_stream.flush()
                    validate_event(record)
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    frames += 1
                    detections_total += len(raw)
                    masks_total += len(segmented.masks)
        finally:
            capture.release()
    if frames == 0:
        raise RuntimeError("real SOP detection produced no frames")
    return {
        "output": str(output_path),
        "events": str(events_path),
        "frames": frames,
        "detections": detections_total,
        "masks": masks_total,
        "confirmed_steps": confirmed_total,
        "current_step": machine.current_step,
        "expected_step": machine.expected_step,
        "complete": machine.is_complete,
        "annotation_used": False,
        "task_id": task.task_id,
        "calibration_version": (
            risk_calibrator.calibration_version
            if decision_policy == "proposed" and risk_calibrator is not None
            else "UNCALIBRATED_RAW_V1"
            if decision_policy == "uncalibrated_certificate"
            else None
        ),
        "risk_threshold": (
            risk_calibrator.risk_threshold
            if decision_policy == "proposed" and risk_calibrator is not None
            else raw_risk_threshold
            if decision_policy == "uncalibrated_certificate"
            else None
        ),
    }
