#!/usr/bin/env python3
"""Offline gates and return-bundle checks for the Task6 v5 pilot.

No command downloads data or sends generation. The opt-in ``gpu-preflight``
starts the pinned local server, checks non-generating loopback endpoints, then
stops it. Unit-test payloads and mocked responses are synthetic.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


EXPERIMENT_ID = "roboassem-task6-dev-20261011-v0.1"
EXPECTED_BACKEND_COMMIT = "8345f333951c661d166b00e6f9362e553768f292"
EXPECTED_ARMS = ("F", "HF")
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[2]
REQUIRED_SERVER_FLAGS = (
    "--no-mmproj-offload",
    "--image-max-tokens",
    "--no-context-shift",
    "--no-cache-prompt",
    "--top-k",
    "--top-p",
    "--temp",
    "--seed",
)
_RUN_EVIDENCE_NAMES = frozenset(
    {
        "attempts.jsonl",
        "attempt-events.jsonl",
        "failures.jsonl",
        "results.json",
        "execution-error.json",
        "execution-config.json",
        "server.log",
        "gpu-memory.csv",
    }
)


def _is_run_evidence_name(name: str) -> bool:
    if name in _RUN_EVIDENCE_NAMES:
        return True
    return any(
        fnmatch.fnmatchcase(name, pattern)
        for pattern in (
            "request-*.json",
            "response-*-raw.json",
            "response-*-http-error-raw.bin",
        )
    )


def _is_complete_gpu_preflight_server_log(path: Path, setup_evidence_dir: Path) -> bool:
    preflight_dir = setup_evidence_dir / "gpu-preflight"
    return (
        path == preflight_dir / "server.log"
        and (preflight_dir / "gpu-preflight.json").is_file()
        and (preflight_dir / "server-stopped.json").is_file()
    )


def _has_prior_execution_artifacts(results_dir: Path) -> bool:
    """Treat any result-root content as one-shot evidence, allowing staged setup evidence."""
    results_dir = Path(results_dir)
    if results_dir.is_symlink():
        return True
    if not results_dir.exists():
        return False
    if not results_dir.is_dir():
        return True
    try:
        for child in results_dir.iterdir():
            if child.name == "runner.lock":
                continue
            if child.name == "setup-evidence":
                if child.is_symlink() or not child.is_dir():
                    return True
                for evidence in child.rglob("*"):
                    if evidence.is_symlink():
                        return True
                    if _is_run_evidence_name(evidence.name) and not _is_complete_gpu_preflight_server_log(evidence, child):
                        return True
                continue
            return True
    except OSError:
        return True
    return False


def sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def safe_artifact_path(root: Path, relative: str) -> Path:
    """Resolve a bundle path and reject absolute paths, traversal, and symlinks."""
    candidate_name = Path(relative)
    if candidate_name.is_absolute() or not relative or "\\" in relative:
        raise ValueError(f"artifact path must be a safe relative path: {relative!r}")
    root_resolved = root.resolve()
    candidate = (root_resolved / candidate_name).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"artifact path escapes the result root: {relative!r}")
    current = root_resolved
    for part in candidate_name.parts:
        if part in ("", ".", ".."):
            raise ValueError(f"artifact path contains traversal: {relative!r}")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"artifact path may not traverse a symlink: {relative!r}")
    return candidate


def validate_runtime_record(runtime: Any, environment: dict[str, Any]) -> list[str]:
    """Check recorded build/hardware/software identity against the candidate lock."""
    issues: list[str] = []
    if not isinstance(runtime, dict):
        return ["runtime verification must be a JSON object"]
    if runtime.get("commit") != EXPECTED_BACKEND_COMMIT:
        issues.append("commit does not match the pinned llama.cpp revision")
    if not isinstance(runtime.get("version"), str) or not runtime["version"].strip():
        issues.append("version must contain llama-server --version output")
    if not isinstance(runtime.get("binary_bytes"), int) or runtime["binary_bytes"] <= 0:
        issues.append("binary_bytes must be a positive integer")
    if not re.fullmatch(r"[0-9a-f]{64}", str(runtime.get("binary_sha256", ""))):
        issues.append("binary_sha256 must be a lowercase 64-character SHA-256")
    if runtime.get("cuda_release") != environment.get("cuda_release"):
        issues.append("cuda_release differs from the environment lock")
    marker = environment.get("nvcc_version_marker")
    if marker and (not isinstance(runtime.get("nvcc_version"), str) or marker not in runtime["nvcc_version"]):
        issues.append("nvcc_version does not match the pinned CUDA update")
    if not isinstance(runtime.get("compute_capability"), (int, float)) or isinstance(runtime.get("compute_capability"), bool) or runtime["compute_capability"] < environment.get("minimum_compute_capability", 0):
        issues.append("compute_capability is below the candidate minimum")
    if not isinstance(runtime.get("gpu_vram_mib"), int) or runtime["gpu_vram_mib"] < environment.get("minimum_vram_mib", 0):
        issues.append("gpu_vram_mib is below the candidate minimum")
    minimum_ram = environment.get("minimum_host_ram_mib")
    if minimum_ram is not None and (not isinstance(runtime.get("host_ram_mib"), int) or runtime["host_ram_mib"] < minimum_ram):
        issues.append("host_ram_mib is below the candidate minimum")
    minimum_disk = environment.get("minimum_free_disk_gib")
    if minimum_disk is not None and (not isinstance(runtime.get("free_disk_gib"), (int, float)) or runtime["free_disk_gib"] < minimum_disk):
        issues.append("free_disk_gib is below the candidate minimum")
    minimum_driver = environment.get("minimum_driver_version")
    if minimum_driver:
        try:
            actual_driver = tuple(int(part) for part in str(runtime.get("driver_version", "")).split("."))
            expected_driver = tuple(int(part) for part in str(minimum_driver).split("."))
            if actual_driver < expected_driver:
                issues.append("driver_version is below the CUDA toolkit driver floor")
        except ValueError:
            issues.append("driver_version must be a dotted numeric version")
    expected_os = environment.get("os")
    if expected_os and runtime.get("os") != expected_os:
        issues.append("os differs from the environment lock")
    expected_machine = environment.get("machine")
    if expected_machine and runtime.get("machine") != expected_machine:
        issues.append("machine architecture differs from the environment lock")
    os_release = runtime.get("os_release")
    if not isinstance(os_release, dict) or not all(
        isinstance(os_release.get(key), str) and os_release[key].strip()
        for key in ("ID", "VERSION_ID")
    ):
        issues.append("os_release must contain non-empty ID and VERSION_ID fields")
    for field in ("cmake_version", "compiler_version"):
        if not isinstance(runtime.get(field), str) or not runtime[field].strip():
            issues.append(f"{field} must contain build-tool version output")
    for field in ("python_version", "jsonschema_version", "pillow_version"):
        if runtime.get(field) != environment.get(field):
            issues.append(f"{field} differs from the environment lock")
    seen_flags = runtime.get("server_flags_verified")
    if not isinstance(seen_flags, list) or any(flag not in seen_flags for flag in REQUIRED_SERVER_FLAGS):
        issues.append("server_flags_verified is missing required llama-server flags")
    if not isinstance(runtime.get("gpu_name"), str) or not runtime["gpu_name"].strip():
        issues.append("gpu_name must identify CUDA0")
    return issues


def _capture(command: list[str], *, timeout: int = 20) -> str:
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return completed.stdout.strip()


def record_runtime(run_root: Path) -> dict[str, Any]:
    """Record a built candidate and visible CUDA0 without loading model weights."""
    run_root = Path(run_root).resolve()
    source_root = run_root / "llama.cpp"
    binary = source_root / "build/bin/llama-server"
    if not source_root.is_dir() or not binary.is_file():
        raise FileNotFoundError("pinned llama.cpp checkout or llama-server binary is missing")
    commit = _capture(["git", "-C", str(source_root), "rev-parse", "HEAD"])
    if commit != EXPECTED_BACKEND_COMMIT:
        raise ValueError("llama.cpp checkout is not the pinned commit; no runtime record written")
    version = _capture([str(binary), "--version"])
    help_text = _capture([str(binary), "--help"])
    flags = [flag for flag in REQUIRED_SERVER_FLAGS if flag in help_text]
    missing_flags = sorted(set(REQUIRED_SERVER_FLAGS) - set(flags))
    if missing_flags:
        raise ValueError("llama-server help lacks required flags: " + ", ".join(missing_flags))
    gpu_rows = _capture(
        [
            "nvidia-smi",
            "--query-gpu=name,compute_cap,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    ).splitlines()
    if not gpu_rows:
        raise ValueError("nvidia-smi did not report CUDA0")
    gpu_values = [part.strip() for part in gpu_rows[0].split(",")]
    if len(gpu_values) != 4:
        raise ValueError("could not parse CUDA0 model/capability/VRAM/driver record")
    nvcc = _capture(["nvcc", "--version"])
    cmake_version = _capture(["cmake", "--version"])
    compiler_version = _capture(["c++", "--version"])
    cuda_match = re.search(r"release\s+(\d+\.\d+)", nvcc)
    if not cuda_match:
        raise ValueError("nvcc --version did not report a CUDA toolkit release")
    binary_bytes, binary_sha = sha256_file(binary)
    try:
        host_ram_mib = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024))
    except (AttributeError, OSError, ValueError):
        host_ram_mib = 0
    free_disk_gib = round(shutil.disk_usage(run_root).free / (1024**3), 2)
    runtime = {
        "schema_version": "steptrace.task6.runtime-verification.v1",
        "commit": commit,
        "binary_path": "llama.cpp/build/bin/llama-server",
        "binary_bytes": binary_bytes,
        "binary_sha256": binary_sha,
        "version": version,
        "server_help_sha256": hashlib.sha256(help_text.encode("utf-8")).hexdigest(),
        "server_flags_verified": flags,
        "cuda_release": cuda_match.group(1),
        "nvcc_version": nvcc,
        "cmake_version": cmake_version,
        "compiler_version": compiler_version,
        "gpu_name": gpu_values[0],
        "compute_capability": float(gpu_values[1]),
        "gpu_vram_mib": int(float(gpu_values[2])),
        "driver_version": gpu_values[3],
        "host_ram_mib": host_ram_mib,
        "free_disk_gib": free_disk_gib,
        "os": platform.system(),
        "machine": platform.machine(),
        "os_release": platform.freedesktop_os_release(),
        "python_version": sys.version.split()[0],
        "jsonschema_version": importlib.metadata.version("jsonschema"),
        "pillow_version": importlib.metadata.version("Pillow"),
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "generation_calls": 0,
        "server_started": False,
    }
    output_path = run_root / "runtime-verification.json"
    output_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return runtime


def _local_json_request(path: str, body: dict[str, Any] | None = None, *, timeout: int = 10, port: int = 8080) -> dict[str, Any]:
    """Issue a JSON request to the private loopback llama-server only."""
    if path not in ("/health", "/props", "/apply-template", "/tokenize"):
        raise ValueError(f"GPU preflight endpoint is not allowed: {path}")
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.URLError:
        raise
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"loopback endpoint {path} did not return a JSON object")
    return value


def _ensure_loopback_port_free(port: int = 8080) -> None:
    """Fail closed if another local service already owns the pinned port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))


