"""Crash-safe, one-shot send journal for the prepared Task 6 runner.

This module only coordinates local files. Network access is supplied by the
caller so tests can use a synthetic sender and production code stays bound to
the runner's existing localhost llama-server.
"""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
import urllib.error
import uuid
from typing import Any, Callable, Mapping, Sequence


DEFAULT_INPUT_ROOT = Path(
    "/private/task6-inputs"
)
REQUEST_TIMEOUT_SECONDS = 60
CANCELLATION_RESERVE_SECONDS = 0.05
EXPECTED_ARMS = ("F", "HF")
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
_PRE_RUN_EVIDENCE_DIR = "setup-evidence"


def has_prior_execution_artifacts(output_dir: str | Path) -> bool:
    """Fail closed on run artifacts while allowing staged preflight evidence."""
    output = Path(output_dir)
    if output.is_symlink():
        return True
    if not output.exists():
        return False
    if not output.is_dir():
        return True
    try:
        for child in output.iterdir():
            if child.name == "runner.lock":
                continue
            if child.name == _PRE_RUN_EVIDENCE_DIR:
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


def _is_complete_gpu_preflight_server_log(path: Path, setup_evidence_dir: Path) -> bool:
    preflight_dir = setup_evidence_dir / "gpu-preflight"
    return (
        path == preflight_dir / "server.log"
        and (preflight_dir / "gpu-preflight.json").is_file()
        and (preflight_dir / "server-stopped.json").is_file()
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


class RunnerBusy(RuntimeError):
    """Another process currently owns the one-shot runner lock."""


class PriorAttemptExists(RuntimeError):
    """A durable send-intent exists; do not replay it after restart."""


class SenderDeadlineExceeded(TimeoutError):
    """The sender callback did not return before its wall-clock deadline."""

    def __init__(
        self,
        message: str,
        *,
        sender_terminated: bool,
        sender_termination_status: str,
        cancellation_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.sender_terminated = sender_terminated
        self.sender_termination_status = sender_termination_status
        self.cancellation_error = cancellation_error


class SenderCancellationResult:
    """Result of cancellation, separating a request from confirmed reaping."""

    def __init__(self, status: str, *, confirmed: bool = False) -> None:
        self.status = status
        self.confirmed = confirmed


def sender_termination_confirmed(*, kill_requested: bool, reaped: bool) -> bool:
    """Return true only when this sender requested a kill and observed reaping."""
    return bool(kill_requested and reaped)


def _cancellation_reserve_seconds(timeout_seconds: float) -> float:
    return min(CANCELLATION_RESERVE_SECONDS, max(0.0, timeout_seconds * 0.1))


def _send_with_wall_clock_deadline(
    send_request: Callable[..., bytes],
    raw_request: bytes,
    *,
    timeout_seconds: float,
    deadline_monotonic: float,
) -> bytes:
    """Enforce the wall deadline and cancel the transport when it expires.

    The callback must expose ``cancel()`` if it owns a cancellable transport.
    Production uses a child process and kills it here. Delivery can still be
    unknown because the local server may have accepted the request already.
    """

    begin = getattr(send_request, "begin", None)
    if begin is not None:
        begin(deadline_monotonic=deadline_monotonic)
    cancel = getattr(send_request, "cancel", None)

    def cancel_sender() -> tuple[bool, str, str | None]:
        if cancel is None:
            return False, "cancel_unavailable", None
        try:
            result = cancel()
            if isinstance(result, SenderCancellationResult):
                return result.confirmed and result.status == "terminated", result.status, None
            if result is True:
                return True, "terminated", None
            if result is False:
                return False, "pending", None
            return False, "unconfirmed", None
        except BaseException as exc:
            return False, "cancel_error", repr(exc)

    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        terminated, status, error = cancel_sender()
        raise SenderDeadlineExceeded(
            "sender exceeded its wall-clock deadline",
            sender_terminated=terminated,
            sender_termination_status=status,
            cancellation_error=error,
        )

    completed: queue.Queue[tuple[float, str, Any]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            value = send_request(raw_request, timeout_seconds=timeout_seconds)
            outcome: tuple[str, Any] = ("return", value)
        except BaseException as exc:
            outcome = ("raise", exc)
        completed.put((time.monotonic(), outcome[0], outcome[1]))

    sender_thread = threading.Thread(
        target=invoke,
        name="task6-one-shot-sender",
        daemon=True,
    )
    sender_thread.start()
    reserve = _cancellation_reserve_seconds(timeout_seconds) if cancel is not None else 0.0
    response_deadline = deadline_monotonic - reserve
    response_wait = max(0.0, response_deadline - time.monotonic())
    try:
        completed_at, outcome_kind, outcome_value = completed.get(timeout=response_wait)
    except queue.Empty as exc:
        terminated, status, error = cancel_sender()
        if status == "completed":
            remaining = deadline_monotonic - time.monotonic()
            if remaining > 0:
                try:
                    completed_at, outcome_kind, outcome_value = completed.get(timeout=remaining)
                except queue.Empty:
                    pass
                else:
                    if completed_at <= deadline_monotonic:
                        if outcome_kind == "raise":
                            raise outcome_value
                        return outcome_value
                    status = "completed_after_deadline"
        raise SenderDeadlineExceeded(
            "sender exceeded its wall-clock deadline; delivery is unknown",
            sender_terminated=terminated,
            sender_termination_status=status,
            cancellation_error=error,
        ) from exc

    if completed_at > deadline_monotonic:
        raise SenderDeadlineExceeded(
            "sender returned after its wall-clock deadline; delivery is unknown",
            sender_terminated=False,
            sender_termination_status="completed_after_deadline",
        )
    if outcome_kind == "raise":
        raise outcome_value
    return outcome_value


def resolve_input_root(
    *, script_path: str | Path, env: Mapping[str, str] | None = None
) -> Path:
    """Resolve the private Kaggle input mount independently from the script copy."""
    values = os.environ if env is None else env
    configured = values.get("TASK6_INPUT_ROOT")
    root = Path(configured).expanduser() if configured else DEFAULT_INPUT_ROOT
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Task 6 private input root is missing: {root}")
    return root


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while persisting Task 6 run artifact")
        view = view[written:]


def write_durable_new(path: str | Path, data: bytes) -> None:
    """Create an artifact once and make its bytes and directory entry durable."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(target.parent)


def append_durable_jsonl(
    path: str | Path, row: Mapping[str, Any], *, create: bool = False
) -> None:
    """Append one JSON record and fsync it before the caller advances state."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if create:
        flags |= os.O_EXCL
    existed = target.exists()
    fd = os.open(target, flags, 0o600)
    try:
        _write_all(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    if not existed:
        _fsync_directory(target.parent)


class OneShotRunGuard:
    """Serialize runners and permanently deny replay after a send intent."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.lock_path = self.output_dir / "runner.lock"
        self.attempts_path = self.output_dir / "attempts.jsonl"
        self._fd: int | None = None

    @property
    def acquired(self) -> bool:
        return self._fd is not None

    def acquire(self) -> "OneShotRunGuard":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RunnerBusy("another Task 6 runner is active; no send was started") from exc
        self._fd = fd
        try:
            if has_prior_execution_artifacts(self.output_dir):
                raise PriorAttemptExists(
                    "prior execution artifacts exist; delivery may be unknown, do not retry"
                )
            _fsync_directory(self.output_dir)
            return self
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "OneShotRunGuard":
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


_BOOTSTRAP_GUARD: Any | None = None


def set_bootstrap_guard(guard: Any) -> None:
    """Hand the bootstrap-held run lock to the runner without a lock gap."""
    global _BOOTSTRAP_GUARD
    if not guard.acquired:
        raise ValueError("bootstrap run guard must already be acquired")
    _BOOTSTRAP_GUARD = guard


def clear_bootstrap_guard(guard: Any) -> None:
    """Clear an unconsumed bootstrap guard after a failed startup."""
    global _BOOTSTRAP_GUARD
    if _BOOTSTRAP_GUARD is guard:
        _BOOTSTRAP_GUARD = None


def acquire_run_guard(output_dir: str | Path) -> OneShotRunGuard:
    """Consume the bootstrap guard or acquire the normal one-shot run lock."""
    global _BOOTSTRAP_GUARD
    output = Path(output_dir).resolve()
    if _BOOTSTRAP_GUARD is not None:
        guard = _BOOTSTRAP_GUARD
        if not guard.acquired or guard.output_dir.resolve() != output:
            raise ValueError("bootstrap run guard targets a different output directory")
        _BOOTSTRAP_GUARD = None
        return guard
    return OneShotRunGuard(output).acquire()


def _append_event(output_dir: Path, event: Mapping[str, Any]) -> None:
    path = output_dir / "attempt-events.jsonl"
    append_durable_jsonl(path, event, create=not path.exists())


def _append_failure(output_dir: Path, event: Mapping[str, Any]) -> None:
    path = output_dir / "failures.jsonl"
    append_durable_jsonl(path, event, create=not path.exists())


def _append_attempt(output_dir: Path, event: Mapping[str, Any]) -> None:
    path = output_dir / "attempts.jsonl"
    append_durable_jsonl(path, event, create=not path.exists())


def execute_pair(
    requests: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    output_dir: str | Path,
    send_request: Callable[..., bytes],
    parse_response: Callable[[str, bytes, Mapping[str, Any]], Any],
    request_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    guard: OneShotRunGuard | None = None,
) -> list[Any]:
    """Send F then HF once each, durably recording intent before each send.

    A send exception is recorded as unknown delivery and is never retried. A
    prior intent file blocks every later invocation, including after a crash.
    """
    arms = tuple(arm for arm, _body in requests)
    if arms != EXPECTED_ARMS:
        raise ValueError("Task 6 request order must be exactly F then HF")
    if (
        len(requests) != 2
        or requests[0][1] is requests[1][1]
        or requests[0][1].get("messages") is requests[1][1].get("messages")
    ):
        raise ValueError("F and HF require two independent request contexts")

    output = Path(output_dir)
    owned_guard = guard is None
    active_guard = guard or OneShotRunGuard(output)
    if owned_guard:
        active_guard.acquire()
    elif not active_guard.acquired or active_guard.output_dir.resolve() != output.resolve():
        raise ValueError("an acquired one-shot run guard for this output directory is required")

    results: list[Any] = []
    seen_ids: set[str] = set()
    try:
        for arm, body in requests:
            raw_request = json.dumps(
                dict(body), ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            request_path = output / f"request-{arm}.json"
            write_durable_new(request_path, raw_request)
            request_id = request_id_factory()
            if not isinstance(request_id, str) or not request_id or request_id in seen_ids:
                raise ValueError("request_id_factory must return unique non-empty strings")
            seen_ids.add(request_id)
            request_hash = hashlib.sha256(raw_request).hexdigest()
            timeout_seconds = REQUEST_TIMEOUT_SECONDS
            started = time.time()
            deadline_monotonic = time.monotonic() + timeout_seconds
            cancellation_reserve = _cancellation_reserve_seconds(timeout_seconds)
            attempt = {
                "condition": arm,
                "attempt": 1,
                "request_id": request_id,
                "request_sha256": request_hash,
                "request_path": request_path.name,
                "state": "send_intent",
                "started_at": started,
                "start_epoch": started,
                "timeout_seconds": timeout_seconds,
                "timeout_mode": "wall_clock",
                "timeout_scope": "per_request_from_send_intent_timestamp",
                "send_deadline_epoch": started + timeout_seconds,
                "cancellation_reserve_seconds": cancellation_reserve,
                "sender_cancel_start_epoch": started + timeout_seconds - cancellation_reserve,
            }
            # This fsync must complete before the HTTP call. If it does not,
            # the request is never sent. If a crash follows, the file blocks replay.
            _append_attempt(output, attempt)
            _append_event(output, dict(attempt))

            send_started = time.monotonic()
            try:
                raw_response = _send_with_wall_clock_deadline(
                    send_request,
                    raw_request,
                    timeout_seconds=timeout_seconds,
                    deadline_monotonic=deadline_monotonic,
                )
            except urllib.error.HTTPError as exc:
                try:
                    error_body = exc.read()
                except Exception:
                    error_body = b""
                error_path = output / f"response-{arm}-http-error-raw.bin"
                write_durable_new(error_path, error_body)
                body_hash = hashlib.sha256(error_body).hexdigest()
                failure = {
                    "condition": arm,
                    "attempt": 1,
                    "request_id": request_id,
                    "state": "http_error",
                    "http_status": exc.code,
                    "response_path": error_path.name,
                    "response_bytes": len(error_body),
                    "response_sha256": body_hash,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "recorded_at": time.time(),
                }
                _append_event(output, failure)
                _append_failure(output, failure)
                raise
            except Exception as exc:
                failure = {
                    "condition": arm,
                    "attempt": 1,
                    "request_id": request_id,
                    "state": "response_unknown",
                    "error_type": type(exc).__name__,
                    "error": repr(exc),
                    "recorded_at": time.time(),
                    "timeout_seconds": timeout_seconds,
                    "timeout_mode": "wall_clock",
                    "timeout_scope": "per_request_from_send_intent_timestamp",
                    "send_deadline_epoch": attempt["send_deadline_epoch"],
                    "elapsed_seconds": time.monotonic() - send_started,
                    "deadline_exceeded": isinstance(exc, SenderDeadlineExceeded),
                    "delivery_unknown": True,
                    "sender_terminated": getattr(exc, "sender_terminated", False),
                }
                if isinstance(exc, SenderDeadlineExceeded):
                    failure["sender_termination_status"] = exc.sender_termination_status
                cancellation_error = getattr(exc, "cancellation_error", None)
                if cancellation_error is not None:
                    failure["sender_cancellation_error"] = cancellation_error
                _append_event(output, failure)
                _append_failure(output, failure)
                raise

            if not isinstance(raw_response, bytes):
                failure = {
                    "condition": arm,
                    "attempt": 1,
                    "request_id": request_id,
                    "state": "response_unknown",
                    "error_type": "TypeError",
                    "error": "send_request must return raw response bytes",
                    "recorded_at": time.time(),
                }
                _append_event(output, failure)
                _append_failure(output, failure)
                raise TypeError(failure["error"])

            response_path = output / f"response-{arm}-raw.json"
            write_durable_new(response_path, raw_response)
            response_event = {
                "condition": arm,
                "attempt": 1,
                "request_id": request_id,
                "state": "response_received",
                "response_path": response_path.name,
                "response_bytes": len(raw_response),
                "response_sha256": hashlib.sha256(raw_response).hexdigest(),
                "recorded_at": time.time(),
            }
            _append_event(output, response_event)
            try:
                outcome_metadata = dict(attempt)
                outcome_metadata["elapsed_seconds"] = time.monotonic() - send_started
                results.append(parse_response(arm, raw_response, outcome_metadata))
            except Exception as exc:
                failure = {
                    "condition": arm,
                    "attempt": 1,
                    "request_id": request_id,
                    "state": "processing_failed",
                    "error_type": type(exc).__name__,
                    "error": repr(exc),
                    "recorded_at": time.time(),
                }
                _append_event(output, failure)
                _append_failure(output, failure)
                raise
        return results
    finally:
        if owned_guard:
            active_guard.release()


def count_send_intents(attempts_path: str | Path) -> int:
    """Count complete durable send-intent records; not a server-side receipt count."""
    path = Path(attempts_path)
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("state") == "send_intent":
            count += 1
    return count
