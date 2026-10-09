"""Temporal, tamper-evident snapshot of current research gate predicates."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping


CURRENT_GATE_SNAPSHOT_SCHEMA = "display_project.current_gate_snapshot-1.0.0"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LOGICAL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_./-]*$")
PREDICATE_KEYS = (
    "g1_prerequisite",
    "g2_prerequisite",
    "implementation_readiness",
    "g3_run_admission",
    "g3_decision",
    "downstream_admission",
)
PREDICATE_VALUES = {
    "g1_prerequisite": {"PASS", "BLOCKED"},
    "g2_prerequisite": {"PASS", "BLOCKED"},
    "implementation_readiness": {"READY", "BLOCKED"},
    "g3_run_admission": {"READY", "BLOCKED"},
    "g3_decision": {"PASS", "FAIL", "BLOCKED", "NOT_RUN"},
    "downstream_admission": {"READY", "BLOCKED"},
}
TRANSITIONS = {
    "INITIAL_BLOCKED",
    "PREREQUISITE_REFRESH",
    "IMPLEMENTATION_ADVANCE",
    "ADMISSION_ADVANCE",
    "DECISION_RECORDED",
    "REVOCATION",
}


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _logical_id_valid(value: object) -> bool:
    if not isinstance(value, str) or LOGICAL_ID_RE.fullmatch(value) is None:
        return False
    _, path = value.split(":", 1)
    return all(part not in {"", ".", ".."} for part in path.split("/"))


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {key: value for key, value in snapshot.items() if key != "artifact_sha256"}
    )


def _predicate_reasons(predicates: object) -> list[str]:
    if not isinstance(predicates, Mapping) or set(predicates) != set(PREDICATE_KEYS):
        return ["PREDICATE_SCHEMA_INVALID"]
    reasons = [
        f"PREDICATE_VALUE_INVALID:{key}"
        for key, allowed in PREDICATE_VALUES.items()
        if predicates.get(key) not in allowed
    ]
    if predicates.get("g3_run_admission") == "READY" and not (
        predicates.get("g1_prerequisite") == "PASS"
        and predicates.get("g2_prerequisite") == "PASS"
        and predicates.get("implementation_readiness") == "READY"
    ):
        reasons.append("RUN_ADMISSION_PREREQUISITES_INCONSISTENT")
    if predicates.get("g3_decision") in {"PASS", "FAIL"} and predicates.get(
        "g3_run_admission"
    ) != "READY":
        reasons.append("G3_DECISION_WITHOUT_ADMISSION")
    if predicates.get("downstream_admission") == "READY" and predicates.get(
        "g3_decision"
    ) != "PASS":
        reasons.append("DOWNSTREAM_WITHOUT_G3_PASS")
    return reasons


def _source_reasons(sources: object) -> list[str]:
    if not isinstance(sources, list) or not sources:
        return ["SOURCE_EVIDENCE_MISSING"]
    required = {"logical_id", "schema_version", "sha256", "source_timestamp"}
    if any(not isinstance(item, Mapping) or set(item) != required for item in sources):
        return ["SOURCE_EVIDENCE_SCHEMA_INVALID"]
    identifiers = [item.get("logical_id") for item in sources]
    reasons: list[str] = []
    if (
        any(not _logical_id_valid(item) for item in identifiers)
        or len(set(identifiers)) != len(identifiers)
        or len({str(item).casefold() for item in identifiers}) != len(identifiers)
    ):
        reasons.append("SOURCE_LOGICAL_IDS_INVALID")
    if any(
        not isinstance(item.get("schema_version"), str)
        or not str(item.get("schema_version")).strip()
        or not isinstance(item.get("sha256"), str)
        or HASH_RE.fullmatch(item["sha256"]) is None
        or _timestamp(item.get("source_timestamp")) is None
        for item in sources
    ):
        reasons.append("SOURCE_EVIDENCE_VALUES_INVALID")
    return reasons


def _transition_reasons(
    snapshot: Mapping[str, Any], predecessor: Mapping[str, Any] | None
) -> list[str]:
    event = snapshot.get("transition_event")
    current = snapshot.get("predicates")
    if event not in TRANSITIONS or not isinstance(current, Mapping):
        return ["TRANSITION_EVENT_INVALID"]
    if predecessor is None:
        if snapshot.get("predecessor") is not None:
            return ["PREDECESSOR_EVIDENCE_MISSING"]
        initial = {
            "g1_prerequisite": "BLOCKED",
            "g2_prerequisite": "BLOCKED",
            "implementation_readiness": "BLOCKED",
            "g3_run_admission": "BLOCKED",
            "g3_decision": "NOT_RUN",
            "downstream_admission": "BLOCKED",
        }
        return [] if event == "INITIAL_BLOCKED" and dict(current) == initial else [
            "INITIAL_TRANSITION_INVALID"
        ]
    link = snapshot.get("predecessor")
    if not isinstance(link, Mapping) or set(link) != {
        "snapshot_id",
        "artifact_sha256",
        "observed_at",
    }:
        return ["PREDECESSOR_LINK_INVALID"]
    if (
        link.get("snapshot_id") != predecessor.get("snapshot_id")
        or link.get("artifact_sha256") != predecessor.get("artifact_sha256")
        or link.get("observed_at") != predecessor.get("observed_at")
        or predecessor.get("artifact_sha256") != _snapshot_hash(predecessor)
    ):
        return ["PREDECESSOR_LINK_INVALID"]
    previous = predecessor.get("predicates")
    if not isinstance(previous, Mapping):
        return ["PREDECESSOR_PREDICATES_INVALID"]
    changed = {key for key in PREDICATE_KEYS if previous.get(key) != current.get(key)}
    if event == "PREREQUISITE_REFRESH":
        allowed = {"g1_prerequisite", "g2_prerequisite"}
        if not changed or not changed <= allowed or any(
            previous.get(key) != "BLOCKED" or current.get(key) != "PASS"
            for key in changed
        ):
            return ["PREREQUISITE_TRANSITION_INVALID"]
    elif event == "IMPLEMENTATION_ADVANCE":
        if changed != {"implementation_readiness"} or not (
            previous.get("implementation_readiness") == "BLOCKED"
            and current.get("implementation_readiness") == "READY"
        ):
            return ["IMPLEMENTATION_TRANSITION_INVALID"]
    elif event == "ADMISSION_ADVANCE":
        if changed != {"g3_run_admission"} or not (
            previous.get("g3_run_admission") == "BLOCKED"
            and current.get("g3_run_admission") == "READY"
        ):
            return ["ADMISSION_TRANSITION_INVALID"]
    elif event == "DECISION_RECORDED":
        if changed != {"g3_decision"} or current.get("g3_decision") not in {
            "PASS",
            "FAIL",
        }:
            return ["DECISION_TRANSITION_INVALID"]
    elif event == "REVOCATION":
        if not changed or any(
            current.get(key) not in {"BLOCKED", "NOT_RUN"} for key in changed
        ):
            return ["REVOCATION_TRANSITION_INVALID"]
    else:
        return ["TRANSITION_EVENT_INVALID"]
    return []


def build_current_gate_snapshot(
    *,
    snapshot_id: str,
    observed_at: str,
    effective_at: str,
    expires_at: str,
    transition_event: str,
    reason_codes: list[str],
    predicates: Mapping[str, str],
    sources: list[Mapping[str, str]],
    predecessor: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a snapshot only when temporal and lineage checks are satisfied."""
    body: dict[str, Any] = {
        "schema_version": CURRENT_GATE_SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "observed_at": observed_at,
        "effective_at": effective_at,
        "expires_at": expires_at,
        "predecessor": (
            None
            if predecessor is None
            else {
                "snapshot_id": predecessor.get("snapshot_id"),
                "artifact_sha256": predecessor.get("artifact_sha256"),
                "observed_at": predecessor.get("observed_at"),
            }
        ),
        "transition_event": transition_event,
        "reason_codes": list(reason_codes),
        "predicates": dict(predicates),
        "sources": [dict(item) for item in sources],
    }
    body["artifact_sha256"] = _snapshot_hash(body)
    valid, reasons = validate_current_gate_snapshot(
        body, predecessor=predecessor, now=now
    )
    if not valid:
        raise ValueError(reasons[0])
    return body


