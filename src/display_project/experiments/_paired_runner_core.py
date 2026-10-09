"""Deterministic full-video paired experiment runner."""

from __future__ import annotations
from dataclasses import asdict, dataclass, field
import hashlib
import inspect
import json
import math
import multiprocessing as mp
import os
from queue import Empty
import re
import tempfile
import time
from pathlib import Path
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
    Sequence,
    TypeGuard,
    runtime_checkable,
)

from display_project.evidence.certificate import AUTHORIZING_EVIDENCE_SOURCES

METHOD_NAMES = (
    "fixed_streak",
    "hmm_or_crf",
    "vlm_only",
    "yolo",
    "grounding_dino",
    "no_calibration_certificate",
    "proposed",
)
LEDGER_INTEGRITY_VERSION = 1

_VLM_AUTHORITY_KEYS = frozenset(
    {
        "authorizations",
        "authorization_events",
        "certificates",
        "certificate",
        "certificate_records",
        "evidence_certificates",
        "state_writes",
        "state_updates",
        "state_transitions",
        "transitions",
        "transition_events",
        "current_state",
        "next_state",
        "state",
        "emitted_advances",
        "valid_advances",
        "advances",
    }
)

# Untrusted evidence fields are audit-only and never runtime authority.
_NON_AUTHORITY_KEYS = frozenset(
    {
        "annotation",
        "annotations",
        "annotation_used",
        "dataset_answer",
        "ground_truth",
        "groundtruth",
        "truth",
        "truth_label",
        "labels",
        "vlm_decision",
        "vlm_state",
        "vlm_step",
        "vlm_transition",
    }
)
_EVENT_EVIDENCE_SOURCES = frozenset({"deterministic", "none", "vlm", "vlm_audit"})
_VLM_EVIDENCE_SOURCES = frozenset({"vlm", "vlm_audit"})
_EMPTY_AUTHORITY_VALUES: tuple[object, ...] = (None, [], {}, (), 0, False, "")
_FORBIDDEN_AUTHORITY_KEY_TOKENS = frozenset(
    {
        "oracle",
        "truth",
        "groundtruth",
        "ground_truth",
        "dataset_answer",
        "dataset_label",
        "oracle_label",
        "oracle_step",
    }
)


def _contains_non_authoritative_evidence(
    value: object,
    *,
    allow_root_vlm_audit: bool = False,
    _root: bool = True,
    _evidence_item: bool = False,
    _reject_root_source: bool = False,
) -> bool:
    """Reject audit-only or unknown evidence hidden in authority payloads."""
    if isinstance(value, Mapping):
        if _evidence_item:
            source = value.get("source")
            if (
                not isinstance(source, str)
                or source.strip().casefold() not in AUTHORIZING_EVIDENCE_SOURCES
            ):
                return True
        for key, item in value.items():
            name = str(key).strip().casefold()
            if (
                any(token in name for token in _FORBIDDEN_AUTHORITY_KEY_TOKENS)
                and item not in _EMPTY_AUTHORITY_VALUES
            ):
                return True
            if _reject_root_source and name == "source":
                if (
                    not isinstance(item, str)
                    or item.strip().casefold() not in AUTHORIZING_EVIDENCE_SOURCES
                ):
                    return True
            if name in _NON_AUTHORITY_KEYS and item not in _EMPTY_AUTHORITY_VALUES:
                return True
            if name == "evidence_source":
                if not isinstance(item, str):
                    return True
                source = item.strip().casefold()
                root_vlm_audit = (
                    _root and allow_root_vlm_audit and source in _VLM_EVIDENCE_SOURCES
                )
                if source not in _EVENT_EVIDENCE_SOURCES or (
                    source in _VLM_EVIDENCE_SOURCES and not root_vlm_audit
                ):
                    return True
            if _contains_non_authoritative_evidence(
                item,
                _root=False,
                _evidence_item=name == "evidence",
                _reject_root_source=_reject_root_source and name != "graph",
            ):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(
            _contains_non_authoritative_evidence(
                item,
                _root=_root,
                _evidence_item=_evidence_item,
                _reject_root_source=_reject_root_source,
            )
            for item in value
        )
    return False


def _event_authority_error(
    event: Mapping[str, Any], *, method: str
) -> tuple[str, str] | None:
    raw_source = event.get("evidence_source")
    decision = event.get("decision")
    observed_step = event.get("observed_step")
    if raw_source is None:
        source = ""
    elif not isinstance(raw_source, str):
        return (
            "event evidence_source must be a string",
            "EVIDENCE_AUTHORITY_VIOLATION",
        )
    else:
        source = raw_source.strip().casefold()
    if decision is not None and not isinstance(decision, str):
        return (
            "event decision must be a string or null",
            "EVIDENCE_AUTHORITY_VIOLATION",
        )
    if observed_step is not None and not isinstance(observed_step, str):
        return (
            "event observed_step must be a string or null",
            "EVIDENCE_AUTHORITY_VIOLATION",
        )
    is_vlm_audit = source in _VLM_EVIDENCE_SOURCES
    if is_vlm_audit and (decision != "unknown" or observed_step is not None):
        return (
            "VLM evidence cannot provide a state decision",
            "VLM_AUTHORITY_VIOLATION",
        )
    if source == "none" and (decision != "unknown" or observed_step is not None):
        return (
            "non-deterministic evidence cannot provide a state decision",
            "EVIDENCE_AUTHORITY_VIOLATION",
        )
    has_state_decision = (
        decision not in {None, "unknown", "abstain"} or observed_step is not None
    )
    if method != "hmm_or_crf" and has_state_decision and source != "deterministic":
        return (
            "state decisions require deterministic evidence",
            "EVIDENCE_AUTHORITY_VIOLATION",
        )
    if _contains_non_authoritative_evidence(
        event, allow_root_vlm_audit=is_vlm_audit, _reject_root_source=True
    ):
        return (
            "event contains annotation/truth/dataset/VLM or unknown evidence authority",
            "EVIDENCE_AUTHORITY_VIOLATION",
        )
    return None


STATUSES = {
    "success",
    "unavailable",
    "crash",
    "timeout",
    "oom",
    "unreadable",
    "missing_frames",
    "manual_intervention",
    "partial",
    "host_interrupted",
}


@runtime_checkable
class MethodAdapter(Protocol):
    def __call__(self, video: str | Path, *, seed: int = 0, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class FixedStreakConfig:
    confirm_frames: int = 4
    cooldown_frames: int = 12


@dataclass(frozen=True)
class FixedStreakAdapter:
    config: FixedStreakConfig = FixedStreakConfig()
    processor: Callable[..., Any] | None = None

    def __post_init__(self):
        if (self.config.confirm_frames, self.config.cooldown_frames) != (4, 12):
            raise ValueError(
                "fixed_streak baseline is frozen at confirm_frames=4, cooldown_frames=12"
            )

    def __call__(self, video: str | Path, *, seed: int = 0, **kwargs: Any) -> Any:
        if self.processor is None:
            return {
                "status": "unavailable",
                "events": [],
                "partial_events": [],
                "frames_processed": 0,
                "annotation_used": False,
                "config": asdict(self.config),
                "seed": seed,
                "error": "fixed-streak processor is not registered for this environment",
                "error_code": "FIXED_STREAK_PROCESSOR_UNAVAILABLE",
            }
        processor_kwargs = dict(kwargs)
        try:
            signature = inspect.signature(self.processor)
            if not any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            ):
                processor_kwargs = {
                    key: value
                    for key, value in processor_kwargs.items()
                    if key in signature.parameters
                }
        except (TypeError, ValueError):
            processor_kwargs = {}
        return self.processor(
            video, seed=seed, confirm_frames=4, cooldown_frames=12, **processor_kwargs
        )


def fixed_streak_adapter(
    config: FixedStreakConfig | None = None,
    *,
    processor: Callable[..., Any] | None = None,
) -> FixedStreakAdapter:
    return FixedStreakAdapter(config or FixedStreakConfig(), processor)


@dataclass(frozen=True)
class UnavailableMethodAdapter:
    name: str
    reason: str = "method adapter is not registered for this environment"

    def __call__(self, video: str | Path, *, seed: int = 0, **kwargs: Any) -> Any:
        return {
            "status": "unavailable",
            "events": [],
            "partial_events": [],
            "frames_processed": 0,
            "annotation_used": False,
            "method": self.name,
            "seed": seed,
            "error": self.reason,
            "error_code": "METHOD_UNAVAILABLE",
        }


def default_method_adapters() -> dict[str, Callable[..., Any]]:
    """Return seven explicit adapters; unconfigured methods fail closed."""
    return {
        name: (
            fixed_streak_adapter()
            if name == "fixed_streak"
            else UnavailableMethodAdapter(name)
        )
        for name in METHOD_NAMES
    }


@dataclass(frozen=True)
class MethodSpec:
    name: str
    runner: Callable[..., Any]
    seed: int = 0
    timeout_s: float | None = None
    config_hash: str = ""
    model_hash: str = ""
    code_hash: str = ""

    def __post_init__(self) -> None:
        if self.timeout_s is not None:
            _validate_timeout(self.timeout_s, "method timeout_s")


