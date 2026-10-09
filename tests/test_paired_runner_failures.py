import pytest
from display_project.experiments.paired_runner import (
    _canonical_json,
    _recover_partial_artifacts,
    _read_partial_jsonl,
    append_jsonl_atomic,
    reconcile_host_interrupted,
    make_unit_identity,
    _decode_video,
    _hash,
    AttemptRecord,
    _resource_schema,
)
import json
from scripts.validate_experiment_ledger import validate


def test_validator_rejects_primary_retry_mutation(tmp_path):
    row = {
        "video_id": "v",
        "method": "fixed_streak",
        "status": "success",
        "attempt": 1,
        "first_attempt": True,
        "primary": False,
    }
    p = tmp_path / "ledger.jsonl"
    p.write_text(json.dumps(row), encoding="utf8")
    assert any("missing fields" in x for x in validate(p))


def test_partial_recovery_without_artifact_is_zero(tmp_path):
    artifacts = {
        "events": str(tmp_path / "missing.events.jsonl"),
        "certificates": str(tmp_path / "missing.cert.jsonl"),
    }
    events, partial, frames, error, code, refs = _recover_partial_artifacts(
        artifacts,
        decoded_hashes=[],
        expected_input_hash="0" * 64,
        expected_model_hash="1" * 64,
        expected_config_hash="2" * 64,
    )
    assert (events, partial, frames, error, code, refs) == ([], [], 0, None, None, [])


def test_partial_jsonl_drops_only_unterminated_torn_tail(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"frame": 0}\n{bad json}', encoding="utf-8")
    assert _read_partial_jsonl(path) == [{"frame": 0}]

    path.write_text('{"frame": 0}\n{bad json}\n', encoding="utf-8")
    import pytest

    with pytest.raises(ValueError):
        _read_partial_jsonl(path)

    path.write_text('{"frame": 0}\n{bad json}\n{"frame": 2}', encoding="utf-8")
    with pytest.raises(ValueError):
        _read_partial_jsonl(path)


def test_partial_recovery_with_certificate_sidecar_uses_preparsed_records(tmp_path):
    from display_project.provenance import sidecar_record
    from tests.test_executable_method_adapters import _run_uncalibrated

    result = _run_uncalibrated(0.10)
    certificate, graph = result["certificate"], result["graph"]
    frame_hash = "a" * 64
    events_path = tmp_path / "cert.events.jsonl"
    cert_path = tmp_path / "cert.records.jsonl"
    event = dict(result["event"])
    event.update(
        frame=0,
        source_frame_sha256=frame_hash,
        certificate_hash=certificate.certificate_hash,
        graph_digest=certificate.graph_digest,
        certificate_artifact_ref=str(cert_path),
    )
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    cert_path.write_text(
        json.dumps(sidecar_record(certificate, graph)) + "\n", encoding="utf-8"
    )

    recovered = _recover_partial_artifacts(
        {
            "events": str(events_path),
            "certificates": str(cert_path),
            "effective_policy": "uncalibrated_certificate",
        },
        decoded_hashes=[frame_hash],
        expected_input_hash="0" * 64,
        expected_model_hash="1" * 64,
        expected_config_hash="2" * 64,
    )

    events, partial, frames, error, code, refs = recovered
    assert events == partial == [event]
    assert frames == 1
    assert error is None and code is None
    assert refs == [str(events_path), str(cert_path)]


def test_partial_recovery_rejects_bad_json(tmp_path):
    events_path = tmp_path / "bad.events.jsonl"
    events_path.write_text("{bad json}\n", encoding="utf-8")
    artifacts = {
        "events": str(events_path),
        "certificates": str(tmp_path / "missing.cert.jsonl"),
    }
    events, partial, frames, error, code, refs = _recover_partial_artifacts(
        artifacts,
        decoded_hashes=[],
        expected_input_hash="0" * 64,
        expected_model_hash="1" * 64,
        expected_config_hash="2" * 64,
    )
    assert (events, partial, frames, refs) == ([], [], 0, [])
    assert code == "RECOVERY_INVALID"
    assert error


