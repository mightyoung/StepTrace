"""Offline-only structural checks for the RoboAssem task 6 pilot package.

This module reads a local JSON plan and, optionally, local frame files. It does
not contact a model, open a network connection, fetch data, or retry requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any


EXPECTED_IDS = ("F", "HF")
EXPECTED_F_PTS = [510]
EXPECTED_HF_PTS = [0, 300, 450, 510]
EXPECTED_TIME_BASE = "1/30"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _unsafe_relative_path(value: str) -> bool:
    posix_path = Path(value)
    windows_path = PureWindowsPath(value)
    return (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    )


def _request_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    requests = plan.get("requests", [])
    if not isinstance(requests, list):
        return {}
    return {
        request.get("request_id"): request
        for request in requests
        if isinstance(request, dict) and isinstance(request.get("request_id"), str)
    }


def validate_plan_shape(plan: dict[str, Any]) -> dict[str, Any]:
    """Check that the draft preserves the reported F/HF frame design.

    Passing this structural check never grants execution readiness; the latter
    also requires real local files, fixed prompt/configuration, and authorization.
    """
    errors: list[str] = []
    if not isinstance(plan, dict):
        return {
            "errors": ["request plan must be a JSON object"],
            "execution_ready": False,
            "check_scope": "plan_shape_only",
        }

    policy = plan.get("run_policy")
    if not isinstance(policy, dict):
        policy = {}
    if policy.get("max_requests") != 2:
        errors.append("max_requests must be 2")
    if policy.get("max_retries") != 0:
        errors.append("max_retries must be 0")
    if policy.get("parallelism") != 1:
        errors.append("parallelism must be 1")
    if policy.get("bind_host") != "127.0.0.1":
        errors.append("bind_host must be 127.0.0.1")

    requests = plan.get("requests")
    if not isinstance(requests, list) or len(requests) != 2:
        errors.append("exactly two request descriptors are required")
        requests = requests if isinstance(requests, list) else []
    request_ids = [
        item.get("request_id") for item in requests if isinstance(item, dict)
    ]
    if request_ids != list(EXPECTED_IDS):
        errors.append("request ids must be exactly [F, HF] in that order")
    by_id = _request_map(plan)

    for request_id, expected_pts in (("F", EXPECTED_F_PTS), ("HF", EXPECTED_HF_PTS)):
        request = by_id.get(request_id)
        if request is None:
            continue
        frames = request.get("frames")
        if not isinstance(frames, list):
            errors.append(f"{request_id} frames must be a list")
            continue
        pts_list = [frame.get("pts") for frame in frames if isinstance(frame, dict)]
        if pts_list != expected_pts:
            expected_label = "510" if request_id == "F" else "[0, 300, 450, 510]"
            errors.append(f"{request_id} frames must use reported PTS order {expected_label}")
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                errors.append(f"{request_id} frame {index} must be an object")
                continue
            if frame.get("time_base") != EXPECTED_TIME_BASE:
                errors.append(f"{request_id} frame {index} time_base must be 1/30")
            path = frame.get("path")
            if not isinstance(path, str) or not path:
                errors.append(f"{request_id} frame {index} needs a local relative path")
            elif _unsafe_relative_path(path):
                errors.append(f"{request_id} frame {index} path must stay relative to the private input root")

    f_request = by_id.get("F")
    hf_request = by_id.get("HF")
    if f_request and hf_request:
        f_frames = f_request.get("frames", [])
        hf_frames = hf_request.get("frames", [])
        if f_frames and hf_frames:
            f_current = f_frames[-1] if isinstance(f_frames[-1], dict) else {}
            hf_current = hf_frames[-1] if isinstance(hf_frames[-1], dict) else {}
            comparable_keys = ("frame_id", "pts", "time_base", "path", "size_bytes", "sha256")
            if any(f_current.get(key) != hf_current.get(key) for key in comparable_keys):
                errors.append("F and HF must reference the same current-frame bytes")

    return {
        "errors": errors,
        "execution_ready": False,
        "check_scope": "plan_shape_only",
        "note": "Structural validation alone never enables model execution.",
    }


def _digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def validate_plan(plan: dict[str, Any], private_input_root: Path) -> dict[str, Any]:
    """Check plan shape, local frame descriptors, and non-model readiness gates."""
    shape = validate_plan_shape(plan)
    errors = list(shape["errors"])
    blockers: list[str] = []
    root = private_input_root.resolve()

    for request in plan.get("requests", []) if isinstance(plan, dict) else []:
        if not isinstance(request, dict):
            continue
        request_id = request.get("request_id", "unknown")
        for index, frame in enumerate(request.get("frames", [])):
            if not isinstance(frame, dict):
                continue
            rel_path = frame.get("path")
            if not isinstance(rel_path, str) or not rel_path:
                blockers.append(f"{request_id} frame {index}: local path is unresolved")
                continue
            candidate = (root / rel_path).resolve()
            if root not in candidate.parents and candidate != root:
                blockers.append(f"{request_id} frame {index}: path escapes the private input root")
                continue
            if not candidate.is_file():
                blockers.append(f"{request_id} frame {index}: local frame file is missing")
                continue
            expected_size = frame.get("size_bytes")
            expected_sha = frame.get("sha256")
            if not isinstance(expected_size, int) or expected_size <= 0:
                blockers.append(f"{request_id} frame {index}: expected byte size is unresolved")
            if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
                blockers.append(f"{request_id} frame {index}: expected SHA256 is unresolved")
            actual_size, actual_sha = _digest_file(candidate)
            if isinstance(expected_size, int) and actual_size != expected_size:
                errors.append(f"{request_id} frame {index}: byte size does not match the manifest")
            if isinstance(expected_sha, str) and SHA256_RE.fullmatch(expected_sha):
                if actual_sha.lower() != expected_sha.lower():
                    errors.append(f"{request_id} frame {index}: SHA256 does not match the manifest")

    prompt = plan.get("task6_prompt", {}) if isinstance(plan, dict) else {}
    if not isinstance(prompt, dict) or prompt.get("status") != "fixed_record_imported":
        blockers.append("task6-specific fixed prompt record is missing")
    elif not isinstance(prompt.get("sha256"), str) or not SHA256_RE.fullmatch(prompt["sha256"]):
        blockers.append("task6 prompt SHA256 is unresolved")
    elif not isinstance(prompt.get("text"), str) or not prompt["text"]:
        blockers.append("task6 prompt text is unresolved")
    elif hashlib.sha256(prompt["text"].encode("utf-8")).hexdigest() != prompt["sha256"].lower():
        errors.append("task6 prompt SHA256 does not match the supplied prompt text")

    runtime = plan.get("runtime", {}) if isinstance(plan, dict) else {}
    if not isinstance(runtime, dict) or not isinstance(runtime.get("parameters"), dict) or not runtime["parameters"]:
        blockers.append("runtime parameters are unresolved")
    if not isinstance(runtime, dict) or not runtime.get("backend_version"):
        blockers.append("runtime backend version is unresolved")

    authorization = plan.get("authorization", {}) if isinstance(plan, dict) else {}
    if not isinstance(authorization, dict) or authorization.get("existing_model_and_cost_authorization") is not True:
        blockers.append("existing model/configuration/cost authorization is not confirmed")
    if not isinstance(authorization, dict) or authorization.get("compute_authorized") is not True:
        blockers.append("compute authorization is not confirmed")

    contract = plan.get("response_contract", {}) if isinstance(plan, dict) else {}
    if not isinstance(contract, dict) or contract.get("task6_content_schema_status") != "fixed_record_imported":
        blockers.append("task6 response content schema is missing")
    elif not isinstance(contract.get("task6_content_schema"), dict) or not contract["task6_content_schema"]:
        blockers.append("task6 response content schema document is unresolved")

    all_blockers = errors + blockers
    return {
        "errors": errors,
        "readiness_blockers": blockers,
        "execution_ready": not all_blockers,
        "model_calls_made": 0,
        "network_used": False,
    }


def validate_response_envelopes(
    plan: dict[str, Any],
    records: list[dict[str, Any]],
    private_response_root: Path | None = None,
) -> dict[str, Any]:
    """Validate response metadata and raw bytes without judging answer content.

    The task-specific semantic response schema is intentionally not inferred.
    """
    errors: list[str] = []
    expected_ids = list(_request_map(plan))
    if expected_ids != list(EXPECTED_IDS):
        errors.append("request plan must define F and HF before responses can be checked")
        expected_ids = list(EXPECTED_IDS)
    if not isinstance(records, list):
        records = []
        errors.append("response records must be a list")

    seen: set[str] = set()
    valid: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"response record {index} must be an object")
            continue
        request_id = record.get("request_id")
        if request_id not in expected_ids:
            errors.append(f"response record {index}: unknown request_id")
            continue
        if request_id in seen:
            errors.append(f"{request_id}: duplicate response record; retries are disabled")
        seen.add(request_id)
        if record.get("attempt") != 1:
            errors.append(f"{request_id}: attempt must be 1 because retries are disabled")
        finish_reason = record.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason.strip():
            errors.append(f"{request_id}: finish_reason is required")
        elif finish_reason.lower() in {"length", "max_tokens", "error", "cancelled"}:
            errors.append(f"{request_id}: finish_reason {finish_reason!r} does not represent a complete answer")
        if record.get("context_truncated") is not False:
            errors.append(f"{request_id}: context_truncated must be false")
        raw_digest = record.get("raw_response_sha256")
        if not isinstance(raw_digest, str) or not SHA256_RE.fullmatch(raw_digest):
            errors.append(f"{request_id}: raw_response_sha256 must be a 64-character hex digest")
        raw_size = record.get("raw_response_bytes")
        if not isinstance(raw_size, int) or raw_size <= 0:
            errors.append(f"{request_id}: raw_response_bytes must be a positive integer")
        raw_path = record.get("raw_response_path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{request_id}: raw_response_path is required")
        elif _unsafe_relative_path(raw_path):
            errors.append(f"{request_id}: raw_response_path must stay relative to the private response root")
        elif private_response_root is None:
            errors.append(f"{request_id}: private_response_root is required to verify raw response bytes")
        elif isinstance(raw_digest, str) and SHA256_RE.fullmatch(raw_digest) and isinstance(raw_size, int) and raw_size > 0:
            root = private_response_root.resolve()
            raw_file = (root / raw_path).resolve()
            if root not in raw_file.parents and raw_file != root:
                errors.append(f"{request_id}: raw_response_path escapes the private response root")
            elif not raw_file.is_file():
                errors.append(f"{request_id}: raw response file is missing")
            else:
                actual_size, actual_digest = _digest_file(raw_file)
                if actual_size != raw_size:
                    errors.append(f"{request_id}: raw response byte count does not match the record")
                if actual_digest.lower() != raw_digest.lower():
                    errors.append(f"{request_id}: raw response SHA256 does not match the record")
        if not any(error.startswith(f"{request_id}:") for error in errors):
            valid.append(request_id)

    missing = [request_id for request_id in expected_ids if request_id not in seen]
    for request_id in missing:
        errors.append(f"{request_id}: response record is missing")

    return {
        "errors": errors,
        "valid_request_ids": valid,
        "missing_request_ids": missing,
        "envelope_integrity_ok": not errors and set(valid) == set(expected_ids),
        "content_schema_validated": False,
        "model_calls_made_by_validator": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline-only task 6 structural preflight")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("request_plan.json"))
    parser.add_argument("--private-input-root", type=Path, default=Path("private_inputs"))
    parser.add_argument("--response-records", type=Path, help="optional JSON array of actual normalized response envelopes")
    parser.add_argument("--private-response-root", type=Path, help="root directory containing raw response bytes")
    args = parser.parse_args()

    with args.manifest.open("r", encoding="utf-8") as stream:
        plan = json.load(stream)
    report = validate_plan(plan, args.private_input_root)
    if args.response_records is not None:
        with args.response_records.open("r", encoding="utf-8") as stream:
            response_records = json.load(stream)
        report["response_integrity"] = validate_response_envelopes(
            plan, response_records, args.private_response_root
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    response_check_ok = "response_integrity" not in report or report["response_integrity"]["envelope_integrity_ok"]
    return 0 if report["execution_ready"] and response_check_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
