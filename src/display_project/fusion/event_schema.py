"""Dependency-free event schema and JSONL validation for SOP decisions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from display_project.evidence.certificate import AUTHORIZING_EVIDENCE_SOURCES

Decision = Literal["normal", "deviation", "unknown"]
EvidenceSource = Literal["deterministic", "vlm", "none"]

_EVENT_FIELDS = {
    "schema_version",
    "event_id",
    "timestamp_ms",
    "decision",
    "expected_step",
    "observed_step",
    "previous_step",
    "current_step",
    "reason",
    "evidence_source",
    "evidence",
    "certificate_hash",
    "graph_digest",
    "certificate_artifact_ref",
    "frame",
    "source_frame_sha256",
}
_REQUIRED_EVENT_FIELDS = _EVENT_FIELDS - {
    "certificate_hash",
    "graph_digest",
    "certificate_artifact_ref",
    "frame",
    "source_frame_sha256",
}
_DECISIONS = {"normal", "deviation", "unknown"}
_EVIDENCE_SOURCES = {"deterministic", "vlm", "none"}


class EventValidationError(ValueError):
    """Raised when an event or JSONL record violates the event contract."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    source: str
    kind: str
    confidence: float
    value: Any


@dataclass(frozen=True, slots=True)
class SOPEvent:
    schema_version: str
    event_id: str
    timestamp_ms: int
    decision: Decision
    expected_step: str | None
    observed_step: str | None
    previous_step: str | None
    current_step: str | None
    reason: str
    evidence_source: EvidenceSource
    evidence: tuple[EvidenceItem, ...]
    certificate_hash: str | None = None
    graph_digest: str | None = None
    certificate_artifact_ref: str | None = None
    frame: int | None = None
    source_frame_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [asdict(item) for item in self.evidence]
        payload = {
            key: value
            for key, value in payload.items()
            if value is not None
            or key
            not in {
                "certificate_hash",
                "graph_digest",
                "certificate_artifact_ref",
                "frame",
                "source_frame_sha256",
            }
        }
        return payload


def _require_string(value: object, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(f"{field} must be a non-empty string")
    return value


def _validate_evidence(value: object) -> tuple[EvidenceItem, ...]:
    if not isinstance(value, list):
        raise EventValidationError("evidence must be an array")

    items: list[EvidenceItem] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise EventValidationError(f"evidence[{index}] must be an object")
        expected_fields = {"source", "kind", "confidence", "value"}
        if set(raw) != expected_fields:
            raise EventValidationError(
                f"evidence[{index}] fields must be {sorted(expected_fields)}"
            )
        source = _require_string(raw["source"], f"evidence[{index}].source")
        kind = _require_string(raw["kind"], f"evidence[{index}].kind")
        if source not in AUTHORIZING_EVIDENCE_SOURCES:
            raise EventValidationError(
                f"evidence[{index}].source is not deterministic authority"
            )
        confidence = raw["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise EventValidationError(f"evidence[{index}].confidence must be numeric")
        if not 0.0 <= float(confidence) <= 1.0:
            raise EventValidationError(
                f"evidence[{index}].confidence must be between 0 and 1"
            )
        if source is None or kind is None:
            raise EventValidationError(f"evidence[{index}] source/kind is missing")
        items.append(
            EvidenceItem(
                source=source,
                kind=kind,
                confidence=float(confidence),
                value=raw["value"],
            )
        )
    return tuple(items)


def validate_event(payload: Mapping[str, Any]) -> SOPEvent:
    if not isinstance(payload, Mapping):
        raise EventValidationError("event must be an object")
    if not _REQUIRED_EVENT_FIELDS.issubset(payload) or set(payload) - _EVENT_FIELDS:
        missing = sorted(_REQUIRED_EVENT_FIELDS - set(payload))
        extra = sorted(set(payload) - _EVENT_FIELDS)
        raise EventValidationError(
            f"event fields mismatch; missing={missing}, extra={extra}"
        )

    schema_version = _require_string(payload["schema_version"], "schema_version")
    if schema_version != "1.0":
        raise EventValidationError("schema_version must be '1.0'")
    event_id = _require_string(payload["event_id"], "event_id")
    timestamp_ms = payload["timestamp_ms"]
    if (
        isinstance(timestamp_ms, bool)
        or not isinstance(timestamp_ms, int)
        or timestamp_ms < 0
    ):
        raise EventValidationError("timestamp_ms must be a non-negative integer")

    decision = payload["decision"]
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise EventValidationError(f"decision must be one of {sorted(_DECISIONS)}")
    evidence_source = payload["evidence_source"]
    if not isinstance(evidence_source, str) or evidence_source not in _EVIDENCE_SOURCES:
        raise EventValidationError(
            f"evidence_source must be one of {sorted(_EVIDENCE_SOURCES)}"
        )

    expected_step = _require_string(
        payload["expected_step"], "expected_step", nullable=True
    )
    observed_step = _require_string(
        payload["observed_step"], "observed_step", nullable=True
    )
    previous_step = _require_string(
        payload["previous_step"], "previous_step", nullable=True
    )
    current_step = _require_string(
        payload["current_step"], "current_step", nullable=True
    )
    reason = _require_string(payload["reason"], "reason")
    evidence = _validate_evidence(payload["evidence"])
    frame = payload.get("frame")
    source_frame_sha256 = payload.get("source_frame_sha256")
    if (frame is None) != (source_frame_sha256 is None):
        raise EventValidationError("frame and source_frame_sha256 must appear together")
    if frame is not None and (
        isinstance(frame, bool) or not isinstance(frame, int) or frame < 0
    ):
        raise EventValidationError("frame must be a non-negative integer")
    if source_frame_sha256 is not None:
        import re

        if not isinstance(source_frame_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", source_frame_sha256
        ):
            raise EventValidationError(
                "source_frame_sha256 must be lowercase SHA-256 hex"
            )

    cert_fields = (
        payload.get("certificate_hash"),
        payload.get("graph_digest"),
        payload.get("certificate_artifact_ref"),
    )
    if any(value is not None for value in cert_fields) and not all(
        isinstance(value, str) and value.strip() for value in cert_fields
    ):
        raise EventValidationError(
            "advancement certificate fields must be non-empty together"
        )

    if decision == "unknown" and observed_step is not None:
        raise EventValidationError("unknown events must not assert an observed_step")
    if decision != "unknown" and evidence_source != "deterministic":
        raise EventValidationError(
            "normal/deviation decisions require deterministic evidence"
        )

    if schema_version is None or event_id is None or reason is None:
        raise EventValidationError("required event strings are missing")
    return SOPEvent(
        schema_version=schema_version,
        event_id=event_id,
        timestamp_ms=timestamp_ms,
        decision=decision,
        expected_step=expected_step,
        observed_step=observed_step,
        previous_step=previous_step,
        current_step=current_step,
        reason=reason,
        evidence_source=evidence_source,
        evidence=evidence,
        certificate_hash=payload.get("certificate_hash"),
        graph_digest=payload.get("graph_digest"),
        certificate_artifact_ref=payload.get("certificate_artifact_ref"),
        frame=frame,
        source_frame_sha256=source_frame_sha256,
    )


def validate_jsonl(path: str | Path) -> list[SOPEvent]:
    events: list[SOPEvent] = []
    source = Path(path)
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                events.append(validate_event(payload))
            except (json.JSONDecodeError, EventValidationError) as exc:
                raise EventValidationError(f"line {line_number}: {exc}") from exc
    return events
