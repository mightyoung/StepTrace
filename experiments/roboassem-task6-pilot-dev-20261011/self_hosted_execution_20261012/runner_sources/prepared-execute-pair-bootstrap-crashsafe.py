"""Materialize and run the pinned prepared sender from the private input mount.

This is a future Kaggle cell source. Unit tests call only ``prepare_runner``;
they never call ``main`` or execute a sender.
"""

from __future__ import annotations

import hashlib
import fcntl
import fnmatch
import importlib.util
import os
from pathlib import Path
import runpy
import sys
import uuid


DEFAULT_INPUT_ROOT = Path(
    "/private/task6-inputs"
)
DEFAULT_WORKING_ROOT = Path("/private/task6-run")
PREPARED_RUNNER_NAME = "prepared-run_pair-crashsafe.py"
SAFETY_MODULE_NAME = "runner_safety.py"

PREPARED_RUNNER_SHA256 = "d6b2e3bd32e1f8fe1652f526586ca60c506053a95e62ff4b7211c5d8602b82b3"
SAFETY_MODULE_SHA256 = "b05ebde87a05f1bf1873da6d9a8c7e45d37d51e9a5e2d97e8a6155e7c9bf4e42"

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


def _has_prior_execution_artifacts(output_dir: str | Path) -> bool:
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
            if child.name == "setup-evidence":
                if child.is_symlink() or not child.is_dir():
                    return True
                for evidence in child.rglob("*"):
                    if evidence.is_symlink() or _is_run_evidence_name(evidence.name):
                        return True
                continue
            return True
    except OSError:
        return True
    return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class BootstrapRunGuard:
    """Hold the shared one-shot lock while code is copied and execution starts."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.lock_path = self.output_dir / "runner.lock"
        self.attempts_path = self.output_dir / "attempts.jsonl"
        self._fd: int | None = None

    @property
    def acquired(self) -> bool:
        return self._fd is not None

    def acquire(self) -> "BootstrapRunGuard":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError(
                "another Task 6 runner is active; no source replacement or send was started"
            ) from exc
        self._fd = fd
        try:
            if _has_prior_execution_artifacts(self.output_dir):
                raise RuntimeError(
                    "prior execution artifacts exist; no retry or bootstrap allowed"
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


def _atomic_durable_write(destination: Path, data: bytes) -> None:
    """Durably write the exact byte buffer already checked by the caller."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while preparing Task 6 runner")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)


def prepare_runner(
    *,
    input_root: str | Path,
    working_root: str | Path,
    expected_runner_sha256: str,
    expected_safety_sha256: str,
    guard: BootstrapRunGuard | None = None,
) -> Path:
    """Verify and copy prepared code; this function does not execute it.

    A standalone call owns the shared results lock only for verification, copy,
    and readback. ``main`` passes an already-held guard so the same lock spans
    that boundary and runner startup without a gap.
    """
    package = Path(input_root).resolve()
    working = Path(working_root).resolve()
    working.mkdir(parents=True, exist_ok=True)
    results = working / "results"
    results.mkdir(parents=True, exist_ok=True)
    active_guard = guard or BootstrapRunGuard(results)
    owns_guard = guard is None
    if owns_guard:
        active_guard.acquire()
    elif (
        not active_guard.acquired
        or active_guard.output_dir.resolve() != results.resolve()
    ):
        raise ValueError("an acquired bootstrap run guard for this output directory is required")
    try:
        runner_source = package / PREPARED_RUNNER_NAME
        safety_source = package / SAFETY_MODULE_NAME
        runner_bytes = runner_source.read_bytes()
        safety_bytes = safety_source.read_bytes()
        if _sha256(runner_bytes) != expected_runner_sha256:
            raise ValueError("prepared runner SHA256 does not match the pinned source")
        if _sha256(safety_bytes) != expected_safety_sha256:
            raise ValueError("runner safety module SHA256 does not match the pinned source")

        runner_destination = working / PREPARED_RUNNER_NAME
        safety_destination = working / SAFETY_MODULE_NAME
        _atomic_durable_write(runner_destination, runner_bytes)
        _atomic_durable_write(safety_destination, safety_bytes)
        if _sha256(runner_destination.read_bytes()) != expected_runner_sha256:
            raise ValueError("copied runner SHA256 does not match the pinned source")
        if _sha256(safety_destination.read_bytes()) != expected_safety_sha256:
            raise ValueError("copied runner safety module SHA256 does not match the pinned source")
        return runner_destination
    finally:
        if owns_guard:
            active_guard.release()


def main() -> None:
    """Hold the results lock continuously from code copy through runner start."""
    package = Path(os.environ.get("TASK6_INPUT_ROOT", DEFAULT_INPUT_ROOT)).resolve()
    working = Path(os.environ.get("TASK6_RUN_ROOT", DEFAULT_WORKING_ROOT)).resolve()
    guard = BootstrapRunGuard(working / "results").acquire()
    safety_name = "runner_safety"
    try:
        runner = prepare_runner(
            input_root=package,
            working_root=working,
            expected_runner_sha256=PREPARED_RUNNER_SHA256,
            expected_safety_sha256=SAFETY_MODULE_SHA256,
            guard=guard,
        )
        # Load exactly the bytes just checked and copied, replacing any stale
        # module already present in this Python process.
        safety_path = working / SAFETY_MODULE_NAME
        spec = importlib.util.spec_from_file_location(safety_name, safety_path)
        if spec is None or spec.loader is None:
            raise ImportError("could not load the pinned runner safety module")
        safety_module = importlib.util.module_from_spec(spec)
        sys.modules[safety_name] = safety_module
        try:
            spec.loader.exec_module(safety_module)
        except BaseException:
            sys.modules.pop(safety_name, None)
            raise
        safety_module.set_bootstrap_guard(guard)

        os.environ["TASK6_INPUT_ROOT"] = str(package)
        os.environ["TASK6_RUN_ROOT"] = str(working)
        sys.path.insert(0, str(working))
        runpy.run_path(str(runner), run_name="__main__")
    finally:
        loaded_safety = sys.modules.get(safety_name)
        if loaded_safety is not None and hasattr(loaded_safety, "clear_bootstrap_guard"):
            loaded_safety.clear_bootstrap_guard(guard)
        guard.release()


if __name__ == "__main__":
    main()