def test_reconcile_missing_artifact_is_zero_frame_and_idempotent(tmp_path):
    import hashlib

    ledger = tmp_path / "host.jsonl"
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    unit_id = make_unit_identity(
        source_dataset="dataset",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="v",
    )
    registration = {
        "schema_version": 1,
        "record_type": "schedule_registration",
        "sequence": 0,
        "prev_hash": "",
        "record_kind": "schedule_registration",
        "registration_id": f"{unit_id}:fixed_streak:1",
        "attempt_identity": f"{unit_id}:fixed_streak:1",
        "video_id": "v",
        "method": "fixed_streak",
        "attempt": 1,
        "primary": True,
        "retry_of": None,
        "video_hash": "",
        "source_video_path": str(tmp_path / "missing.mp4"),
        "expected_frames": 0,
        "schedule_id": "schedule",
        "schedule": ["fixed_streak"],
        "source_dataset": "dataset",
        "canonical_root_id": "root",
        "canonical_group_id": "group",
        "unit_identity": unit_id,
        "operator_id": "operator",
        "complete_job_id": "job",
        "task_id": "task",
        "model_hash": "",
        "config_hash": "",
        "effective_policy": "fixed_streak",
        "artifact_paths": {},
        "scorer_metadata": {},
        "status": "registered",
    }
    registration["record_hash"] = hashlib.sha256(
        _canonical_json(registration).encode("utf-8")
    ).hexdigest()
    append_jsonl_atomic(sidecar, [registration])
    assert reconcile_host_interrupted(ledger) == 1
    assert reconcile_host_interrupted(ledger) == 0
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["status"] == "host_interrupted"
    assert row["frames_processed"] == 0
    assert row["attempt"] == 1 and row["primary"] is True
    assert validate(ledger, expected_methods=["fixed_streak"]) == []