def _validate_timeout(value: object, label: str) -> float:
    """Return a finite, strictly positive timeout or fail before spawning."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number greater than zero")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{label} must be a finite number greater than zero")
    return timeout


def _resource_schema(
    wall: float,
    frames: int,
    *,
    child_pid: int | None = None,
    termination_kind: str = "completed",
    exit_code: int | None = None,
    kill_requested: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    unavailable = {"status": "unavailable", "value": None, "reason": "not measured"}
    return {
        "schema_version": 1,
        "wall_time_s": float(wall),
        "frames_processed": int(frames),
        "child_pid": child_pid,
        "termination": {
            "kind": termination_kind,
            "observed_at": time.time(),
            "exit_code": exit_code,
            "kill_requested": bool(kill_requested),
            "reason": reason,
        },
        "cpu": unavailable.copy(),
        "rss": unavailable.copy(),
        "gpu": {
            "status": "not_applicable",
            "value": None,
            "reason": "no GPU metrics collected",
        },
    }


@dataclass
class AttemptRecord:
    video_id: str
    method: str
    status: str
    source_dataset: str = "UNKNOWN"
    canonical_root_id: str = "UNKNOWN"
    canonical_group_id: str = "UNKNOWN"
    unit_identity: str = ""
    attempt: int = 1
    first_attempt: bool = True
    primary: bool = True
    retry_of: int | None = None
    lineage_id: str = ""
    task_id: str = "UNKNOWN"
    operator_id: str = "UNKNOWN"
    complete_job_id: str = "UNKNOWN"
    video_hash: str = ""
    expected_frames: int | None = None
    frames_processed: int | None = None
    complete_video: bool = False
    code_hash: str = ""
    config_hash: str = ""
    model_hash: str = ""
    environment_hash: str = ""
    # Explicit on every terminal outcome; annotations are never runtime input.
    annotation_used: bool = False
    annotation_hash: str = ""
    partition_hash: str = ""
    manifest_hash: str = ""
    task_config_hash: str = ""
    task_source_hash: str = ""
    experiment_profile: str = ""
    seed: int = 0
    randomization: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    elapsed_s: float = 0.0
    resources: dict[str, Any] = field(default_factory=lambda: _resource_schema(0.0, 0))
    events: list[dict[str, Any]] = field(default_factory=list)
    partial_events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    output: Any = None
    schedule_id: str = ""
    schedule: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.unit_identity:
            self.unit_identity = make_unit_identity(
                source_dataset=self.source_dataset,
                canonical_root_id=self.canonical_root_id,
                operator_id=self.operator_id,
                complete_job_id=self.complete_job_id,
                video_id=self.video_id,
            )


@dataclass
class PairedRun:
    video_id: str
    video_hash: str
    expected_frames: int | None
    attempts: list[AttemptRecord]
    schedule_id: str = ""

    def rows(self):
        return [asdict(x) for x in self.attempts]


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def _decode_video(path: Path) -> tuple[bool, int, list[str]]:
    try:
        import cv2

        c = cv2.VideoCapture(str(path))
        if not c.isOpened():
            return False, 0, []
        n = 0
        hashes: list[str] = []
        while True:
            ok, frame = c.read()
            if not ok:
                break
            n += 1
            hashes.append(hashlib.sha256(frame.tobytes()).hexdigest())
        c.release()
        return n > 0, n, hashes
    except Exception:
        return False, 0, []


def _invoke(adapter, video, seed, retry, q, attempt_artifacts=None, run_context=None):
    try:
        call_kwargs = {"seed": seed, "retry": retry}
        try:
            signature = inspect.signature(adapter)
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in signature.parameters.values()
            )
            accepts_artifacts = (
                accepts_kwargs or "attempt_artifacts" in signature.parameters
            )
            accepts_context = accepts_kwargs or "run_context" in signature.parameters
        except (TypeError, ValueError):
            accepts_artifacts = accepts_context = False
        if attempt_artifacts is not None and accepts_artifacts:
            call_kwargs["attempt_artifacts"] = attempt_artifacts
        if run_context is not None and accepts_context:
            call_kwargs["run_context"] = dict(run_context)
        out = adapter(video, **call_kwargs)
        json.dumps(out, ensure_ascii=False, allow_nan=False)
        q.put(("ok", out))
    except (MemoryError, RuntimeError) as e:
        s = str(e).lower()
        q.put(
            ("oom", f"{type(e).__name__}: {e}")
            if "cuda" in s or "out of memory" in s
            else ("crash", f"{type(e).__name__}: {e}")
        )
    except Exception as e:
        q.put(("crash", f"{type(e).__name__}: {e}"))


def _classify_exit(exitcode: int | None) -> tuple[str, str, str]:
    """Classify process termination without treating every crash as OOM."""
    if exitcode in (-9, 137):
        return "oom", f"child exited with code {exitcode}", "OOM_EXIT"
    if exitcode is None:
        return "crash", "child exit code unavailable", "CRASH"
    return "crash", f"child exited with code {exitcode}", "CRASH"


def _valid_frame_trace(trace: object, count: int, decoded_hashes: list[str]) -> bool:
    if not isinstance(trace, list) or len(trace) != count:
        return False
    if any(not isinstance(item, Mapping) for item in trace):
        return False
    indices = [item.get("frame") for item in trace]
    return indices == list(range(count)) and all(
        item.get("source_frame_sha256") == decoded_hashes[index]
        for index, item in enumerate(trace)
    )


def _normalise_child_result(
    out: object,
    *,
    expected: int,
    decoded: int,
    decoded_hashes: list[str],
    video_hash: str,
    method: str,
) -> tuple[
    str, list[dict[str, Any]], list[dict[str, Any]], int, str | None, str | None, Any
]:
    """Validate the untrusted child payload; never infer success from shape."""
    if not isinstance(out, Mapping):
        return (
            "crash",
            [],
            [],
            0,
            "child result must be an object",
            "CHILD_RESULT_SCHEMA",
            out,
        )
    status = out.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        return (
            "crash",
            [],
            [],
            0,
            "child result status is missing or invalid",
            "CHILD_RESULT_SCHEMA",
            out,
        )
    events, partial = out.get("events", []), out.get("partial_events", [])
    if (
        not isinstance(events, list)
        or not isinstance(partial, list)
        or not all(isinstance(item, Mapping) for item in events)
        or not all(isinstance(item, Mapping) for item in partial)
    ):
        return (
            "crash",
            [],
            [],
            0,
            "events and partial_events must be lists",
            "CHILD_RESULT_SCHEMA",
            out,
        )
    frames_raw = out.get("frames_processed")
    if (
        isinstance(frames_raw, bool)
        or not isinstance(frames_raw, int)
        or not 0 <= frames_raw <= expected
    ):
        return (
            "crash",
            events,
            partial,
            0,
            "frames_processed is invalid",
            "CHILD_RESULT_SCHEMA",
            out,
        )
    if out.get("annotation_used", None) is not False:
        return (
            "crash",
            events,
            partial,
            frames_raw,
            "annotation_used must be explicitly false",
            "ANNOTATION_USED_FORBIDDEN",
            out,
        )
    declared_hash = out.get("video_hash")
    if declared_hash is not None and declared_hash != video_hash:
        return (
            "crash",
            events,
            partial,
            frames_raw,
            "child video_hash does not match runner input",
            "VIDEO_HASH_MISMATCH",
            out,
        )
    complete = out.get("complete_video")
    if complete is not None and not isinstance(complete, bool):
        return (
            "crash",
            events,
            partial,
            frames_raw,
            "complete_video must be boolean",
            "CHILD_RESULT_SCHEMA",
            out,
        )
    if status == "success" and (frames_raw != expected or not decoded):
        return (
            "missing_frames",
            events,
            partial,
            frames_raw,
            f"frames_processed={frames_raw}, expected={expected}",
            "MISSING_FRAMES",
            out,
        )
    if method == "vlm_only" and any(
        key in out and out[key] not in (None, [], {}, 0, False, "")
        for key in _VLM_AUTHORITY_KEYS
    ):
        return (
            "crash",
            events,
            partial,
            frames_raw,
            "VLM-only produced authority outputs",
            "VLM_AUTHORITY_VIOLATION",
            out,
        )

    # Reject annotation/truth/dataset answers in every event and authority
    # payload before they can reach scoring, recovery, or the ledger.
    if _contains_non_authoritative_evidence(
        out.get("authorizations", []), _reject_root_source=True
    ) or _contains_non_authoritative_evidence(
        out.get("certificates", []), _reject_root_source=True
    ):
        return (
            "crash",
            events,
            partial,
            frames_raw,
            "authority output contains annotation/truth/dataset/VLM evidence",
            "EVIDENCE_AUTHORITY_VIOLATION",
            out,
        )
    for event in [*events, *partial]:
        authority_error = _event_authority_error(event, method=method)
        if authority_error is not None:
            message, error_code = authority_error
            return (
                "crash",
                events,
                partial,
                frames_raw,
                message,
                error_code,
                out,
            )
    if method == "hmm_or_crf":
        if (
            out.get("authorizations", [])
            or out.get("certificates", [])
            or out.get("state_writes", 0) != 0
        ):
            return (
                "crash",
                events,
                partial,
                frames_raw,
                "HMM produced state/certificate authority",
                "HMM_AUTHORITY_VIOLATION",
                out,
            )
        has_predictions = (
            status in {"success", "partial"}
            or frames_raw > 0
            or bool(events)
            or bool(partial)
        )
        if has_predictions:
            if out.get("authority_scope") != "experiment_prediction_only":
                return (
                    "crash",
                    events,
                    partial,
                    frames_raw,
                    "HMM authority scope is missing or invalid",
                    "HMM_AUTHORITY_VIOLATION",
                    out,
                )
            method_decisions = out.get("method_decisions")
            expected_decisions = [
                event
                for event in events
                if event.get("decision") not in {"abstain", "unknown", None}
            ]
            coverage = out.get("coverage")
            if (
                not isinstance(method_decisions, list)
                or method_decisions != expected_decisions
                or not isinstance(coverage, Mapping)
            ):
                return (
                    "crash",
                    events,
                    partial,
                    frames_raw,
                    "HMM method decisions are inconsistent",
                    "HMM_DECISION_SCHEMA_INVALID",
                    out,
                )
            expected_value = len(expected_decisions) / len(events) if events else 0.0
            value = coverage.get("value")
            if (
                coverage.get("opportunities") != len(events)
                or coverage.get("definitive") != len(expected_decisions)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or abs(float(value) - expected_value) > 1e-12
            ):
                return (
                    "crash",
                    events,
                    partial,
                    frames_raw,
                    "HMM coverage is inconsistent",
                    "HMM_DECISION_SCHEMA_INVALID",
                    out,
                )
    trace = out.get("frame_trace", events)
    if status == "success":
        if not _valid_frame_trace(trace, expected, decoded_hashes):
            return (
                "missing_frames",
                events,
                partial,
                frames_raw,
                "success requires an exact per-frame trace",
                "SEMANTIC_COMPLETION_INVALID",
                out,
            )
    elif frames_raw > 0 and not _valid_frame_trace(trace, frames_raw, decoded_hashes):
        return (
            "crash",
            events,
            partial,
            frames_raw,
            "non-success output with processed frames requires a bound prefix trace",
            "PARTIAL_TRACE_INVALID",
            out,
        )
    if status == "success" and complete is not True:
        return (
            "missing_frames",
            events,
            partial,
            frames_raw,
            "child did not explicitly attest complete_video=True",
            "INCOMPLETE_VIDEO",
            out,
        )
    if method == "vlm_only":
        event_keys = set(_VLM_AUTHORITY_KEYS) | {
            "authorization",
            "state_transition",
            "rollback",
            "override",
            "transition",
            "state_write",
            "certificate",
            "advance",
        }
        if any(
            "decision" not in event
            or event.get("decision") != "unknown"
            or "observed_step" not in event
            or event.get("observed_step") is not None
            or event.get("evidence_source") not in {"vlm", "vlm_audit"}
            or any(
                key in event and event[key] not in (None, [], {}, 0, False, "")
                for key in event_keys
            )
            for event in [*events, *partial]
        ):
            return (
                "crash",
                events,
                partial,
                frames_raw,
                "VLM-only produced authority-bearing event",
                "VLM_AUTHORITY_VIOLATION",
                out,
            )
    error = out.get("error")
    code = out.get("error_code")
    return (
        status,
        events,
        partial,
        frames_raw,
        str(error) if error is not None else None,
        str(code) if code is not None else None,
        out,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _valid_hash(value: object, *, allow_empty: bool = False) -> TypeGuard[str]:
    return (
        value == ""
        if allow_empty
        else isinstance(value, str) and bool(_HASH_RE.fullmatch(value))
    )


def verify_ledger_integrity(data: bytes | str) -> tuple[list[str], str]:
    """Verify the append-only forward chain, returning errors and the tail hash."""
    errors: list[str] = []
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            return [f"ledger is not valid UTF-8: {exc}"], ""
    elif isinstance(data, str):
        text = data
    else:
        return ["ledger must be bytes or str"], ""
    if not text:
        return [], ""
    previous = ""
    batches: dict[str, tuple[int, int, str, list[str]]] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            errors.append(f"line {line_no}: blank lines are not allowed")
            continue
        try:
            row = json.loads(line)
        except Exception as exc:
            errors.append(f"line {line_no}: invalid JSON ({exc})")
            continue
        if not isinstance(row, dict):
            errors.append(f"line {line_no}: row must be object")
            continue
        meta = row.get("ledger_integrity")
        required = {
            "version",
            "batch_hash",
            "batch_index",
            "batch_size",
            "previous_chain_hash",
            "row_hash",
            "chain_hash",
        }
        if not isinstance(meta, dict) or required - set(meta):
            errors.append(f"line {line_no}: missing ledger_integrity metadata")
            continue
        payload = dict(row)
        payload.pop("ledger_integrity", None)
        try:
            row_hash = hashlib.sha256(
                _canonical_json(payload).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError, UnicodeError) as exc:
            errors.append(f"line {line_no}: invalid canonical row ({exc})")
            continue
        if meta.get("version") != LEDGER_INTEGRITY_VERSION:
            errors.append(f"line {line_no}: unsupported ledger integrity version")
        meta_row_hash = meta.get("row_hash")
        if not _valid_hash(meta_row_hash):
            errors.append(f"line {line_no}: invalid row hash")
        elif meta_row_hash != row_hash:
            errors.append(f"line {line_no}: row hash mismatch")
        try:
            index, size = meta["batch_index"], meta["batch_size"]
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or isinstance(size, bool)
                or not isinstance(size, int)
            ):
                raise TypeError
        except (TypeError, ValueError):
            errors.append(f"line {line_no}: invalid batch index/size")
            continue
        if index < 0 or size < 1 or index >= size:
            errors.append(f"line {line_no}: invalid batch index/size")
        batch_hash = meta.get("batch_hash")
        if not _valid_hash(batch_hash):
            errors.append(f"line {line_no}: invalid batch hash")
            continue
        meta_previous_raw = meta.get("previous_chain_hash")
        if not isinstance(meta_previous_raw, str):
            errors.append(f"line {line_no}: invalid previous chain hash")
            # Keep the verifier's typed state fail-closed while still allowing
            # the remaining structural checks to run for this row.
            meta_previous = ""
        else:
            meta_previous = meta_previous_raw
        if meta_previous and not _valid_hash(meta_previous):
            errors.append(f"line {line_no}: invalid previous chain hash")
        elif (
            not meta_previous
            and batches
            and not any(batch[2] == "" for batch in batches.values())
        ):
            errors.append(f"line {line_no}: empty previous chain hash after genesis")
        batch = batches.get(batch_hash)
        if batch is None:
            batches[batch_hash] = (1, size, meta_previous, [row_hash])
        else:
            count, old_size, old_prev, hashes = batch
            if meta_previous != old_prev or size != old_size or index != count:
                errors.append(f"line {line_no}: batch continuity mismatch")
            hashes.append(row_hash)
            batches[batch_hash] = (count + 1, old_size, old_prev, hashes)
        if index == 0 and meta_previous != previous:
            errors.append(f"line {line_no}: forward-chain predecessor mismatch")
        expected_chain = hashlib.sha256(
            (previous + "\n" + batch_hash + "\n" + row_hash).encode("ascii")
        ).hexdigest()
        meta_chain = meta.get("chain_hash")
        if not _valid_hash(meta_chain):
            errors.append(f"line {line_no}: invalid chain hash")
            previous = expected_chain
        else:
            if meta_chain != expected_chain:
                errors.append(f"line {line_no}: chain hash mismatch")
            previous = meta_chain
    for batch_hash, (count, size, batch_previous, hashes) in batches.items():
        if count != size:
            errors.append(f"batch {batch_hash}: incomplete batch ({count}/{size})")
        expected_batch = hashlib.sha256(
            _canonical_json(
                {
                    "version": LEDGER_INTEGRITY_VERSION,
                    "previous_chain_hash": batch_previous,
                    "row_hashes": hashes,
                }
            ).encode("utf-8")
        ).hexdigest()
        if batch_hash != expected_batch:
            errors.append(f"batch {batch_hash}: batch hash mismatch")
    return errors, previous


def append_jsonl_atomic(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> None:
    p = Path(path)
    vals = []
    for row in rows:
        if "ledger_integrity" in row:
            raise ValueError(
                "ledger_integrity is reserved and must not be supplied by callers"
            )
        try:
            vals.append(dict(row))
        except (TypeError, ValueError) as e:
            raise TypeError(f"ledger row is not JSON serializable: {e}") from e
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_bytes() if p.exists() else b""
    integrity_errors, previous = verify_ledger_integrity(old)
    if integrity_errors:
        raise ValueError(
            "existing ledger integrity check failed: " + "; ".join(integrity_errors[:3])
        )
    encoded_rows = []
    if vals:
        row_hashes = [
            hashlib.sha256(_canonical_json(row).encode("utf-8")).hexdigest()
            for row in vals
        ]
        batch_hash = hashlib.sha256(
            _canonical_json(
                {
                    "version": LEDGER_INTEGRITY_VERSION,
                    "previous_chain_hash": previous,
                    "row_hashes": row_hashes,
                }
            ).encode("utf-8")
        ).hexdigest()
        chain = previous
        for index, (row, row_hash) in enumerate(zip(vals, row_hashes)):
            chain = hashlib.sha256(
                (chain + "\n" + batch_hash + "\n" + row_hash).encode("ascii")
            ).hexdigest()
            row["ledger_integrity"] = {
                "version": LEDGER_INTEGRITY_VERSION,
                "batch_hash": batch_hash,
                "batch_index": index,
                "batch_size": len(vals),
                "previous_chain_hash": previous,
                "row_hash": row_hash,
                "chain_hash": chain,
            }
            encoded_rows.append(_canonical_json(row))
    sep = b"" if not old or old.endswith(b"\n") else b"\n"
    data = (
        old + sep + (("\n".join(encoded_rows) + "\n").encode() if encoded_rows else b"")
    )
    fd, name = tempfile.mkstemp(prefix="." + p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for retry in range(6):
            try:
                os.replace(name, p)
                break
            except PermissionError:
                if retry == 5:
                    raise
                # Windows workers can briefly retain a read handle after an
                # attempt exits. Keep the atomic replace, but tolerate only a
                # short bounded hand-off window.
                time.sleep(0.02 * (retry + 1))
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _resolved_path_sha256(path: str | Path) -> str:
    return hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()


def _mapping_get(value: object, key: str) -> object:
    return value.get(key) if isinstance(value, Mapping) else None


def _claim_g3_unit_token(
    *,
    ledger_path: Path,
    token: Mapping[str, Any],
    receipt: Mapping[str, Any],
    unit_id: str,
) -> Path:
    """Atomically consume a destination-bound token before any video decode."""
    claims_dir = ledger_path.parent / f".{ledger_path.name}.g3-token-claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    token_id = str(token["token_id"])
    claim_path = claims_dir / f"{hashlib.sha256(token_id.encode('utf-8')).hexdigest()}.json"
    claim = {
        "schema_version": "display_project.g3_unit_token_claim-1.0.0",
        "record_kind": "unit_token_claim",
        "token_id": token_id,
        "unit_id": unit_id,
        "batch_id": receipt["batch_id"],
        "batch_receipt_sha256": receipt["artifact_sha256"],
        "unit_token_sha256": token["artifact_sha256"],
        "result_ledger_id": receipt["result_destination"]["ledger_id"],
        "result_ledger_path_sha256": _resolved_path_sha256(ledger_path),
        "state": "PRIMARY_ATTEMPT_REGISTERING",
    }
    claim["artifact_sha256"] = hashlib.sha256(
        _canonical_json(claim).encode("utf-8")
    ).hexdigest()
    data = (_canonical_json(claim) + "\n").encode("utf-8")
    try:
        fd = os.open(str(claim_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("unit execution token already consumed") from exc
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        # The exclusive marker deliberately remains fail-closed after an
        # interrupted or failed claim write; it must never permit silent replay.
        raise
    append_jsonl_atomic(
        ledger_path.with_suffix(ledger_path.suffix + ".token-claims.jsonl"),
        [claim],
    )
    return claim_path


def _complete_g3_unit_token(
    *,
    ledger_path: Path,
    token: Mapping[str, Any],
    receipt: Mapping[str, Any],
    unit_id: str,
    attempts: Sequence["AttemptRecord"],
) -> None:
    primary = [attempt for attempt in attempts if attempt.primary]
    methods = [attempt.method for attempt in primary]
    expected_methods = list(token["method_schedule"])
    if methods != expected_methods:
        raise ValueError("cannot terminalize token without every primary method outcome")
    ledger_errors, ledger_tail = verify_ledger_integrity(ledger_path.read_bytes())
    if ledger_errors:
        raise ValueError("cannot terminalize token with invalid paired ledger")
    registration_path = ledger_path.with_suffix(
        ledger_path.suffix + ".registration.jsonl"
    )
    _registrations, registration_tail = validate_registration_records(
        registration_path
    )
    terminal = {
        "schema_version": "display_project.g3_unit_token_terminal-1.0.0",
        "record_kind": "unit_token_terminal",
        "token_id": token["token_id"],
        "unit_id": unit_id,
        "batch_id": receipt["batch_id"],
        "batch_receipt_sha256": receipt["artifact_sha256"],
        "unit_token_sha256": token["artifact_sha256"],
        "result_ledger_id": receipt["result_destination"]["ledger_id"],
        "result_ledger_path_sha256": _resolved_path_sha256(ledger_path),
        "primary_method_statuses": [
            {"method": attempt.method, "status": attempt.status} for attempt in primary
        ],
        "paired_ledger_tail_sha256": ledger_tail,
        "registration_tail_sha256": registration_tail,
        "state": "TERMINAL_RECORDED",
    }
    terminal["artifact_sha256"] = hashlib.sha256(
        _canonical_json(terminal).encode("utf-8")
    ).hexdigest()
    append_jsonl_atomic(
        ledger_path.with_suffix(ledger_path.suffix + ".token-claims.jsonl"),
        [terminal],
    )


def validate_g3_token_claim_records(
    path: str | Path,
    *,
    expected_token: Mapping[str, Any],
    expected_receipt: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate the claim-to-terminal lifecycle for one admitted unit token."""
    claim_path = Path(path)
    if not claim_path.is_file():
        return [], ["TOKEN_CLAIM_LEDGER_MISSING"]
    raw = claim_path.read_bytes()
    integrity_errors, _tail = verify_ledger_integrity(raw)
    reasons = [f"TOKEN_CLAIM_INTEGRITY:{item}" for item in integrity_errors]
    rows: list[dict[str, Any]] = []
    try:
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], [*reasons, "TOKEN_CLAIM_LEDGER_INVALID_JSON"]
    if len(rows) != 2:
        reasons.append("TOKEN_LIFECYCLE_INCOMPLETE")
    expected_states = ["PRIMARY_ATTEMPT_REGISTERING", "TERMINAL_RECORDED"]
    if [row.get("state") for row in rows] != expected_states:
        reasons.append("TOKEN_LIFECYCLE_STATE_INVALID")
    binding = {
        "token_id": expected_token.get("token_id"),
        "unit_id": _mapping_get(expected_token.get("unit"), "unit_id"),
        "batch_id": expected_receipt.get("batch_id"),
        "batch_receipt_sha256": expected_receipt.get("artifact_sha256"),
        "unit_token_sha256": expected_token.get("artifact_sha256"),
        "result_ledger_id": _mapping_get(
            expected_receipt.get("result_destination"), "ledger_id"
        ),
        "result_ledger_path_sha256": _mapping_get(
            expected_receipt.get("result_destination"), "ledger_path_sha256"
        ),
    }
    for row in rows:
        body = {
            key: value
            for key, value in row.items()
            if key not in {"artifact_sha256", "ledger_integrity"}
        }
        supplied = row.get("artifact_sha256")
        if not _valid_hash(supplied) or supplied != hashlib.sha256(
            _canonical_json(body).encode("utf-8")
        ).hexdigest():
            reasons.append("TOKEN_CLAIM_ARTIFACT_HASH_INVALID")
        if any(row.get(key) != value for key, value in binding.items()):
            reasons.append("TOKEN_CLAIM_BINDING_INVALID")
    if len(rows) == 2:
        terminal = rows[1]
        statuses = terminal.get("primary_method_statuses")
        methods = (
            [item.get("method") for item in statuses]
            if isinstance(statuses, list)
            and all(isinstance(item, Mapping) for item in statuses)
            else []
        )
        if methods != list(expected_token.get("method_schedule", [])):
            reasons.append("TOKEN_TERMINAL_METHODS_INVALID")
        if not _valid_hash(terminal.get("paired_ledger_tail_sha256")) or not _valid_hash(
            terminal.get("registration_tail_sha256")
        ):
            reasons.append("TOKEN_TERMINAL_LEDGER_BINDING_INVALID")
    return rows, list(dict.fromkeys(reasons))


