import json

import pytest

from display_project.experiments.paired_runner import (
    METHOD_NAMES,
    AttemptRecord,
    append_jsonl_atomic,
    verify_ledger_integrity,
    _resource_schema,
)
from scripts.validate_experiment_ledger import validate


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_first_and_multiple_batches_validate(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(path, [{"value": 1}, {"value": 2}])
    append_jsonl_atomic(path, [{"value": 3}])
    assert verify_ledger_integrity(path.read_bytes())[0] == []


def test_content_reorder_and_deletions_fail_closed(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(path, [{"value": i} for i in range(3)])
    original = path.read_text(encoding="utf-8").splitlines()
    for lines in (
        [
            json.dumps(
                {**json.loads(original[0]), "value": 99},
                sort_keys=True,
                separators=(",", ":"),
            )
        ]
        + original[1:],
        [original[1], original[0], original[2]],
        [original[0], original[2]],
        [original[0], original[1]],
    ):
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert verify_ledger_integrity(path.read_bytes())[0]
        assert validate(path)
    with pytest.raises(ValueError, match="integrity"):
        append_jsonl_atomic(path, [{"value": 4}])


def test_forged_or_incomplete_batch_rejected(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(path, [{"value": 1}, {"value": 2}])
    rows = _rows(path)
    rows[0]["ledger_integrity"]["batch_size"] = 3
    path.write_text(
        "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows)
        + "\n",
        encoding="utf-8",
    )
    assert verify_ledger_integrity(path.read_bytes())[0]


def test_existing_unprotected_ledger_cannot_be_appended(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"value":1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        append_jsonl_atomic(path, [{"value": 2}])


def test_integrity_validator_rejects_non_utf8_without_raising():
    errors, tail = verify_ledger_integrity(b"\xff\xfe")
    assert errors and tail == ""


@pytest.mark.parametrize(
    "field,value",
    [
        ("batch_hash", "?"),
        ("row_hash", ["bad"]),
        ("chain_hash", {"bad": True}),
        ("previous_chain_hash", "?"),
        ("batch_hash", "A" * 64),
    ],
)
def test_integrity_validator_rejects_noncanonical_hash_types(tmp_path, field, value):
    path = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(path, [{"value": 1}])
    row = _rows(path)[0]
    row["ledger_integrity"][field] = value
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    errors, _ = verify_ledger_integrity(path.read_bytes())
    assert errors
    assert validate(path)


def test_integrity_validator_rejects_surrogate_payload_without_raising():
    line = json.dumps(
        {
            "value": "\ud800",
            "ledger_integrity": {},
        }
    )
    errors, _ = verify_ledger_integrity(line)
    assert errors


def _vlm_rows(status: str, *, authority: dict | None = None, event: dict | None = None):
    rows = []
    for method in METHOD_NAMES:
        is_vlm = method == "vlm_only"
        rows.append(
            {
                **__import__("dataclasses").asdict(
                    AttemptRecord(
                        video_id="vlm-boundary",
                        source_dataset="SOFTWARE_FIXTURE_DATASET",
                        canonical_root_id="SOFTWARE_FIXTURE_ROOT",
                        canonical_group_id="SOFTWARE_FIXTURE_GROUP",
                        operator_id="SOFTWARE_FIXTURE_OPERATOR",
                        complete_job_id="SOFTWARE_FIXTURE_JOB",
                        method=method,
                        status=status if is_vlm else "unavailable",
                        lineage_id=f"{method}-lineage",
                        expected_frames=0,
                        frames_processed=0,
                        complete_video=status == "success" if is_vlm else False,
                        error_code=None if (is_vlm and status == "success") else "TEST",
                        resources=_resource_schema(0.0, 0),
                        events=[event] if (is_vlm and event is not None) else [],
                        partial_events=[],
                    )
                ),
                "output": authority.get("output", {}) if authority else {},
                "authorization_events": authority.get("authorization_events", [])
                if authority
                else [],
                "state_writes": authority.get("state_writes", 0) if authority else 0,
                "certificates": authority.get("certificates", []) if authority else [],
            }
        )
    return rows


@pytest.mark.parametrize(
    "status", ["success", "partial", "unavailable", "crash", "timeout"]
)
def test_vlm_authority_boundary_applies_to_every_status(tmp_path, status):
    path = tmp_path / f"vlm-{status}.jsonl"
    append_jsonl_atomic(
        path,
        _vlm_rows(
            status,
            event={
                "decision": "unknown",
                "observed_step": None,
                "evidence_source": "vlm_audit",
            },
        ),
    )
    assert not [e for e in validate(path) if "VLM" in e]

    rows = _vlm_rows(
        status,
        authority={"state_writes": 1},
        event={
            "decision": "unknown",
            "observed_step": None,
            "evidence_source": "vlm_audit",
        },
    )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert any("state_writes" in e for e in validate(path))


@pytest.mark.parametrize(
    "event",
    [
        {
            "decision": "normal",
            "observed_step": "step-1",
            "evidence_source": "annotation",
        },
        {
            "decision": "normal",
            "observed_step": "step-1",
            "evidence_source": "oracle",
        },
        {
            "decision": "normal",
            "observed_step": "step-1",
            "evidence_source": "none",
        },
        {
            "decision": "normal",
            "observed_step": "step-1",
            "evidence_source": "deterministic",
            "evidence": [{"source": "oracle"}],
        },
    ],
)
def test_ledger_validator_rejects_non_deterministic_or_unknown_sources(tmp_path, event):
    path = tmp_path / "hostile-source.jsonl"
    rows = _vlm_rows("unavailable")
    yolo = next(row for row in rows if row["method"] == "yolo")
    yolo["events"] = [event]
    append_jsonl_atomic(path, rows)
    errors = validate(path)
    assert any("evidence" in error.lower() for error in errors)


@pytest.mark.parametrize(
    "field",
    [
        "authorization",
        "state_transition",
        "rollback",
        "override",
        "transition",
        "state_write",
        "certificate",
        "advance",
    ],
)
def test_vlm_event_authority_fields_rejected_for_partial_status(tmp_path, field):
    path = tmp_path / f"event-{field}.jsonl"
    event = {
        "decision": "unknown",
        "observed_step": None,
        "evidence_source": "vlm",
        field: True,
    }
    append_jsonl_atomic(path, _vlm_rows("partial", event=event))
    assert any("authority field" in e for e in validate(path))


@pytest.mark.parametrize(
    "mutation",
    [
        {"event": {"source": "oracle"}},
        {"output": {"authorizations": [{"source": "oracle"}]}},
        {"output": {"authorizations": [{"meta": {"source": "oracle"}}]}},
        {"output": {"certificates": [{"source": "annotation"}]}},
        {"output": {"authorizations": [{"oracle_step": "step-1"}]}},
    ],
)
def test_ledger_rejects_direct_authority_sources_and_oracle_keys(tmp_path, mutation):
    path = tmp_path / "direct-authority.jsonl"
    rows = _vlm_rows("unavailable")
    yolo = next(row for row in rows if row["method"] == "yolo")
    if "event" in mutation:
        yolo["events"] = [
            {
                "decision": "unknown",
                "observed_step": None,
                "evidence_source": "deterministic",
                **mutation["event"],
            }
        ]
    else:
        yolo["output"] = mutation["output"]
    append_jsonl_atomic(path, rows)
    assert any("evidence" in error.lower() for error in validate(path))


@pytest.mark.parametrize("value", ["0", False])
def test_integrity_rejects_non_integer_batch_metadata(tmp_path, value):
    path = tmp_path / "typed-integrity.jsonl"
    append_jsonl_atomic(path, [{"value": 1}])
    row = _rows(path)[0]
    row["ledger_integrity"]["batch_index"] = value
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    assert verify_ledger_integrity(path.read_bytes())[0]


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("attempt", [], "attempt"),
        ("first_attempt", 1, "primary"),
        ("primary", 1, "primary"),
        ("video_id", 7, "identity"),
        ("source_dataset", 7, "identity"),
        ("method", [], "method"),
        ("lineage_id", [], "lineage"),
        ("schedule_id", [], "schedule"),
        ("expected_frames", "0", "expected_frames"),
        ("frames_processed", "0", "frames_processed"),
        ("complete_video", 1, "complete_video"),
    ],
)
def test_ledger_validator_rejects_native_type_coercion(tmp_path, field, value, message):
    path = tmp_path / "typed-ledger.jsonl"
    rows = _vlm_rows("unavailable")
    target = next(row for row in rows if row["method"] == "yolo")
    target[field] = value
    append_jsonl_atomic(path, rows)

    errors = validate(path)

    assert errors
    assert any(message in error.lower() for error in errors)


def test_unknown_top_level_truth_field_rejected_with_valid_integrity(tmp_path):
    path = tmp_path / "unknown-top-level.jsonl"
    rows = _vlm_rows("unavailable")
    rows[0]["dataset_answer"] = {"step": "oracle"}
    append_jsonl_atomic(path, rows)
    errors = validate(path)
    assert any("unknown top-level fields" in error for error in errors)


def test_nested_truth_fields_rejected_in_allowed_containers(tmp_path):
    path = tmp_path / "nested-truth.jsonl"
    rows = _vlm_rows("unavailable")
    yolo = next(row for row in rows if row["method"] == "yolo")
    yolo["artifact_paths"] = {"events": [{"dataset_answer": "oracle"}]}
    append_jsonl_atomic(path, rows)
    errors = validate(path)
    assert any("artifact_paths" in error for error in errors)


def test_real_artifact_paths_source_is_allowed(tmp_path):
    path = tmp_path / "artifact-paths.jsonl"
    rows = _vlm_rows("unavailable")
    yolo = next(row for row in rows if row["method"] == "yolo")
    yolo["artifact_paths"] = {
        "source": str(tmp_path / "source.mp4"),
        "events": str(tmp_path / "events.jsonl"),
        "effective_policy": "yolo",
    }
    append_jsonl_atomic(path, rows)
    assert not any("artifact_paths contains" in error for error in validate(path))


def test_nested_scorer_metadata_truth_is_rejected(tmp_path):
    path = tmp_path / "nested-scorer-truth.jsonl"
    rows = _vlm_rows("unavailable")
    yolo = next(row for row in rows if row["method"] == "yolo")
    yolo["scorer_metadata"] = {"scoring": {"dataset_answer": "oracle"}}
    append_jsonl_atomic(path, rows)
    assert any("scorer_metadata.scoring" in error for error in validate(path))