def run_gpu_preflight(
    *,
    manifest_path: Path,
    contract_path: Path,
    input_root: Path,
    run_root: Path,
    rights_path: Path,
    provider_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Start the pinned local backend, inspect props/token bounds, and stop without generation."""
    for label, path in (("input_root", input_root), ("run_root", run_root), ("rights_path", rights_path)):
        try:
            Path(path).resolve().relative_to(REPO_ROOT.resolve())
        except ValueError:
            pass
        else:
            raise ValueError(f"GPU preflight {label} must be outside the Git worktree")
    output_dir = Path(output_dir).resolve()
    try:
        output_dir.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("GPU preflight output must be outside the Git worktree")
    if output_dir.exists():
        raise FileExistsError("GPU preflight output directory already exists; preserve it and choose a new private directory")
    output_dir.mkdir(parents=True, mode=0o700)

    manifest = _read_json(Path(manifest_path))
    cpu_gate = run_preflight(
        manifest_path=Path(manifest_path),
        input_root=Path(input_root),
        run_root=Path(run_root),
        rights_path=Path(rights_path),
        provider_id=provider_id,
        phase="execution",
        require_gpu=True,
    )
    (output_dir / "cpu-preflight.json").write_text(
        json.dumps(cpu_gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report: dict[str, Any] = {
        "schema_version": "steptrace.task6.gpu-non-generating-preflight.v1",
        "experiment_id": manifest.get("experiment_id"),
        "status": "blocked" if not cpu_gate.get("execution_ready") else "running",
        "preflight_passed": False,
        "generation_calls": 0,
        "external_network_calls": 0,
        "local_backend_http_requests": [],
        "local_server": "not_started",
        "cpu_gate_execution_ready": bool(cpu_gate.get("execution_ready")),
        "cpu_gate_blockers": cpu_gate.get("blockers", []),
        "context_values": [],
        "arms": {arm: {"status": "not_attempted", "generation_calls": 0} for arm in EXPECTED_ARMS},
        "errors": [],
    }
    if not cpu_gate.get("execution_ready"):
        (output_dir / "gpu-preflight.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report

    contract = _read_json(Path(contract_path))
    run_root = Path(run_root).resolve()
    runtime = _read_json(run_root / "runtime-verification.json")
    binary = run_root / "llama.cpp/build/bin/llama-server"
    model_files = [item["filename"] for item in manifest["model"]["files"]]
    if len(model_files) != 2:
        raise ValueError("manifest must pin exactly two model artifacts for this GPU preflight")
    server_args = manifest["backend"]["server_arguments"]
    try:
        host_index = server_args.index("--host")
        port_index = server_args.index("--port")
        if server_args[host_index + 1] != "127.0.0.1" or server_args[port_index + 1] != "8080":
            raise ValueError("pinned server must bind only to 127.0.0.1:8080")
    except (ValueError, IndexError) as exc:
        raise ValueError("manifest server arguments must bind only to 127.0.0.1:8080") from exc
    command = [
        str(binary), "-m", str(run_root / model_files[0]), "--mmproj", str(run_root / model_files[1]),
        *server_args,
    ]
    report["runtime_commit"] = runtime.get("commit")
    report["server_args"] = command
    report["output_dir"] = str(output_dir)
    arms = {arm: {"status": "not_attempted", "generation_calls": 0} for arm in EXPECTED_ARMS}
    context_values: list[int] = []
    server_process: subprocess.Popen | None = None
    server_log = None

    def request(path: str, body: dict[str, Any] | None = None, *, timeout: int = 10) -> dict[str, Any]:
        report["local_backend_http_requests"].append(path)
        return _local_json_request(path, body, timeout=timeout)

    try:
        server_log = (output_dir / "server.log").open("w", encoding="utf-8")
        _ensure_loopback_port_free(port=8080)
        server_process = subprocess.Popen(
            command,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        ready_deadline = time.monotonic() + int(manifest["budget"]["server_ready_timeout_seconds"])
        while True:
            if server_process.poll() is not None:
                raise RuntimeError("pinned llama-server exited before health check passed")
            try:
                request("/health", timeout=5)
                if server_process.poll() is not None:
                    raise RuntimeError("pinned llama-server exited before health check passed")
                break
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                if time.monotonic() >= ready_deadline:
                    raise TimeoutError("llama-server health check exceeded the pinned readiness timeout")
                time.sleep(2)

        props = request("/props")
        if server_process.poll() is not None:
            raise RuntimeError("pinned llama-server exited while reading backend props")
        (output_dir / "backend-props.json").write_text(
            json.dumps(props, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        def collect_context_values(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("n_ctx", "n_ctx_per_seq") and isinstance(item, int) and not isinstance(item, bool):
                        context_values.append(item)
                    collect_context_values(item)
            elif isinstance(value, list):
                for item in value:
                    collect_context_values(item)

        collect_context_values(props)
        report["context_values"] = context_values
        if not context_values or min(context_values) < 16384:
            raise ValueError(f"effective backend context is missing or below 16384: {context_values}")

        for request_spec in contract.get("requests", []):
            arm = request_spec.get("condition")
            if arm not in EXPECTED_ARMS:
                raise ValueError("input contract contains an unknown preflight arm")
            text_content = [
                {"type": "text", "text": part["text"]}
                for part in request_spec.get("exact_user_content_sequence", [])
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            ]
            if not text_content:
                raise ValueError(f"{arm} request has no pinned text content for preflight")
            template_result = request(
                "/apply-template",
                {
                    "messages": [
                        {"role": "system", "content": contract["system_prompt_exact"]},
                        {"role": "user", "content": text_content},
                    ],
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            prompt = template_result.get("prompt", template_result.get("content"))
            if not isinstance(prompt, str):
                raise ValueError(f"{arm} template endpoint did not return a prompt string")
            token_result = request("/tokenize", {"content": prompt, "add_special": True})
            tokens = token_result.get("tokens")
            if not isinstance(tokens, list):
                raise ValueError(f"{arm} tokenize endpoint did not return a token array")
            text_tokens = len(tokens)
            image_count = len(request_spec.get("input_ids", []))
            bound = text_tokens + image_count * 2048 + 2048 + 128
            arms[arm] = {
                "status": "passed" if bound <= min(context_values) else "failed",
                "text_tokens": text_tokens,
                "image_count": image_count,
                "conservative_context_bound": bound,
                "effective_context": min(context_values),
                "generation_calls": 0,
            }
            (output_dir / f"preflight-{arm}.json").write_text(
                json.dumps(arms[arm], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if bound > min(context_values):
                raise ValueError(f"{arm} conservative context bound exceeds effective context: {bound}")

        if [arm.get("condition") for arm in contract.get("requests", [])] != list(EXPECTED_ARMS):
            raise ValueError("contract request order must be exactly F then HF")
        report["status"] = "ready_for_operator_review"
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        for arm in EXPECTED_ARMS:
            if arms[arm]["status"] == "not_attempted":
                arms[arm]["status"] = "not_attempted_after_preflight_failure"
    finally:
        if server_process is not None:
            try:
                if server_process.poll() is None:
                    try:
                        server_process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        server_process.wait(timeout=int(manifest["budget"]["server_stop_wait_seconds"]))
                    except subprocess.TimeoutExpired:
                        try:
                            server_process.kill()
                        except ProcessLookupError:
                            pass
                        try:
                            server_process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            report["errors"].append("llama-server could not be reaped after kill")
            except Exception as exc:
                report["errors"].append(f"llama-server cleanup error: {type(exc).__name__}: {exc}")
            try:
                if server_process.poll() is None:
                    report["local_server"] = "unknown"
                    report["errors"].append("llama-server stop status remains unknown")
                else:
                    report["local_server"] = "verified_stopped"
                    (output_dir / "server-stopped.json").write_text(
                        json.dumps(
                            {"returncode": server_process.returncode, "generation_calls": 0},
                            ensure_ascii=False,
                            indent=2,
                        ) + "\n",
                        encoding="utf-8",
                    )
            except Exception as exc:
                report["local_server"] = "unknown"
                report["errors"].append(f"llama-server status readback error: {type(exc).__name__}: {exc}")
        if server_log is not None:
            server_log.close()
        report["arms"] = arms
        report["preflight_passed"] = (
            report["status"] == "ready_for_operator_review"
            and not report["errors"]
            and report["local_server"] == "verified_stopped"
            and all(arms[arm]["status"] == "passed" for arm in EXPECTED_ARMS)
        )
        if not report["preflight_passed"] and report["status"] == "ready_for_operator_review":
            report["status"] = "failed"
        (output_dir / "gpu-preflight.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number} is not valid JSON: {exc.msg}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_number} must contain a JSON object")
            continue
        rows.append(value)
    return rows


def validate_rights_attestation(
    attestation: Any,
    *,
    experiment_id: str,
    input_contract_sha256: str,
    provider_id: str,
) -> list[str]:
    """Require an explicit, contract-bound permission record before transfer/use."""
    issues: list[str] = []
    if not isinstance(attestation, dict):
        return ["rights attestation must be a JSON object"]
    expected = {
        "schema_version": "steptrace.data-rights-attestation.v1",
        "experiment_id": experiment_id,
        "input_contract_sha256": input_contract_sha256,
        "authorization_status": "authorized",
        "provider_id": provider_id,
    }
    for key, value in expected.items():
        if attestation.get(key) != value:
            issues.append(f"{key} must equal the approved value")
    # Matching two placeholders does not identify a receiving environment.
    if not isinstance(provider_id, str) or provider_id.strip().casefold() in {
        "", "unset", "unknown", "self-hosted", "self hosted", "自托管",
    }:
        issues.append("provider_id must identify the selected receiving environment")
    if attestation.get("synthetic_template_only") is not False:
        issues.append("synthetic_template_only must be false for a real-data authorization")
    for key in ("rights_basis", "license_id", "evidence_reference", "expires_utc"):
        if not isinstance(attestation.get(key), str) or not attestation[key].strip():
            issues.append(f"{key} is required")
    for key in ("inference_authorized", "transfer_authorized"):
        if attestation.get(key) is not True:
            issues.append(f"{key} must be true")
    if attestation.get("public_redistribution_authorized") is not False:
        issues.append("public_redistribution_authorized must be false for this private input")
    evidence_file = attestation.get("evidence_file")
    if not isinstance(evidence_file, str) or not evidence_file.strip():
        issues.append("evidence_file must name private rights evidence")
    evidence_bytes = attestation.get("evidence_bytes")
    if type(evidence_bytes) is not int or evidence_bytes <= 0:
        issues.append("evidence_bytes must be a positive integer")
    evidence_sha256 = attestation.get("evidence_sha256")
    if not isinstance(evidence_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", evidence_sha256) is None:
        issues.append("evidence_sha256 must be a lowercase SHA-256 digest")
    expiry = attestation.get("expires_utc")
    if isinstance(expiry, str) and expiry.strip():
        try:
            parsed_expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if parsed_expiry.tzinfo is None:
                issues.append("expires_utc must include a UTC offset")
            elif parsed_expiry <= datetime.now(timezone.utc):
                issues.append("rights attestation has expired")
        except ValueError:
            issues.append("expires_utc must be an ISO-8601 timestamp")
    return issues


def _frame_contract(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    frames = contract.get("model_input_frames")
    if not isinstance(frames, list):
        return {}
    return {
        str(item.get("input_id")): item
        for item in frames
        if isinstance(item, dict) and isinstance(item.get("input_id"), str)
    }


def _image_hashes(request: Any, arm: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    hashes: list[str] = []
    if not isinstance(request, dict):
        return hashes, [f"{arm} request must be an object"]
    messages = request.get("messages")
    if not isinstance(messages, list) or [m.get("role") for m in messages if isinstance(m, dict)] != ["system", "user"]:
        return hashes, [f"{arm} must be a fresh system/user context with no prior turns"]
    user_content = messages[1].get("content")
    if not isinstance(user_content, list):
        return hashes, [f"{arm} user content must be a sequence"]
    for index, part in enumerate(user_content):
        if not isinstance(part, dict) or part.get("type") != "image_url":
            continue
        url = part.get("image_url", {}).get("url") if isinstance(part.get("image_url"), dict) else None
        if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
            errors.append(f"{arm} image {index} must be an inline PNG data URL")
            continue
        try:
            image_bytes = base64.b64decode(url.split(",", 1)[1], validate=True)
        except (ValueError, base64.binascii.Error):
            errors.append(f"{arm} image {index} has invalid base64")
            continue
        hashes.append(hashlib.sha256(image_bytes).hexdigest())
    return hashes, errors


def audit_request_pair(
    request_f: Any, request_hf: Any, contract: dict[str, Any]
) -> list[str]:
    """Check frozen F/HF inputs, independent contexts, and shared current k04."""
    issues: list[str] = []
    requests = contract.get("requests") if isinstance(contract, dict) else None
    if not isinstance(requests, list) or [r.get("condition") for r in requests if isinstance(r, dict)] != ["F", "HF"]:
        return ["contract request order must be exactly F then HF"]
    expected_ids = [r.get("input_ids") for r in requests]
    if expected_ids != [["k04"], ["k01", "k02", "k03", "k04"]]:
        issues.append("contract must pin F=k04 and HF=k01,k02,k03,k04")
    if not isinstance(request_f, dict) or not isinstance(request_hf, dict):
        return issues + ["both request files must contain JSON objects"]
    for arm, request in (("F", request_f), ("HF", request_hf)):
        messages = request.get("messages")
        if not isinstance(messages, list) or len(messages) != 2 or [m.get("role") for m in messages if isinstance(m, dict)] != ["system", "user"]:
            issues.append(f"{arm} must contain exactly one fresh system/user context")
        fmt = request.get("response_format", {})
        json_schema = fmt.get("json_schema", {}) if isinstance(fmt, dict) else {}
        if (
            fmt.get("type") != "json_schema"
            or json_schema.get("name") != "roboassem_task6_visual_state"
            or json_schema.get("strict") is not True
        ):
            issues.append(f"{arm} must use the pinned strict JSON Schema response format")
    f_hashes, f_errors = _image_hashes(request_f, "F")
    hf_hashes, hf_errors = _image_hashes(request_hf, "HF")
    issues.extend(f_errors)
    issues.extend(hf_errors)
    frames = _frame_contract(contract)
    for arm, hashes, input_ids in (
        ("F", f_hashes, ["k04"]),
        ("HF", hf_hashes, ["k01", "k02", "k03", "k04"]),
    ):
        expected_hashes = [frames.get(input_id, {}).get("sha256") for input_id in input_ids]
        if hashes != expected_hashes:
            issues.append(f"{arm} image sequence does not match its pinned frame IDs/SHA-256")
    if f_hashes and hf_hashes and f_hashes[-1] != hf_hashes[-1]:
        issues.append("F and HF current k04 image bytes differ")
    f_common = {key: value for key, value in request_f.items() if key not in ("messages", "response_format")}
    hf_common = {key: value for key, value in request_hf.items() if key not in ("messages", "response_format")}
    if f_common != hf_common:
        issues.append("F/HF generation parameters differ")
    return issues


def _arm_statuses() -> dict[str, dict[str, Any]]:
    return {arm: {"status": "not_attempted", "request_id": None} for arm in EXPECTED_ARMS}


def _validate_response_content(arm: str, response_bytes: bytes, contract: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    try:
        response = json.loads(response_bytes.decode("utf-8"))
        choice = response["choices"][0]
        content = json.loads(choice["message"]["content"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        return [f"{arm} raw response is not a complete chat-completion JSON response: {exc}"]
    if choice.get("finish_reason") != "stop":
        issues.append(f"{arm} finish_reason is not stop")
    usage = response.get("usage")
    if not isinstance(usage, dict) or not isinstance(usage.get("prompt_tokens"), int):
        issues.append(f"{arm} response is missing integer usage.prompt_tokens")
    elif usage["prompt_tokens"] + 2048 > 16384:
        issues.append(f"{arm} response prompt plus max output exceeds the pinned context")
    if response.get("truncated", False) is not False:
        issues.append(f"{arm} response indicates truncation")
    schemas = contract.get("content_schemas", {})
    schema = schemas.get(arm) if isinstance(schemas, dict) else None
    try:
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(schema)
        issues.extend(
            f"{arm} content: {error.message}"
            for error in Draft202012Validator(schema).iter_errors(content)
        )
    except ImportError:
        issues.append("jsonschema==4.26.0 is required to validate model response contents")
    except Exception as exc:
        issues.append(f"{arm} output schema could not be evaluated: {exc}")
    relations = content.get("current_relations", []) if isinstance(content, dict) else []
    seats = [relation.get("seat_id") for relation in relations if isinstance(relation, dict)]
    if sorted(seats) != ["blue_seat_left", "blue_seat_right"]:
        issues.append(f"{arm} current relations must contain each seat exactly once")
    return issues


def summarize_v5_run(
    results_dir: Path,
    contract: dict[str, Any],
    stop_attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit the v5 journals, request/response bytes, and schema-checked outputs."""
    results_dir = Path(results_dir)
    errors: list[str] = []
    attempts_path = results_dir / "attempts.jsonl"
    events_path = results_dir / "attempt-events.jsonl"
    failures_path = results_dir / "failures.jsonl"
    attempts = _read_jsonl(attempts_path, errors)
    events = _read_jsonl(events_path, errors)
    failures = _read_jsonl(failures_path, errors)
    parsed_path = results_dir / "results.json"
    parsed_records: list[dict[str, Any]] = []
    if parsed_path.exists():
        try:
            payload = _read_json(parsed_path)
            if isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
                parsed_records = payload
            else:
                errors.append("results.json must be an array of result objects")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"results.json cannot be read: {exc}")

    arms = _arm_statuses()
    sequence = [row.get("condition") for row in attempts]
    if len(attempts) > 2:
        errors.append("attempt journal exceeds the maximum of two requests")
    if sequence != list(EXPECTED_ARMS[: len(sequence)]):
        errors.append("attempt order must be an F/HF prefix; F must precede HF")
    if len(set(sequence)) != len(sequence):
        errors.append("duplicate arm attempts violate zero-retry policy")
    valid_attempt_rows: list[dict[str, Any]] = []
    invalid_attempt_arms: set[str] = set()
    unattributed_attempt_row = False
    for row in attempts:
        arm = row.get("condition")
        if arm not in EXPECTED_ARMS:
            errors.append(f"attempt row contains unknown arm {arm!r}")
            unattributed_attempt_row = True
            continue
        arms[arm]["status"] = "unknown"
        arms[arm]["request_id"] = row.get("request_id")
        if row.get("attempt") != 1 or row.get("state") != "send_intent":
            errors.append(f"{arm} attempt must be its single durable send_intent")
            invalid_attempt_arms.add(arm)
        else:
            valid_attempt_rows.append(row)
        request_path_text = row.get("request_path")
        request_digest = row.get("request_sha256")
        if not isinstance(request_path_text, str) or not isinstance(request_digest, str):
            errors.append(f"{arm} send intent lacks request path or SHA-256")
        else:
            try:
                request_path = safe_artifact_path(results_dir, request_path_text)
                if not request_path.is_file():
                    errors.append(f"{arm} request artifact is missing")
                else:
                    size, digest = sha256_file(request_path)
                    if digest != request_digest:
                        errors.append(f"{arm} request artifact SHA-256 does not match journal")
                    try:
                        json.loads(request_path.read_bytes())
                    except (OSError, json.JSONDecodeError):
                        errors.append(f"{arm} request artifact is not JSON")
            except ValueError as exc:
                errors.append(str(exc))

    failure_by_arm: dict[str, list[dict[str, Any]]] = {arm: [] for arm in EXPECTED_ARMS}
    for failure in failures:
        arm = failure.get("condition")
        if arm in failure_by_arm:
            failure_by_arm[arm].append(failure)
    event_by_arm: dict[str, list[dict[str, Any]]] = {arm: [] for arm in EXPECTED_ARMS}
    for event in events:
        arm = event.get("condition")
        if arm in event_by_arm:
            event_by_arm[arm].append(event)

    response_bytes_by_event: dict[tuple[str, str], bytes] = {}
    for arm in EXPECTED_ARMS:
        for event in event_by_arm[arm]:
            if event.get("state") != "response_received":
                continue
            response_path_text = event.get("response_path")
            if not isinstance(response_path_text, str):
                errors.append(f"{arm} response receipt lacks a path")
                continue
            try:
                response_path = safe_artifact_path(results_dir, response_path_text)
                if not response_path.is_file():
                    errors.append(f"{arm} raw response file is missing")
                    continue
                size, digest = sha256_file(response_path)
                if size != event.get("response_bytes") or digest != event.get("response_sha256"):
                    errors.append(f"{arm} raw response bytes/hash do not match receipt")
                    continue
                response_bytes_by_event[(arm, response_path_text)] = response_path.read_bytes()
            except ValueError as exc:
                errors.append(str(exc))

    result_by_arm: dict[str, dict[str, Any]] = {}
    for record in parsed_records:
        arm = record.get("condition")
        if arm not in EXPECTED_ARMS or arm in result_by_arm:
            errors.append("results.json contains an unknown or duplicate arm")
            continue
        result_by_arm[arm] = record

    orphaned_attempt_state = not valid_attempt_rows and _has_prior_execution_artifacts(results_dir)
    if orphaned_attempt_state:
        errors.append(
            "attempts.jsonl is absent or lacks a valid send-intent for existing execution artifacts; delivery is unknown and replay is blocked"
        )

    valid_attempt_arms = {row["condition"] for row in valid_attempt_rows}
    for arm in EXPECTED_ARMS:
        if arm not in valid_attempt_arms:
            arm_has_artifacts = (
                bool(event_by_arm[arm])
                or bool(failure_by_arm[arm])
                or arm in result_by_arm
                or (results_dir / f"request-{arm}.json").exists()
                or (results_dir / f"response-{arm}-raw.json").exists()
                or (results_dir / f"response-{arm}-http-error-raw.bin").exists()
            )
            if orphaned_attempt_state or arm_has_artifacts or arm in invalid_attempt_arms or unattributed_attempt_row:
                arms[arm]["status"] = "unknown"
                if arm_has_artifacts and not orphaned_attempt_state:
                    errors.append(f"{arm} artifacts exist without a matching valid send-intent; delivery is unknown")
            continue
        fail_states = {row.get("state") for row in failure_by_arm[arm]}
        response_events = [row for row in event_by_arm[arm] if row.get("state") == "response_received"]
        if "response_unknown" in fail_states:
            arms[arm]["status"] = "unknown"
        elif fail_states & {"http_error", "processing_failed"}:
            arms[arm]["status"] = "failed"
        elif not response_events:
            arms[arm]["status"] = "unknown"
        else:
            event = response_events[-1]
            response_path_text = event.get("response_path")
            response_bytes = (
                response_bytes_by_event.get((arm, response_path_text))
                if isinstance(response_path_text, str)
                else None
            )
            record = result_by_arm.get(arm)
            if record is None:
                arms[arm]["status"] = "failed"
                if not failure_by_arm[arm]:
                    errors.append(f"{arm} response was received but has no parsed result or failure record")
            elif response_bytes is not None:
                if record.get("response_sha256") != event.get("response_sha256"):
                    errors.append(f"{arm} parsed result response hash differs from response receipt")
                if record.get("response_bytes") != event.get("response_bytes"):
                    errors.append(f"{arm} parsed result response size differs from response receipt")
                try:
                    raw_response = json.loads(response_bytes.decode("utf-8"))
                    decoded = json.loads(raw_response["choices"][0]["message"]["content"])
                    if decoded != record.get("content"):
                        errors.append(f"{arm} parsed result differs from raw response content")
                except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    errors.append(f"{arm} raw response cannot be reconciled to parsed result: {exc}")
                schema_errors = _validate_response_content(arm, response_bytes, contract)
                errors.extend(schema_errors)
                if record.get("schema_valid") is not True or record.get("schema_errors"):
                    errors.append(f"{arm} result record does not report schema_valid=true")
                if schema_errors:
                    arms[arm]["status"] = "failed"
                else:
                    arms[arm]["status"] = "completed"
                arms[arm]["response_sha256"] = event.get("response_sha256")

    request_files = {}
    for arm in EXPECTED_ARMS:
        request_path = results_dir / f"request-{arm}.json"
        if request_path.is_file():
            try:
                request_files[arm] = _read_json(request_path)
            except (OSError, json.JSONDecodeError):
                errors.append(f"request-{arm}.json cannot be parsed")
    if set(request_files) == set(EXPECTED_ARMS):
        errors.extend(audit_request_pair(request_files["F"], request_files["HF"], contract))

    if attempts and arms["F"]["status"] != "completed" and arms["HF"]["status"] != "not_attempted":
        errors.append("HF must not be sent after F did not complete and validate")
    if len(parsed_records) > 2:
        errors.append("results.json exceeds the two-response request budget")

    local_stop = "unknown"
    stop_file = results_dir / "server-stopped.json"
    if stop_file.is_file():
        try:
            stop_data = _read_json(stop_file)
            if isinstance(stop_data, dict) and isinstance(stop_data.get("returncode"), int):
                local_stop = "verified_stopped"
            else:
                errors.append("server-stopped.json lacks an integer process return code")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"server-stopped.json cannot be read: {exc}")
    provider_stop = "unknown"
    if isinstance(stop_attestation, dict):
        has_reference = isinstance(stop_attestation.get("evidence_reference"), str) and bool(stop_attestation["evidence_reference"].strip())
        timestamp_value = stop_attestation.get("stopped_at_utc")
        has_timestamp = False
        if isinstance(timestamp_value, str) and timestamp_value.strip():
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
                has_timestamp = (
                    parsed_timestamp.tzinfo is not None
                    and parsed_timestamp.utcoffset() == timezone.utc.utcoffset(parsed_timestamp)
                )
            except ValueError:
                pass
            if not has_timestamp:
                errors.append("stopped_at_utc must be a valid ISO-8601 UTC timestamp")
        has_instance = isinstance(stop_attestation.get("instance_reference"), str) and bool(stop_attestation["instance_reference"].strip())
        if (
            stop_attestation.get("status") == "stopped"
            and stop_attestation.get("resource_type") == "cloud-vm"
            and stop_attestation.get("provider_id")
            and has_instance
            and has_reference
            and has_timestamp
        ):
            provider_stop = "verified_stopped"
        elif (
            stop_attestation.get("status") == "not_applicable"
            and stop_attestation.get("resource_type") == "self-managed-host"
            and stop_attestation.get("provider_id") == "self-managed-local"
            and has_reference
        ):
            provider_stop = "not_applicable"
        else:
            errors.append("provider stop evidence needs status, resource_type, provider_id, instance_reference, stopped_at_utc, and evidence_reference")

    if orphaned_attempt_state or unattributed_attempt_row:
        inference_status = "unknown"
    elif not attempts and (results_dir / "execution-error.json").exists():
        inference_status = "failed"
    elif not attempts:
        inference_status = "not_started"
    elif any(arms[arm]["status"] == "unknown" for arm in EXPECTED_ARMS):
        inference_status = "unknown"
    elif any(arms[arm]["status"] == "failed" for arm in EXPECTED_ARMS):
        inference_status = "failed"
    elif all(arms[arm]["status"] == "completed" for arm in EXPECTED_ARMS):
        inference_status = "success"
    else:
        inference_status = "failed"

    return {
        "schema_version": "steptrace.task6.v5-run-audit.v1",
        "synthetic": False,
        "valid": not errors,
        "errors": errors,
        "inference_status": inference_status,
        "gpu_execution_status": "not_verified",
        "arms": arms,
        "request_count": len(valid_attempt_rows),
        "replay_blocked": attempts_path.exists() or orphaned_attempt_state or _has_prior_execution_artifacts(results_dir),
        "resource_cleanup": {"local_server": local_stop, "provider_session": provider_stop},
    }


