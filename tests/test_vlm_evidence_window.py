import hashlib

import pytest

from display_project.vlm_sop.evidence_window import (
    EvidenceWindowError,
    OnlineEvidenceCache,
    build_evidence_window,
    validate_window,
)


H = "a" * 64


def _frame(identifier="frame-1", time=1.0):
    return {"id": identifier, "time": time, "image_sha256": hashlib.sha256(identifier.encode()).hexdigest(), "image_verified": True, "cache_ref": f"cache-{identifier}"}


def _window(**overrides):
    body = {"schema_version": "1.0", "task_id": "task-a", "window_id": "window-1", "decision_time": 2.0, "frame_refs": [_frame()], "object_track_refs": [], "relation_events": [], "observability": {"view": "partial", "missing": ["detector_summary_unavailable"], "absence_confirmed": []}, "hashes": {"source_sha256": H, "model_sha256": H, "config_sha256": H}}
    body.update(overrides)
    return body


def test_window_is_causal_truth_free_and_detached():
    raw = _window()
    checked = validate_window(raw)
    raw["frame_refs"][0]["id"] = "altered"
    assert checked["frame_refs"][0]["id"] == "frame-1"
    with pytest.raises(EvidenceWindowError, match="future"):
        validate_window(_window(frame_refs=[_frame(time=3.0)]))
    with pytest.raises(EvidenceWindowError, match="truth"):
        validate_window(_window(object_track_refs=[{"id": "track-1", "time": 1.0, "truth": "x"}]))


def test_missing_is_not_absence_and_verified_images_are_required():
    with pytest.raises(EvidenceWindowError, match="complete"):
        validate_window(_window(observability={"view": "occluded", "missing": [], "absence_confirmed": ["bolt"]}))
    bad = _frame()
    bad["image_verified"] = False
    with pytest.raises(EvidenceWindowError, match="verified"):
        validate_window(_window(frame_refs=[bad]))


def test_cache_is_bounded_and_never_samples_future_frames():
    cache = OnlineEvidenceCache(max_frames=2)
    cache.add_frame(_frame("frame-1", 1.0))
    cache.add_frame(_frame("frame-2", 2.0))
    cache.add_frame(_frame("frame-3", 3.0))
    window = build_evidence_window(task_id="task-a", window_id="window-3", decision_time=2.5, cache=cache, observability={"view": "partial", "missing": [], "absence_confirmed": []}, hashes={"source_sha256": H, "model_sha256": H, "config_sha256": H})
    assert [frame["id"] for frame in window["frame_refs"]] == ["frame-2"]


def test_cache_verifies_supplied_image_bytes_and_window_rejects_ambiguous_refs():
    image = b"verified image"
    frame = _frame()
    frame["image_sha256"] = hashlib.sha256(image).hexdigest()
    cache = OnlineEvidenceCache()
    cache.add_frame(frame, image_bytes=image)
    with pytest.raises(EvidenceWindowError, match="match"):
        cache.add_frame(frame, image_bytes=b"rewritten image")
    with pytest.raises(EvidenceWindowError, match="unique"):
        validate_window(_window(object_track_refs=[{"id": "frame-1", "time": 1.0}]))
