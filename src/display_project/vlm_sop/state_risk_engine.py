"""Qwen HTTP adapter and fail-closed state/risk estimation engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Callable, Mapping, Sequence
import urllib.error
import urllib.request

from .evidence_window import validate_window
from .state_risk_contract import validate_inference
from .state_risk_knowledge import validate_knowledge
from .state_risk_prompt import METHOD_P, build_state_risk_request


class QwenCallError(RuntimeError):
    """Raised after all bounded HTTP attempts have failed."""


@dataclass(frozen=True, slots=True)
class QwenCallResult:
    parsed: dict[str, object]
    raw_response: dict[str, object]
    attempts: int
    raw_response_sha256: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_object(text: str) -> dict[str, object]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("model response JSON must be an object")
    return value


def _append_jsonl(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")


def _validate_images(image_urls: Sequence[str], frame_refs: object) -> list[str]:
    frames = list(frame_refs)  # type: ignore[arg-type]
    urls = list(image_urls)
    if len(urls) != len(frames):
        raise ValueError("image_urls must contain exactly one image for each frame_ref")
    for index, (url, frame) in enumerate(zip(urls, frames, strict=True)):
        if not isinstance(url, str):
            raise ValueError(f"image_urls[{index}] must be an embedded base64 image")
        match = re.fullmatch(r"data:image/[A-Za-z0-9.+-]+;base64,([A-Za-z0-9+/]*={0,2})", url)
        if match is None:
            raise ValueError(f"image_urls[{index}] must be an embedded base64 image")
        try:
            payload = base64.b64decode(match.group(1), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"image_urls[{index}] contains invalid base64") from exc
        if not payload or hashlib.sha256(payload).hexdigest() != frame["image_sha256"]:
            raise ValueError(f"image_urls[{index}] does not match frame_ref image_sha256")
    return urls


class QwenHTTPAdapter:
    """OpenAI-compatible llama.cpp/Qwen client with bounded retry logging."""

    def __init__(
        self,
        server_url: str,
        *,
        model: str = "Qwen3.5-9B-Q4_K_M",
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.0,
        urlopen: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not server_url.startswith(("http://", "https://")):
            raise ValueError("server_url must be HTTP(S)")
        if timeout_seconds <= 0 or not 1 <= max_attempts <= 5 or retry_backoff_seconds < 0:
            raise ValueError("invalid bounded timeout/retry settings")
        self.server_url = server_url.rstrip("/")
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._urlopen = urlopen or urllib.request.urlopen
        self._clock = clock

    def _endpoint(self) -> str:
        if self.server_url.endswith("/v1/chat/completions"):
            return self.server_url
        return self.server_url + "/v1/chat/completions"

    def infer(self, request: Mapping[str, object], *, ledger_dir: str | Path, request_id: str) -> QwenCallResult:
        if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,240}", request_id) is None:
            raise ValueError("request_id must be a safe opaque identifier")
        ledger = Path(ledger_dir)
        api_payload = {
            "model": self.model,
            "messages": request["messages"],
            "response_format": request["response_format"],
            "temperature": 0.0,
            "seed": 20260907,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        body = json.dumps(api_payload, ensure_ascii=False).encode("utf-8")
        request_bytes = len(body)
        prompt_bytes = len(json.dumps(request["messages"], ensure_ascii=False).encode("utf-8"))
        image_count = sum(
            1
            for message in request["messages"]  # type: ignore[union-attr]
            if isinstance(message, Mapping)
            for item in message.get("content", [])
            if isinstance(item, Mapping) and item.get("type") == "image_url"
        )
        method_id = request.get("method_id")
        raw_directory = ledger / "raw-responses"
        raw_paths = [raw_directory / f"{request_id}-attempt-{attempt}.json" for attempt in range(1, self.max_attempts + 1)]
        if any(path.exists() for path in raw_paths):
            raise ValueError("request_id attempt artifacts already exist")
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = self._clock()
            try:
                http_request = urllib.request.Request(
                    self._endpoint(),
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                response = self._urlopen(http_request, timeout=self.timeout_seconds)
                with response:  # type: ignore[attr-defined]
                    raw_bytes = response.read()  # type: ignore[attr-defined]
                raw_path = raw_paths[attempt - 1]
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                with raw_path.open("xb") as raw_handle:
                    raw_handle.write(raw_bytes)
                raw = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Qwen HTTP response must be an object")
                content = raw["choices"][0]["message"]["content"]  # type: ignore[index]
                parsed = _parse_json_object(str(content))
                digest = hashlib.sha256(raw_bytes).hexdigest()
                usage = raw.get("usage", {})
                if not isinstance(usage, Mapping):
                    usage = {}
                _append_jsonl(ledger / "calls.jsonl", {
                    "request_id": request_id, "attempt": attempt, "status": "success",
                    "started_at": started, "finished_at": self._clock(), "recorded_at": _utc_now(),
                    "raw_response_path": str(raw_path), "raw_response_sha256": digest,
                    "request_bytes": request_bytes,
                    "prompt_bytes": prompt_bytes,
                    "image_count": image_count,
                    "method_id": method_id,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                })
                return QwenCallResult(parsed, raw, attempt, digest)
            except (TimeoutError, urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError, UnicodeError) as exc:
                last_error = exc
                _append_jsonl(ledger / "failures.jsonl", {
                    "request_id": request_id, "attempt": attempt, "status": "failed",
                    "started_at": started, "finished_at": self._clock(), "recorded_at": _utc_now(),
                    "error_type": type(exc).__name__, "error": str(exc),
                    "request_bytes": request_bytes,
                    "prompt_bytes": prompt_bytes,
                    "image_count": image_count,
                    "method_id": method_id,
                })
                if attempt < self.max_attempts and self.retry_backoff_seconds:
                    time.sleep(self.retry_backoff_seconds)
        raise QwenCallError(f"Qwen request {request_id} failed after {self.max_attempts} attempts: {last_error}") from last_error


class StateRiskEngine:
    """Validate input, call Qwen, and update only the estimated-state memory."""

    def __init__(self, knowledge: Mapping[str, object], adapter: object, ledger_dir: str | Path) -> None:
        self.knowledge = validate_knowledge(knowledge)
        self.adapter = adapter
        self.ledger_dir = Path(ledger_dir)

    def infer(
        self,
        window: Mapping[str, object],
        *,
        method_id: str = METHOD_P,
        state_memory: object | None = None,
        image_urls: Sequence[str] = (),
        model_sha256: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, object]:
        normalized_window = validate_window(window)
        if normalized_window["task_id"] != self.knowledge["task_id"]:
            raise ValueError("window task_id must match engine knowledge")
        checked_image_urls = _validate_images(image_urls, normalized_window["frame_refs"])
        actual_request_id = request_id or f"{normalized_window['window_id']}-{method_id}"
        actual_model_sha256 = model_sha256 or normalized_window["hashes"]["model_sha256"]
        if state_memory is not None:
            record_window = getattr(state_memory, "record_window", None)
            if not callable(record_window):
                raise TypeError("state_memory must implement record_window")
            record_window(normalized_window)
        prompt_request = build_state_risk_request(
            self.knowledge, normalized_window, method_id=method_id,
            state_memory=state_memory, image_urls=checked_image_urls,
        )
        try:
            call = self.adapter.infer(prompt_request, ledger_dir=self.ledger_dir, request_id=actual_request_id)
            parsed = call.parsed if isinstance(call, QwenCallResult) else call
            inference = validate_inference(parsed, knowledge=self.knowledge, window=normalized_window)
            if state_memory is not None:
                memory_inference = dict(inference)
                memory_inference["decision_time"] = normalized_window["decision_time"]
                state_memory.append_estimate(
                    memory_inference,
                    model_sha256=actual_model_sha256,
                    request_id=actual_request_id,
                )
        except Exception as exc:
            _append_jsonl(self.ledger_dir / "terminal.jsonl", {
                "request_id": actual_request_id, "window_id": normalized_window["window_id"],
                "method_id": method_id, "status": "failed", "recorded_at": _utc_now(),
                "error_type": type(exc).__name__, "error": str(exc),
            })
            if state_memory is not None:
                record_failure = getattr(state_memory, "record_failure", None)
                if callable(record_failure):
                    record_failure(normalized_window["window_id"], decision_time=normalized_window["decision_time"], reason=str(exc))
            raise
        _append_jsonl(self.ledger_dir / "terminal.jsonl", {
            "request_id": actual_request_id, "window_id": normalized_window["window_id"],
            "method_id": method_id, "status": "success", "recorded_at": _utc_now(),
            "authority": "state_estimation_only",
        })
        return inference