def test_reconcile_valid_source_with_empty_artifact_paths_is_host_interrupted(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "empty-artifacts.jsonl"
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture-video")
    unit_id = make_unit_identity(
        source_dataset="SOFTWARE_FIXTURE",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="v",
    )
    registration = {
        "schema_version": 1,
        "record_type": "schedule_registration",
        "sequence": 0,
        "prev_hash": "",
        "record_kind": "schedule_registration",
        "registration_id": f"{unit_id}:fixed_streak:1",
        "attempt_identity": f"{unit_id}:fixed_streak:1",
        "video_id": "v",
        "method": "fixed_streak",
        "attempt": 1,
        "primary": True,
        "retry_of": None,
        "video_hash": _hash(source),
        "source_video_path": str(source),
        "expected_frames": 1,
        "schedule_id": "schedule",
        "schedule": ["fixed_streak"],
        "source_dataset": "SOFTWARE_FIXTURE",
        "canonical_root_id": "root",
        "canonical_group_id": "group",
        "unit_identity": unit_id,
        "operator_id": "operator",
        "complete_job_id": "job",
        "task_id": "task",
        "model_hash": "d" * 64,
        "config_hash": "c" * 64,
        "effective_policy": "not_applicable",
        "artifact_paths": {},
        "scorer_metadata": {
            "experiment_profile": "SOFTWARE_FIXTURE",
            "manifest_hash": "1" * 64,
            "task_config_hash": "2" * 64,
            "task_source_hash": "3" * 64,
        },
        "status": "registered",
    }
    registration["record_hash"] = __import__("hashlib").sha256(
        _canonical_json(registration).encode("utf-8")
    ).hexdigest()
    append_jsonl_atomic(sidecar, [registration])
    monkeypatch.setattr(
        "display_project.experiments.paired_runner._decode_video",
        lambda _path: (True, 1, ["a" * 64]),
    )

    assert reconcile_host_interrupted(ledger) == 1
    row = json.loads(ledger.read_text(encoding="utf-8"))
    assert row["status"] == "host_interrupted"
    assert row["frames_processed"] == 0
    assert row["manifest_hash"] == "1" * 64
    assert row["task_config_hash"] == "2" * 64
    assert row["task_source_hash"] == "3" * 64


def test_unit_identity_strips_fields_and_rejects_placeholders():
    clean = make_unit_identity(
        source_dataset=" dataset ",
        canonical_root_id=" root ",
        operator_id=" operator ",
        complete_job_id=" job ",
        video_id=" video ",
    )
    assert clean == make_unit_identity(
        source_dataset="dataset",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="video",
    )
    assert make_unit_identity(
        source_dataset=" UNKNOWN ",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="video",
    ) == make_unit_identity(
        source_dataset="UNKNOWN",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="video",
    )
    import pytest

    with pytest.raises(ValueError):
        make_unit_identity(
            source_dataset=" ",
            canonical_root_id="root",
            operator_id="operator",
            complete_job_id="job",
            video_id="video",
        )


def _write_registration(sidecar, registration):
    import hashlib

    registration["record_hash"] = hashlib.sha256(
        _canonical_json(registration).encode("utf-8")
    ).hexdigest()
    append_jsonl_atomic(sidecar, [registration])


def test_reconcile_fixed_streak_recovers_verified_prefix(tmp_path):
    import cv2
    import numpy as np

    video = tmp_path / "source.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (32, 24))
    for value in (0, 50, 100):
        writer.write(np.full((24, 32, 3), value, dtype=np.uint8))
    writer.release()
    decoded_ok, decoded_count, hashes = _decode_video(video)
    assert decoded_ok and decoded_count == 3
    events_path = tmp_path / "attempt.events.jsonl"
    events = []
    for frame in range(2):
        events.append(
            {
                "schema_version": "1.0",
                "event_id": f"task-frame-{frame:06d}",
                "timestamp_ms": frame * 200,
                "decision": "unknown",
                "expected_step": "step",
                "observed_step": None,
                "previous_step": None,
                "current_step": None,
                "reason": "insufficient_evidence",
                "evidence_source": "none",
                "evidence": [],
                "certificate_hash": None,
                "graph_digest": None,
                "certificate_artifact_ref": None,
                "frame": frame,
                "source_frame_sha256": hashes[frame],
            }
        )
    events_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    ledger = tmp_path / "prefix.jsonl"
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    unit_id = make_unit_identity(
        source_dataset="dataset",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="video",
    )
    registration = {
        "schema_version": 1,
        "record_type": "schedule_registration",
        "sequence": 0,
        "prev_hash": "",
        "record_kind": "schedule_registration",
        "registration_id": f"{unit_id}:fixed_streak:1",
        "attempt_identity": f"{unit_id}:fixed_streak:1",
        "video_id": "video",
        "method": "fixed_streak",
        "attempt": 1,
        "primary": True,
        "retry_of": None,
        "video_hash": _hash(video),
        "source_video_path": str(video),
        "expected_frames": 3,
        "schedule_id": "schedule",
        "schedule": ["fixed_streak"],
        "source_dataset": "dataset",
        "canonical_root_id": "root",
        "canonical_group_id": "group",
        "unit_identity": unit_id,
        "operator_id": "operator",
        "complete_job_id": "job",
        "task_id": "task",
        "model_hash": "",
        "config_hash": "",
        "effective_policy": "fixed_streak",
        "artifact_paths": {
            "events": str(events_path),
            "certificates": str(tmp_path / "missing-cert.jsonl"),
            "source": str(video),
            "effective_policy": "fixed_streak",
        },
        "scorer_metadata": {},
        "status": "registered",
    }
    _write_registration(sidecar, registration)
    assert reconcile_host_interrupted(ledger) == 1
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["frames_processed"] == 2
    assert row["output"]["frame_trace"] == [
        {"frame": index, "source_frame_sha256": hashes[index]} for index in range(2)
    ]
    assert validate(ledger, expected_methods=["fixed_streak"]) == []


def test_reconcile_retry_preserves_retry_identity(tmp_path):
    import dataclasses

    ledger = tmp_path / "retry.jsonl"
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    unit_id = make_unit_identity(
        source_dataset="dataset",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="video",
    )
    base = {
        "schema_version": 1,
        "record_type": "schedule_registration",
        "record_kind": "schedule_registration",
        "video_id": "video",
        "method": "fixed_streak",
        "video_hash": "",
        "source_video_path": str(tmp_path / "missing.mp4"),
        "expected_frames": 0,
        "schedule_id": "schedule",
        "schedule": ["fixed_streak"],
        "source_dataset": "dataset",
        "canonical_root_id": "root",
        "canonical_group_id": "group",
        "unit_identity": unit_id,
        "operator_id": "operator",
        "complete_job_id": "job",
        "task_id": "task",
        "model_hash": "",
        "config_hash": "",
        "effective_policy": "fixed_streak",
        "artifact_paths": {},
        "scorer_metadata": {},
        "status": "registered",
    }
    primary_registration = {
        **base,
        "sequence": 0,
        "prev_hash": "",
        "registration_id": f"{unit_id}:fixed_streak:1",
        "attempt_identity": f"{unit_id}:fixed_streak:1",
        "attempt": 1,
        "primary": True,
        "retry_of": None,
    }
    _write_registration(sidecar, primary_registration)
    retry_registration = {
        **base,
        "sequence": 1,
        "prev_hash": primary_registration["record_hash"],
        "registration_id": f"{unit_id}:fixed_streak:2",
        "attempt_identity": f"{unit_id}:fixed_streak:2",
        "attempt": 2,
        "primary": False,
        "retry_of": 1,
    }
    _write_registration(sidecar, retry_registration)
    primary = AttemptRecord(
        video_id="video",
        method="fixed_streak",
        status="crash",
        source_dataset="dataset",
        canonical_root_id="root",
        canonical_group_id="group",
        unit_identity=unit_id,
        attempt=1,
        first_attempt=True,
        primary=True,
        lineage_id=f"{unit_id}:fixed_streak",
        task_id="task",
        operator_id="operator",
        complete_job_id="job",
        expected_frames=0,
        frames_processed=0,
        resources=_resource_schema(0.0, 0, termination_kind="crash", reason="fixture"),
        error="fixture",
        error_code="CRASH",
        schedule_id="schedule",
        schedule=("fixed_streak",),
    )
    primary_payload = dataclasses.asdict(primary)
    primary_payload.update(
        {
            "record_kind": "attempt_outcome",
            "attempt_identity": f"{unit_id}:fixed_streak:1",
            "source_video_path": base["source_video_path"],
            "artifact_paths": {},
            "effective_policy": "fixed_streak",
            "scorer_metadata": {},
            "annotation_used": False,
            "vlm_authority_verified": True,
        }
    )
    append_jsonl_atomic(ledger, [primary_payload])
    assert reconcile_host_interrupted(ledger) == 1
    rows = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["attempt"] for row in rows] == [1, 2]
    assert rows[1]["primary"] is False and rows[1]["retry_of"] == 1
    assert rows[0]["lineage_id"] == rows[1]["lineage_id"]
    assert validate(ledger, expected_methods=["fixed_streak"]) == []


