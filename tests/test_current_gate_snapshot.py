from __future__ import annotations

from datetime import datetime, timezone

import pytest

from display_project.current_gate_snapshot import (
    build_current_gate_snapshot,
    validate_current_gate_snapshot,
)


NOW = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
SOURCE = b'{"status":"PASS"}\n'
SOURCES = [
    {
        "logical_id": "gate:g1-g2-current",
        "schema_version": "g1-g2-joint-gate-1.1.0",
        "sha256": __import__("hashlib").sha256(SOURCE).hexdigest(),
        "source_timestamp": "2026-08-20T12:00:00Z",
    }
]
BLOCKED = {
    "g1_prerequisite": "BLOCKED",
    "g2_prerequisite": "BLOCKED",
    "implementation_readiness": "BLOCKED",
    "g3_run_admission": "BLOCKED",
    "g3_decision": "NOT_RUN",
    "downstream_admission": "BLOCKED",
}


def initial():
    return build_current_gate_snapshot(
        snapshot_id="snapshot:pre-g3-0001",
        observed_at="2026-08-20T12:00:00Z",
        effective_at="2026-08-20T12:00:00Z",
        expires_at="2026-08-21T12:00:00Z",
        transition_event="INITIAL_BLOCKED",
        reason_codes=["G1_G2_NOT_REFRESHED"],
        predicates=BLOCKED,
        sources=SOURCES,
        now=NOW,
    )


def test_historical_blocked_can_refresh_to_current_g1_g2_pass():
    previous = initial()
    predicates = {
        **BLOCKED,
        "g1_prerequisite": "PASS",
        "g2_prerequisite": "PASS",
    }
    current = build_current_gate_snapshot(
        snapshot_id="snapshot:pre-g3-0002",
        observed_at="2026-08-20T13:00:00Z",
        effective_at="2026-08-20T13:00:00Z",
        expires_at="2026-08-21T13:00:00Z",
        transition_event="PREREQUISITE_REFRESH",
        reason_codes=["REMOTE_G1_G2_PASS"],
        predicates=predicates,
        sources=SOURCES,
        predecessor=previous,
        now=NOW,
    )
    valid, reasons = validate_current_gate_snapshot(
        current,
        predecessor=previous,
        source_payloads={"gate:g1-g2-current": SOURCE},
        now=NOW,
    )
    assert valid, reasons
    assert current["predicates"]["g3_run_admission"] == "BLOCKED"


def test_run_admission_cannot_be_ready_without_prerequisites():
    with pytest.raises(ValueError, match="RUN_ADMISSION_PREREQUISITES"):
        build_current_gate_snapshot(
            snapshot_id="snapshot:invalid",
            observed_at="2026-08-20T12:00:00Z",
            effective_at="2026-08-20T12:00:00Z",
            expires_at="2026-08-21T12:00:00Z",
            transition_event="INITIAL_BLOCKED",
            reason_codes=["INVALID"],
            predicates={**BLOCKED, "g3_run_admission": "READY"},
            sources=SOURCES,
            now=NOW,
        )


def test_future_timestamp_and_invalid_interval_fail_closed():
    for observed, effective, expiry in (
        (
            "2026-08-20T15:00:00Z",
            "2026-08-20T15:00:00Z",
            "2026-08-21T15:00:00Z",
        ),
        (
            "2026-08-20T12:00:00Z",
            "2026-08-21T12:00:00Z",
            "2026-08-21T12:00:00Z",
        ),
    ):
        with pytest.raises(ValueError, match="SNAPSHOT_TIME_INVALID"):
            build_current_gate_snapshot(
                snapshot_id="snapshot:invalid-time",
                observed_at=observed,
                effective_at=effective,
                expires_at=expiry,
                transition_event="INITIAL_BLOCKED",
                reason_codes=["INVALID_TIME"],
                predicates=BLOCKED,
                sources=SOURCES,
                now=NOW,
            )


def test_broken_predecessor_and_stale_source_are_detected():
    previous = initial()
    current = build_current_gate_snapshot(
        snapshot_id="snapshot:pre-g3-0002",
        observed_at="2026-08-20T13:00:00Z",
        effective_at="2026-08-20T13:00:00Z",
        expires_at="2026-08-21T13:00:00Z",
        transition_event="PREREQUISITE_REFRESH",
        reason_codes=["G1_PASS"],
        predicates={**BLOCKED, "g1_prerequisite": "PASS"},
        sources=SOURCES,
        predecessor=previous,
        now=NOW,
    )
    forged_previous = dict(previous)
    forged_previous["snapshot_id"] = "snapshot:forged"
    valid, reasons = validate_current_gate_snapshot(
        current, predecessor=forged_previous, now=NOW
    )
    assert valid is False
    assert "PREDECESSOR_LINK_INVALID" in reasons
    valid, reasons = validate_current_gate_snapshot(
        current,
        predecessor=previous,
        source_payloads={"gate:g1-g2-current": b"mutated"},
        now=NOW,
    )
    assert valid is False
    assert "SOURCE_HASH_STALE" in reasons


def test_historical_checklist_cannot_be_consumed_as_current_snapshot():
    valid, reasons = validate_current_gate_snapshot(
        {"schema_version": "submission-checklist-1.0.0", "status": "BLOCKED"},
        now=NOW,
    )
    assert valid is False
    assert "SNAPSHOT_SCHEMA_VERSION_INVALID" in reasons
