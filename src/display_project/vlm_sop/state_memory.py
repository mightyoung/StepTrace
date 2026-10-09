from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from .evidence_window import EvidenceWindowError, validate_window


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}\Z")
_FORBIDDEN_INFERENCE_KEYS = frozenset({"execution_state", "observed_evidence", "state_write", "state_writes"})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class StateMemory:
    def __init__(self, task_id: str | None = None, *, max_observations: int = 128, max_estimates: int = 128) -> None:
        if task_id is not None and (not isinstance(task_id, str) or _OPAQUE_ID.fullmatch(task_id) is None):
            raise ValueError("task_id must be an opaque anonymous identifier when supplied")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (max_observations, max_estimates)):
            raise ValueError("memory capacities must be positive integers")
        self.task_id = task_id
        self._max_observations = max_observations
        self._max_estimates = max_estimates
        self._observations: list[dict[str, Any]] = []
        self._estimates: list[dict[str, Any]] = []
        self._observation_fingerprints: set[str] = set()
        self._request_fingerprints: dict[str, str] = {}
        self._window_times: dict[str, float] = {}
        self._windows_by_id: dict[str, dict[str, object]] = {}
        self._observation_version = 0
        self._estimate_version = 0
        self._failure_watermark: float | None = None
        self._current: dict[str, Any] | None = None

    @property
    def observation_history(self) -> tuple[Mapping[str, object], ...]:
        return tuple(MappingProxyType(deepcopy(row)) for row in self._observations)

    @property
    def estimate_history(self) -> tuple[Mapping[str, object], ...]:
        return tuple(MappingProxyType(deepcopy(row)) for row in self._estimates)

    @property
    def current_estimate(self) -> Mapping[str, object] | None:
        return MappingProxyType(deepcopy(self._current)) if self._current is not None else None

    def record_window(self, window: Mapping[str, object]) -> bool:
        checked = validate_window(window)
        if self.task_id is None:
            self.task_id = checked["task_id"]
        if checked["task_id"] != self.task_id:
            raise EvidenceWindowError("window task_id does not match StateMemory")
        window_id = str(checked["window_id"])
        prior_window = self._windows_by_id.get(window_id)
        if prior_window is not None:
            if prior_window != checked:
                raise EvidenceWindowError("window_id cannot be replayed with different evidence facts")
            return False
        self._windows_by_id[window_id] = deepcopy(checked)
        self._window_times[window_id] = float(checked["decision_time"])
        fact_snapshot = dict(checked)
        fact_snapshot.pop("window_id")
        fact_snapshot.pop("decision_time")
        fingerprint = _digest(fact_snapshot)
        if fingerprint in self._observation_fingerprints:
            return False
        self._observation_fingerprints.add(fingerprint)
        self._observation_version += 1
        self._observations.append({"version": self._observation_version, "window": checked, "window_sha256": fingerprint})
        return True

    def append_estimate(self, inference: Mapping[str, object], *, model_sha256: str, request_id: str | None = None) -> Mapping[str, object]:
        if not isinstance(inference, Mapping):
            raise ValueError("inference must be a mapping")
        copied = json.loads(_canonical(inference))
        self._reject_forbidden_inference_fields(copied)
        candidate_task = copied.get("task_id")
        window_id = copied.get("window_id")
        if self.task_id is None and isinstance(candidate_task, str) and _OPAQUE_ID.fullmatch(candidate_task):
            self.task_id = candidate_task
        if copied.get("task_id") != self.task_id or not isinstance(window_id, str) or _OPAQUE_ID.fullmatch(window_id) is None:
            raise ValueError("inference task_id and window_id are required and must match")
        if copied.get("authority") != "state_estimation_only":
            raise ValueError("inference authority must be state_estimation_only")
        if not isinstance(model_sha256, str) or _HASH.fullmatch(model_sha256) is None:
            raise ValueError("model_sha256 must be lowercase SHA-256 hex")
        if request_id is not None and (not isinstance(request_id, str) or _OPAQUE_ID.fullmatch(request_id) is None):
            raise ValueError("request_id must be an opaque anonymous identifier when supplied")
        decision_time = self._decision_time(copied.get("decision_time"), field="inference.decision_time")
        recorded_time = self._window_times.get(window_id)
        if recorded_time is None:
            raise ValueError("inference window_id must reference a recorded evidence window")
        if decision_time != recorded_time:
            raise ValueError("inference.decision_time must match its recorded evidence window")
        fingerprint = _digest({"inference": copied, "model_sha256": model_sha256})
        if request_id is not None:
            existing = self._request_fingerprints.get(request_id)
            if existing is not None:
                if existing != fingerprint:
                    raise ValueError("request_id cannot be replayed with different estimate facts")
                return next(MappingProxyType(deepcopy(row)) for row in self._estimates if row.get("request_id") == request_id)
            self._request_fingerprints[request_id] = fingerprint
        self._estimate_version += 1
        record = {"version": self._estimate_version, "kind": "estimate", "inference": copied, "decision_time": decision_time, "model_sha256": model_sha256, "request_id": request_id}
        self._estimates.append(record)
        if (self._failure_watermark is None or decision_time >= self._failure_watermark) and (
            self._current is None or decision_time >= self._current["decision_time"]
        ):
            self._current = record
        return MappingProxyType(deepcopy(record))

    def record_failure(self, window_id: str, *, decision_time: float, reason: str) -> Mapping[str, object]:
        if not isinstance(window_id, str) or _OPAQUE_ID.fullmatch(window_id) is None or not isinstance(reason, str) or not reason.strip():
            raise ValueError("window_id must be opaque and reason must be non-empty")
        moment = self._decision_time(decision_time, field="decision_time")
        recorded_time = self._window_times.get(window_id)
        if recorded_time is None:
            raise ValueError("failure window_id must reference a recorded evidence window")
        if moment != recorded_time:
            raise ValueError("failure decision_time must match its recorded evidence window")
        self._estimate_version += 1
        record = {"version": self._estimate_version, "kind": "failure", "window_id": window_id, "decision_time": moment, "reason": reason}
        self._estimates.append(record)
        self._failure_watermark = max(self._failure_watermark, moment) if self._failure_watermark is not None else moment
        if self._current is not None and moment >= self._current["decision_time"]:
            self._current = None
        return MappingProxyType(deepcopy(record))

    def prompt_history(self, before_time: float) -> dict[str, list[dict[str, object]]]:
        deadline = self._decision_time(before_time, field="before_time")
        observations = [deepcopy(row["window"]) for row in self._observations if row["window"]["decision_time"] < deadline]
        estimates = [deepcopy(row["inference"]) for row in self._estimates if row["kind"] == "estimate" and row["decision_time"] < deadline]
        failures = [
            {"window_id": row["window_id"], "decision_time": row["decision_time"], "status": "missing"}
            for row in self._estimates
            if row["kind"] == "failure" and row["decision_time"] < deadline
        ]
        observations = observations[-self._max_observations:]
        estimates = estimates[-self._max_estimates:]
        return {"observations": observations, "estimates": estimates, "failures": failures[-self._max_estimates:]}

    @staticmethod
    def _reject_forbidden_inference_fields(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).casefold() in _FORBIDDEN_INFERENCE_KEYS:
                    raise ValueError(f"inference contains forbidden field: {key}")
                StateMemory._reject_forbidden_inference_fields(child)
        elif isinstance(value, list):
            for child in value:
                StateMemory._reject_forbidden_inference_fields(child)

    @staticmethod
    def _decision_time(value: object, *, field: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{field} must be a finite number")
        return float(value)
