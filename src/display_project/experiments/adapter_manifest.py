"""Versioned, fail-closed adapter manifest compiler for paired runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
from typing import Any, Callable, Mapping

import yaml  # type: ignore[import-untyped]

from display_project.sop.task_config import (
    CompiledTask,
    compile_task_config,
    load_task_config,
)
from .paired_runner import METHOD_NAMES, MethodSpec

SCHEMA_VERSION = 1
G3_METHOD_NAMES = ("fixed_streak", "hmm_or_crf", "proposed")
ALLOWED_PROFILES = frozenset(
    {
        "SOFTWARE_FIXTURE",
        "DEVELOPMENT_ONLY",
        "DEVELOPMENT_TUNING",
        "DEVELOPMENT_G3_EVALUATION",
    }
)
FORBIDDEN_PROFILES = frozenset({"PILOT", "FORMAL", "FORMAL_TEST_SEALED"})
FACTORIES = frozenset({"unavailable", "vlm_audit_only", "render_sop", "hmm_sequence"})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
TOP_KEYS = frozenset(
    {
        "schema_version",
        "profile",
        "task_config",
        "fixed_streak",
        "schedule",
        "anchors",
        "methods",
    }
)
ANCHOR_KEYS = frozenset(
    {"code_hash", "environment_hash", "annotation_hash", "partition_hash"}
)
METHOD_KEYS = frozenset(
    {"name", "factory", "config", "seed", "timeout_s", "model_hash"}
)
CONFIG_KEYS = {
    "hmm_sequence": frozenset(
        {
            "fit_artifact",
            "expected_fit_artifact_hash",
            "expected_fit_partition_hash",
            "task_config_path",
        }
    ),
    "unavailable": frozenset({"reason", "fit_artifact", "fit_artifact_hash"}),
    "vlm_audit_only": frozenset({"review_json"}),
    "render_sop": frozenset(
        {
            "policy",
            "processor_name",
            "detector_kind",
            "yolo_image_size",
            "yolo_iou_threshold",
            "model_path",
            "segmenter_path",
            "font_path",
            "task_config_path",
            "output_root",
            "device",
            "calibration_artifact",
            "calibration_artifact_hash",
            "calibration_partition_hash",
            "raw_risk_threshold",
        }
    ),
}


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class MethodPreflight:
    name: str
    status: str
    error_code: str | None = None
    reason: str = ""
    config_hash: str = ""
    model_hash: str = ""


@dataclass(frozen=True)
class CompiledAdapterManifest:
    schema_version: int
    profile: str
    manifest_hash: str
    task: CompiledTask
    task_source: str
    task_source_hash: str
    task_config_hash: str
    schedule: tuple[str, ...]
    anchors: Mapping[str, str]
    adapters: Mapping[str, Callable[..., Any]]
    method_specs: tuple[MethodSpec, ...]
    preflight: tuple[MethodPreflight, ...]
    raw: Mapping[str, Any]

    @property
    def blocked(self) -> bool:
        return any(item.status == "BLOCKED" for item in self.preflight)

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "status": "BLOCKED" if self.blocked else "READY",
            "blocked": self.blocked,
            "frozen_anchors": {
                "manifest_hash": self.manifest_hash,
                "task_source": self.task_source,
                "task_source_hash": self.task_source_hash,
                "task_config_hash": self.task_config_hash,
                "task_id": self.task.task_id,
                "schedule": list(self.schedule),
                "fixed_streak": {"confirm_frames": 4, "cooldown_frames": 12},
                **dict(self.anchors),
            },
            "methods": [asdict(item) for item in self.preflight],
        }


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = (
            json.load(handle)
            if path.suffix.lower() == ".json"
            else yaml.safe_load(handle)
        )
    if not isinstance(value, Mapping):
        raise ValueError("adapter manifest must be a mapping")
    return dict(value)


def _default_registry() -> dict[str, Callable[..., Any]]:
    # Import errors are deliberately not masked by placeholder adapters.
    from .adapters import (
        ExplicitUnavailableAdapter,
        RenderSopAdapter,
        VlmAuditOnlyAdapter,
    )
    from .hmm_sequence import HMMSequenceAdapter

    return {
        "unavailable": ExplicitUnavailableAdapter,
        "vlm_audit_only": VlmAuditOnlyAdapter,
        "render_sop": RenderSopAdapter,
        "hmm_sequence": HMMSequenceAdapter,
    }


def _reject_forbidden_references(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, str) and (
            re.search(r"(^|[^a-z])(pilot|formal)([^a-z]|$)", item.lower())
            or any(token in item.lower() for token in ("sealed_test", "test_sealed"))
        ):
            raise ValueError(
                "pilot/formal assets are not permitted in adapter manifest"
            )


def _resolve(value: Any, base: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _task(raw: Any, profile: str, base: Path) -> tuple[CompiledTask, str, str, str]:
    if isinstance(raw, str):
        path = _resolve(raw, base)
        if path is None or not path.is_file():
            raise ValueError("task_config path is missing or not a file")
        task = load_task_config(path)
        source, source_hash = str(path), _file_sha256(path)
    elif isinstance(raw, Mapping) and profile == "SOFTWARE_FIXTURE":
        task = compile_task_config(raw)
        source, source_hash = "INLINE_SOFTWARE_FIXTURE", canonical_sha256(raw)
    else:
        raise ValueError(
            "task_config must be an existing path outside SOFTWARE_FIXTURE"
        )
    return task, source, source_hash, canonical_sha256(asdict(task))


def _blocked(
    name: str, code: str, reason: str, config_hash: str, model_hash: str
) -> MethodPreflight:
    return MethodPreflight(name, "BLOCKED", code, reason, config_hash, model_hash)


def _path(
    config: Mapping[str, Any], key: str, base: Path, kind: str = "any"
) -> tuple[Path | None, str | None]:
    path = _resolve(config.get(key), base)
    ok = (
        path is not None
        and ({"file": path.is_file, "dir": path.is_dir}.get(kind, path.exists))()
    )
    return (path, None) if ok else (path, f"{key} does not exist: {path}")


def _output_root(
    config: Mapping[str, Any], base: Path
) -> tuple[Path | None, tuple[str, str] | None]:
    """Validate an output destination without creating it during preflight."""
    path = _resolve(config.get("output_root"), base)
    if path is None:
        return None, (
            "DEPENDENCY_PATH_INVALID",
            "output_root path could not be resolved",
        )
    if path.exists():
        if not path.is_dir():
            return None, (
                "DEPENDENCY_PATH_INVALID",
                f"output_root is not a directory: {path}",
            )
        writable_parent = path
    else:
        writable_parent = path.parent
        while not writable_parent.exists() and writable_parent != writable_parent.parent:
            writable_parent = writable_parent.parent
        if not writable_parent.is_dir():
            return None, (
                "DEPENDENCY_PATH_INVALID",
                f"output_root has no existing parent directory: {path}",
            )
    if not os.access(writable_parent, os.W_OK):
        return None, (
            "OUTPUT_ROOT_NOT_WRITABLE",
            f"{path} (nearest existing directory: {writable_parent})",
        )
    return path, None


BUILTIN_FIXTURE_PROCESSOR = (
    "display_project.experiments.adapters:software_fixture_processor"
)


def _processor_preflight(name: str) -> tuple[bool, str]:
    try:
        from .adapters import _resolve_processor

        _resolve_processor(name)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _render_kwargs(
    name: str,
    config: Mapping[str, Any],
    profile: str,
    base: Path,
    task_hash: str,
    config_hash: str,
    model_hash: str,
) -> tuple[dict[str, Any], tuple[str, str] | None]:
    policy = config.get("policy")
    if policy not in {"fixed_streak", "proposed", "uncalibrated_certificate"}:
        return {}, ("RENDER_POLICY_INVALID", "renderer policy is missing or invalid")
    required = {
        "fixed_streak": "fixed_streak",
        "proposed": "proposed",
        "no_calibration_certificate": "uncalibrated_certificate",
        "yolo": "fixed_streak",
        "grounding_dino": "fixed_streak",
    }.get(name)
    if required is not None and policy != required:
        return {}, ("RENDER_POLICY_MISMATCH", f"{name} requires policy={required}")
    detector_kind = config.get("detector_kind")
    if detector_kind not in {"grounding_dino", "yolo"}:
        return {}, (
            "DETECTOR_KIND_INVALID",
            "detector_kind must be explicitly grounding_dino or yolo",
        )
    required_detector = {"yolo": "yolo", "grounding_dino": "grounding_dino"}.get(name)
    if required_detector is not None and detector_kind != required_detector:
        return {}, (
            "DETECTOR_KIND_MISMATCH",
            f"{name} requires detector_kind={required_detector}",
        )
    if profile != "SOFTWARE_FIXTURE" and "device" not in config:
        return {}, ("DEVICE_NOT_FROZEN", "device must be explicit")
    if detector_kind == "yolo" and not {
        "yolo_image_size",
        "yolo_iou_threshold",
    } <= set(config):
        return {}, (
            "YOLO_CONFIG_MISSING",
            "yolo_image_size and yolo_iou_threshold must be explicit",
        )
    try:
        image_size = int(config.get("yolo_image_size", 1280))
        iou_threshold = float(config.get("yolo_iou_threshold", 0.50))
    except (TypeError, ValueError):
        return {}, ("YOLO_CONFIG_INVALID", "YOLO numeric settings are invalid")
    if image_size <= 0 or not 0.0 <= iou_threshold <= 1.0:
        return {}, ("YOLO_CONFIG_INVALID", "YOLO numeric settings are out of range")
    kwargs: dict[str, Any] = {
        "policy": policy,
        "config_hash": config_hash,
        "model_hash": model_hash,
        "name": name,
        "device": str(config.get("device", "auto")),
        "detector_kind": detector_kind,
        "yolo_image_size": image_size,
        "yolo_iou_threshold": iou_threshold,
    }
    calibration_keys = {
        "calibration_artifact",
        "calibration_artifact_hash",
        "calibration_partition_hash",
    }
    if policy == "uncalibrated_certificate":
        raw_threshold = config.get("raw_risk_threshold")
        if (
            isinstance(raw_threshold, bool)
            or not isinstance(raw_threshold, (int, float))
            or not 0.0 <= float(raw_threshold) <= 1.0
        ):
            return {}, (
                "RAW_RISK_THRESHOLD_INVALID",
                "raw_risk_threshold must be explicit and in [0,1]",
            )
        if calibration_keys & set(config):
            return {}, (
                "UNCALIBRATED_CALIBRATION_FORBIDDEN",
                "uncalibrated_certificate may not reference calibration artifacts",
            )
        kwargs["raw_risk_threshold"] = float(raw_threshold)
    elif "raw_risk_threshold" in config:
        return {}, (
            "RAW_RISK_THRESHOLD_FORBIDDEN",
            "raw_risk_threshold is only valid for uncalibrated_certificate",
        )
    elif policy != "proposed" and calibration_keys & set(config):
        return {}, (
            "CALIBRATION_ANCHOR_FORBIDDEN",
            "calibration anchors are only valid for proposed",
        )
    processor = config.get("processor_name")
    if processor is not None:
        if not isinstance(processor, str) or not processor.strip():
            return {}, ("PROCESSOR_NAME_INVALID", "processor_name must be non-empty")
        if profile != "SOFTWARE_FIXTURE" or processor != BUILTIN_FIXTURE_PROCESSOR:
            return {}, (
                "PROCESSOR_NOT_ALLOWLISTED",
                "only the built-in SOFTWARE_FIXTURE processor is permitted",
            )
        ok, processor_error = _processor_preflight(processor)
        if not ok:
            return {}, ("PROCESSOR_UNAVAILABLE", processor_error)
        kwargs["processor_name"] = processor
    rules = {
        "model_path": "any",
        "segmenter_path": "any",
        "font_path": "file",
        "task_config_path": "file",
    }
    for key, kind in rules.items():
        if profile == "SOFTWARE_FIXTURE" and processor and key not in config:
            continue
        path, path_error = _path(config, key, base, kind)
        if path_error or path is None:
            return {}, (
                "DEPENDENCY_PATH_INVALID",
                path_error or f"{key} path could not be resolved",
            )
        kwargs[key] = str(path)
    if not (profile == "SOFTWARE_FIXTURE" and processor and "output_root" not in config):
        output_root, output_error = _output_root(config, base)
        if output_error or output_root is None:
            return {}, output_error or (
                "DEPENDENCY_PATH_INVALID",
                "output_root path could not be resolved",
            )
        kwargs["output_root"] = str(output_root)
    if (
        kwargs.get("task_config_path")
        and _file_sha256(Path(kwargs["task_config_path"])) != task_hash
    ):
        return {}, (
            "TASK_CONFIG_HASH_MISMATCH",
            "renderer task config differs from manifest task config",
        )
    if policy == "proposed":
        artifact, artifact_error = _path(config, "calibration_artifact", base, "file")
        if artifact_error:
            return {}, ("CALIBRATION_ARTIFACT_MISSING", artifact_error)
        artifact_hash = config.get("calibration_artifact_hash")
        partition_hash = config.get("calibration_partition_hash")
        if not _is_hash(artifact_hash):
            return {}, (
                "CALIBRATION_ARTIFACT_HASH_INVALID",
                "calibration_artifact_hash must be lowercase sha256",
            )
        if not _is_hash(partition_hash):
            return {}, (
                "CALIBRATION_PARTITION_HASH_INVALID",
                "calibration_partition_hash must be lowercase sha256",
            )
        try:
            from display_project.risk import RiskCalibrator

            RiskCalibrator.from_artifact(
                artifact,
                expected_artifact_hash=artifact_hash,
                expected_partition_hash=partition_hash,
            )
        except (OSError, TypeError, ValueError) as exc:
            return {}, ("CALIBRATION_ARTIFACT_INVALID", str(exc))
        kwargs.update(
            calibration_artifact=str(artifact),
            expected_calibration_artifact_hash=artifact_hash,
            expected_partition_hash=partition_hash,
        )
    return kwargs, None


def _path_ownership_errors(
    methods: list[Mapping[str, Any]], base: Path
) -> dict[str, tuple[str, str]]:
    roots: dict[Path, list[str]] = {}
    evidence: dict[Path, list[str]] = {}
    for item in methods:
        name = str(item.get("name"))
        config = item.get("config")
        if not isinstance(config, Mapping):
            continue
        output = _resolve(config.get("output_root"), base)
        if output is not None:
            roots.setdefault(output, []).append(name)
        for key in ("review_json", "fit_artifact", "calibration_artifact"):
            path = _resolve(config.get(key), base)
            if path is not None:
                evidence.setdefault(path, []).append(name)
    errors: dict[str, tuple[str, str]] = {}
    for path, owners in roots.items():
        if len(set(owners)) > 1:
            for owner in owners:
                errors[owner] = (
                    "OUTPUT_ROOT_REUSED",
                    f"output_root is shared by methods {sorted(set(owners))}: {path}",
                )
    for path, owners in evidence.items():
        if len(set(owners)) > 1:
            for owner in owners:
                errors.setdefault(
                    owner,
                    (
                        "EVIDENCE_PATH_REUSED",
                        f"evidence path is shared by methods {sorted(set(owners))}: {path}",
                    ),
                )
        for root, root_owners in roots.items():
            if path == root or path.is_relative_to(root):
                for owner in set(owners + root_owners):
                    errors.setdefault(
                        owner,
                        (
                            "OUTPUT_EVIDENCE_PATH_OVERLAP",
                            f"evidence path {path} is inside output_root {root}",
                        ),
                    )
    return errors


def _perception_anchor_errors(
    methods: list[Mapping[str, Any]], base: Path
) -> dict[str, tuple[str, str]]:
    compared = ("fixed_streak", "no_calibration_certificate", "proposed")
    by_name = {str(item.get("name")): item for item in methods}
    if any(
        not isinstance(by_name.get(name), Mapping)
        or by_name[name].get("factory") != "render_sop"
        for name in compared
    ):
        return {}

    def anchor(item: Mapping[str, Any]) -> str:
        config = item.get("config")
        if not isinstance(config, Mapping):
            return "INVALID"
        values: dict[str, Any] = {
            "detector_kind": config.get("detector_kind"),
            "model_hash": item.get("model_hash"),
            "device": config.get("device"),
            "yolo_image_size": config.get("yolo_image_size"),
            "yolo_iou_threshold": config.get("yolo_iou_threshold"),
        }
        for key in ("model_path", "segmenter_path", "task_config_path"):
            path = _resolve(config.get(key), base)
            values[key] = str(path) if path is not None else None
        return canonical_sha256(values)

    hashes = {name: anchor(by_name[name]) for name in compared}
    if len(set(hashes.values())) == 1:
        return {}
    reason = f"paired decision ablations do not share perception anchors: {hashes}"
    return {name: ("PERCEPTION_ANCHOR_MISMATCH", reason) for name in compared}


def compile_adapter_manifest(
    source: str | Path | Mapping[str, Any],
    *,
    registry: Mapping[str, Callable[..., Any]] | None = None,
) -> CompiledAdapterManifest:
    source_path = Path(source).resolve() if isinstance(source, (str, Path)) else None
    if source_path is not None:
        raw = _read(source_path)
    elif isinstance(source, Mapping):
        raw = dict(source)
    else:
        raise TypeError("adapter manifest source must be a path or mapping")
    base = source_path.parent if source_path is not None else Path.cwd()
    unknown = set(raw) - TOP_KEYS
    if unknown:
        raise ValueError(f"unknown adapter manifest fields: {sorted(unknown)}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported adapter manifest schema_version; expected 1")
    profile = str(raw.get("profile", ""))
    if profile in FORBIDDEN_PROFILES or profile not in ALLOWED_PROFILES:
        raise ValueError(f"profile is not allowed for development preflight: {profile}")
    _reject_forbidden_references(raw)
    task, task_source, task_source_hash, task_config_hash = _task(
        raw.get("task_config"), profile, base
    )
    fixed = raw.get("fixed_streak")
    if not isinstance(fixed, Mapping) or set(fixed) != {
        "confirm_frames",
        "cooldown_frames",
    }:
        raise ValueError(
            "fixed_streak must explicitly declare confirm_frames and cooldown_frames"
        )
    if (fixed.get("confirm_frames"), fixed.get("cooldown_frames")) != (4, 12):
        raise ValueError(
            "fixed-streak baseline is frozen at confirm_frames=4, cooldown_frames=12"
        )
    methods = raw.get("methods")
    expected_method_names = (
        G3_METHOD_NAMES if profile == "DEVELOPMENT_G3_EVALUATION" else METHOD_NAMES
    )
    if (
        not isinstance(methods, list)
        or len(methods) != len(expected_method_names)
        or any(not isinstance(item, Mapping) for item in methods)
    ):
        raise ValueError(f"methods must contain exactly {expected_method_names}")
    names = [str(item.get("name")) for item in methods]
    if len(set(names)) != len(names) or set(names) != set(expected_method_names):
        raise ValueError(f"methods must contain exactly {expected_method_names}")
    raw_schedule = raw.get("schedule")
    if raw_schedule is None and profile == "SOFTWARE_FIXTURE":
        schedule = tuple(names)
    elif not isinstance(raw_schedule, list) or any(
        not isinstance(item, str) for item in raw_schedule
    ):
        raise ValueError("schedule must be an explicit list")
    else:
        schedule = tuple(raw_schedule)
    if (
        not schedule
        or len(schedule) != len(set(schedule))
        or not set(schedule) <= set(METHOD_NAMES)
    ):
        raise ValueError("schedule contains duplicates or unknown methods")
    if profile != "SOFTWARE_FIXTURE" and (
        len(schedule) != len(expected_method_names)
        or set(schedule) != set(expected_method_names)
    ):
        raise ValueError(
            "non-fixture schedule must contain the complete profile method set"
        )
    raw_anchors = raw.get("anchors", {} if profile == "SOFTWARE_FIXTURE" else None)
    if not isinstance(raw_anchors, Mapping) or set(raw_anchors) - ANCHOR_KEYS:
        raise ValueError(f"anchors may contain only {sorted(ANCHOR_KEYS)}")
    anchors = {str(key): str(value) for key, value in raw_anchors.items()}
    if profile != "SOFTWARE_FIXTURE":
        for key in ANCHOR_KEYS:
            if not _is_hash(anchors.get(key)):
                raise ValueError(f"{key} must be a lowercase sha256 frozen anchor")
    if registry is not None and profile != "SOFTWARE_FIXTURE":
        raise ValueError(
            "injected adapter registries are allowed only for SOFTWARE_FIXTURE"
        )
    reg = dict(registry) if registry is not None else _default_registry()
    ownership_errors = _path_ownership_errors(methods, base)
    perception_errors = _perception_anchor_errors(methods, base)
    adapters: dict[str, Callable[..., Any]] = {}
    specs: list[MethodSpec] = []
    checks: list[MethodPreflight] = []
    for item in methods:
        name = str(item["name"])
        raw_config = item.get("config")
        method_hash_payload: dict[str, Any] = {"method": item}
        if (
            isinstance(raw_config, Mapping)
            and raw_config.get("policy") == "fixed_streak"
        ):
            method_hash_payload["fixed_streak"] = dict(fixed)
        method_hash, model_hash = (
            canonical_sha256(method_hash_payload),
            str(item.get("model_hash", "")),
        )
        if set(item) - METHOD_KEYS:
            checks.append(
                _blocked(
                    name,
                    "METHOD_SCHEMA_INVALID",
                    f"unknown fields: {sorted(set(item) - METHOD_KEYS)}",
                    method_hash,
                    model_hash,
                )
            )
            continue
        factory_name = item.get("factory")
        if factory_name not in FACTORIES or factory_name not in reg:
            checks.append(
                _blocked(
                    name,
                    "FACTORY_NOT_ALLOWLISTED",
                    str(factory_name),
                    method_hash,
                    model_hash,
                )
            )
            continue
        required_factory = {
            "fixed_streak": "render_sop",
            "vlm_only": "vlm_audit_only",
            "yolo": "render_sop",
            "grounding_dino": "render_sop",
            "no_calibration_certificate": "render_sop",
            "proposed": "render_sop",
        }.get(name)
        if (
            profile != "SOFTWARE_FIXTURE"
            and required_factory is not None
            and factory_name != required_factory
        ):
            checks.append(
                _blocked(
                    name,
                    "METHOD_FACTORY_MISMATCH",
                    f"{name} requires factory={required_factory}",
                    method_hash,
                    model_hash,
                )
            )
            continue
        timeout_raw = item.get("timeout_s")
        if timeout_raw is not None:
            import math

            if (
                isinstance(timeout_raw, bool)
                or not isinstance(timeout_raw, (int, float))
                or not math.isfinite(float(timeout_raw))
                or float(timeout_raw) <= 0
            ):
                checks.append(
                    _blocked(
                        name,
                        "TIMEOUT_INVALID",
                        "timeout_s must be finite and > 0",
                        canonical_sha256({"method": item}),
                        str(item.get("model_hash", "")),
                    )
                )
                continue
        config = item.get("config", {})
        if (
            not isinstance(config, Mapping)
            or set(config) - CONFIG_KEYS[str(factory_name)]
        ):
            checks.append(
                _blocked(
                    name,
                    "METHOD_CONFIG_INVALID",
                    "config has invalid or unknown fields",
                    method_hash,
                    model_hash,
                )
            )
            continue
        if name in ownership_errors:
            code, reason = ownership_errors[name]
            checks.append(_blocked(name, code, reason, method_hash, model_hash))
            continue
        if name in perception_errors:
            code, reason = perception_errors[name]
            checks.append(_blocked(name, code, reason, method_hash, model_hash))
            continue
        if (profile != "SOFTWARE_FIXTURE" or model_hash) and not _is_hash(model_hash):
            checks.append(
                _blocked(
                    name,
                    "MODEL_HASH_INVALID",
                    "model_hash must be lowercase sha256",
                    method_hash,
                    model_hash,
                )
            )
            continue
        kwargs: dict[str, Any]
        if factory_name == "unavailable":
            if name == "hmm_or_crf":
                fit_path, fit_error = _path(config, "fit_artifact", base, "file")
                fit_hash = config.get("fit_artifact_hash")
                if fit_error or fit_path is None or not _is_hash(fit_hash):
                    checks.append(
                        _blocked(
                            name,
                            "HMM_FIT_ARTIFACT_MISSING",
                            fit_error or "fit_artifact_hash must be lowercase sha256",
                            method_hash,
                            model_hash,
                        )
                    )
                    continue
                if _file_sha256(fit_path) != fit_hash:
                    checks.append(
                        _blocked(
                            name,
                            "HMM_FIT_ARTIFACT_HASH_MISMATCH",
                            str(fit_path),
                            method_hash,
                            model_hash,
                        )
                    )
                    continue
            kwargs = {
                "name": name,
                "reason": str(config.get("reason", "method is not registered")),
            }
        elif factory_name == "hmm_sequence":
            fit_path, fit_error = _path(config, "fit_artifact", base, "file")
            expected_hash = config.get("expected_fit_artifact_hash")
            expected_partition = config.get("expected_fit_partition_hash")
            if (
                fit_error
                or fit_path is None
                or not _is_hash(expected_hash)
                or not _is_hash(expected_partition)
            ):
                checks.append(
                    _blocked(
                        name,
                        "HMM_FIT_ARTIFACT_MISSING",
                        fit_error or "HMM artifact and expected hashes are required",
                        method_hash,
                        model_hash,
                    )
                )
                continue
            try:
                from .hmm_sequence import load_hmm_artifact

                load_hmm_artifact(
                    fit_path,
                    expected_hash=expected_hash,
                    expected_partition_hash=expected_partition,
                    expected_task_id=task.task_id,
                    expected_task_config_hash=task_config_hash,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                checks.append(
                    _blocked(
                        name, "HMM_ARTIFACT_INVALID", str(exc), method_hash, model_hash
                    )
                )
                continue
            kwargs = {
                "fit_artifact": fit_path,
                "expected_fit_artifact_hash": expected_hash,
                "expected_fit_partition_hash": expected_partition,
                "task_config_hash": task_config_hash,
                "task_id": task.task_id,
                "states": task.steps,
                "name": name,
            }
        elif factory_name == "vlm_audit_only":
            review = config.get("review_json")
            if review is not None:
                review_path, error = _path(config, "review_json", base, "file")
                if error:
                    checks.append(
                        _blocked(
                            name, "REVIEW_JSON_INVALID", error, method_hash, model_hash
                        )
                    )
                    continue
                review = str(review_path)
            kwargs = {"review_json": review, "name": name}
        else:
            kwargs, render_error = _render_kwargs(
                name, config, profile, base, task_source_hash, method_hash, model_hash
            )
            if render_error:
                checks.append(
                    _blocked(
                        name, render_error[0], render_error[1], method_hash, model_hash
                    )
                )
                continue
        try:
            adapter = reg[str(factory_name)](**kwargs)
        except Exception as exc:
            checks.append(
                _blocked(
                    name,
                    "ADAPTER_CONSTRUCTION_FAILED",
                    f"{type(exc).__name__}: {exc}",
                    method_hash,
                    model_hash,
                )
            )
            continue
        try:
            pickle.dumps(adapter)
        except Exception as exc:
            checks.append(
                _blocked(
                    name,
                    "ADAPTER_NOT_PICKLEABLE",
                    f"{type(exc).__name__}: {exc}",
                    method_hash,
                    model_hash,
                )
            )
            continue
        if factory_name == "unavailable":
            checks.append(
                _blocked(
                    name,
                    "METHOD_UNAVAILABLE",
                    str(config.get("reason", "method is not registered")),
                    method_hash,
                    model_hash,
                )
            )
            continue
        adapters[name] = adapter
        specs.append(
            MethodSpec(
                name=name,
                runner=adapter,
                seed=int(item.get("seed", 0)),
                timeout_s=float(item["timeout_s"])
                if item.get("timeout_s") is not None
                else None,
                config_hash=method_hash,
                model_hash=model_hash,
                code_hash=anchors.get("code_hash", ""),
            )
        )
        checks.append(MethodPreflight(name, "READY", None, "", method_hash, model_hash))
    return CompiledAdapterManifest(
        SCHEMA_VERSION,
        profile,
        canonical_sha256(raw),
        task,
        task_source,
        task_source_hash,
        task_config_hash,
        schedule,
        anchors,
        adapters,
        tuple(specs),
        tuple(checks),
        raw,
    )


def load_adapter_manifest(path: str | Path, **kwargs: Any) -> CompiledAdapterManifest:
    return compile_adapter_manifest(path, **kwargs)
