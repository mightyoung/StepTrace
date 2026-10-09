"""Fail-closed IndustReal metadata and PSR registry helpers."""

from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

PSR_ERROR_TYPES = {
    "skip",
    "reverse_order",
    "wrong_tool",
    "wrong_part",
    "omission",
    "extra_action",
    "unknown",
}
NO_ERROR = "no_error"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and row[name].strip():
            return row[name].strip()
    return "UNKNOWN"


def _error_value(row: dict[str, str]) -> str:
    value = _value(row, "error_type", "psr_error", "error")
    if value == "UNKNOWN":
        return "UNKNOWN"
    value = value.lower().replace("-", "_").replace(" ", "_")
    return NO_ERROR if value in {"none", "no_error"} else value


def _official_rows(
    inventory: str | Path | None, split_csv: str | Path | None
) -> list[dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    split_paths = []
    if split_csv:
        candidate = Path(split_csv)
        split_paths = (
            sorted(candidate.glob("*.csv")) if candidate.is_dir() else [candidate]
        )
    for split_path in split_paths:
        if not split_path.is_file():
            continue
        with split_path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        header = {v.strip().lower() for v in (rows[0] if rows else [])}
        has_header = bool(header & {"recording", "recording_id", "video", "split"})
        for values in rows[1:] if has_header else rows:
            if not values:
                continue
            if has_header:
                row = dict(zip(rows[0], values))
                recording = _value(row, "recording", "recording_id", "video")
                task = _value(row, "task", "task_id")
                explicit_operator = _value(row, "operator", "actor", "operator_id")
                explicit_job = _value(row, "job", "complete_job", "complete_job_id")
            else:
                recording = values[0].strip()
                task = (
                    values[2].strip()
                    if len(values) > 2 and values[2].strip()
                    else "UNKNOWN"
                )
                explicit_operator = "UNKNOWN"
                explicit_job = "UNKNOWN"
            if recording == "UNKNOWN":
                continue
            operator = explicit_operator
            complete_job = (
                explicit_job
                if explicit_job != "UNKNOWN"
                else f"INDUSTREAL-RECORDING-{recording}"
            )
            operator_token = recording.split("_", 1)[0].lstrip("0") or "0"
            out[recording] = {
                "recording": recording,
                "operator": operator,
                "operator_token": operator_token,
                # The complete official recording is the authoritative job
                # unit even while the participant/operator mapping is unknown.
                "job": complete_job,
                "job_token": recording,
                "job_identity_status": "COMPLETE_RECORDING_BOUND",
                "task": task,
                "official_split": split_path.stem,
                "error_type": "UNKNOWN",
                "derivation": "official recording token; operator prefix and complete-job identity from IndustReal naming contract",
            }
    if out:
        return list(out.values())
    if inventory and Path(inventory).is_file():
        payload = json.loads(Path(inventory).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in ("records", "recordings", "jobs"):
                inventory_values: object = payload.get(key, [])
                if isinstance(inventory_values, list):
                    for value in inventory_values:
                        if not isinstance(value, dict):
                            continue
                        record: dict[str, str] = {}
                        for raw_key, raw_value in value.items():
                            if not isinstance(raw_key, str):
                                continue
                            if raw_value is None:
                                record[raw_key] = "UNKNOWN"
                            elif isinstance(raw_value, str):
                                record[raw_key] = raw_value
                            else:
                                record[raw_key] = str(raw_value)
                        recording = record.get(
                            "recording", record.get("recording_id", "UNKNOWN")
                        )
                        out[recording] = record
    return list(out.values())


def _split_map(path: str | Path | None) -> dict[str, str]:
    if not path or not Path(path).exists():
        return {}
    split_path = Path(path)
    return {
        _value(row, "recording", "recording_id", "video"): _value(
            row, "official_split", "split"
        )
        for row in _official_rows(None, split_path)
    }


def _record(
    row: dict[str, str], split_map: dict[str, str], fallback_split: str = "UNKNOWN"
) -> dict[str, str]:
    recording = _value(row, "recording", "video", "recording_id")
    split = split_map.get(recording) or _value(row, "official_split", "split")
    if split == "UNKNOWN":
        split = fallback_split
    operator_id = _value(row, "operator", "actor", "operator_id")
    job_id = _value(row, "job", "complete_job", "complete_job_id")
    job_token = _value(row, "job_token")
    job_status = str(row.get("job_identity_status", "")) or (
        "VERIFIED_METADATA" if job_id != "UNKNOWN" else "UNKNOWN"
    )
    return {
        "recording_id": recording,
        "operator_id": operator_id,
        "complete_job_id": job_id,
        "task_id": _value(row, "task", "task_id"),
        "official_split": split,
        "error_type": _error_value(row),
        "operator_identity_status": (
            "VERIFIED_METADATA"
            if operator_id != "UNKNOWN"
            else "UNVERIFIED_NAME_DERIVED"
        ),
        "operator_token": _value(row, "operator_token"),
        "complete_job_identity_status": job_status,
        "complete_job_token": job_token,
    }


def _validate_error(error: str) -> None:
    if error not in PSR_ERROR_TYPES and error != NO_ERROR and error != "UNKNOWN":
        raise ValueError(f"unknown PSR error type: {error}")


def _source_sha256(path: str | Path | None) -> str:
    if not path:
        return "UNKNOWN"
    candidate = Path(path)
    files = sorted(candidate.glob("*.csv")) if candidate.is_dir() else [candidate]
    if not files or not all(f.is_file() for f in files):
        return "UNKNOWN"
    digest = hashlib.sha256()
    for item in files:
        data = item.read_bytes()
        digest.update(item.name.encode("utf-8"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _asset_status(inventory: str | Path | None, key: str, default: str) -> str:
    if not inventory or not Path(inventory).is_file():
        return default
    try:
        payload = json.loads(Path(inventory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    value = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(value, dict):
        status = str(value.get("status", "")).upper()
        if status in {"AVAILABLE", "COMPLETE", "VERIFIED"}:
            return "AVAILABLE"
        return "UNAVAILABLE"
    return default


def _psr_error_status(
    inventory: str | Path | None, records: list[dict[str, str]]
) -> str:
    if any(r["error_type"] == "UNKNOWN" for r in records):
        return "UNAVAILABLE"
    if inventory and Path(inventory).is_file():
        try:
            payload = json.loads(Path(inventory).read_text(encoding="utf-8"))
            labels = payload.get("labels", {}) if isinstance(payload, dict) else {}
            marker = (
                str(labels.get("psr_error_labels", "")).upper()
                if isinstance(labels, dict)
                else ""
            )
            if "MISSING" in marker or "UNAVAILABLE" in marker:
                return "UNAVAILABLE"
        except (OSError, ValueError):
            return "UNAVAILABLE"
    return "AVAILABLE"


def audit_industreal_records(
    path: str | Path,
    *,
    official_split_csv: str | Path | None = None,
    official_inventory: str | Path | None = None,
    expected_videos: int | None = 84,
    expected_operators: int | None = 27,
) -> dict[str, Any]:
    p = Path(path)
    raw = (
        _rows(p)
        if p.is_file()
        else _official_rows(official_inventory, official_split_csv)
    )
    if not raw:
        return {
            "status": "BLOCKED_MISSING_EXTERNAL_DATA",
            "source_path": str(p),
            "records": [],
            "errors": ["records CSV missing"],
        }
    split_map = _split_map(official_split_csv)
    records = [_record(row, split_map) for row in raw]
    errors: list[str] = []
    split_keys: dict[tuple[str, str], set[str]] = {}
    seen_recordings: set[str] = set()
    seen_jobs: set[str] = set()
    for r in records:
        _validate_error(r["error_type"])
        if r["error_type"] == "UNKNOWN":
            errors.append(f"missing PSR error label: {r['recording_id']}")
        if official_split_csv and r["recording_id"] not in split_map:
            errors.append(
                f"recording missing from official split CSV: {r['recording_id']}"
            )
        key = (r["operator_id"], r["complete_job_id"])
        if r["operator_id"] != "UNKNOWN" and r["complete_job_id"] != "UNKNOWN":
            split_keys.setdefault(key, set()).add(r["official_split"])
        if r["recording_id"] in seen_recordings:
            errors.append(f"duplicate recording_id: {r['recording_id']}")
        if r["complete_job_id"] != "UNKNOWN" and r["complete_job_id"] in seen_jobs:
            errors.append(f"duplicate complete_job_id: {r['complete_job_id']}")
        seen_recordings.add(r["recording_id"])
        seen_jobs.add(r["complete_job_id"])
    for key, splits in split_keys.items():
        if len(splits) > 1:
            errors.append(f"operator/job crosses official splits: {key[0]}/{key[1]}")
    operators = {r["operator_id"] for r in records if r["operator_id"] != "UNKNOWN"}
    operator_tokens = {
        r.get("operator_token")
        for r in records
        if r.get("operator_token") not in {None, "UNKNOWN"}
    }
    if expected_videos is not None and len(seen_recordings) != expected_videos:
        errors.append(
            f"expected {expected_videos} videos, found {len(seen_recordings)}"
        )
    if expected_operators is not None and len(operators) != expected_operators:
        if len(operators) != expected_operators:
            errors.append(
                f"expected {expected_operators} verified operators, found {len(operators)}"
            )
    incomplete = [
        r
        for r in records
        if any(
            r[k] == "UNKNOWN"
            for k in (
                "recording_id",
                "operator_id",
                "complete_job_id",
                "task_id",
                "official_split",
                "error_type",
            )
        )
    ]
    if incomplete:
        errors.append(f"incomplete metadata rows: {len(incomplete)}")
    return {
        "status": "PASS" if not errors else "BLOCKED",
        "source_path": (
            [str(Path(official_split_csv)), str(Path(official_inventory))]
            if not p.is_file() and official_split_csv and official_inventory
            else str(p)
        ),
        "record_count": len(records),
        "video_count": len(seen_recordings),
        "operator_count": len(operators),
        "operator_name_token_count": len(operator_tokens),
        "operator_identity_status": "VERIFIED"
        if len(operators) == len(operator_tokens) and len(operators)
        else "UNVERIFIED_NAME_DERIVED",
        "non_cross_split_key": "source_dataset+recording_id+operator_id+complete_job_id",
        "records": records,
        "errors": errors,
        "action_labels_source_sha256": _source_sha256(official_split_csv),
        "official_split_csv": str(official_split_csv)
        if official_split_csv
        else "UNKNOWN",
        "official_inventory": str(official_inventory)
        if official_inventory
        else "UNKNOWN",
        "video_asset_status": _asset_status(
            official_inventory, "rgb_video_asset", "UNAVAILABLE"
        ),
        "psr_error_asset_status": _psr_error_status(official_inventory, records),
        "error_labels_declared": not any(r["error_type"] == "UNKNOWN" for r in records),
    }


def register_industreal_records(
    path: str | Path,
    *,
    official_split: str = "UNKNOWN",
    official_split_csv: str | Path | None = None,
    official_inventory: str | Path | None = None,
    expected_videos: int | None = None,
    expected_operators: int | None = None,
) -> list[dict[str, Any]]:
    p = Path(path)
    raw = (
        _rows(p)
        if p.is_file()
        else _official_rows(official_inventory, official_split_csv)
    )
    if not raw:
        return [{"status": "BLOCKED_MISSING_EXTERNAL_DATA", "source_path": str(p)}]
    split_map = _split_map(official_split_csv)
    out = []
    for row in raw:
        item = _record(row, split_map, official_split)
        _validate_error(item["error_type"])
        identity_ok = all(
            item[k] != "UNKNOWN"
            for k in (
                "recording_id",
                "operator_id",
                "complete_job_id",
                "task_id",
                "official_split",
            )
        )
        ready = identity_ok and item["error_type"] != "UNKNOWN" and p.is_file()
        out.append(
            {
                **item,
                "status": "REGISTERED"
                if ready
                else "BLOCKED_METADATA_ONLY_VIDEO_OR_ERROR_UNAVAILABLE",
                "video_asset_status": "AVAILABLE"
                if p.is_file() and p.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"}
                else "UNAVAILABLE",
                "psr_error_asset_status": "AVAILABLE"
                if item["error_type"] != "UNKNOWN"
                else "UNAVAILABLE",
            }
        )
    return out