def _registration_candidate():
    import hashlib

    unit = make_unit_identity(
        source_dataset="dataset",
        canonical_root_id="root",
        operator_id="operator",
        complete_job_id="job",
        video_id="video",
    )
    row = {
        "schema_version": 1,
        "record_type": "schedule_registration",
        "sequence": 0,
        "prev_hash": "",
        "record_kind": "schedule_registration",
        "registration_id": f"{unit}:fixed_streak:1",
        "attempt_identity": f"{unit}:fixed_streak:1",
        "video_id": "video",
        "method": "fixed_streak",
        "attempt": 1,
        "primary": True,
        "retry_of": None,
        "video_hash": "",
        "source_video_path": "video.mp4",
        "expected_frames": 1,
        "schedule_id": "schedule",
        "schedule": ["fixed_streak"],
        "source_dataset": "dataset",
        "canonical_root_id": "root",
        "canonical_group_id": "group",
        "unit_identity": unit,
        "operator_id": "operator",
        "complete_job_id": "job",
        "task_id": "task",
        "model_hash": "",
        "config_hash": "",
        "effective_policy": "fixed_streak",
        "artifact_paths": {},
        "scorer_metadata": {},
        "status": "registered",
    }
    row["record_hash"] = hashlib.sha256(
        _canonical_json(row).encode("utf-8")
    ).hexdigest()
    return row


def _rehash_registration_candidate(row):
    import hashlib

    body = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(
        _canonical_json(body).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    "field",
    [
        "registration_id",
        "attempt_identity",
        "video_id",
        "method",
        "source_video_path",
        "schedule_id",
        "source_dataset",
        "canonical_root_id",
        "canonical_group_id",
        "operator_id",
        "complete_job_id",
        "task_id",
        "effective_policy",
    ],
)
def test_registration_candidate_rejects_non_string_schema_fields(field):
    from display_project.experiments.paired_runner import (
        validate_registration_candidate,
    )

    row = _registration_candidate()
    row[field] = 123
    if field == "method":
        row["schedule"] = [123]
    identity_fields = {
        key: str(row[key])
        for key in (
            "source_dataset",
            "canonical_root_id",
            "operator_id",
            "complete_job_id",
            "video_id",
        )
    }
    unit = make_unit_identity(**identity_fields)
    if field in identity_fields:
        row["unit_identity"] = unit
        row["registration_id"] = f"{unit}:{row['method']}:1"
        row["attempt_identity"] = f"{unit}:{row['method']}:1"
    _rehash_registration_candidate(row)
    with pytest.raises(ValueError, match="string|schedule"):
        validate_registration_candidate(row, sequence=0, previous="")
