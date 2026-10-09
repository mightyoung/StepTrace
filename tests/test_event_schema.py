import json

import pytest

from display_project.fusion.event_schema import (
    EventValidationError,
    SOPEvent,
    validate_event,
    validate_jsonl,
)


def valid_event() -> dict:
    return {
        "schema_version": "1.0",
        "event_id": "event-0001",
        "timestamp_ms": 1200,
        "decision": "normal",
        "expected_step": "step_1",
        "observed_step": "step_1",
        "previous_step": None,
        "current_step": "step_1",
        "reason": "expected_step_observed",
        "evidence_source": "deterministic",
        "evidence": [
            {
                "source": "deterministic",
                "kind": "action_interval",
                "confidence": 1.0,
                "value": "step_1",
            }
        ],
    }


def test_structured_event_round_trips() -> None:
    event = validate_event(valid_event())

    assert isinstance(event, SOPEvent)
    assert event.to_dict() == valid_event()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("timestamp_ms", -1),
        ("decision", "maybe"),
        ("evidence_source", "oracle"),
    ],
)
def test_invalid_event_fields_are_rejected(field: str, value: object) -> None:
    payload = valid_event()
    payload[field] = value

    with pytest.raises(EventValidationError):
        validate_event(payload)


@pytest.mark.parametrize("source", ["annotation", "vlm", "oracle"])
def test_nested_evidence_requires_deterministic_authority_source(source: str) -> None:
    payload = valid_event()
    payload["evidence"][0]["source"] = source
    with pytest.raises(EventValidationError, match="deterministic authority"):
        validate_event(payload)


def test_unknown_requires_no_observed_step_and_an_unknown_reason() -> None:
    payload = valid_event()
    payload.update(
        decision="unknown",
        observed_step=None,
        current_step=None,
        reason="insufficient_deterministic_evidence",
        evidence_source="vlm",
    )

    assert validate_event(payload).decision == "unknown"

    payload["observed_step"] = "step_1"
    with pytest.raises(EventValidationError):
        validate_event(payload)


def test_jsonl_validation_reports_line_number(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    invalid = valid_event()
    invalid["decision"] = "bad"
    path.write_text(
        json.dumps(valid_event()) + "\n" + json.dumps(invalid) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EventValidationError, match="line 2"):
        validate_jsonl(path)


def test_jsonl_validation_accepts_valid_events(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(valid_event()) + "\n", encoding="utf-8")

    events = validate_jsonl(path)

    assert len(events) == 1
    assert events[0].event_id == "event-0001"
