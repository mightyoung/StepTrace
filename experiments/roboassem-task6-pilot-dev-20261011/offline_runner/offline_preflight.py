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

from jsonschema import Draft202012Validator


EXPECTED_IDS = ("F", "HF")
EXPECTED_F_PTS = [510]
EXPECTED_HF_PTS = [0, 300, 450, 510]
EXPECTED_F_FRAME_IDS = ["k04"]
EXPECTED_HF_FRAME_IDS = ["k01", "k02", "k03", "k04"]
EXPECTED_FRAME_TIMES = {"k01": (0, 0), "k02": (10, 300), "k03": (15, 450), "k04": (17, 510)}
EXPECTED_FRAME_METADATA = {
    "k01": {
        "masked_size_bytes": 182917,
        "masked_sha256": "d460c282a5fb02b5c98b36ad0b90f0840a037edfd9114fad4e2609a35a95fcc9",
        "source_filename": "frame-01.png",
        "source_size_bytes": 188844,
        "source_sha256": "6133cfd6a5c7e43bf8c15969fd24736786b727e8d508fcc8f27ac4fa536773ec",
    },
    "k02": {
        "masked_size_bytes": 194260,
        "masked_sha256": "6f73af6b1dc20d8adc5ba01a5bc55246c5f3f1c8373c0e9ad55b3d3db0915091",
        "source_filename": "frame-02.png",
        "source_size_bytes": 200574,
        "source_sha256": "e4c2c8aaedf45460a1414ada5dc3f5b3abca81dea5b785d2e405b4c0dd69b2c3",
    },
    "k03": {
        "masked_size_bytes": 195651,
        "masked_sha256": "601c93866a4263af370480dcf0d79f672342b110d7cf4ab67b918ec5e2bf262d",
        "source_filename": "frame-03.png",
        "source_size_bytes": 202480,
        "source_sha256": "f553ab609b40c421076e7fb3735a14cdc3d7818a068616344feba7f223bcb8db",
    },
    "k04": {
        "masked_size_bytes": 223238,
        "masked_sha256": "7fa67434a107f5086628a5175c74a9a7935ff2992b0ef10a4d1ffed0506645a8",
        "source_filename": "frame-04.png",
        "source_size_bytes": 229759,
        "source_sha256": "cc2f0bed03c5df2245392b58fd6da5a02b53a0e0e9a6acd6cb3b75750aec80a2",
    },
}
EXPECTED_TIME_BASE = "1/30"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
EXPECTED_SYSTEM_SHA256 = "4f08f7f8529e2badafa35cc80a66bf781b8f6bff376102556f1e762f0e273a57"
EXPECTED_QUESTION_SHA256 = "160e6d72526c226aa4f124abf7bf675840b2b74be467ddfb483a6ca8495f48d6"
EXPECTED_MAC_CONTRACT_SHA256 = "2bb0306715bb33a40ff829c0ef2c1f2d786fe9f1d6b8c2ff4788ed7993a97ce8"
EXPECTED_MAC_PARAMETERS = {
    "model": "Qwen3.5-9B-Q4_K_M",
    "temperature": 0,
    "seed": 1,
    "max_tokens": 2048,
    "stream": False,
    "chat_template_kwargs": {"enable_thinking": False},
}
EXPECTED_SOURCE_SCHEMA_SHA256 = {
    "F": "77240e2c680ae3d5b4ec6f28083c32bdb54eb8e757b47e0678b63ac40c751b4d",
    "HF": "45075bdcf0eb9144ddca918c74600901f4660f4b4a2d06b0601277b5b2193753",
}


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


def authorization_blockers(authorization: Any) -> list[str]:
    """Require three separately evidenced action authorizations."""
    requirements = (
        ("kaggle_upload", "Kaggle upload"),
        ("model_download", "model download"),
        ("inference", "inference"),
    )
    blockers: list[str] = []
    if not isinstance(authorization, dict):
        authorization = {}
    for key, label in requirements:
        status = authorization.get(key)
        if not isinstance(status, dict) or status.get("status") != "authorized":
            blockers.append(f"{label} authorization is not confirmed")
        elif not isinstance(status.get("evidence"), str) or not status["evidence"].strip():
            blockers.append(f"{label} authorization evidence is missing")
    return blockers