def _file_check(path: Path, expected_bytes: int, expected_sha256: str) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        return "missing", {"path": path.name, "expected_bytes": expected_bytes, "expected_sha256": expected_sha256}
    size, digest = sha256_file(path)
    status = "verified" if size == expected_bytes and digest == expected_sha256 else "mismatch"
    return status, {"path": path.name, "bytes": size, "sha256": digest, "expected_bytes": expected_bytes, "expected_sha256": expected_sha256}


def run_preflight(
    *,
    manifest_path: Path,
    input_root: Path,
    run_root: Path,
    rights_path: Path,
    provider_id: str,
    phase: str,
    require_gpu: bool = False,
) -> dict[str, Any]:
    """Read-only local gate; it never invokes network, GPU build, server, or model."""
    manifest = _read_json(manifest_path)
    contract_path = input_root / "execution-input-contract.json"
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []

    if not contract_path.is_file():
        blockers.append("execution-input-contract.json is missing")
        contract = {}
        contract_digest = ""
    else:
        contract_size, contract_digest = sha256_file(contract_path)
        expected_contract = manifest["input_contract"]
        contract = _read_json(contract_path)
        status = "verified" if contract_size == expected_contract["bytes"] and contract_digest == expected_contract["sha256"] else "mismatch"
        checks.append({"name": "input_contract", "status": status, "bytes": contract_size, "sha256": contract_digest})
        if status != "verified":
            blockers.append("execution input contract size/SHA-256 mismatch")

    frame_specs = manifest["inputs"]["frames"]
    for frame in frame_specs:
        status, detail = _file_check(input_root / frame["filename"], frame["bytes"], frame["sha256"])
        checks.append({"name": f"frame_{frame['input_id']}", "status": status, **detail})
        if status != "verified":
            blockers.append(f"{frame['input_id']} private PNG is {status}")
    source = manifest["inputs"]["source_video"]
    status, detail = _file_check(input_root / source["filename"], source["bytes"], source["sha256"])
    checks.append({"name": "source_video_provenance", "status": status, **detail})
    if status != "verified":
        blockers.append(f"private source video provenance file is {status}")

    rights_issues: list[str]
    rights_identity: dict[str, Any] = {}
    if not rights_path.is_file():
        rights_issues = ["private rights attestation file is missing"]
    else:
        try:
            attestation_size, attestation_sha256 = sha256_file(rights_path)
            rights_identity = {
                "attestation_file": rights_path.name,
                "attestation_bytes": attestation_size,
                "attestation_sha256": attestation_sha256,
            }
            attestation = _read_json(rights_path)
            rights_issues = validate_rights_attestation(
                attestation,
                experiment_id=manifest["experiment_id"],
                input_contract_sha256=manifest["input_contract"]["sha256"],
                provider_id=provider_id,
            )
            evidence_file = attestation.get("evidence_file") if isinstance(attestation, dict) else None
            if isinstance(evidence_file, str) and evidence_file:
                try:
                    evidence_path = safe_artifact_path(input_root, evidence_file)
                    if not evidence_path.is_file():
                        rights_issues.append("private evidence_file is missing from input root")
                    else:
                        evidence_size, evidence_sha256 = sha256_file(evidence_path)
                        if (
                            evidence_size != attestation.get("evidence_bytes")
                            or evidence_sha256 != attestation.get("evidence_sha256")
                        ):
                            rights_issues.append("private evidence_file size/SHA-256 mismatch with attestation")
                        rights_identity.update(
                            {
                                "evidence_file": evidence_file,
                                "evidence_bytes": evidence_size,
                                "evidence_sha256": evidence_sha256,
                            }
                        )
                except ValueError as exc:
                    rights_issues.append(str(exc))
        except (OSError, json.JSONDecodeError) as exc:
            rights_issues = [f"private rights attestation is invalid JSON: {exc}"]
    checks.append(
        {
            "name": "data_rights",
            "status": "verified" if not rights_issues else "blocked",
            "issues": rights_issues,
            **rights_identity,
        }
    )
    blockers.extend(rights_issues)

    if phase == "execution":
        for artifact in manifest["model"]["files"]:
            status, detail = _file_check(run_root / artifact["filename"], artifact["bytes"], artifact["sha256"])
            checks.append({"name": f"model_{artifact['filename']}", "status": status, **detail})
            if status != "verified":
                blockers.append(f"model artifact {artifact['filename']} is {status}; do not redownload through runner")

    runner_root = PACKAGE_ROOT / "runner_sources"
    for artifact in manifest["runner_files"]:
        source_path = runner_root / artifact["filename"]
        status, _detail = _file_check(source_path, artifact["bytes"], artifact["sha256"])
        checks.append({"name": f"pinned_source_{artifact['filename']}", "status": status})
        if status != "verified":
            blockers.append(f"pinned v5 source {artifact['filename']} is {status}")
        local_copy = input_root / artifact["filename"]
        copy_status, _copy_detail = _file_check(local_copy, artifact["bytes"], artifact["sha256"])
        checks.append({"name": f"private_input_copy_{artifact['filename']}", "status": copy_status})
        if copy_status != "verified":
            blockers.append(f"private input copy of v5 source {artifact['filename']} is {copy_status}")

    results_root = run_root / "results"
    prior_run_artifacts = _has_prior_execution_artifacts(results_root)
    if prior_run_artifacts:
        blockers.append("prior result artifacts exist or cannot be inspected; the one-shot run may not be replayed")
    checks.append({"name": "one_shot_root", "status": "blocked" if prior_run_artifacts else "clear"})

    if phase == "execution":
        runtime_path = run_root / "runtime-verification.json"
        if not runtime_path.is_file():
            blockers.append("runtime-verification.json is missing")
            checks.append({"name": "backend_runtime", "status": "missing"})
        else:
            try:
                runtime = _read_json(runtime_path)
                runtime_issues = validate_runtime_record(runtime, manifest["environment"])
                if runtime.get("binary_path") != "llama.cpp/build/bin/llama-server":
                    runtime_issues.append("binary_path does not match the pinned run-root layout")
                server_path = run_root / "llama.cpp/build/bin/llama-server"
                server_status, server_info = _file_check(
                    server_path, runtime.get("binary_bytes", -1), runtime.get("binary_sha256", "")
                )
                if server_status != "verified":
                    runtime_issues.append("llama-server bytes do not match runtime-verification.json")
                checks.append({"name": "backend_runtime", "status": "verified" if not runtime_issues else "mismatch", "server": server_info, "issues": runtime_issues})
                blockers.extend(runtime_issues)
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                blockers.append(f"runtime-verification.json is invalid: {exc}")
                checks.append({"name": "backend_runtime", "status": "invalid"})
        if require_gpu:
            try:
                output = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    text=True,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                ).strip()
                if not output:
                    raise ValueError("nvidia-smi returned no GPUs")
                checks.append({"name": "gpu_visible", "status": "observed", "device_count": len(output.splitlines())})
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                blockers.append(f"required NVIDIA GPU could not be verified: {exc}")
                checks.append({"name": "gpu_visible", "status": "missing"})

    input_ready = not any(
        check.get("status") in ("missing", "mismatch", "blocked")
        for check in checks
        if check.get("name") != "backend_runtime" and check.get("name") != "gpu_visible"
    )
    ready = phase == "execution" and not blockers
    return {
        "schema_version": "steptrace.task6.cpu-preflight.v1",
        "experiment_id": manifest["experiment_id"],
        "phase": phase,
        "execution_ready": ready,
        "input_ready": input_ready,
        "generation_calls": 0,
        "network_calls": 0,
        "checks": checks,
        "blockers": blockers,
    }


