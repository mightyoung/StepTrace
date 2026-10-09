"""Fail-closed capability policy for IndustReal evidence.

README or inventory mentions of ``OD_labels`` are not provenance evidence.
Detection metrics are enabled only after provenance, schema, hash, coverage,
and the exact six-class schema are independently verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

ACTION_STEP_STATE_ONLY = "ACTION_STEP_STATE_ONLY"
SIX_CLASS_DETECTION = "SIX_CLASS_DETECTION"
_REQUIRED = (
    "provenance_verified",
    "schema_verified",
    "hash_verified",
    "coverage_verified",
)
_EXPECTED_CLASSES = (
    "hand",
    "metal bar",
    "partial assembly",
    "screw",
    "bolt",
    "screwdriver",
)


def _od_provenance(report: Mapping[str, Any]) -> Mapping[str, Any]:
    od = report.get("od_provenance")
    if isinstance(od, Mapping):
        return od
    legacy = report.get("OD_provenance")
    return legacy if isinstance(legacy, Mapping) else {}


def audit_industreal_capability(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return additive capability flags; never infer OD readiness."""

    od = _od_provenance(report)
    checks, _ = _od_evidence_checks(od, base_dir=Path.cwd())
    eligible = all(checks.values())
    return {
        "capability_status": (
            SIX_CLASS_DETECTION if eligible else ACTION_STEP_STATE_ONLY
        ),
        "action_step_state_only": not eligible,
        "six_class_detection_allowed": eligible,
        "detection_metrics_allowed": eligible,
        "detection_metric_policy": "ALLOWED" if eligible else "PROHIBITED",
        "od_checks": checks,
    }


