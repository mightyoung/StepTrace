"""Build hash-bound, non-authorizing inputs for G3 run admission."""

from __future__ import annotations

from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml  # type: ignore[import-untyped]

from display_project.experiments.adapter_manifest import canonical_sha256
from display_project.experiments.paired_runner import make_unit_identity
from display_project.g3_run_admission import (
    METHOD_SCHEDULE,
    PROFILE,
    validate_admission_input_bundle,
)
from display_project.sop.task_config import load_task_config


BUNDLE_SPEC_SCHEMA = "display_project.g3_admission_bundle_spec-1.0.0"
BUNDLE_REPORT_SCHEMA = "display_project.g3_admission_bundle_report-1.0.0"
TASK_DATASETS = {"meccano": "MECCANO", "industreal": "INDUSTREAL"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_value_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["artifact_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return body


def _resolve(base: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}:PATH_MISSING")
    path = Path(value)
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _load_spec(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as stream:
        value = json.load(stream) if path.suffix.lower() == ".json" else yaml.safe_load(stream)
    if not isinstance(value, Mapping):
        raise ValueError("SPEC_NOT_MAPPING")
    return dict(value)


def _method_digest(base: Path, entries: object, method: str) -> str:
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"METHOD_SOURCES_MISSING:{method}")
    sources = []
    for index, raw in enumerate(entries):
        path = _resolve(base, raw, f"method_sources.{method}[{index}]")
        if not path.is_file():
            raise ValueError(f"METHOD_SOURCE_NOT_FILE:{method}:{path}")
        sources.append({"path": path.as_posix(), "sha256": _sha256(path)})
    return canonical_sha256(sources)


def _dataset_key(value: object) -> str:
    return str(value).replace("_", "").replace("-", "").casefold()


def _registered_unit(
    *,
    item: Mapping[str, Any],
    video_sha256: str,
    split_path: Path,
    ledger_path: Path,
    index: int,
) -> None:
    try:
        split_payload = json.loads(split_path.read_text(encoding="utf-8-sig"))
        split_records = split_payload.get("records", [])
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise ValueError(f"UNIT_SPLIT_MANIFEST_INVALID:{index}:{type(exc).__name__}") from exc
    if not isinstance(split_records, list):
        raise ValueError(f"UNIT_SPLIT_MANIFEST_INVALID:{index}:records")
    matches = [
        row
        for row in split_records
        if isinstance(row, Mapping)
        and str(row.get("video_id")) == str(item["video_id"])
        and _dataset_key(row.get("source_dataset")) == _dataset_key(item["dataset_id"])
    ]
    if len(matches) != 1:
        raise ValueError(f"UNIT_SPLIT_MEMBERSHIP_INVALID:{index}:{len(matches)}")
    split = matches[0]
    for key in ("canonical_root_id", "operator_id", "complete_job_id"):
        if split.get(key) != item[key]:
            raise ValueError(f"UNIT_SPLIT_IDENTITY_MISMATCH:{index}:{key}")
    if "canonical_group_id" in split and split.get("canonical_group_id") != item["canonical_group_id"]:
        raise ValueError(f"UNIT_SPLIT_IDENTITY_MISMATCH:{index}:canonical_group_id")
    if (
        split.get("independent_unit_eligible") != "YES"
        or str(split.get("project_split", "")).startswith("UNASSIGNED")
        or split.get("tombstone_id") not in {None, "NONE"}
    ):
        raise ValueError(f"UNIT_SPLIT_NOT_ELIGIBLE:{index}")
    fallback = (
        str(item.get("dataset_id", "")).replace("_", "").replace("-", "").casefold()
        == "industreal"
        and str(item.get("operator_id", "")).upper() == "UNKNOWN"
        and item.get("cluster_level") == "complete_job"
        and item.get("operator_identity_status") == "UNKNOWN_DISCLOSED"
    )
    if fallback and (
        split.get("cluster_level") != "complete_job"
        or split.get("operator_identity_status") != "UNKNOWN_DISCLOSED"
        or split.get("truth_capability") != "ACTION_STEP_STATE_ONLY"
    ):
        raise ValueError(f"UNIT_SPLIT_FALLBACK_PROVENANCE_INVALID:{index}")
    try:
        with ledger_path.open(encoding="utf-8-sig", newline="") as stream:
            ledger_rows = list(csv.DictReader(stream))
    except OSError as exc:
        raise ValueError(f"UNIT_DATASET_LEDGER_INVALID:{index}:{type(exc).__name__}") from exc
    ledger_matches = [
        row
        for row in ledger_rows
        if row.get("record_type") == "CANONICAL_ROOT_VIDEO"
        and row.get("canonical_root_id") == item["canonical_root_id"]
        and _dataset_key(row.get("source_dataset")) == _dataset_key(item["dataset_id"])
    ]
    if len(ledger_matches) != 1:
        raise ValueError(f"UNIT_LEDGER_MEMBERSHIP_INVALID:{index}:{len(ledger_matches)}")
    ledger = ledger_matches[0]
    for key in ("canonical_group_id", "operator_id", "complete_job_id"):
        if ledger.get(key) != item[key]:
            raise ValueError(f"UNIT_LEDGER_IDENTITY_MISMATCH:{index}:{key}")
    if fallback and (
        ledger.get("cluster_level") != "complete_job"
        or ledger.get("operator_identity_status") != "UNKNOWN_DISCLOSED"
        or ledger.get("truth_capability") != "ACTION_STEP_STATE_ONLY"
    ):
        raise ValueError(f"UNIT_LEDGER_FALLBACK_PROVENANCE_INVALID:{index}")
    if (
        ledger.get("split_locked") != "YES"
        or ledger.get("independent_unit_eligible") != "YES"
        or ledger.get("tombstone_id") not in {None, "", "NONE"}
    ):
        raise ValueError(f"UNIT_LEDGER_NOT_ELIGIBLE:{index}")
    if str(ledger.get("sha256", "")).casefold() != video_sha256:
        raise ValueError(f"UNIT_LEDGER_VIDEO_HASH_MISMATCH:{index}")


def build_g3_admission_bundle(spec_path: str | Path) -> dict[str, Any]:
    """Build all non-authority admission artifacts, or a BLOCKED report."""
    source = Path(spec_path).resolve()
    base = source.parent
    reasons: list[str] = []
    artifacts: dict[str, Any] = {}
    try:
        spec = _load_spec(source)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return {
            "schema_version": BUNDLE_REPORT_SCHEMA,
            "status": "BLOCKED",
            "ready": False,
            "reason_codes": [f"SPEC_LOAD_FAILED:{type(exc).__name__}:{exc}"],
            "source": str(source),
            "artifacts": {},
        }
    if spec.get("schema_version") != BUNDLE_SPEC_SCHEMA:
        reasons.append("SPEC_SCHEMA_INVALID")
    if spec.get("profile") != PROFILE:
        reasons.append("EVALUATION_PROFILE_INVALID")

    task_bindings = []
    tasks = spec.get("tasks")
    if not isinstance(tasks, Mapping) or set(tasks) != set(TASK_DATASETS):
        reasons.append("TASK_SPEC_BIJECTION_INVALID")
    else:
        for task_id, dataset_id in TASK_DATASETS.items():
            item = tasks.get(task_id)
            try:
                if not isinstance(item, Mapping) or item.get("dataset_id") != dataset_id:
                    raise ValueError(f"TASK_DATASET_INVALID:{task_id}")
                config_path = _resolve(base, item.get("config"), f"tasks.{task_id}.config")
                if not config_path.is_file():
                    raise ValueError(f"TASK_CONFIG_NOT_FILE:{task_id}:{config_path}")
                compiled = load_task_config(config_path)
                if compiled.task_id.casefold() != task_id:
                    raise ValueError(f"TASK_CONFIG_ID_MISMATCH:{task_id}")
                task_bindings.append(
                    {
                        "task_id": task_id,
                        "dataset_id": dataset_id,
                        "task_config_hash": canonical_sha256(asdict(compiled)),
                    }
                )
            except (OSError, ValueError) as exc:
                reasons.append(str(exc))

    method_hashes: dict[str, str] = {}
    method_sources = spec.get("method_sources")
    if not isinstance(method_sources, Mapping) or set(method_sources) != set(METHOD_SCHEDULE):
        reasons.append("METHOD_SOURCE_BIJECTION_INVALID")
    else:
        for method in METHOD_SCHEDULE:
            try:
                method_hashes[method] = _method_digest(base, method_sources[method], method)
            except (OSError, ValueError) as exc:
                reasons.append(str(exc))
    seed = spec.get("randomization_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        reasons.append("RANDOMIZATION_SEED_INVALID")
    if len(method_hashes) == len(METHOD_SCHEDULE) and not reasons:
        artifacts["method_manifest"] = _artifact(
            {
                "schema_version": "g3-method-manifest-1.0.0",
                "profile": PROFILE,
                "schedule": list(METHOD_SCHEDULE),
                "randomization_seed": seed,
                "method_hashes": method_hashes,
            }
        )

    binding_hashes = {row["task_id"]: row["task_config_hash"] for row in task_bindings}
    unit_inventory = []
    units = spec.get("units")
    if not isinstance(units, list) or not units:
        reasons.append("UNIT_SPEC_MISSING")
    else:
        required_identity = {
            "task_id", "dataset_id", "operator_id", "complete_job_id",
            "canonical_root_id", "canonical_group_id", "video_id",
            "video", "split_manifest", "dataset_ledger",
        }
        for index, item in enumerate(units):
            try:
                if not isinstance(item, Mapping) or set(item) not in {
                    frozenset(required_identity),
                    frozenset(required_identity | {"cluster_level", "operator_identity_status"}),
                }:
                    raise ValueError(f"UNIT_SPEC_SCHEMA_INVALID:{index}")
                task_id = str(item["task_id"])
                dataset_id = str(item["dataset_id"])
                if TASK_DATASETS.get(task_id) != dataset_id:
                    raise ValueError(f"UNIT_TASK_DATASET_INVALID:{index}")
                paths = {
                    name: _resolve(base, item[name], f"units[{index}].{name}")
                    for name in ("video", "split_manifest", "dataset_ledger")
                }
                missing = [name for name, path in paths.items() if not path.is_file()]
                if missing:
                    raise ValueError(f"UNIT_SOURCE_NOT_FILE:{index}:{','.join(missing)}")
                video_sha256 = _sha256(paths["video"])
                _registered_unit(
                    item=item,
                    video_sha256=video_sha256,
                    split_path=paths["split_manifest"],
                    ledger_path=paths["dataset_ledger"],
                    index=index,
                )
                cluster_level = item.get("cluster_level", "operator")
                operator_identity_status = item.get(
                    "operator_identity_status", "VERIFIED"
                )
                if str(item["operator_id"]).strip().upper() == "UNKNOWN" and not (
                    task_id == "industreal"
                    and cluster_level == "complete_job"
                    and operator_identity_status == "UNKNOWN_DISCLOSED"
                ):
                    raise ValueError(f"UNIT_OPERATOR_FALLBACK_INVALID:{index}")
                if cluster_level == "complete_job":
                    unit_id = _canonical_value_sha256(
                        {
                            "dataset_id": dataset_id,
                            "canonical_root_id": str(item["canonical_root_id"]),
                            "complete_job_id": str(item["complete_job_id"]),
                            "video_id": str(item["video_id"]),
                            "cluster_level": cluster_level,
                        }
                    )
                else:
                    unit_id = make_unit_identity(
                        source_dataset=dataset_id,
                        canonical_root_id=str(item["canonical_root_id"]),
                        operator_id=str(item["operator_id"]),
                        complete_job_id=str(item["complete_job_id"]),
                        video_id=str(item["video_id"]),
                    )
                unit_inventory.append(
                    {
                        "unit_id": unit_id,
                        "task_id": task_id,
                        "dataset_id": dataset_id,
                        "operator_id": str(item["operator_id"]),
                        "complete_job_id": str(item["complete_job_id"]),
                        "canonical_root_id": str(item["canonical_root_id"]),
                        "canonical_group_id": str(item["canonical_group_id"]),
                        "video_id": str(item["video_id"]),
                        "video_sha256": video_sha256,
                        "task_config_hash": binding_hashes.get(task_id, ""),
                        "split_sha256": _sha256(paths["split_manifest"]),
                        "ledger_sha256": _sha256(paths["dataset_ledger"]),
                        "cluster_level": cluster_level,
                        "operator_identity_status": operator_identity_status,
                    }
                )
            except (OSError, ValueError) as exc:
                reasons.append(str(exc))

    producer = spec.get("statistical_producer")
    if isinstance(producer, Mapping):
        try:
            source_values = producer.get("sources")
            if not isinstance(source_values, list) or not source_values:
                raise ValueError("STATISTICAL_PRODUCER_SOURCES_MISSING")
            source_paths = [
                _resolve(base, value, f"statistical_producer.sources[{index}]")
                for index, value in enumerate(source_values)
            ]
            environment_path = _resolve(
                base, producer.get("environment_lock"), "statistical_producer.environment_lock"
            )
            if any(not path.is_file() for path in source_paths) or not environment_path.is_file():
                raise ValueError("STATISTICAL_PRODUCER_SOURCE_NOT_FILE")
            runtime_values = producer.get("runtime_assets")
            if not isinstance(runtime_values, Mapping) or not runtime_values:
                raise ValueError("STATISTICAL_RUNTIME_ASSETS_MISSING")
            runtime_paths = {
                str(key): _resolve(
                    base, value, f"statistical_producer.runtime_assets.{key}"
                )
                for key, value in runtime_values.items()
            }
            if any(not path.is_file() for path in runtime_paths.values()):
                raise ValueError("STATISTICAL_RUNTIME_ASSET_NOT_FILE")
            source_hashes = sorted({_sha256(path) for path in source_paths})
            artifacts["statistical_producer"] = _artifact(
                {
                    "schema_version": "g3-statistical-producer-1.1.0",
                    "producer_id": producer.get("producer_id"),
                    "producer_version": producer.get("producer_version"),
                    "runtime": producer.get("runtime"),
                    "fixture": False,
                    "real_input_enabled": producer.get("real_input_enabled"),
                    "source_sha256": source_hashes,
                    "runtime_asset_sha256": {
                        key: _sha256(path) for key, path in sorted(runtime_paths.items())
                    },
                    "code_sha256": _canonical_value_sha256(source_hashes),
                    "environment_sha256": _sha256(environment_path),
                    "bootstrap_count": producer.get("bootstrap_count"),
                }
            )
        except (OSError, ValueError) as exc:
            reasons.append(str(exc))
    else:
        reasons.append("STATISTICAL_PRODUCER_SPEC_MISSING")

    destination = spec.get("result_destination")
    if isinstance(destination, Mapping):
        try:
            ledger_path = _resolve(base, destination.get("ledger"), "result_destination.ledger")
            artifacts["result_destination"] = {
                "schema_version": "g3-result-destination-1.0.0",
                "ledger_id": destination.get("ledger_id"),
                "ledger_path_sha256": hashlib.sha256(
                    str(ledger_path).encode("utf-8")
                ).hexdigest(),
            }
        except ValueError as exc:
            reasons.append(str(exc))
    else:
        reasons.append("RESULT_DESTINATION_SPEC_MISSING")

    artifacts["task_bindings"] = task_bindings
    artifacts["unit_inventory"] = unit_inventory
    required = {
        "task_bindings", "method_manifest", "unit_inventory",
        "statistical_producer", "result_destination",
    }
    if set(artifacts) == required:
        valid, validation_reasons = validate_admission_input_bundle(**artifacts)
        if not valid:
            reasons.extend(validation_reasons)
    reasons = list(dict.fromkeys(reasons))
    return {
        "schema_version": BUNDLE_REPORT_SCHEMA,
        "status": "READY" if not reasons else "BLOCKED",
        "ready": not reasons,
        "reason_codes": reasons,
        "source": str(source),
        "source_sha256": _sha256(source),
        "artifacts": artifacts if not reasons else {},
    }


__all__ = ["build_g3_admission_bundle"]