def _schema_spec(plan: dict[str, Any], request_id: str) -> dict[str, Any] | None:
    contract = plan.get("response_contract")
    if not isinstance(contract, dict):
        return None
    schemas = contract.get("schemas")
    if not isinstance(schemas, dict):
        return None
    spec = schemas.get(request_id)
    return spec if isinstance(spec, dict) else None


def _load_arm_schema(
    plan: dict[str, Any], request_id: str, package_root: Path
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    errors: list[str] = []
    spec = _schema_spec(plan, request_id)
    if spec is None:
        return None, None, [f"{request_id}: separate content schema is missing"]
    relative_path = spec.get("path")
    expected_digest = spec.get("local_file_sha256")
    if not isinstance(relative_path, str) or not relative_path or _unsafe_relative_path(relative_path):
        return None, None, [f"{request_id}: schema path must be a safe package-relative path"]
    if not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(expected_digest):
        return None, None, [f"{request_id}: local schema SHA256 is unresolved"]
    root = package_root.resolve()
    schema_path = (root / relative_path).resolve()
    if root not in schema_path.parents:
        return None, None, [f"{request_id}: schema path escapes the package root"]
    if not schema_path.is_file():
        return None, relative_path, [f"{request_id}: local schema file is missing"]
    actual_size, actual_digest = _digest_file(schema_path)
    if actual_size <= 0 or actual_digest.lower() != expected_digest.lower():
        return None, relative_path, [f"{request_id}: local schema file SHA256 does not match the plan"]
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, relative_path, [f"{request_id}: local schema JSON cannot be read: {exc}"]
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:  # jsonschema exposes several schema-error subclasses.
        return None, relative_path, [f"{request_id}: local schema is not valid Draft 2020-12 JSON Schema: {exc}"]
    return schema, relative_path, errors


def validate_response_content(
    plan: dict[str, Any],
    request_id: str,
    content: Any,
    package_root: Path | None = None,
) -> dict[str, Any]:
    """Validate a decoded response object against the correct F or HF schema."""
    root = package_root or Path(__file__).resolve().parent
    schema, schema_file, errors = _load_arm_schema(plan, request_id, root)
    if schema is None:
        return {"valid": False, "errors": errors, "schema_file": schema_file}
    validator = Draft202012Validator(schema)
    validation_errors = sorted(
        validator.iter_errors(content),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    for error in validation_errors:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append(f"{request_id} {path}: {error.message}")
    return {
        "valid": not errors,
        "errors": errors,
        "schema_file": schema_file,
        "validator": "jsonschema.Draft202012Validator",
        "offline_schema_validation": True,
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

    expected_by_id = {
        "F": (EXPECTED_F_PTS, EXPECTED_F_FRAME_IDS),
        "HF": (EXPECTED_HF_PTS, EXPECTED_HF_FRAME_IDS),
    }
    expected_roles = {"k01": "past", "k02": "past", "k03": "past", "k04": "current"}
    for request_id in EXPECTED_IDS:
        expected_pts, expected_frame_ids = expected_by_id[request_id]
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
        frame_ids = [frame.get("frame_id") for frame in frames if isinstance(frame, dict)]
        if frame_ids != expected_frame_ids:
            errors.append(f"{request_id} frame IDs must be exactly {expected_frame_ids}")
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                errors.append(f"{request_id} frame {index} must be an object")
                continue
            expected_frame_id = expected_frame_ids[index] if index < len(expected_frame_ids) else None
            if frame.get("time_base") != EXPECTED_TIME_BASE:
                errors.append(f"{request_id} frame {index} time_base must be 1/30")
            if frame.get("frame_id") != expected_frame_id or frame.get("path") != f"{expected_frame_id}.png":
                errors.append(f"{request_id} frame {index} must map the reported input ID to its masked PNG filename")
            if frame.get("role") != expected_roles.get(expected_frame_id):
                errors.append(f"{request_id} frame {index} role does not match the Mac input contract")
            expected_time = EXPECTED_FRAME_TIMES.get(expected_frame_id)
            if expected_time and (frame.get("video_time_seconds_reported"), frame.get("pts")) != expected_time:
                errors.append(f"{request_id} frame {index} time/PTS differs from the Mac input contract")
            if frame.get("source_frame_index_reported") != frame.get("pts"):
                errors.append(f"{request_id} frame {index} source frame index differs from the Mac input contract")
            if frame.get("width") != 640 or frame.get("height") != 480 or frame.get("format") != "PNG":
                errors.append(f"{request_id} frame {index} must be a 640x480 PNG")
            if frame.get("masked_derivative") is not True:
                errors.append(f"{request_id} frame {index} must reference the masked PNG derivative")
            if frame.get("mask") != {
                "type": "opaque_black_rectangle",
                "x": 0,
                "y": 400,
                "width": 112,
                "height": 80,
                "opacity": 1,
                "outside_rectangle_rgb_unchanged": True,
                "provenance": "Mac executor report: protocol overlay rule and observed 1x label position; source pixels are not in this cloud workspace",
            }:
                errors.append(f"{request_id} frame {index} mask metadata differs from the Mac contract")
            size = frame.get("size_bytes")
            digest = frame.get("sha256")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                errors.append(f"{request_id} frame {index} masked PNG byte size is invalid")
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                errors.append(f"{request_id} frame {index} masked PNG SHA256 is invalid")
            known_metadata = EXPECTED_FRAME_METADATA.get(expected_frame_id)
            if known_metadata and size != known_metadata["masked_size_bytes"]:
                errors.append(f"{request_id} frame {index} masked PNG byte size differs from the Mac report")
            if known_metadata and digest != known_metadata["masked_sha256"]:
                errors.append(f"{request_id} frame {index} masked PNG SHA256 differs from the Mac report")
            source_frame = frame.get("source_frame")
            if not isinstance(source_frame, dict) or source_frame.get("is_model_input") is not False:
                errors.append(f"{request_id} frame {index} must retain source-frame provenance separately from model input")
            elif (
                not isinstance(source_frame.get("size_bytes"), int)
                or isinstance(source_frame.get("size_bytes"), bool)
                or source_frame.get("size_bytes") <= 0
                or not isinstance(source_frame.get("sha256"), str)
                or not SHA256_RE.fullmatch(source_frame["sha256"])
            ):
                errors.append(f"{request_id} frame {index} source-frame byte metadata is invalid")
            elif known_metadata and (
                source_frame.get("filename") != known_metadata["source_filename"]
                or source_frame.get("size_bytes") != known_metadata["source_size_bytes"]
                or source_frame.get("sha256") != known_metadata["source_sha256"]
            ):
                errors.append(f"{request_id} frame {index} source-frame identity differs from the Mac report")
            path = frame.get("path")
            if not isinstance(path, str) or not path:
                errors.append(f"{request_id} frame {index} needs a local relative path")
            elif _unsafe_relative_path(path):
                errors.append(f"{request_id} frame {index} path must stay relative to the private input root")
            role_block = frame.get("image_prompt_text_block")
            if role_block != f"图像ID={expected_frame_id}；角色={expected_roles.get(expected_frame_id)}":
                errors.append(f"{request_id} frame {index} image role text block does not match the input contract")
        image_blocks = request.get("image_blocks_in_order")
        expected_blocks = [
            {
                "input_id": frame_id,
                "role": expected_roles[frame_id],
                "text_block": f"图像ID={frame_id}；角色={expected_roles[frame_id]}",
                "image_path": f"{frame_id}.png",
                "block_order": "text_then_image",
            }
            for frame_id in expected_frame_ids
        ]
        if image_blocks != expected_blocks:
            errors.append(f"{request_id} image blocks must preserve the Mac text-then-image order")

    f_request = by_id.get("F")
    hf_request = by_id.get("HF")
    if f_request and hf_request:
        f_frames = f_request.get("frames", [])
        hf_frames = hf_request.get("frames", [])
        if f_frames and hf_frames:
            f_current = f_frames[-1] if isinstance(f_frames[-1], dict) else {}
            hf_current = hf_frames[-1] if isinstance(hf_frames[-1], dict) else {}
            if f_current != hf_current:
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


def validate_plan(
    plan: dict[str, Any],
    private_input_root: Path,
    package_root: Path | None = None,
) -> dict[str, Any]:
    """Check the imported Mac contract, local files, schemas, and independent gates."""
    shape = validate_plan_shape(plan)
    errors = list(shape["errors"])
    blockers: list[str] = []
    root = private_input_root.resolve()
    package = package_root or Path(__file__).resolve().parent

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
    if not isinstance(prompt, dict) or prompt.get("status") != "mac_reported_text_imported":
        blockers.append("task6 Mac prompt text is missing")
    else:
        system_text = prompt.get("system_text")
        system_digest = prompt.get("system_text_sha256")
        if not isinstance(system_text, str) or not isinstance(system_digest, str) or not SHA256_RE.fullmatch(system_digest):
            blockers.append("task6 system prompt text or SHA256 is unresolved")
        elif hashlib.sha256(system_text.encode("utf-8")).hexdigest() != system_digest.lower():
            errors.append("task6 system prompt SHA256 does not match its stored UTF-8 text")
        elif system_digest.lower() != EXPECTED_SYSTEM_SHA256:
            errors.append("task6 system prompt differs from the Mac-reported SHA256")

        shared_question = prompt.get("shared_user_question_text")
        question_digest = prompt.get("shared_user_question_sha256")
        if not isinstance(shared_question, str) or not isinstance(question_digest, str) or not SHA256_RE.fullmatch(question_digest):
            blockers.append("task6 shared user question or SHA256 is unresolved")
        elif hashlib.sha256(shared_question.encode("utf-8")).hexdigest() != question_digest.lower():
            errors.append("task6 shared user question SHA256 does not match its stored UTF-8 text")
        elif question_digest.lower() != EXPECTED_QUESTION_SHA256:
            errors.append("task6 shared user question differs from the Mac-reported SHA256")

        if prompt.get("payload_sha256") is not None:
            errors.append("full multimodal payload SHA256 must remain unresolved until exact backend serialization is known")

        request_map = _request_map(plan)
        expected_role_ids = {"F": EXPECTED_F_FRAME_IDS, "HF": EXPECTED_HF_FRAME_IDS}
        if isinstance(shared_question, str):
            for request_id, input_ids in expected_role_ids.items():
                request = request_map.get(request_id, {})
                condition_text = request.get("condition_role_text")
                expected_roles = [
                    {"input_id": input_id, "role": "current" if input_id == "k04" else "past"}
                    for input_id in input_ids
                ]
                expected_suffix = f"{request_id}:\n本条件图像角色：{json.dumps(expected_roles, ensure_ascii=False, separators=(',', ':'))}"
                if condition_text != expected_suffix:
                    errors.append(f"{request_id} condition role text differs from the Mac input contract")
                elif request.get("condition_role_text_sha256") != hashlib.sha256(expected_suffix.encode("utf-8")).hexdigest():
                    errors.append(f"{request_id} condition role text SHA256 does not match")
                elif request.get("assembled_user_text_sha256") != hashlib.sha256((shared_question + "\n" + expected_suffix).encode("utf-8")).hexdigest():
                    errors.append(f"{request_id} assembled text SHA256 does not match its stored components")
                if request.get("full_multimodal_request_bytes_sha256") is not None:
                    errors.append(f"{request_id} full multimodal payload hash must remain unresolved")

    generation = plan.get("generation_parameters", {}) if isinstance(plan, dict) else {}
    parameters = generation.get("parameters") if isinstance(generation, dict) else None
    runtime = plan.get("runtime", {}) if isinstance(plan, dict) else {}
    if not isinstance(parameters, dict):
        blockers.append("Mac-reported generation parameters are missing")
    else:
        expected_parameter_keys = set(EXPECTED_MAC_PARAMETERS) | {"top_p", "top_k"}
        if set(parameters) != expected_parameter_keys:
            errors.append("generation parameter fields differ from the Mac contract record")
        for key, expected in EXPECTED_MAC_PARAMETERS.items():
            actual = parameters.get(key)
            if type(actual) is not type(expected) or actual != expected:
                errors.append(f"generation parameter {key} differs from the Mac-reported value")
        chat_template = parameters.get("chat_template_kwargs")
        if not isinstance(chat_template, dict) or type(chat_template.get("enable_thinking")) is not bool or chat_template.get("enable_thinking") is not False:
            errors.append("generation parameter enable_thinking differs from the Mac-reported boolean false")
        for key in ("top_p", "top_k"):
            status_key = f"{key}_status"
            if key not in parameters or parameters[key] is None:
                blockers.append(f"{key} is unresolved; do not silently default or substitute it")
            elif generation.get(status_key) != "verified_from_authoritative_backend_record":
                blockers.append(f"{key} lacks an authoritative backend value record")
        if type(generation.get("request_timeout_seconds")) is not int or generation.get("request_timeout_seconds") != 60:
            errors.append("per-request HTTP timeout must remain the Mac-reported 60 seconds")
        if (
            type(generation.get("parallelism")) is not int
            or generation.get("parallelism") != 1
            or type(generation.get("retry_count")) is not int
            or generation.get("retry_count") != 0
        ):
            errors.append("request execution policy differs from the Mac contract")
        if generation.get("request_order") != ["F", "HF"]:
            errors.append("request order must remain F then HF")
        if isinstance(runtime, dict) and runtime.get("parameters") != parameters:
            errors.append("runtime parameter record differs from the Mac-reported generation parameter record")

    if not isinstance(runtime, dict) or not runtime.get("backend") or not runtime.get("backend_version"):
        blockers.append("actual backend and version are unresolved")
    backend_schema = runtime.get("backend_json_schema_support") if isinstance(runtime, dict) else None
    if not isinstance(backend_schema, dict) or backend_schema.get("status") != "verified":
        blockers.append("backend strict JSON Schema support is not confirmed")
    if isinstance(backend_schema, dict) and backend_schema.get("contains_min_max_support") != "verified":
        blockers.append("backend contains/minContains/maxContains support is not confirmed")

    authorization = plan.get("authorization", {}) if isinstance(plan, dict) else {}
    blockers.extend(authorization_blockers(authorization))
    if not isinstance(authorization, dict) or authorization.get("existing_model_and_cost_authorization") is not True:
        blockers.append("existing model/configuration/cost authorization is not confirmed")
    if not isinstance(authorization, dict) or authorization.get("compute_authorized") is not True:
        blockers.append("compute authorization is not confirmed")

    contract = plan.get("response_contract", {}) if isinstance(plan, dict) else {}
    if (
        not isinstance(contract, dict)
        or contract.get("format") != "json_schema"
        or contract.get("name") != "roboassem_task6_visual_state"
        or contract.get("strict") is not True
    ):
        errors.append("response format must remain strict json_schema")
    if isinstance(contract, dict) and contract.get("schema_file_bytes_available_from_mac") is not False:
        errors.append("Mac source schema byte availability must not be overstated")
    if isinstance(contract, dict):
        reported_contract_digest = contract.get("source_contract_report_sha256_reported_by_mac")
        if reported_contract_digest != EXPECTED_MAC_CONTRACT_SHA256:
            errors.append("reported Mac text-contract SHA256 differs from the supplied identity")
        if contract.get("source_contract_hash_status") != "reported_not_recomputed_source_bytes_unavailable":
            errors.append("Mac source text-contract hash must remain marked reported, not locally verified")
    if isinstance(contract, dict) and contract.get("request_schema_selection_status") != "confirmed":
        blockers.append("request-time schema profile is not confirmed against the Mac contract and backend")
    if isinstance(contract, dict) and contract.get("offline_validation_profile_version") != "2":
        errors.append("offline validation profile version differs from the recorded derived schema profile")
    for request_id in EXPECTED_IDS:
        schema, schema_path, schema_errors = _load_arm_schema(plan, request_id, package)
        errors.extend(schema_errors)
        spec = _schema_spec(plan, request_id)
        if not isinstance(spec, dict) or spec.get("byte_identity_with_mac_source_claimed") is not False:
            errors.append(f"{request_id} schema must not claim byte identity with the unavailable Mac schema file")
        source_digest = spec.get("mac_original_schema_file_sha256") if isinstance(spec, dict) else None
        if source_digest != EXPECTED_SOURCE_SCHEMA_SHA256[request_id]:
            errors.append(f"{request_id} source schema SHA256 differs from the Mac-reported identity")
        if schema is not None:
            condition = schema.get("properties", {}).get("condition", {}).get("const")
            if condition != request_id:
                errors.append(f"{request_id} validation schema is not arm-specific")
            if schema.get("properties", {}).get("schema_version", {}).get("const") != "1":
                errors.append(f"{request_id} validation schema must retain source output schema_version 1")
            if not isinstance(schema_path, str):
                errors.append(f"{request_id} schema file path is unresolved")
    if isinstance(contract, dict) and contract.get("backend_schema_support") != "verified":
        blockers.append("backend strict JSON Schema support is not confirmed in the response contract")

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
    package_root: Path | None = None,
) -> dict[str, Any]:
    """Check response metadata, exact body bytes, and per-arm JSON Schema content."""
    errors: list[str] = []
    content_validation: dict[str, dict[str, Any]] = {}
    schema_root = package_root or Path(__file__).resolve().parent
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
        record_error_start = len(errors)
        if type(record.get("attempt")) is not int or record.get("attempt") != 1:
            errors.append(f"{request_id}: attempt must be 1 because retries are disabled")
        finish_reason = record.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason.strip():
            errors.append(f"{request_id}: finish_reason is required")
        elif finish_reason.lower() in {"length", "max_tokens", "error", "cancelled", "content_filter", "filtered", "abort"}:
            errors.append(f"{request_id}: finish_reason {finish_reason!r} does not represent a complete answer")
        if record.get("context_truncated") is not False:
            errors.append(f"{request_id}: context_truncated must be false")
        raw_digest = record.get("raw_response_sha256")
        if not isinstance(raw_digest, str) or not SHA256_RE.fullmatch(raw_digest):
            errors.append(f"{request_id}: raw_response_sha256 must be a 64-character hex digest")
        raw_size = record.get("raw_response_bytes")
        if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size <= 0:
            errors.append(f"{request_id}: raw_response_bytes must be a positive integer")
        raw_path = record.get("raw_response_path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{request_id}: raw_response_path is required")
            raw_file = None
        elif _unsafe_relative_path(raw_path):
            errors.append(f"{request_id}: raw_response_path must stay relative to the private response root")
            raw_file = None
        elif private_response_root is None:
            errors.append(f"{request_id}: private_response_root is required to verify raw response bytes")
            raw_file = None
        else:
            root = private_response_root.resolve()
            raw_file = (root / raw_path).resolve()
            if root not in raw_file.parents and raw_file != root:
                errors.append(f"{request_id}: raw_response_path escapes the private response root")
                raw_file = None
            elif not raw_file.is_file():
                errors.append(f"{request_id}: raw response file is missing")
                raw_file = None
        verified_body: bytes | None = None
        if raw_file is not None and isinstance(raw_digest, str) and SHA256_RE.fullmatch(raw_digest) and isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size > 0:
            if raw_file.is_file():
                actual_size, actual_digest = _digest_file(raw_file)
                if actual_size != raw_size:
                    errors.append(f"{request_id}: raw response byte count does not match the record")
                if actual_digest.lower() != raw_digest.lower():
                    errors.append(f"{request_id}: raw response SHA256 does not match the record")
                if actual_size == raw_size and actual_digest.lower() == raw_digest.lower():
                    verified_body = raw_file.read_bytes()
        if verified_body is not None:
            try:
                decoded = json.loads(verified_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"{request_id}: verified response bytes are not a UTF-8 JSON value: {exc}")
            else:
                content_result = validate_response_content(plan, request_id, decoded, schema_root)
                content_validation[request_id] = content_result
                if not content_result["valid"]:
                    errors.extend(content_result["errors"])
        if len(errors) == record_error_start and verified_body is not None and content_validation.get(request_id, {}).get("valid") is True:
            valid.append(request_id)

    missing = [request_id for request_id in expected_ids if request_id not in seen]
    for request_id in missing:
        errors.append(f"{request_id}: response record is missing")

    return {
        "errors": errors,
        "valid_request_ids": valid,
        "missing_request_ids": missing,
        "envelope_integrity_ok": not errors and set(valid) == set(expected_ids),
        "content_schema_validated": set(content_validation) == set(expected_ids),
        "content_validation": content_validation,
        "model_calls_made_by_validator": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline-only task 6 structural preflight")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("request_plan.json"))
    parser.add_argument("--private-input-root", type=Path, default=Path(__file__).with_name("private_inputs"))
    parser.add_argument("--response-records", type=Path, help="optional JSON array of actual normalized response envelopes")
    parser.add_argument("--private-response-root", type=Path, help="root directory containing raw response bytes")
    args = parser.parse_args()

    with args.manifest.open("r", encoding="utf-8") as stream:
        plan = json.load(stream)
    report = validate_plan(plan, args.private_input_root, args.manifest.parent)
    if args.response_records is not None:
        with args.response_records.open("r", encoding="utf-8") as stream:
            response_records = json.load(stream)
        report["response_integrity"] = validate_response_envelopes(
            plan, response_records, args.private_response_root, args.manifest.parent
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    response_check_ok = "response_integrity" not in report or report["response_integrity"]["envelope_integrity_ok"]
    return 0 if report["execution_ready"] and response_check_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