def canonical_sha256(value: Any) -> str:
    """Hash a JSON value with stable key ordering and UTF-8 encoding."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _registry_sha256(value: Mapping[str, Any]) -> str:
    body = dict(value)
    body["canonical_sha256"] = ""
    raw = (
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bound(od: Mapping[str, Any], name: str) -> tuple[str, str]:
    nested = od.get(name)
    if isinstance(nested, Mapping):
        return str(nested.get("path", "")), str(nested.get("sha256", ""))
    return str(od.get(f"{name}_path", "")), str(od.get(f"{name}_sha256", ""))


def _load_json(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _od_evidence_checks(
    od: Mapping[str, Any], *, base_dir: Path
) -> tuple[dict[str, bool], list[str]]:
    """Validate machine-readable OD evidence; claim booleans are ignored."""
    checks = {key: False for key in _REQUIRED}
    checks["six_class_schema"] = False
    paths: dict[str, tuple[Path, str]] = {}
    for name in ("provenance", "schema", "coverage", "source"):
        raw, supplied = _bound(od, name)
        path = Path(raw)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        else:
            path = path.resolve()
        paths[name] = (path, supplied)
    for name, (path, supplied) in paths.items():
        if (
            path.is_file()
            and re.fullmatch(r"[0-9a-f]{64}", supplied or "")
            and _file_sha256(path) == supplied
        ):
            checks["hash_verified" if name == "source" else f"{name}_verified"] = True

    schema_doc = _load_json(paths["schema"][0]) if checks["schema_verified"] else None
    classes = schema_doc.get("classes") if schema_doc else None
    if not isinstance(classes, list) and schema_doc:
        names = schema_doc.get("names")
        classes = list(names.values()) if isinstance(names, Mapping) else names
    checks["six_class_schema"] = (
        isinstance(schema_doc, Mapping)
        and schema_doc.get("schema_version") == "industreal-od-schema-1.0.0"
        and schema_doc.get("status") == "VERIFIED"
        and schema_doc.get("task") == "six_class_detection"
        and isinstance(classes, list)
        and tuple(classes) == _EXPECTED_CLASSES
    )
    checks["schema_verified"] = checks["schema_verified"] and checks["six_class_schema"]

    provenance_doc = (
        _load_json(paths["provenance"][0]) if checks["provenance_verified"] else None
    )
    if provenance_doc:
        checks["provenance_verified"] = (
            provenance_doc.get("schema_version") == "industreal-od-provenance-1.0.0"
            and provenance_doc.get("status") == "VERIFIED"
            and provenance_doc.get("task") == "six_class_detection"
        )
        for name in ("source", "schema", "coverage"):
            bound_path, bound_hash = _bound(provenance_doc, name)
            expected_path, expected_hash = paths[name]
            checks["provenance_verified"] = (
                checks["provenance_verified"]
                and Path(bound_path).resolve() == expected_path
                and bound_hash == expected_hash
            )

    coverage_doc = (
        _load_json(paths["coverage"][0]) if checks["coverage_verified"] else None
    )
    if coverage_doc:
        expected = coverage_doc.get("expected_recording_ids")
        covered = coverage_doc.get("covered_recording_ids")
        try:
            expected_count = int(coverage_doc.get("expected_count", 0))
            covered_count = int(coverage_doc.get("covered_count", 0))
        except (TypeError, ValueError):
            expected_count = covered_count = -1
        checks["coverage_verified"] = (
            checks["coverage_verified"]
            and coverage_doc.get("schema_version") == "industreal-od-coverage-1.0.0"
            and coverage_doc.get("status") == "PASS"
            and coverage_doc.get("task") == "six_class_detection"
            and isinstance(expected, list)
            and expected == sorted(set(expected))
            and all(isinstance(item, str) and item for item in expected)
            and isinstance(covered, list)
            and covered == expected
            and expected_count == len(expected) > 0
            and covered_count == len(covered) > 0
            and isinstance(coverage_doc.get("labeled_frame_count"), int)
            and not isinstance(coverage_doc.get("labeled_frame_count"), bool)
            and coverage_doc.get("labeled_frame_count", 0) > 0
            and isinstance(coverage_doc.get("box_count"), int)
            and not isinstance(coverage_doc.get("box_count"), bool)
            and coverage_doc.get("box_count", 0) > 0
        )
    source_doc = _load_json(paths["source"][0]) if checks["hash_verified"] else None
    records = source_doc.get("records") if source_doc else None
    derived_ids: list[str] = []
    derived_frames = derived_boxes = 0
    derived_classes: set[str] = set()
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, Mapping) or not isinstance(
                record.get("recording_id"), str
            ):
                continue
            derived_ids.append(record["recording_id"])
            frames = record.get("frames")
            if isinstance(frames, list):
                derived_frames += len(frames)
                for frame in frames:
                    if isinstance(frame, Mapping) and isinstance(
                        frame.get("boxes"), list
                    ):
                        derived_boxes += len(frame["boxes"])
                        for box in frame["boxes"]:
                            if isinstance(box, Mapping) and isinstance(
                                box.get("class"), str
                            ):
                                derived_classes.add(box["class"])
    derived_ids = sorted(set(derived_ids))
    checks["hash_verified"] = (
        checks["hash_verified"]
        and bool(derived_ids)
        and derived_classes == set(_EXPECTED_CLASSES)
    )
    if coverage_doc and checks["coverage_verified"]:
        checks["coverage_verified"] = (
            checks["coverage_verified"]
            and coverage_doc.get("expected_recording_ids") == derived_ids
            and coverage_doc.get("covered_recording_ids") == derived_ids
            and coverage_doc.get("labeled_frame_count") == derived_frames
            and coverage_doc.get("box_count") == derived_boxes
        )

    return checks, list(classes) if isinstance(classes, list) else []


def build_industreal_capability_artifact(
    registry: Mapping[str, Any], *, registry_path: str | Path
) -> dict[str, Any]:
    """Build a deterministic capability decision from a registry."""

    path = Path(registry_path)
    errors: list[str] = []
    actual: Mapping[str, Any] = registry
    if not path.is_file():
        errors.append(f"registry path missing: {path}")
    else:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, Mapping):
                raise ValueError("registry is not an object")
            actual = loaded
            if loaded != registry:
                errors.append("registry argument does not match registry path")
            if loaded.get("canonical_sha256") != _registry_sha256(loaded):
                errors.append("registry canonical_sha256 does not verify")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"registry unreadable: {exc}")
    od = _od_provenance(actual)
    checks, evidence_classes = _od_evidence_checks(od, base_dir=path.parent)
    eligible = all(checks.values()) and not errors
    decision = {
        "od_checks": checks,
        "capability_status": SIX_CLASS_DETECTION
        if eligible
        else ACTION_STEP_STATE_ONLY,
        "action_step_state_only": not eligible,
        "detection_metric_policy": "ALLOWED" if eligible else "PROHIBITED",
    }
    source_hash = actual.get("canonical_sha256")
    if (
        not isinstance(source_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
    ):
        source_hash = ""
    classes = evidence_classes
    artifact: dict[str, Any] = {
        "schema_version": "industreal-capability-1.0.0",
        "status": "PASS" if not errors else "BLOCKED",
        "capability_status": decision["capability_status"],
        "action_step_state_only": decision["action_step_state_only"],
        "allowed_result_layers": ["action", "step", "state"]
        + (["six_class_detection"] if eligible else []),
        "six_class_detection_allowed": eligible,
        "detection_metrics_allowed": eligible,
        "detection_metric_policy": decision["detection_metric_policy"],
        "prohibited_detection_metrics": (
            []
            if eligible
            else ["six_class_detection_mAP", "six_class_detection_Recall"]
        ),
        "detection_evaluation_reason": (
            "OD_labels provenance, schema, hash, coverage, and the exact "
            "six-class schema are independently verified."
            if eligible
            else "OD_labels provenance, schema, hash, and coverage are not "
            "independently verified; README or inventory mentions do not "
            "establish detection truth."
        ),
        "od_labels": {
            "status": "VERIFIED" if eligible else "UNVERIFIED",
            "provenance_verified": checks["provenance_verified"],
            "schema_verified": checks["schema_verified"],
            "hash_verified": checks["hash_verified"],
            "coverage_verified": checks["coverage_verified"],
            "class_count": len(evidence_classes) if evidence_classes else None,
            "classes": list(classes) if isinstance(classes, (list, tuple)) else [],
        },
        "source_registry": {
            # POSIX separators keep the frozen artifact byte-identical across
            # Windows and Linux checkouts.
            "path": Path(registry_path).as_posix(),
            "canonical_sha256": source_hash,
            "status": actual.get("status", "UNKNOWN"),
            "video_count": actual.get("video_count"),
            "action_labeled_recording_count": actual.get(
                "action_labeled_recording_count"
            ),
            "complete_job_id_status": actual.get("complete_job_id_status"),
            "operator_mapping_status": actual.get("operator_mapping_status"),
        },
        "source_evidence": {
            name: {"path": raw, "sha256": supplied}
            for name in ("provenance", "schema", "coverage", "source")
            for raw, supplied in [_bound(od, name)]
        },
        "decision_checks": decision["od_checks"],
        "errors": errors,
        "artifact_sha256": "",
    }
    artifact["artifact_sha256"] = canonical_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    return artifact


def _rebuild_from_sources(capability: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild a persisted artifact from its bound registry and evidence files."""
    registry = capability.get("source_registry")
    if not isinstance(registry, Mapping):
        raise ValueError("source_registry missing")
    registry_path = registry.get("path")
    if not isinstance(registry_path, str) or not registry_path:
        raise ValueError("registry path missing")
    # Accept legacy Windows separators so artifacts frozen before portable
    # paths were introduced can still be reopened for validation.
    path = Path(registry_path.replace("\\", "/"))
    if not path.is_file():
        raise FileNotFoundError(path)
    loaded = _load_json(path)
    if loaded is None:
        raise ValueError("registry unreadable")
    evidence = capability.get("source_evidence")
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "provenance", "schema", "coverage", "source"
    }:
        raise ValueError("source evidence schema mismatch")
    od = _od_provenance(loaded)
    for name in evidence:
        bound = evidence[name]
        if not isinstance(bound, Mapping):
            raise ValueError("source evidence binding malformed")
        raw, supplied = _bound(od, name)
        if bound.get("path") != raw or bound.get("sha256") != supplied:
            raise ValueError("source evidence binding mismatch")
    return build_industreal_capability_artifact(loaded, registry_path=path)


def validate_industreal_capability(
    capability: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    """Reopen every bound source and require exact artifact reconstruction."""
    reasons: list[str] = []
    if (
        not isinstance(capability, Mapping)
        or capability.get("schema_version") != "industreal-capability-1.0.0"
    ):
        return False, ["schema_version"]
    supplied = capability.get("artifact_sha256")
    body = {key: value for key, value in capability.items() if key != "artifact_sha256"}
    if not isinstance(supplied, str) or supplied != canonical_sha256(body):
        reasons.append("artifact_sha256")
    try:
        rebuilt = _rebuild_from_sources(capability)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError):
        reasons.append("source_rebuild")
    else:
        if rebuilt != dict(capability):
            reasons.append("source_rebuild_mismatch")
    return not reasons, reasons


def write_capability_artifact(path: str | Path, artifact: Mapping[str, Any]) -> None:
    """Atomically write and verify a capability artifact as UTF-8 LF JSON."""

    expected = canonical_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    if artifact.get("artifact_sha256") != expected:
        raise ValueError("capability artifact hash mismatch")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(artifact), ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