REGISTRATION_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "sequence",
        "prev_hash",
        "record_hash",
        "record_kind",
        "registration_id",
        "attempt_identity",
        "video_id",
        "method",
        "attempt",
        "primary",
        "retry_of",
        "video_hash",
        "source_video_path",
        "expected_frames",
        "schedule_id",
        "schedule",
        "source_dataset",
        "canonical_root_id",
        "canonical_group_id",
        "unit_identity",
        "operator_id",
        "complete_job_id",
        "task_id",
        "model_hash",
        "config_hash",
        "effective_policy",
        "artifact_paths",
        "scorer_metadata",
        "status",
        "ledger_integrity",
    }
)


def _clean_identity(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-placeholder string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string")
    return cleaned


def make_unit_identity(
    *,
    source_dataset: str,
    canonical_root_id: str,
    operator_id: str,
    complete_job_id: str,
    video_id: str,
    cluster_level: str = "operator",
) -> str:
    level = _clean_identity(cluster_level, "cluster_level")
    normalized_dataset = _clean_identity(source_dataset, "source_dataset")
    normalized_operator = _clean_identity(operator_id, "operator_id")
    normalized_job = _clean_identity(complete_job_id, "complete_job_id")
    is_industreal_complete_job_fallback = (
        normalized_dataset.replace("_", "").replace("-", "").casefold()
        == "industreal"
        and normalized_operator.upper() == "UNKNOWN"
        and normalized_job.upper().startswith("INDUSTREAL-RECORDING-")
    )
    if level == "complete_job" or is_industreal_complete_job_fallback:
        payload = {
            "dataset_id": normalized_dataset.upper(),
            "canonical_root_id": _clean_identity(canonical_root_id, "canonical_root_id"),
            "complete_job_id": normalized_job,
            "video_id": _clean_identity(video_id, "video_id"),
            "cluster_level": "complete_job",
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    payload = {
        "source_dataset": normalized_dataset,
        "canonical_root_id": _clean_identity(canonical_root_id, "canonical_root_id"),
        "operator_id": normalized_operator,
        "complete_job_id": normalized_job,
        "video_id": _clean_identity(video_id, "video_id"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_registration_candidate(
    row: Mapping[str, Any],
    *,
    sequence: int,
    previous: str,
) -> None:
    expected_fields = REGISTRATION_FIELDS - {"ledger_integrity"}
    if set(row) != expected_fields:
        raise ValueError("registration candidate exact schema is invalid")
    if (
        row.get("schema_version") != 1
        or row.get("record_type") != "schedule_registration"
        or row.get("record_kind") != "schedule_registration"
        or row.get("status") != "registered"
    ):
        raise ValueError("registration candidate constants are invalid")
    attempt = row.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("registration candidate attempt is invalid")
    if row.get("primary") is not (attempt == 1) or row.get("retry_of") != (
        None if attempt == 1 else attempt - 1
    ):
        raise ValueError("registration candidate retry lineage is invalid")
    if (
        not isinstance(row.get("schedule"), list)
        or not row["schedule"]
        or any(
            not isinstance(item, str) or not item.strip() for item in row["schedule"]
        )
        or len(row["schedule"]) != len(set(row["schedule"]))
        or row.get("method") not in row["schedule"]
    ):
        raise ValueError("registration candidate schedule is invalid")
    expected_frames = row.get("expected_frames")
    if (
        isinstance(expected_frames, bool)
        or not isinstance(expected_frames, int)
        or expected_frames < 0
    ):
        raise ValueError("registration candidate expected_frames is invalid")
    if not isinstance(row.get("artifact_paths"), dict) or not isinstance(
        row.get("scorer_metadata"), dict
    ):
        raise ValueError("registration candidate metadata is invalid")
    body = dict(row)
    supplied = body.pop("record_hash")
    if row.get("sequence") != sequence or row.get("prev_hash") != previous:
        raise ValueError("registration candidate sequence is invalid")
    if supplied != hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest():
        raise ValueError("registration candidate hash is invalid")
    for key in ("video_hash", "model_hash", "config_hash"):
        if row.get(key) and not _valid_hash(row[key]):
            raise ValueError(f"registration {key} is invalid")
    string_fields = (
        "registration_id",
        "attempt_identity",
        "video_id",
        "method",
        "source_video_path",
        "schedule_id",
        "source_dataset",
        "canonical_root_id",
        "canonical_group_id",
        "unit_identity",
        "operator_id",
        "complete_job_id",
        "task_id",
        "effective_policy",
    )
    if any(
        not isinstance(row.get(key), str) or not row[key].strip()
        for key in string_fields
    ):
        raise ValueError("registration string fields are invalid")
    identity_fields = (
        "source_dataset",
        "canonical_root_id",
        "operator_id",
        "complete_job_id",
        "video_id",
    )
    expected_unit = make_unit_identity(**{key: row[key] for key in identity_fields})
    if row.get("unit_identity") != expected_unit:
        raise ValueError("registration unit identity is invalid")


def validate_registration_records(path: str | Path) -> tuple[list[dict[str, Any]], str]:
    source = Path(path)
    raw = source.read_bytes()
    integrity_errors, _ = verify_ledger_integrity(raw)
    if integrity_errors:
        raise ValueError(
            "registration integrity check failed: " + "; ".join(integrity_errors[:3])
        )
    rows = [
        json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()
    ]
    previous = ""
    registration_ids: set[str] = set()
    attempt_identities: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != REGISTRATION_FIELDS:
            raise ValueError("registration exact schema is invalid")
        if (
            row["schema_version"] != 1
            or row["record_type"] != "schedule_registration"
            or row["record_kind"] != "schedule_registration"
            or row["status"] != "registered"
        ):
            raise ValueError("registration constants are invalid")
        if (
            isinstance(row["sequence"], bool)
            or row["sequence"] != index
            or isinstance(row["attempt"], bool)
            or not isinstance(row["attempt"], int)
            or row["attempt"] < 1
        ):
            raise ValueError("registration sequence/attempt is invalid")
        if row["primary"] is not (row["attempt"] == 1) or row["retry_of"] != (
            None if row["attempt"] == 1 else row["attempt"] - 1
        ):
            raise ValueError("registration retry lineage is invalid")
        if (
            not isinstance(row["schedule"], list)
            or not row["schedule"]
            or any(
                not isinstance(item, str) or not item.strip()
                for item in row["schedule"]
            )
            or len(row["schedule"]) != len(set(row["schedule"]))
            or row["method"] not in row["schedule"]
        ):
            raise ValueError("registration schedule is invalid")
        if (
            isinstance(row["expected_frames"], bool)
            or not isinstance(row["expected_frames"], int)
            or row["expected_frames"] < 0
        ):
            raise ValueError("registration expected_frames is invalid")
        for key, seen in (
            ("registration_id", registration_ids),
            ("attempt_identity", attempt_identities),
        ):
            value = row[key]
            if not isinstance(value, str) or not value or value in seen:
                raise ValueError("registration identity is invalid or duplicated")
            seen.add(value)
        for key in (
            "video_id",
            "source_video_path",
            "schedule_id",
            "source_dataset",
            "canonical_root_id",
            "canonical_group_id",
            "operator_id",
            "complete_job_id",
            "task_id",
            "effective_policy",
        ):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError(f"registration {key} is invalid")
        expected_unit = make_unit_identity(
            source_dataset=row["source_dataset"],
            canonical_root_id=row["canonical_root_id"],
            operator_id=row["operator_id"],
            complete_job_id=row["complete_job_id"],
            video_id=row["video_id"],
        )
        if (
            row["unit_identity"] != expected_unit
            or row["attempt_identity"]
            != f"{expected_unit}:{row['method']}:{row['attempt']}"
        ):
            raise ValueError("registration unit identity is invalid")
        for key in ("video_hash", "model_hash", "config_hash"):
            if row[key] and not _valid_hash(row[key]):
                raise ValueError(f"registration {key} is invalid")
        if not isinstance(row["artifact_paths"], dict) or not isinstance(
            row["scorer_metadata"], dict
        ):
            raise ValueError("registration artifact/scorer metadata is invalid")
        body = dict(row)
        supplied = body.pop("record_hash")
        body.pop("ledger_integrity")
        expected = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
        if row["prev_hash"] != previous or supplied != expected:
            raise ValueError("registration hash chain is invalid")
        previous = supplied
    return rows, previous


def _artifact_paths(
    root: Path, video: Path, method: str, seed: int, retry: int
) -> tuple[Path, Path, Path]:
    safe = "".join(c if c.isalnum() or c in {"-", "_"} else "_" for c in method)
    stem = f"{video.stem}.{safe}.seed-{seed}.retry-{retry}"
    events = root / f"{stem}.events.jsonl"
    return (
        root / f"{stem}.mp4",
        events,
        events.parent / (events.stem + ".certificates") / "records.jsonl",
    )


def _attempt_artifacts(
    spec: MethodSpec, video: Path, retry: int, metadata: Mapping[str, Any]
) -> dict[str, str] | None:
    adapter = spec.runner
    root = getattr(adapter, "output_root", None)
    if not root:
        return None
    output, events, certificates = _artifact_paths(
        Path(root), video, spec.name, spec.seed, retry
    )
    effective_policy = getattr(adapter, "policy", None)
    return {
        "output": str(output),
        "events": str(events),
        "certificates": str(certificates),
        "source": str(video),
        "effective_policy": str(effective_policy or "proposed"),
    }


def _read_partial_jsonl(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    terminated = text.endswith("\n")
    lines = text.splitlines()
    parsed: list[Any] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not terminated:
                break
            raise
    return parsed


def _recover_partial_artifacts(
    artifacts: Mapping[str, str] | None,
    *,
    decoded_hashes: list[str],
    expected_input_hash: str,
    expected_model_hash: str,
    expected_config_hash: str,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], int, str | None, str | None, list[str]
]:
    if not artifacts:
        return [], [], 0, None, None, []
    events_path = Path(artifacts["events"])
    cert_path = Path(artifacts["certificates"])
    if not events_path.exists() and not cert_path.exists():
        return [], [], 0, None, None, []
    if not events_path.is_file():
        return [], [], 0, "artifact events file is missing", "RECOVERY_INVALID", []
    try:
        from display_project.fusion.event_schema import validate_event

        rows = _read_partial_jsonl(events_path)
        if not all(isinstance(row, Mapping) for row in rows):
            raise ValueError("event rows must be objects")
        events = [dict(row) for row in rows]

        for event in events:
            source = str(event.get("evidence_source", "")).strip().casefold()
            is_vlm_audit = source in _VLM_EVIDENCE_SOURCES
            if is_vlm_audit and (
                event.get("decision") != "unknown"
                or event.get("observed_step") is not None
            ):
                raise ValueError(
                    "recovered VLM evidence cannot provide a state decision"
                )
            if _contains_non_authoritative_evidence(
                event, allow_root_vlm_audit=is_vlm_audit
            ):
                raise ValueError(
                    "recovered event contains annotation/truth/dataset/VLM authority"
                )
        for index, event in enumerate(events):
            validate_event(event)
            if event.get("source_frame_sha256") != decoded_hashes[index]:
                raise ValueError("source frame hash does not match input prefix")
            if event.get("frame") not in (None, index):
                raise ValueError("event frame index is not a prefix")
        if len(events) > len(decoded_hashes):
            raise ValueError("partial event prefix exceeds decoded video")
        policy = str(artifacts.get("effective_policy", "proposed"))
        if policy == "no_calibration":
            policy = "uncalibrated_certificate"
        if (
            events
            and policy in {"proposed", "uncalibrated_certificate"}
            and not cert_path.is_file()
        ):
            raise ValueError(
                "certificate sidecar is required for certificate policy recovery"
            )
        if cert_path.exists():
            cert_rows = _read_partial_jsonl(cert_path)
            if _contains_non_authoritative_evidence(cert_rows):
                raise ValueError(
                    "recovered certificate contains annotation/truth/dataset authority"
                )
            from display_project.experiments.adapters import _parse_render_audit

            _parse_render_audit(
                policy,
                events,
                expected_input_hash=expected_input_hash,
                expected_model_hash=expected_model_hash,
                expected_config_hash=expected_config_hash,
                allow_single_trailing_orphan=True,
                preparsed_certificate_records={cert_path: cert_rows},
            )
        refs = [str(events_path)] + ([str(cert_path)] if cert_path.exists() else [])
        return events, events, len(events), None, None, refs
    except Exception as exc:
        return [], [], 0, str(exc), "RECOVERY_INVALID", []


def reconcile_host_interrupted(ledger_path: str | Path) -> int:
    """Materialize missing primary outcomes from a verified registration sidecar."""
    ledger = Path(ledger_path)
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    if not sidecar.is_file():
        raise ValueError("registration sidecar is required")
    errors, _ = verify_ledger_integrity(sidecar.read_bytes())
    if errors:
        raise ValueError(
            "registration integrity check failed: " + "; ".join(errors[:3])
        )
    outcomes = (
        [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if ledger.is_file()
        else []
    )
    identities = {
        row.get("attempt_identity") for row in outcomes if isinstance(row, dict)
    }
    regs, _registration_tail = validate_registration_records(sidecar)
    added = 0
    for reg in regs:
        if (
            not isinstance(reg, dict)
            or reg.get("record_kind") != "schedule_registration"
        ):
            continue
        identity = reg.get("attempt_identity")
        if identity in identities:
            continue
        source_path = Path(str(reg.get("source_video_path", "")))
        source_valid = source_path.is_file() and _hash(source_path) == reg.get(
            "video_hash"
        )
        decoded_valid, decoded_count, source_hashes = (
            _decode_video(source_path) if source_valid else (False, 0, [])
        )
        source_valid = (
            source_valid
            and decoded_valid
            and decoded_count == reg.get("expected_frames")
        )
        artifacts = (
            reg.get("artifact_paths")
            if isinstance(reg.get("artifact_paths"), dict)
            else None
        )
        recovered_events: list[dict[str, Any]] = []
        recovered_refs: list[str] = []
        recovered_frames = 0
        if artifacts is not None and source_valid:
            (
                recovered_events,
                _partial,
                recovered_frames,
                _rec_error,
                _rec_code,
                recovered_refs,
            ) = _recover_partial_artifacts(
                artifacts,
                decoded_hashes=source_hashes,
                expected_input_hash=str(reg.get("video_hash", "")),
                expected_model_hash=str(reg.get("model_hash", "")),
                expected_config_hash=str(reg.get("config_hash", "")),
            )
        row = AttemptRecord(
            video_id=str(reg.get("video_id", "UNKNOWN")),
            method=str(reg.get("method", "")),
            status="host_interrupted",
            source_dataset=str(reg.get("source_dataset", "UNKNOWN")),
            canonical_root_id=str(reg.get("canonical_root_id", "UNKNOWN")),
            canonical_group_id=str(reg.get("canonical_group_id", "UNKNOWN")),
            unit_identity=str(reg.get("unit_identity", "")),
            attempt=int(reg.get("attempt", 1)),
            first_attempt=bool(reg.get("primary", False)),
            primary=bool(reg.get("primary", False)),
            retry_of=reg.get("retry_of"),
            lineage_id=f"{reg.get('unit_identity')}:{reg.get('method')}",
            task_id=str(reg.get("task_id", "UNKNOWN")),
            operator_id=str(reg.get("operator_id", "UNKNOWN")),
            complete_job_id=str(reg.get("complete_job_id", "UNKNOWN")),
            video_hash=str(reg.get("video_hash", "")),
            expected_frames=reg.get("expected_frames", 0),
            frames_processed=recovered_frames,
            complete_video=False,
            model_hash=str(reg.get("model_hash", "")),
            config_hash=str(reg.get("config_hash", "")),
            events=recovered_events,
            partial_events=recovered_events,
            resources=_resource_schema(
                0.0,
                recovered_frames,
                termination_kind="host_interrupted",
                reason="host process interrupted before terminal outcome",
            ),
            error="host process interrupted before terminal outcome",
            error_code="HOST_INTERRUPTED",
            output={
                "artifact_refs": recovered_refs,
                "frame_trace": [
                    {
                        "frame": index,
                        "source_frame_sha256": event["source_frame_sha256"],
                    }
                    for index, event in enumerate(recovered_events)
                ],
                "annotation_used": False,
                "vlm_authority_verified": True,
                "authority_boundary": "runner_verified_no_annotation_or_truth_authority",
            },
            schedule_id=str(reg.get("schedule_id", "")),
            schedule=tuple(reg.get("schedule", [])),
        )
        payload = asdict(row)
        payload["record_kind"] = "attempt_outcome"
        payload["attempt_identity"] = identity
        payload["source_video_path"] = reg["source_video_path"]
        payload["artifact_paths"] = reg["artifact_paths"]
        payload["effective_policy"] = reg["effective_policy"]
        payload["scorer_metadata"] = reg["scorer_metadata"]
        payload["annotation_used"] = False
        payload["vlm_authority_verified"] = True
        if isinstance(reg["scorer_metadata"].get("scoring"), Mapping):
            payload["scoring"] = dict(reg["scorer_metadata"]["scoring"])
        append_jsonl_atomic(ledger, [payload])
        identities.add(identity)
        added += 1
    return added


class PairedExperimentRunner:
    def __init__(
        self,
        methods: Mapping[str, Callable[..., Any]] | Sequence[MethodSpec],
        *,
        timeout_s: float = 3600,
        ledger_path: str | Path | None = None,
        retries: int = 0,
        frozen_schedule: Sequence[str] | None = None,
        schedule_id: str = "",
        metadata: Mapping[str, Any] | None = None,
        capability: str | Path | None = None,
        admission_receipt: str | Path | None = None,
    ):
        specs = (
            [MethodSpec(k, v) for k, v in methods.items()]
            if isinstance(methods, Mapping)
            else list(methods)
        )
        self.methods = {m.name: m for m in specs}
        unknown = set(self.methods) - set(METHOD_NAMES)
        if unknown:
            raise ValueError(f"unsupported methods: {sorted(unknown)}")
        schedule = (
            tuple(frozen_schedule)
            if frozen_schedule is not None
            else tuple(self.methods)
        )
        if frozen_schedule is None and set(schedule) != set(METHOD_NAMES):
            raise ValueError(
                "default paired schedule must contain all seven methods; pass frozen_schedule explicitly"
            )
        if len(schedule) != len(set(schedule)) or set(schedule) != set(self.methods):
            raise ValueError(
                "frozen schedule must contain each registered method exactly once"
            )
        self.schedule = schedule
        self.schedule_id = (
            schedule_id or hashlib.sha256("|".join(schedule).encode()).hexdigest()
        )
        self.timeout_s = _validate_timeout(timeout_s, "runner timeout_s")
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.retries = max(0, int(retries))
        self.metadata = dict(metadata or {})
        profile = str(self.metadata.get("experiment_profile", "SOFTWARE_FIXTURE"))
        self._fixture_profile = profile == "SOFTWARE_FIXTURE"
        self._g3_evaluation_profile = profile == "DEVELOPMENT_G3_EVALUATION"
        self.capability_source = (
            Path(capability).resolve() if capability is not None else None
        )
        self.admission_receipt_source = (
            Path(admission_receipt).resolve()
            if admission_receipt is not None
            else None
        )
        self.admission_receipt: dict[str, Any] | None = None
        if self._g3_evaluation_profile:
            if self.capability_source is not None:
                raise ValueError(
                    "DEVELOPMENT_G3_EVALUATION rejects legacy joint-gate capability"
                )
            if self.admission_receipt_source is None:
                raise ValueError(
                    "DEVELOPMENT_G3_EVALUATION requires a batch admission receipt"
                )
            from display_project.g3_run_admission import validate_batch_receipt

            loaded = json.loads(
                self.admission_receipt_source.read_text(encoding="utf-8-sig")
            )
            valid_receipt, receipt_reasons = validate_batch_receipt(
                loaded, expected_consumer_id="runner:g3-paired-panel"
            )
            if not valid_receipt:
                raise ValueError(
                    "batch admission receipt invalid: " + "; ".join(receipt_reasons)
                )
            self.admission_receipt = dict(loaded)
        elif not self._fixture_profile:
            if self.capability_source is None:
                raise ValueError(
                    "non-fixture paired runs require a persisted capability source"
                )
            from display_project.gate_audit import verify_joint_gate_permission

            verify_joint_gate_permission(
                self.capability_source, permission="execute_g3_method_panel_allowed"
            )

    def run_video(
        self,
        video: str | Path,
        *,
        video_id: str | None = None,
        expected_frames: int | None = None,
        task_id: str = "UNKNOWN",
        operator_id: str = "UNKNOWN",
        complete_job_id: str = "UNKNOWN",
        source_dataset: str = "UNKNOWN",
        canonical_root_id: str = "UNKNOWN",
        canonical_group_id: str = "UNKNOWN",
        manual_intervention: bool = False,
        randomization: Mapping[str, Any] | None = None,
        unit_token: str | Path | None = None,
    ) -> PairedRun:
        token_payload: dict[str, Any] | None = None
        if self._g3_evaluation_profile:
            if self.admission_receipt_source is None or unit_token is None:
                raise ValueError(
                    "DEVELOPMENT_G3_EVALUATION requires batch receipt and unit token"
                )
            from display_project.g3_run_admission import validate_batch_receipt

            refreshed = json.loads(
                self.admission_receipt_source.read_text(encoding="utf-8-sig")
            )
            valid_receipt, receipt_reasons = validate_batch_receipt(
                refreshed, expected_consumer_id="runner:g3-paired-panel"
            )
            if not valid_receipt:
                raise ValueError(
                    "batch admission receipt invalid: " + "; ".join(receipt_reasons)
                )
            self.admission_receipt = dict(refreshed)
            token_path = Path(unit_token).resolve()
            token_payload = json.loads(token_path.read_text(encoding="utf-8-sig"))
            if not isinstance(token_payload, dict):
                raise ValueError("unit token must contain a JSON object")
        elif not self._fixture_profile:
            from display_project.gate_audit import verify_joint_gate_permission

            verify_joint_gate_permission(
                self.capability_source, permission="execute_g3_method_panel_allowed"
            )
        path = Path(video)
        vid = video_id or path.stem
        source_dataset = str(source_dataset).strip()
        canonical_root_id = str(canonical_root_id).strip()
        operator_id = str(operator_id).strip()
        complete_job_id = str(complete_job_id).strip()
        vid = _clean_identity(vid, "video_id")
        unit_id = make_unit_identity(
            source_dataset=source_dataset,
            canonical_root_id=canonical_root_id,
            operator_id=operator_id,
            complete_job_id=complete_job_id,
            video_id=vid,
            cluster_level=str(self.metadata.get("cluster_level", "operator")),
        )
        if self._g3_evaluation_profile:
            from display_project.g3_run_admission import validate_unit_execution_token

            valid_token, token_reasons = validate_unit_execution_token(
                token_payload,
                self.admission_receipt,
                expected_consumer_id="runner:g3-paired-panel",
                expected_unit_id=unit_id,
            )
            if not valid_token:
                raise ValueError("unit execution token invalid: " + "; ".join(token_reasons))
            if list(self.schedule) != token_payload["method_schedule"]:
                raise ValueError("runner schedule differs from admitted unit schedule")
            admitted_unit = token_payload["unit"]
            expected_metadata = {
                "task_config_hash": admitted_unit["task_config_hash"],
                "split_manifest_sha256": admitted_unit["split_sha256"],
                "dataset_ledger_sha256": admitted_unit["ledger_sha256"],
            }
            for key, expected_value in expected_metadata.items():
                if self.metadata.get(key) != expected_value:
                    raise ValueError(f"runner metadata differs from unit token: {key}")
            if canonical_group_id != admitted_unit["canonical_group_id"]:
                raise ValueError("runner canonical_group_id differs from unit token")
            if vid != admitted_unit["video_id"]:
                raise ValueError("runner video_id differs from unit token")
            scorer_metadata = dict(self.metadata.get("scorer_metadata", {}))
            scorer_metadata.update(
                admission_batch_id=self.admission_receipt["batch_id"],
                admission_receipt_sha256=self.admission_receipt["artifact_sha256"],
                unit_token_id=token_payload["token_id"],
                unit_token_sha256=token_payload["artifact_sha256"],
                admitted_randomization_seed=token_payload["randomization_seed"],
            )
            self.metadata["scorer_metadata"] = scorer_metadata
        try:
            vh = _hash(path)
        except (FileNotFoundError, OSError):
            vh = ""
        if self._g3_evaluation_profile and vh != token_payload["unit"]["video_sha256"]:
            raise ValueError("video hash differs from admitted unit token")
        if self._g3_evaluation_profile:
            if self.ledger_path is None:
                raise ValueError("DEVELOPMENT_G3_EVALUATION requires a paired ledger")
            destination = self.admission_receipt["result_destination"]
            if self.metadata.get("paired_ledger_id") != destination["ledger_id"]:
                raise ValueError("runner ledger id differs from admission receipt")
            if _resolved_path_sha256(self.ledger_path) != destination["ledger_path_sha256"]:
                raise ValueError("runner ledger path differs from admission receipt")
            scorer_metadata = dict(self.metadata.get("scorer_metadata", {}))
            scorer_metadata.update(
                result_ledger_id=destination["ledger_id"],
                result_ledger_path_sha256=destination["ledger_path_sha256"],
            )
            self.metadata["scorer_metadata"] = scorer_metadata
            _claim_g3_unit_token(
                ledger_path=self.ledger_path,
                token=token_payload,
                receipt=self.admission_receipt,
                unit_id=unit_id,
            )
        valid, decoded, decoded_hashes = _decode_video(path) if vh else (False, 0, [])
        expected = expected_frames if expected_frames is not None else decoded
        mismatch = expected_frames is not None and (
            not isinstance(expected_frames, int) or expected_frames != decoded
        )
        attempts: list[AttemptRecord] = []
        if self.ledger_path:
            registration_path = self.ledger_path.with_suffix(
                self.ledger_path.suffix + ".registration.jsonl"
            )
            if self.ledger_path.is_file() and self.ledger_path.stat().st_size > 0:
                if not registration_path.is_file():
                    raise ValueError(
                        "non-empty paired ledger requires registration sidecar"
                    )
                ledger_errors, _ledger_tail = verify_ledger_integrity(
                    self.ledger_path.read_bytes()
                )
                if ledger_errors:
                    raise ValueError("existing paired ledger integrity check failed")
            existing_registrations: list[dict[str, Any]] = []
            previous_record_hash = ""
            if registration_path.exists():
                existing_registrations, previous_record_hash = (
                    validate_registration_records(registration_path)
                )
            registrations = []
            pending_identities = {f"{unit_id}:{method}:1" for method in self.schedule}
            existing_identities = {
                str(row["attempt_identity"]) for row in existing_registrations
            }
            if pending_identities & existing_identities:
                raise ValueError("attempt identity already registered")
            for method in self.schedule:
                spec = self.methods[method]
                artifacts = _attempt_artifacts(spec, path, 0, self.metadata) or {}
                registrations.append(
                    {
                        "record_kind": "schedule_registration",
                        "registration_id": f"{unit_id}:{method}:1",
                        "attempt_identity": f"{unit_id}:{method}:1",
                        "video_id": vid,
                        "method": method,
                        "attempt": 1,
                        "primary": True,
                        "retry_of": None,
                        "video_hash": vh,
                        "source_video_path": str(path.resolve()),
                        "expected_frames": expected,
                        "schedule_id": self.schedule_id,
                        "schedule": list(self.schedule),
                        "source_dataset": source_dataset,
                        "canonical_root_id": canonical_root_id,
                        "canonical_group_id": canonical_group_id,
                        "unit_identity": unit_id,
                        "operator_id": operator_id,
                        "complete_job_id": complete_job_id,
                        "task_id": task_id,
                        "model_hash": spec.model_hash
                        or self.metadata.get("model_hash", ""),
                        "config_hash": spec.config_hash
                        or self.metadata.get("config_hash", ""),
                        "effective_policy": artifacts.get("effective_policy")
                        or "not_applicable",
                        "artifact_paths": artifacts,
                        "scorer_metadata": dict(
                            self.metadata.get("scorer_metadata", {})
                        ),
                        "status": "registered",
                    }
                )
            sequence_offset = len(existing_registrations)
            for offset, registration in enumerate(registrations):
                sequence = sequence_offset + offset
                registration["schema_version"] = 1
                registration["record_type"] = "schedule_registration"
                registration["sequence"] = sequence
                registration["prev_hash"] = previous_record_hash
                registration["record_hash"] = hashlib.sha256(
                    _canonical_json(registration).encode("utf-8")
                ).hexdigest()
                validate_registration_candidate(
                    registration, sequence=sequence, previous=previous_record_hash
                )
                previous_record_hash = str(registration["record_hash"])
            append_jsonl_atomic(registration_path, registrations)
        for name in self.schedule:
            spec = self.methods[name]
            if mismatch:
                rejection = self._record_rejection(
                    path,
                    vid,
                    vh,
                    expected,
                    spec,
                    task_id,
                    operator_id,
                    complete_job_id,
                    source_dataset,
                    canonical_root_id,
                    canonical_group_id,
                    randomization or {},
                    "EXPECTED_FRAMES_MISMATCH",
                    f"expected_frames={expected}, decoded={decoded}",
                )
                attempts.append(rejection)
                if self.ledger_path:
                    row = asdict(rejection)
                    row["record_kind"] = "attempt_outcome"
                    row["attempt_identity"] = (
                        f"{unit_id}:{rejection.method}:{rejection.attempt}"
                    )
                    row["source_video_path"] = str(path.resolve())
                    row["artifact_paths"] = (
                        _attempt_artifacts(spec, path, 0, self.metadata) or {}
                    )
                    row["effective_policy"] = (
                        row["artifact_paths"].get("effective_policy")
                        or "not_applicable"
                    )
                    row["scorer_metadata"] = dict(
                        self.metadata.get("scorer_metadata", {})
                    )
                    append_jsonl_atomic(self.ledger_path, [row])
                continue
            for retry in range(self.retries + 1):
                if retry > 0 and self.ledger_path:
                    registration_path = self.ledger_path.with_suffix(
                        self.ledger_path.suffix + ".registration.jsonl"
                    )
                    existing, registration_tail = validate_registration_records(
                        registration_path
                    )
                    identity = f"{unit_id}:{name}:{retry + 1}"
                    if any(row["attempt_identity"] == identity for row in existing):
                        raise ValueError("retry attempt identity already registered")
                    base = next(row for row in registrations if row["method"] == name)
                    retry_registration = {
                        key: value
                        for key, value in base.items()
                        if key not in {"record_hash", "sequence", "prev_hash"}
                    }
                    retry_registration.update(
                        {
                            "registration_id": identity,
                            "attempt_identity": identity,
                            "attempt": retry + 1,
                            "primary": False,
                            "retry_of": retry,
                            "artifact_paths": _attempt_artifacts(
                                spec, path, retry, self.metadata
                            )
                            or {},
                            "sequence": len(existing),
                            "prev_hash": registration_tail,
                        }
                    )
                    retry_registration["record_hash"] = hashlib.sha256(
                        _canonical_json(retry_registration).encode("utf-8")
                    ).hexdigest()
                    validate_registration_candidate(
                        retry_registration,
                        sequence=len(existing),
                        previous=registration_tail,
                    )
                    append_jsonl_atomic(registration_path, [retry_registration])
                r = self._attempt(
                    path,
                    vid,
                    vh,
                    expected,
                    spec,
                    retry,
                    valid,
                    decoded,
                    decoded_hashes,
                    task_id,
                    operator_id,
                    complete_job_id,
                    source_dataset,
                    canonical_root_id,
                    canonical_group_id,
                    manual_intervention,
                    randomization or {},
                )
                attempts.append(r)
                if self.ledger_path:
                    row = asdict(r)
                    row["record_kind"] = "attempt_outcome"
                    row["attempt_identity"] = f"{unit_id}:{r.method}:{r.attempt}"
                    row["source_video_path"] = str(path.resolve())
                    row["artifact_paths"] = (
                        _attempt_artifacts(spec, path, r.attempt - 1, self.metadata)
                        or {}
                    )
                    row["effective_policy"] = (
                        row["artifact_paths"].get("effective_policy")
                        or "not_applicable"
                    )
                    row["scorer_metadata"] = dict(
                        self.metadata.get("scorer_metadata", {})
                    )
                    append_jsonl_atomic(self.ledger_path, [row])
                if r.status in {"success", "unavailable"}:
                    break
        run = PairedRun(vid, vh, expected, attempts, self.schedule_id)
        if self._g3_evaluation_profile:
            _complete_g3_unit_token(
                ledger_path=self.ledger_path,
                token=token_payload,
                receipt=self.admission_receipt,
                unit_id=unit_id,
                attempts=attempts,
            )
        return run

    def _record_rejection(
        self,
        path,
        vid,
        vh,
        expected,
        spec,
        task,
        operator,
        job,
        source_dataset,
        canonical_root_id,
        canonical_group_id,
        randomization,
        code,
        error,
    ):
        return AttemptRecord(
            video_id=vid,
            method=spec.name,
            status="missing_frames",
            source_dataset=source_dataset,
            canonical_root_id=canonical_root_id,
            canonical_group_id=canonical_group_id,
            unit_identity=make_unit_identity(
                source_dataset=source_dataset,
                canonical_root_id=canonical_root_id,
                operator_id=operator,
                complete_job_id=job,
                video_id=vid,
            ),
            attempt=1,
            first_attempt=True,
            primary=True,
            lineage_id=f"{make_unit_identity(source_dataset=source_dataset, canonical_root_id=canonical_root_id, operator_id=operator, complete_job_id=job, video_id=vid)}:{spec.name}",
            task_id=task,
            operator_id=operator,
            complete_job_id=job,
            video_hash=vh,
            expected_frames=expected,
            frames_processed=0,
            complete_video=False,
            code_hash=spec.code_hash or self.metadata.get("code_hash", ""),
            config_hash=spec.config_hash or self.metadata.get("config_hash", ""),
            model_hash=spec.model_hash or self.metadata.get("model_hash", ""),
            environment_hash=self.metadata.get("environment_hash", ""),
            annotation_hash=self.metadata.get("annotation_hash", ""),
            partition_hash=self.metadata.get("partition_hash", ""),
            manifest_hash=self.metadata.get("manifest_hash", ""),
            task_config_hash=self.metadata.get("task_config_hash", ""),
            task_source_hash=self.metadata.get("task_source_hash", ""),
            experiment_profile=str(
                self.metadata.get("experiment_profile", "SOFTWARE_FIXTURE")
            ),
            seed=spec.seed,
            randomization=dict(randomization),
            resources=_resource_schema(
                0.0, 0, termination_kind="missing_frames", reason=error
            ),
            error=error,
            error_code=code,
            schedule_id=self.schedule_id,
            schedule=self.schedule,
        )

    def _attempt(
        self,
        path,
        vid,
        vh,
        expected,
        spec,
        retry,
        valid,
        decoded,
        decoded_hashes,
        task,
        operator,
        job,
        source_dataset,
        canonical_root_id,
        canonical_group_id,
        manual,
        randomization,
    ):
        started = time.time()
        status = "success"
        error = None
        error_code = None
        out = None
        events: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        frames = 0
        artifacts: dict[str, str] | None = None
        if manual:
            status, error, error_code = (
                "manual_intervention",
                "manual intervention required",
                "MANUAL_INTERVENTION",
            )
        elif not valid:
            status, error, error_code = (
                "unreadable",
                "video could not be fully decoded",
                "VIDEO_UNREADABLE",
            )
        else:
            ctx = mp.get_context("spawn")
            q = ctx.Queue()
            artifacts = _attempt_artifacts(spec, path, retry, self.metadata)
            if artifacts is not None and any(
                Path(value).exists()
                for key, value in artifacts.items()
                if key in {"output", "events", "certificates"}
            ):
                status, error, error_code = (
                    "unavailable",
                    "attempt artifact already exists",
                    "OUTPUT_ALREADY_EXISTS",
                )
                proc = None
            else:
                run_context = {
                    "input_canonical_group": hashlib.sha256(
                        str(canonical_group_id).encode("utf-8")
                    ).hexdigest(),
                    "source_dataset": source_dataset,
                    "canonical_root_id": canonical_root_id,
                    "canonical_group_id": canonical_group_id,
                    "canonical_group_commitment": hashlib.sha256(
                        str(canonical_group_id).encode("utf-8")
                    ).hexdigest(),
                    "operator_id": operator,
                    "complete_job_id": job,
                    "task_id": task,
                    "video_hash": vh,
                    "partition_hash": self.metadata.get("partition_hash", ""),
                }
                proc = ctx.Process(
                    target=_invoke,
                    args=(
                        spec.runner,
                        str(path),
                        spec.seed,
                        retry,
                        q,
                        artifacts,
                        run_context,
                    ),
                )
            started_child = False
            if proc is not None:
                try:
                    proc.start()
                    started_child = True
                except Exception as exc:
                    status, error, error_code = (
                        "crash",
                        f"child start failed: {exc}",
                        "CHILD_START_FAILED",
                    )
            if started_child:
                timeout_s = (
                    spec.timeout_s if spec.timeout_s is not None else self.timeout_s
                )
                deadline = time.monotonic() + timeout_s
                tag = val = None
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    try:
                        tag, val = q.get(timeout=min(0.1, remaining))
                        break
                    except Empty:
                        if not proc.is_alive():
                            try:
                                tag, val = q.get(timeout=min(0.1, remaining))
                            except Empty:
                                tag, val, exit_code = _classify_exit(proc.exitcode)
                                error_code = exit_code
                            break

                if tag is None:
                    proc.terminate()
                    proc.join()
                    status, error, error_code = (
                        "timeout",
                        "timeout (child process terminated)",
                        "TIMEOUT",
                    )
                else:
                    proc.join(max(0.0, deadline - time.monotonic()))
                    if proc.is_alive():
                        proc.terminate()
                        proc.join()
                        status, error, error_code = (
                            "timeout",
                            "timeout after child result (child process terminated)",
                            "TIMEOUT",
                        )
                    elif tag == "ok":
                        status, events, partial, frames, error, error_code, out = (
                            _normalise_child_result(
                                val,
                                expected=expected,
                                decoded=decoded,
                                decoded_hashes=decoded_hashes,
                                video_hash=vh,
                                method=spec.name,
                            )
                        )
                    elif tag in {"oom", "crash"}:
                        status, error, error_code = tag, str(val), tag.upper()
                    else:
                        status, error, error_code = (
                            "crash",
                            str(val),
                            "CHILD_RESULT_TAG",
                        )
        if status in {"timeout", "crash", "oom"} and artifacts is not None:
            (
                recovered_events,
                recovered_partial,
                recovered_frames,
                recover_error,
                recover_code,
                artifact_refs,
            ) = _recover_partial_artifacts(
                artifacts,
                decoded_hashes=decoded_hashes,
                expected_input_hash=vh,
                expected_model_hash=spec.model_hash
                or self.metadata.get("model_hash", ""),
                expected_config_hash=spec.config_hash
                or self.metadata.get("config_hash", ""),
            )
            if recover_code is not None:
                error_code = recover_code
                error = recover_error
                events, partial, frames = [], [], 0
            else:
                events, partial, frames = (
                    recovered_events,
                    recovered_partial,
                    recovered_frames,
                )
                out = (
                    {
                        "artifact_refs": artifact_refs,
                        "authority_boundary": "runner_verified_no_annotation_or_truth_authority",
                    }
                    if artifact_refs
                    else {
                        "authority_boundary": "runner_verified_no_annotation_or_truth_authority"
                    }
                )
        complete = bool(valid and frames == expected and status == "success")
        return AttemptRecord(
            video_id=vid,
            method=spec.name,
            status=status,
            source_dataset=source_dataset
            if source_dataset != "UNKNOWN"
            else str(self.metadata.get("source_dataset", "UNKNOWN")),
            canonical_root_id=canonical_root_id
            if canonical_root_id != "UNKNOWN"
            else str(self.metadata.get("canonical_root_id", "UNKNOWN")),
            canonical_group_id=canonical_group_id
            if canonical_group_id != "UNKNOWN"
            else str(self.metadata.get("canonical_group_id", "UNKNOWN")),
            unit_identity=make_unit_identity(
                source_dataset=source_dataset,
                canonical_root_id=canonical_root_id,
                operator_id=operator,
                complete_job_id=job,
                video_id=vid,
            ),
            attempt=retry + 1,
            first_attempt=retry == 0,
            primary=retry == 0,
            retry_of=retry if retry else None,
            lineage_id=f"{make_unit_identity(source_dataset=source_dataset, canonical_root_id=canonical_root_id, operator_id=operator, complete_job_id=job, video_id=vid)}:{spec.name}",
            task_id=task,
            operator_id=operator,
            complete_job_id=job,
            video_hash=vh,
            expected_frames=expected,
            frames_processed=frames,
            complete_video=complete,
            code_hash=spec.code_hash or self.metadata.get("code_hash", ""),
            config_hash=spec.config_hash or self.metadata.get("config_hash", ""),
            model_hash=spec.model_hash or self.metadata.get("model_hash", ""),
            environment_hash=self.metadata.get("environment_hash", ""),
            annotation_hash=self.metadata.get("annotation_hash", ""),
            partition_hash=self.metadata.get("partition_hash", ""),
            manifest_hash=self.metadata.get("manifest_hash", ""),
            task_config_hash=self.metadata.get("task_config_hash", ""),
            task_source_hash=self.metadata.get("task_source_hash", ""),
            experiment_profile=str(
                self.metadata.get("experiment_profile", "SOFTWARE_FIXTURE")
            ),
            seed=spec.seed,
            randomization=dict(randomization),
            started_at=started,
            elapsed_s=time.time() - started,
            resources=_resource_schema(
                time.time() - started,
                frames,
                termination_kind=status,
                reason=error or "",
            ),
            events=events,
            partial_events=partial,
            error=error,
            error_code=error_code,
            output=out,
            schedule_id=self.schedule_id,
            schedule=self.schedule,
        )