def write_return_files(
    results_dir: Path,
    contract: dict[str, Any],
    return_dir: Path,
    stop_attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = summarize_v5_run(results_dir, contract, stop_attestation)
    files = []
    if results_dir.exists():
        for path in sorted(results_dir.rglob("*")):
            if path.is_symlink():
                report["errors"].append(f"symlink is prohibited in result archive: {path.name}")
                report["valid"] = False
                continue
            if path.is_file():
                size, digest = sha256_file(path)
                files.append({"path": path.relative_to(results_dir).as_posix(), "bytes": size, "sha256": digest, "classification": "private"})
    private_manifest = {
        "schema_version": "steptrace.task6.private-return-manifest.v1",
        "experiment_id": EXPERIMENT_ID,
        "synthetic": False,
        "classification": "private",
        "audit": report,
        "private_artifacts": files,
    }
    public_summary = {
        "schema_version": "steptrace.task6.redacted-summary.v1",
        "experiment_id": EXPERIMENT_ID,
        "synthetic": False,
        "publication_status": "held_for_owner_review",
        "publication_allowed": False,
        "inference_status": report["inference_status"],
        "gpu_execution_status": report["gpu_execution_status"],
        "arm_status": {arm: report["arms"][arm]["status"] for arm in EXPECTED_ARMS},
        "request_count": report["request_count"],
        "provider_session_status": report["resource_cleanup"]["provider_session"],
        "contains_model_output": False,
        "contains_private_paths": False,
        "contains_private_artifact_hashes": False,
    }
    return_dir.mkdir(parents=True, exist_ok=True)
    (return_dir / "private-return-manifest.json").write_text(
        json.dumps(private_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (return_dir / "redacted-summary.json").write_text(
        json.dumps(public_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"audit": report, "private_manifest": str(return_dir / "private-return-manifest.json"), "redacted_summary": str(return_dir / "redacted-summary.json")}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight_parser = subparsers.add_parser("preflight", help="check local inputs and gates; zero model/network calls")
    preflight_parser.add_argument("--manifest", type=Path, default=PACKAGE_ROOT / "experiment-manifest.json")
    preflight_parser.add_argument("--input-root", type=Path, required=True)
    preflight_parser.add_argument("--run-root", type=Path, required=True)
    preflight_parser.add_argument("--rights", type=Path, required=True)
    preflight_parser.add_argument("--provider-id", required=True)
    preflight_parser.add_argument("--phase", choices=("inputs", "execution"), default="inputs")
    preflight_parser.add_argument("--require-gpu", action="store_true")
    preflight_parser.add_argument("--report", type=Path)

    validate_parser = subparsers.add_parser("validate-results", help="audit a private v5 results directory")
    validate_parser.add_argument("--results-dir", type=Path, required=True)
    validate_parser.add_argument("--contract", type=Path, default=PACKAGE_ROOT / "execution-input-contract.json")
    validate_parser.add_argument("--stop-attestation", type=Path)

    return_parser = subparsers.add_parser("write-return", help="write private and redacted return manifests")
    return_parser.add_argument("--results-dir", type=Path, required=True)
    return_parser.add_argument("--contract", type=Path, default=PACKAGE_ROOT / "execution-input-contract.json")
    return_parser.add_argument("--return-dir", type=Path, required=True)
    return_parser.add_argument("--stop-attestation", type=Path)

    runtime_parser = subparsers.add_parser("record-runtime", help="record the pinned server binary and visible CUDA0; no model load")
    runtime_parser.add_argument("--run-root", type=Path, required=True)

    gpu_preflight_parser = subparsers.add_parser(
        "gpu-preflight", help="start the pinned local server, check props/token bounds, stop without generation"
    )
    gpu_preflight_parser.add_argument("--manifest", type=Path, default=PACKAGE_ROOT / "experiment-manifest.json")
    gpu_preflight_parser.add_argument("--contract", type=Path, default=PACKAGE_ROOT / "execution-input-contract.json")
    gpu_preflight_parser.add_argument("--input-root", type=Path, required=True)
    gpu_preflight_parser.add_argument("--run-root", type=Path, required=True)
    gpu_preflight_parser.add_argument("--rights", type=Path, required=True)
    gpu_preflight_parser.add_argument("--provider-id", required=True)
    gpu_preflight_parser.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "record-runtime":
        runtime = record_runtime(args.run_root)
        print(json.dumps(runtime, ensure_ascii=False, indent=2))
        return 0
    if args.command == "gpu-preflight":
        report = run_gpu_preflight(
            manifest_path=args.manifest,
            contract_path=args.contract,
            input_root=args.input_root,
            run_root=args.run_root,
            rights_path=args.rights,
            provider_id=args.provider_id,
            output_dir=args.output_dir,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["preflight_passed"] else 2
    if args.command == "preflight":
        report = run_preflight(
            manifest_path=args.manifest,
            input_root=args.input_root,
            run_root=args.run_root,
            rights_path=args.rights,
            provider_id=args.provider_id,
            phase=args.phase,
            require_gpu=args.require_gpu,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if report["execution_ready"] else 2
    if args.command in ("validate-results", "write-return"):
        contract = _read_json(args.contract)
        stop_attestation = _read_json(args.stop_attestation) if args.stop_attestation else None
        if args.command == "validate-results":
            report = summarize_v5_run(args.results_dir, contract, stop_attestation)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 2
        output = write_return_files(args.results_dir, contract, args.return_dir, stop_attestation)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output["audit"]["valid"] else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
