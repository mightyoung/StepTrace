"""Machine-readable G1/G2 joint gate decisions.

This module is intentionally fail closed: G1/G2 passage may authorize G3
freeze-package construction, but actual G3 execution requires a separate
run-admission receipt and is never authorized by this gate alone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
import zipfile

from display_project.data.industreal_capability import validate_industreal_capability
from display_project.data.meccano_yolo_audit import validate_meccano_annotation_sources


JOINT_GATE_SCHEMA = "g1-g2-joint-gate-1.1.0"
METHOD_NAMES = (
    "fixed_streak",
    "hmm_or_crf",
    "vlm_only",
    "yolo",
    "grounding_dino",
    "no_calibration_certificate",
    "proposed",
)
GATE_PERMISSIONS = frozenset(
    {
        "freeze_paired_metrics_allowed",
        "freeze_method_inventory_allowed",
        "freeze_thresholds_allowed",
        "freeze_mechanism_gate_manifest_allowed",
        "execute_g3_method_panel_allowed",
    }
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ALL_PERMISSION_KEYS = frozenset(
    (*GATE_PERMISSIONS, "pilot_power_formal_test_cross_task_allowed")
)
_G1_CHECK_KEYS = frozenset(
    {
        "all_seven_methods_preflight_ready",
        "fixed_streak_parameters_frozen",
        "preflight_frozen_anchors_valid",
        "risk_calibration_artifact_valid",
        "hmm_fit_artifact_valid",
        "yolo_best_weight_valid",
    }
)
_G2_CHECK_KEYS = frozenset(
    {
        "official_dataset_qa_pass",
        "held_out_09_absent",
        "candidate_payload_hash_valid",
        "candidate_dataset_hashes_match_qa",
        "video_isolation",
        "adjacent_frame_isolation",
        "operator_identity_complete",
        "complete_job_identity_complete",
        "source_identity_manifest_ready",
        "candidate_formally_admissible",
        "industreal_capability_audited",
        "manual_annotation_count_zero",
    }
)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_hash(value: Any, *, allow_zero: bool = False) -> bool:
    return (
        isinstance(value, str)
        and HASH_RE.fullmatch(value) is not None
        and (allow_zero or value != "0" * 64)
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_source_evidence(
    path: str | Path, payload: Mapping[str, Any]
) -> dict[str, Any]:
    # Keep repository-relative paths relative so the frozen commitment stays
    # machine-independent; absolute inputs (e.g. test fixtures) stay absolute.
    raw = Path(path)
    target = raw.resolve()
    loaded = load_json(target)
    canonical_sha256 = _canonical_sha256(loaded)
    if canonical_sha256 != _canonical_sha256(payload):
        raise ValueError(f"input payload does not match source file: {target}")
    return {
        "path": (target if raw.is_absolute() else raw).as_posix(),
        "file_sha256": _file_sha256(target),
        "canonical_sha256": canonical_sha256,
    }


def _all_methods_ready(preflight: Mapping[str, Any]) -> bool:
    methods = preflight.get("methods")
    return (
        preflight.get("status") == "READY"
        and preflight.get("blocked") is False
        and isinstance(methods, list)
        and len(methods) == 7
        and [method.get("name") for method in methods if isinstance(method, Mapping)]
        == list(METHOD_NAMES)
        and all(
            isinstance(method, Mapping)
            and method.get("status") == "READY"
            and method.get("error_code") is None
            and _is_hash(method.get("config_hash"))
            and _is_hash(method.get("model_hash"))
            for method in methods
        )
    )


def _fixed_streak_frozen(preflight: Mapping[str, Any]) -> bool:
    anchors = preflight.get("frozen_anchors")
    fixed = anchors.get("fixed_streak") if isinstance(anchors, Mapping) else None
    return isinstance(fixed, Mapping) and dict(fixed) == {
        "confirm_frames": 4,
        "cooldown_frames": 12,
    }


def _frozen_anchors_valid(preflight: Mapping[str, Any]) -> bool:
    anchors = preflight.get("frozen_anchors")
    if not isinstance(anchors, Mapping):
        return False
    required_hashes = {
        "manifest_hash",
        "task_source_hash",
        "task_config_hash",
        "code_hash",
        "environment_hash",
        "annotation_hash",
        "partition_hash",
    }
    return (
        all(_is_hash(anchors.get(key)) for key in required_hashes)
        and anchors.get("schedule") == list(METHOD_NAMES)
        and isinstance(anchors.get("task_id"), str)
        and bool(str(anchors.get("task_id")).strip())
    )


def _artifact_result(path: str | Path) -> dict[str, Any]:
    return {"path": Path(path).as_posix(), "status": "FAIL"}


def _validate_calibration_artifact(path: str | Path) -> dict[str, Any]:
    result = _artifact_result(path)
    target = Path(path)
    try:
        from display_project.risk import RiskCalibrator

        payload = load_json(target)
        RiskCalibrator.from_artifact(payload)
        result.update(
            status="PASS",
            file_sha256=_file_sha256(target),
            artifact_hash=payload["artifact_hash"],
            partition_hash=payload["calibration_partition_hash"],
        )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _validate_hmm_artifact(path: str | Path) -> dict[str, Any]:
    result = _artifact_result(path)
    target = Path(path)
    try:
        from display_project.experiments.hmm_sequence import load_hmm_artifact

        payload = load_hmm_artifact(target)
        result.update(
            status="PASS",
            file_sha256=_file_sha256(target),
            artifact_hash=payload["artifact_hash"],
            partition_hash=payload["fit_provenance"]["partition_manifest_hash"],
        )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _load_yolo_model(path: Path):
    try:
        import torch
        from ultralytics import YOLO
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("Ultralytics/torch runtime unavailable") from exc
    return YOLO(str(path)), torch


def _validate_yolo_artifact(
    path: str | Path, *, expected_dataset_hash: Any
) -> dict[str, Any]:
    result = _artifact_result(path)
    target = Path(path)
    provenance_path = Path(f"{target}.provenance.json")
    result["provenance_path"] = provenance_path.as_posix()
    common_required = {
        "schema_version",
        "status",
        "artifact_sha256",
        "dataset_content_sha256",
        "base_model_sha256",
        "training_run_id",
        "split_role",
        "training_completed",
    }
    profile_required = {
        "task_profile",
        "class_names",
        "ultralytics_version",
        "training_parameters",
    }
    try:
        if not target.is_file() or target.stat().st_size < 1024:
            raise ValueError("YOLO artifact is missing or implausibly small")
        if not zipfile.is_zipfile(target):
            raise ValueError("YOLO artifact is not a PyTorch zip checkpoint")
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
        if not any(name.endswith("/data.pkl") for name in names) or not any(
            name.endswith("/version") for name in names
        ):
            raise ValueError("YOLO checkpoint structure is invalid")
        try:
            model, torch = _load_yolo_model(target)
        except Exception as exc:
            raise ValueError("YOLO checkpoint could not be loaded safely") from exc
        graph = getattr(model, "model", None)
        if graph is None or not isinstance(graph, torch.nn.Module):
            raise ValueError("YOLO checkpoint did not load a torch model graph")
        task = getattr(model, "task", None) or getattr(graph, "task", None)
        if task not in (None, "detect"):
            raise ValueError("YOLO checkpoint is not a detection model")
        provenance = load_json(provenance_path)
        schema_version = provenance.get("schema_version")
        expected_fields = (
            common_required
            if schema_version == "display_project.yolo_training_artifact-1.0.0"
            else common_required | profile_required
            if schema_version == "display_project.yolo_training_artifact-1.1.0"
            else set()
        )
        if not expected_fields or set(provenance) != expected_fields:
            raise ValueError("YOLO provenance schema mismatch")
        if schema_version == "display_project.yolo_training_artifact-1.1.0":
            task_profile = provenance["task_profile"]
            class_names = provenance["class_names"]
            ultralytics_version = provenance["ultralytics_version"]
            training_parameters = provenance["training_parameters"]
            required_training_parameters = {
                "imgsz",
                "epochs",
                "batch",
                "workers",
                "device",
                "seed",
                "deterministic",
            }
            if (
                not isinstance(task_profile, str)
                or not task_profile.strip()
                or not isinstance(class_names, list)
                or not class_names
                or len(class_names) != len(set(class_names))
                or any(
                    not isinstance(name, str) or not name.strip()
                    for name in class_names
                )
                or not isinstance(ultralytics_version, str)
                or not ultralytics_version.strip()
                or not isinstance(training_parameters, Mapping)
                or set(training_parameters) != required_training_parameters
                or not isinstance(training_parameters["imgsz"], int)
                or training_parameters["imgsz"] <= 0
                or not isinstance(training_parameters["epochs"], int)
                or training_parameters["epochs"] <= 0
                or not isinstance(training_parameters["batch"], int)
                or training_parameters["batch"] <= 0
                or not isinstance(training_parameters["workers"], int)
                or training_parameters["workers"] < 0
                or not isinstance(training_parameters["device"], str)
                or not training_parameters["device"].strip()
                or not isinstance(training_parameters["seed"], int)
                or training_parameters["deterministic"] is not True
            ):
                raise ValueError("YOLO task-profile provenance validation failed")
        artifact_sha256 = _file_sha256(target)
        if (
            provenance["status"] != "TRAINED"
            or provenance["training_completed"] is not True
            or provenance["split_role"] != "TRAIN"
            or provenance["artifact_sha256"] != artifact_sha256
            or not _is_hash(provenance["base_model_sha256"])
            or not isinstance(provenance["training_run_id"], str)
            or not provenance["training_run_id"].strip()
            or not _is_hash(provenance["dataset_content_sha256"])
            or provenance["dataset_content_sha256"] != expected_dataset_hash
        ):
            raise ValueError("YOLO training provenance validation failed")
        result.update(
            status="PASS",
            file_sha256=artifact_sha256,
            dataset_content_sha256=provenance["dataset_content_sha256"],
            base_model_sha256=provenance["base_model_sha256"],
            training_run_id=provenance["training_run_id"],
            provenance_schema_version=schema_version,
        )
        if schema_version == "display_project.yolo_training_artifact-1.1.0":
            result.update(
                task_profile=provenance["task_profile"],
                class_names=provenance["class_names"],
            )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _embedded_hash_valid(payload: Mapping[str, Any], field: str) -> bool:
    expected = payload.get(field)
    if not _is_hash(expected):
        return False
    body = {key: value for key, value in payload.items() if key != field}
    return expected == _canonical_sha256(body)


def _candidate_manifest_valid(
    candidate: Mapping[str, Any],
    dataset_qa: Mapping[str, Any],
    candidate_manifest_path: str | Path,
) -> bool:
    records = candidate.get("records")
    if (
        not isinstance(records, list)
        or len(records) != 19
        or candidate.get("schema_version") != "1.0.0"
    ):
        return False
    if candidate_manifest_path is None:
        return False
    target = Path(candidate_manifest_path).resolve()
    try:
        source_reference = Path(str(candidate["source_identity_manifest"]))
        if source_reference.is_absolute() or ".." in source_reference.parts:
            return False
        repository_root = target.parents[2]
        source_path = (repository_root / source_reference).resolve()
        source_path.relative_to(repository_root)
        source = load_json(source_path)
        if _file_sha256(source_path) != candidate.get(
            "source_identity_manifest_sha256"
        ):
            return False
        if source.get("schema_version") != "1.1.0" or not isinstance(
            source.get("records"), list
        ):
            return False
        dataset_status = source.get("dataset_identity_status")
        expected_meccano_status = (
            dataset_status.get("MECCANO")
            if isinstance(dataset_status, Mapping)
            else source.get("status")
        )
        if candidate.get("source_identity_manifest_status") != expected_meccano_status:
            return False
    except (OSError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not _is_hash(candidate.get("source_identity_manifest_sha256")) or not _is_hash(
        candidate.get("conversion_report_sha256")
    ):
        return False
    if (
        not isinstance(candidate.get("source_identity_manifest"), str)
        or not candidate["source_identity_manifest"].strip()
    ):
        return False
    if candidate.get("conversion_report_sha256") != dataset_qa.get(
        "conversion_report_sha256"
    ):
        return False
    qa_videos = dataset_qa.get("videos")
    qa_counts = dataset_qa.get("video_frame_counts")
    if not isinstance(qa_videos, Mapping) or not isinstance(qa_counts, Mapping):
        return False
    seen = set()
    source_records = {
        str(r.get("video_id")): r for r in source["records"] if isinstance(r, Mapping)
    }
    for record in records:
        if not isinstance(record, Mapping):
            return False
        vid = str(record.get("video_id", ""))
        if (
            vid in seen
            or vid in {"0009", "09"}
            or record.get("tombstone_id") not in ("NONE", None)
        ):
            return False
        seen.add(vid)
        src = source_records.get(vid)
        if src is None or any(
            record.get(key) != src.get(key)
            for key in (
                "asset_id",
                "canonical_root_id",
                "canonical_group_id",
                "video_id",
                "operator_id",
                "complete_job_id",
                "tombstone_id",
            )
        ):
            return False
        split = record.get("dataset_split")
        if split not in qa_videos or str(record.get("dataset_video_id")) not in {
            str(v) for v in qa_videos[split]
        }:
            return False
        split_counts = qa_counts.get(split)
        if not isinstance(split_counts, Mapping) or record.get(
            "frame_count"
        ) != split_counts.get(str(record.get("dataset_video_id"))):
            return False
        if (
            record.get("identity_values_invented") is not False
            or record.get("adjacent_frames_inherit_video_split") is not True
        ):
            return False
    return (
        len(seen) == 19
        and candidate.get("dataset_content_sha256") == dataset_qa.get("content_sha256")
        and candidate.get("file_manifest_sha256")
        == dataset_qa.get("file_manifest_sha256")
    )


def _dataset_qa_valid(dataset_qa: Mapping[str, Any]) -> bool:
    crosscheck = dataset_qa.get("conversion_crosscheck")
    provenance, provenance_errors = validate_meccano_annotation_sources(
        dataset_qa.get("annotation_source_provenance"),
        splits=dataset_qa.get("videos", {}),
        held_out_videos=dataset_qa.get("held_out_videos", ["09"]),
    )
    return (
        dataset_qa.get("schema_version") == "1.0.0"
        and dataset_qa.get("status") == "PASS"
        and dataset_qa.get("passed") is True
        and dataset_qa.get("errors") == []
        and dataset_qa.get("decode_failures") == 0
        and dataset_qa.get("invalid_labels") == 0
        and dataset_qa.get("missing_label_pairs") == 0
        and dataset_qa.get("missing_image_pairs") == 0
        and dataset_qa.get("duplicate_image_hash_count") == 0
        and dataset_qa.get("video_cross_split") == {}
        and isinstance(crosscheck, Mapping)
        and crosscheck.get("status") == "PASS"
        and crosscheck.get("mismatches") == []
        and provenance_errors == []
        and provenance.get("status") == "PASS"
        and provenance.get("source_count") == 22
        and _is_hash(dataset_qa.get("content_sha256"))
        and _is_hash(dataset_qa.get("file_manifest_sha256"))
    )


def _industreal_capability_valid(capability: Mapping[str, Any], capability_path: str | Path) -> bool:
    try:
        persisted = load_json(capability_path)
        if dict(persisted) != dict(capability):
            return False
        valid, _ = validate_industreal_capability(persisted)
        if not valid:
            return False
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False
    if (
        capability.get("schema_version") != "industreal-capability-1.0.0"
        or capability.get("status") != "PASS"
        or not _embedded_hash_valid(capability, "artifact_sha256")
    ):
        return False
    checks = capability.get("decision_checks")
    if not isinstance(checks, Mapping) or set(checks) != {
        "provenance_verified",
        "schema_verified",
        "hash_verified",
        "coverage_verified",
        "six_class_schema",
    }:
        return False
    if capability.get("detection_metric_policy") == "ALLOWED":
        return (
            capability.get("capability_status") == "SIX_CLASS_DETECTION"
            and capability.get("six_class_detection_allowed") is True
            and capability.get("detection_metrics_allowed") is True
            and checks.get("provenance_verified") is True
            and checks.get("schema_verified") is True
            and checks.get("hash_verified") is True
            and checks.get("coverage_verified") is True
            and checks.get("six_class_schema") is True
        )
    registry = capability.get("source_registry")
    if (
        not isinstance(registry, Mapping)
        or not isinstance(registry.get("path"), str)
        or not _is_hash(registry.get("canonical_sha256"))
        or registry.get("status") != "BLOCKED"
        or registry.get("video_count") != 86
        or registry.get("action_labeled_recording_count") != 84
        or registry.get("complete_job_id_status") != "COMPLETE_RECORDING_BOUND"
        or registry.get("operator_mapping_status") != "UNKNOWN"
    ):
        return False
    return (
        capability.get("detection_metric_policy") == "PROHIBITED"
        and capability.get("capability_status") == "ACTION_STEP_STATE_ONLY"
        and capability.get("six_class_detection_allowed") is False
        and capability.get("detection_metrics_allowed") is False
        and capability.get("action_step_state_only") is True
    )


def verify_joint_gate_permission(
    source: str | Path | Mapping[str, Any], *, permission: str
) -> dict[str, Any]:
    """Validate a persisted joint decision and require one explicit permission."""

    if permission not in GATE_PERMISSIONS:
        raise ValueError(f"unsupported joint-gate permission: {permission}")
    decision = load_json(source) if isinstance(source, (str, Path)) else dict(source)
    if decision.get("schema_version") != JOINT_GATE_SCHEMA:
        raise ValueError("joint G1/G2 gate schema mismatch")
    claimed_hash = decision.get("decision_sha256")
    body = {key: value for key, value in decision.items() if key != "decision_sha256"}
    if not _is_hash(claimed_hash) or claimed_hash != _canonical_sha256(body):
        raise ValueError("joint G1/G2 gate hash mismatch")
    commitments = decision.get("input_commitments")
    required_sources = {
        "preflight",
        "dataset_qa",
        "candidate_manifest",
        "industreal_capability",
        "cvat_audit",
    }
    if not isinstance(commitments, Mapping) or set(commitments) != required_sources:
        raise ValueError("joint G1/G2 input commitments missing or invalid")
    loaded_inputs: dict[str, dict[str, Any]] = {}
    for name in required_sources:
        evidence = commitments.get(name)
        if (
            not isinstance(evidence, Mapping)
            or set(evidence) != {"path", "file_sha256", "canonical_sha256"}
            or not isinstance(evidence.get("path"), str)
            or not _is_hash(evidence.get("file_sha256"))
            or not _is_hash(evidence.get("canonical_sha256"))
        ):
            raise ValueError("joint G1/G2 input source evidence incomplete")
        input_path = Path(evidence["path"])
        resolved_path = input_path
        if not input_path.is_absolute() and not input_path.is_file():
            # Relative commitments are repository-root relative. When the
            # caller is not running from the repository root, derive the root
            # from the decision file location (<root>/research/audits/*.json).
            if isinstance(source, (str, Path)):
                candidate_root = Path(source).resolve().parent.parent.parent
                candidate = candidate_root / input_path
                if candidate.is_file():
                    resolved_path = candidate
        loaded = load_json(resolved_path)
        if not loaded:
            raise ValueError("joint G1/G2 input document is empty")
        actual = _input_source_evidence(resolved_path, loaded)
        actual["path"] = input_path.as_posix()
        if dict(evidence) != actual:
            raise ValueError("joint G1/G2 input source evidence hash mismatch")
        loaded_inputs[name] = loaded
    artifacts = (
        decision.get("g1", {}).get("artifacts")
        if isinstance(decision.get("g1"), Mapping)
        else None
    )
    if not isinstance(artifacts, Mapping):
        raise ValueError("joint G1/G2 artifact evidence missing")
    artifact_paths: dict[str, Path] = {}
    for name in ("risk_calibration", "hmm_or_crf", "yolo"):
        evidence = artifacts.get(name)
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("status") != "PASS"
            or not isinstance(evidence.get("path"), str)
        ):
            raise ValueError("joint G1/G2 artifact evidence incomplete")
        artifact_path = Path(evidence["path"])
        if not artifact_path.is_file() or evidence.get("file_sha256") != _file_sha256(artifact_path):
            raise ValueError("joint G1/G2 artifact evidence hash mismatch")
        artifact_paths[name] = artifact_path

    reconstructed = build_joint_gate_decision(
        preflight=loaded_inputs["preflight"],
        dataset_qa=loaded_inputs["dataset_qa"],
        candidate_manifest=loaded_inputs["candidate_manifest"],
        industreal_capability=loaded_inputs["industreal_capability"],
        cvat_audit=loaded_inputs["cvat_audit"],
        calibration_artifact=artifact_paths["risk_calibration"],
        hmm_artifact=artifact_paths["hmm_or_crf"],
        yolo_artifact=artifact_paths["yolo"],
        preflight_path=commitments["preflight"]["path"],
        dataset_qa_path=commitments["dataset_qa"]["path"],
        candidate_manifest_path=commitments["candidate_manifest"]["path"],
        industreal_capability_path=commitments["industreal_capability"]["path"],
        cvat_audit_path=commitments["cvat_audit"]["path"],
    )
    if decision != reconstructed:
        raise ValueError("joint G1/G2 persisted decision does not match reconstruction")
    for gate_name in ("g1", "g2"):
        gate = decision.get(gate_name)
        checks = gate.get("checks") if isinstance(gate, Mapping) else None
        if (
            not isinstance(gate, Mapping)
            or not isinstance(checks, Mapping)
            or set(checks) != (_G1_CHECK_KEYS if gate_name == "g1" else _G2_CHECK_KEYS)
            or gate.get("status") != "PASS"
            or not checks
            or not all(value is True for value in checks.values())
            or gate.get("failed_checks") != []
        ):
            raise ValueError(f"joint G1/G2 gate {gate_name.upper()} is not PASS")
    if set(key for key in decision if key.endswith("_allowed")) != _ALL_PERMISSION_KEYS:
        raise ValueError("joint G1/G2 permission schema mismatch")
    expected_action = (
        "AUTHORIZED"
        if decision.get("status") == "PASS"
        else "CONDITION_UNMET_AND_ACTIONS_NOT_PERFORMED"
    )
    if decision.get("action_state") != expected_action:
        raise ValueError("joint G1/G2 action state mismatch")
    if (
        decision.get("status") != "PASS"
        or decision.get("joint_g1_g2_pass") is not True
        or decision.get(permission) is not True
    ):
        raise ValueError(f"joint G1/G2 gate does not allow {permission}")
    if decision.get("pilot_power_formal_test_cross_task_allowed") is not False:
        raise ValueError("joint G1/G2 gate may not authorize downstream G3 actions")
    return decision


def build_joint_gate_decision(
    *,
    preflight: Mapping[str, Any],
    dataset_qa: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
    industreal_capability: Mapping[str, Any],
    cvat_audit: Mapping[str, Any],
    calibration_artifact: str | Path,
    hmm_artifact: str | Path,
    yolo_artifact: str | Path,
    preflight_path: str | Path,
    dataset_qa_path: str | Path,
    candidate_manifest_path: str | Path,
    industreal_capability_path: str | Path,
    cvat_audit_path: str | Path,
) -> dict[str, Any]:
    """Return a deterministic fail-closed G1/G2 decision."""

    calibration_validation = _validate_calibration_artifact(calibration_artifact)
    hmm_validation = _validate_hmm_artifact(hmm_artifact)
    yolo_validation = _validate_yolo_artifact(
        yolo_artifact,
        expected_dataset_hash=dataset_qa.get("content_sha256"),
    )
    g1_checks = {
        "all_seven_methods_preflight_ready": _all_methods_ready(preflight),
        "fixed_streak_parameters_frozen": _fixed_streak_frozen(preflight),
        "preflight_frozen_anchors_valid": _frozen_anchors_valid(preflight),
        "risk_calibration_artifact_valid": (calibration_validation["status"] == "PASS"),
        "hmm_fit_artifact_valid": hmm_validation["status"] == "PASS",
        "yolo_best_weight_valid": yolo_validation["status"] == "PASS",
    }
    candidate_checks = candidate_manifest.get("checks")
    if not isinstance(candidate_checks, Mapping):
        candidate_checks = {}
    candidate_source_ready = (
        candidate_manifest.get("source_identity_manifest_status") == "READY"
    )
    g2_checks = {
        "official_dataset_qa_pass": _dataset_qa_valid(dataset_qa),
        "held_out_09_absent": (
            dataset_qa.get("held_out_test_assets") == []
            and dataset_qa.get("held_out_videos") == ["09"]
            and isinstance(dataset_qa.get("videos"), Mapping)
            and "09" not in dataset_qa["videos"].get("test", [])
        ),
        "candidate_payload_hash_valid": _embedded_hash_valid(
            candidate_manifest, "candidate_payload_sha256"
        )
        and _candidate_manifest_valid(
            candidate_manifest, dataset_qa, candidate_manifest_path
        ),
        "candidate_dataset_hashes_match_qa": (
            candidate_manifest.get("dataset_content_sha256")
            == dataset_qa.get("content_sha256")
            and candidate_manifest.get("file_manifest_sha256")
            == dataset_qa.get("file_manifest_sha256")
        ),
        "video_isolation": candidate_checks.get("video_isolation") == "PASS",
        "adjacent_frame_isolation": (
            candidate_checks.get("adjacent_frame_isolation") == "PASS"
        ),
        "operator_identity_complete": (
            candidate_checks.get("operator_identity_completeness") == "PASS"
        ),
        "complete_job_identity_complete": (
            candidate_checks.get("complete_job_identity_completeness") == "PASS"
        ),
        "source_identity_manifest_ready": candidate_source_ready,
        "candidate_formally_admissible": (
            candidate_manifest.get("status") == "DEVELOPMENT_CANDIDATE"
            and candidate_manifest.get("formal_admission") == "ADMITTED"
            and candidate_manifest.get("formal_admission_scope")
            == "ELIGIBLE_FOR_FUTURE_FORMAL_PARTITION_NOT_ASSIGNED"
            and candidate_manifest.get("formal_use_currently_allowed") is False
            and candidate_source_ready
        ),
        "industreal_capability_audited": _industreal_capability_valid(
            industreal_capability, industreal_capability_path
        ),
        "manual_annotation_count_zero": (
            cvat_audit.get("labels") == 0
            and cvat_audit.get("audit_status") == "BLOCKED"
        ),
    }
    g1_pass = all(g1_checks.values())
    g2_pass = all(g2_checks.values())
    joint_pass = g1_pass and g2_pass
    decision: dict[str, Any] = {
        "schema_version": JOINT_GATE_SCHEMA,
        "status": "PASS" if joint_pass else "BLOCKED",
        "action_state": (
            "AUTHORIZED" if joint_pass else "CONDITION_UNMET_AND_ACTIONS_NOT_PERFORMED"
        ),
        "input_commitments": {
            "preflight": _input_source_evidence(preflight_path, preflight),
            "dataset_qa": _input_source_evidence(dataset_qa_path, dataset_qa),
            "candidate_manifest": _input_source_evidence(
                candidate_manifest_path, candidate_manifest
            ),
            "industreal_capability": _input_source_evidence(
                industreal_capability_path, industreal_capability
            ),
            "cvat_audit": _input_source_evidence(cvat_audit_path, cvat_audit),
        },
        "g1": {
            "status": "PASS" if g1_pass else "BLOCKED",
            "checks": g1_checks,
            "failed_checks": [name for name, passed in g1_checks.items() if not passed],
            "artifacts": {
                "risk_calibration": calibration_validation,
                "hmm_or_crf": hmm_validation,
                "yolo": yolo_validation,
            },
        },
        "g2": {
            "status": "PASS" if g2_pass else "BLOCKED",
            "checks": g2_checks,
            "failed_checks": [name for name, passed in g2_checks.items() if not passed],
            "candidate_status": candidate_manifest.get("status"),
        },
        "joint_g1_g2_pass": joint_pass,
        "freeze_paired_metrics_allowed": joint_pass,
        "freeze_method_inventory_allowed": joint_pass,
        "freeze_thresholds_allowed": joint_pass,
        "freeze_mechanism_gate_manifest_allowed": joint_pass,
        # G1/G2 is a prerequisite for, not an authorization of, a G3 run.
        # A separate, narrowly bound run-admission receipt owns this permission.
        "execute_g3_method_panel_allowed": False,
        "pilot_power_formal_test_cross_task_allowed": False,
        "prohibited_while_blocked": [
            "paired_metric_freeze",
            "method_or_threshold_freeze",
            "mechanism_gate_manifest_freeze",
            "G3_method_panel_execution",
            "pilot",
            "power_analysis",
            "formal_test",
            "cross_task_generalization",
        ]
        if not joint_pass
        else [
            "G3_method_panel_execution_without_run_admission",
            "pilot",
            "power_analysis",
            "formal_test",
            "cross_task_generalization",
        ],
    }
    decision["decision_sha256"] = _canonical_sha256(decision)
    return decision


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_decision(path: str | Path, decision: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(decision), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
