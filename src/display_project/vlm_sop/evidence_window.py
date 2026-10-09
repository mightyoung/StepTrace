from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


class EvidenceWindowError(ValueError):
    pass

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ANONYMOUS_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}\Z")
_FORBIDDEN_KEYS = frozenset(
    {
        "annotation",
        "annotations",
        "answer",
        "correct_step",
        "dataset",
        "dataset_answer",
        "ground_truth",
        "label",
        "labels",
        "oracle",
        "progress",
        "psr_labels",
        "target_step",
        "truth",
        "truth_store",
        "video_path",
    }
)
_FORBIDDEN_MARKERS = (
    "annotation",
    "ground_truth",
    "psr_label",
    "truth",
    "cvat",
    "meccano",
    "industreal",
)
_VIEWS = frozenset({"complete", "partial", "occluded", "unavailable"})


def _json_copy(value: object, *, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise EvidenceWindowError(f"{field} must contain JSON facts only") from exc


def _finite_time(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceWindowError(f"{field} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise EvidenceWindowError(f"{field} must be a finite number")
    return numeric


def _anonymous_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _ANONYMOUS_ID.fullmatch(value) is None:
        raise EvidenceWindowError(f"{field} must be an opaque anonymous identifier")
    lowered = value.casefold()
    if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
        raise EvidenceWindowError(f"{field} contains a forbidden dataset or truth cue")
    return value


def _reject_forbidden(value: object, *, field: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).strip().casefold()
            if key_text in _FORBIDDEN_KEYS:
                raise EvidenceWindowError(f"{field} contains forbidden field: {key}")
            _reject_forbidden(child, field=f"{field}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _reject_forbidden(child, field=f"{field}[{index}]")
    elif isinstance(value, str):
        lowered = value.casefold().replace("\\", "/")
        if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
            raise EvidenceWindowError(f"{field} contains a forbidden dataset or truth cue")
        if "/" in lowered and ("label" in lowered or "truth" in lowered):
            raise EvidenceWindowError(f"{field} contains a forbidden artifact path")


def _hashes(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise EvidenceWindowError("hashes must be a mapping")
    required = ("source_sha256", "model_sha256", "config_sha256")
    if set(raw) != set(required):
        raise EvidenceWindowError("hashes must contain only source_sha256, model_sha256, config_sha256")
    result: dict[str, str] = {}
    for name in required:
        value = raw[name]
        if not isinstance(value, str) or _HASH.fullmatch(value) is None:
            raise EvidenceWindowError(f"hashes.{name} must be lowercase SHA-256 hex")
        result[name] = value
    return result


def _refs(raw: object, *, field: str, decision_time: float, frames: bool = False) -> list[dict[str, Any]]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise EvidenceWindowError(f"{field} must be a list")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise EvidenceWindowError(f"{field}[{index}] must be a mapping")
        _reject_forbidden(item, field=f"{field}[{index}]")
        copied = _json_copy(item, field=f"{field}[{index}]")
        identifier = _anonymous_id(copied.get("id"), field=f"{field}[{index}].id")
        if identifier in seen:
            raise EvidenceWindowError(f"{field} contains duplicate id: {identifier}")
        seen.add(identifier)
        moment = _finite_time(copied.get("time"), field=f"{field}[{index}].time")
        if moment > decision_time:
            raise EvidenceWindowError(f"{field}[{index}] is future evidence")
        copied["id"] = identifier
        copied["time"] = moment
        if frames:
            image_hash = copied.get("image_sha256")
            if not isinstance(image_hash, str) or _HASH.fullmatch(image_hash) is None:
                raise EvidenceWindowError(f"{field}[{index}].image_sha256 must be lowercase SHA-256 hex")
            if copied.get("image_verified") is not True:
                raise EvidenceWindowError(f"{field}[{index}] must reference a verified image")
            cache_ref = copied.get("cache_ref")
            if cache_ref is not None:
                copied["cache_ref"] = _anonymous_id(cache_ref, field=f"{field}[{index}].cache_ref")
        output.append(copied)
    return output


def _unique_evidence_ids(*groups: Sequence[Mapping[str, object]]) -> None:
    seen: set[str] = set()
    for group in groups:
        for item in group:
            identifier = str(item["id"])
            if identifier in seen:
                raise EvidenceWindowError(f"evidence ids must be unique across window fields: {identifier}")
            seen.add(identifier)


def _observability(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise EvidenceWindowError("observability must be a mapping")
    copied = _json_copy(raw, field="observability")
    if set(copied) != {"view", "missing", "absence_confirmed"}:
        raise EvidenceWindowError("observability must contain only view, missing, absence_confirmed")
    view = copied["view"]
    if view not in _VIEWS:
        raise EvidenceWindowError("observability.view is invalid")
    for name in ("missing", "absence_confirmed"):
        values = copied[name]
        if isinstance(values, (str, bytes)) or not isinstance(values, list):
            raise EvidenceWindowError(f"observability.{name} must be a list")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise EvidenceWindowError(f"observability.{name} entries must be non-empty fact ids")
    if copied["absence_confirmed"] and view != "complete":
        raise EvidenceWindowError("absence_confirmed requires complete observation")
    _reject_forbidden(copied, field="observability")
    return copied


def validate_window(raw: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise EvidenceWindowError("evidence window must be a mapping")
    _reject_forbidden(raw, field="window")
    required = {
        "schema_version", "task_id", "window_id", "decision_time", "frame_refs",
        "object_track_refs", "relation_events", "observability", "hashes",
    }
    if set(raw) != required:
        raise EvidenceWindowError("evidence window has missing or unsupported fields")
    if raw.get("schema_version") != "1.0":
        raise EvidenceWindowError("unsupported evidence window schema_version")
    decision_time = _finite_time(raw.get("decision_time"), field="decision_time")
    frame_refs = _refs(raw.get("frame_refs"), field="frame_refs", decision_time=decision_time, frames=True)
    object_track_refs = _refs(raw.get("object_track_refs"), field="object_track_refs", decision_time=decision_time)
    relation_events = _refs(raw.get("relation_events"), field="relation_events", decision_time=decision_time)
    _unique_evidence_ids(frame_refs, object_track_refs, relation_events)
    return {
        "schema_version": "1.0",
        "task_id": _anonymous_id(raw.get("task_id"), field="task_id"),
        "window_id": _anonymous_id(raw.get("window_id"), field="window_id"),
        "decision_time": decision_time,
        "frame_refs": frame_refs,
        "object_track_refs": object_track_refs,
        "relation_events": relation_events,
        "observability": _observability(raw.get("observability")),
        "hashes": _hashes(raw.get("hashes")),
    }


class OnlineEvidenceCache:
    def __init__(self, max_frames: int = 32) -> None:
        if isinstance(max_frames, bool) or not isinstance(max_frames, int) or max_frames < 1:
            raise ValueError("max_frames must be a positive integer")
        self._max_frames = max_frames
        self._frames: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def add_frame(self, frame_ref: Mapping[str, object], *, image_bytes: bytes | None = None) -> None:
        checked = _refs([frame_ref], field="frame_refs", decision_time=float("inf"), frames=True)[0]
        if image_bytes is not None:
            if not isinstance(image_bytes, bytes):
                raise TypeError("image_bytes must be bytes when supplied")
            digest = hashlib.sha256(image_bytes).hexdigest()
            if digest != checked["image_sha256"]:
                raise EvidenceWindowError("frame image_sha256 does not match supplied image bytes")
        existing = self._frames.get(checked["id"])
        if existing is not None and existing != checked:
            raise EvidenceWindowError("cache frame id cannot be rewritten")
        self._frames[checked["id"]] = checked
        self._frames.move_to_end(checked["id"])
        while len(self._frames) > self._max_frames:
            self._frames.popitem(last=False)

    def sample(self, decision_time: float, *, limit: int | None = None) -> list[dict[str, Any]]:
        deadline = _finite_time(decision_time, field="decision_time")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError("limit must be a positive integer")
        candidates = [deepcopy(frame) for frame in self._frames.values() if frame["time"] <= deadline]
        candidates.sort(key=lambda frame: (frame["time"], frame["id"]))
        if limit is None or len(candidates) <= limit:
            return candidates
        indexes = [round(index * (len(candidates) - 1) / (limit - 1)) if limit > 1 else len(candidates) - 1 for index in range(limit)]
        return [candidates[index] for index in indexes]


def build_evidence_window(
    *, task_id: str, window_id: str, decision_time: float,
    frame_refs: Sequence[Mapping[str, object]] | None = None,
    object_track_refs: Sequence[Mapping[str, object]] = (),
    relation_events: Sequence[Mapping[str, object]] = (),
    observability: Mapping[str, object], hashes: Mapping[str, object],
    cache: OnlineEvidenceCache | None = None, frame_limit: int | None = None,
) -> dict[str, object]:
    if frame_refs is not None and cache is not None:
        raise ValueError("supply frame_refs or cache, not both")
    selected = cache.sample(decision_time, limit=frame_limit) if cache is not None else list(frame_refs or ())
    return validate_window({
        "schema_version": "1.0", "task_id": task_id, "window_id": window_id,
        "decision_time": decision_time, "frame_refs": selected,
        "object_track_refs": list(object_track_refs), "relation_events": list(relation_events),
        "observability": dict(observability), "hashes": dict(hashes),
    })