def validate_current_gate_snapshot(
    snapshot: Mapping[str, Any] | object,
    *,
    predecessor: Mapping[str, Any] | None = None,
    source_payloads: Mapping[str, bytes] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    if not isinstance(snapshot, Mapping):
        return False, ["SNAPSHOT_NOT_MAPPING"]
    expected = {
        "schema_version",
        "snapshot_id",
        "observed_at",
        "effective_at",
        "expires_at",
        "predecessor",
        "transition_event",
        "reason_codes",
        "predicates",
        "sources",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(snapshot) != expected:
        reasons.append("SNAPSHOT_SCHEMA_KEYS_INVALID")
    if snapshot.get("schema_version") != CURRENT_GATE_SNAPSHOT_SCHEMA:
        reasons.append("SNAPSHOT_SCHEMA_VERSION_INVALID")
    if not _logical_id_valid(snapshot.get("snapshot_id")):
        reasons.append("SNAPSHOT_ID_INVALID")
    try:
        if snapshot.get("artifact_sha256") != _snapshot_hash(snapshot):
            reasons.append("SNAPSHOT_HASH_INVALID")
    except (TypeError, ValueError):
        reasons.append("SNAPSHOT_HASH_INVALID")
    observed = _timestamp(snapshot.get("observed_at"))
    effective = _timestamp(snapshot.get("effective_at"))
    expiry = _timestamp(snapshot.get("expires_at"))
    current_time = now or datetime.now(timezone.utc)
    if (
        observed is None
        or effective is None
        or expiry is None
        or observed > effective
        or effective >= expiry
        or observed > current_time
    ):
        reasons.append("SNAPSHOT_TIME_INVALID")
    predecessor_observed = (
        _timestamp(predecessor.get("observed_at"))
        if isinstance(predecessor, Mapping)
        else None
    )
    if observed is not None and predecessor_observed is not None and observed < predecessor_observed:
        reasons.append("SNAPSHOT_TIME_REGRESSION")
    reasons.extend(_predicate_reasons(snapshot.get("predicates")))
    reasons.extend(_source_reasons(snapshot.get("sources")))
    reasons.extend(_transition_reasons(snapshot, predecessor))
    if not isinstance(snapshot.get("reason_codes"), list) or not snapshot.get(
        "reason_codes"
    ):
        reasons.append("REASON_CODES_INVALID")
    if source_payloads is not None and isinstance(snapshot.get("sources"), list):
        committed = {
            item.get("logical_id"): item.get("sha256")
            for item in snapshot["sources"]
            if isinstance(item, Mapping)
        }
        if set(source_payloads) != set(committed) or any(
            hashlib.sha256(payload).hexdigest() != committed.get(logical_id)
            for logical_id, payload in source_payloads.items()
        ):
            reasons.append("SOURCE_HASH_STALE")
    return not reasons, reasons
