from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from display_project.experiments import _paired_runner_core as core
from display_project.experiments.paired_runner import (
    PairedExperimentRunner,
    make_unit_identity,
    validate_g3_token_claim_records,
)
from display_project.g3_run_admission import issue_unit_execution_tokens
from tests.test_g3_run_admission import _evaluate, _units
from tests.test_paired_runner_integration import (
    _make_fixture_video,
    fixture_success,
)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _admitted_case(tmp_path: Path):
    video = _make_fixture_video(tmp_path)
    video_id = video.stem
    identity = {
        "source_dataset": "MECCANO",
        "canonical_root_id": "ROOT-M-1",
        "canonical_group_id": "GROUP-M-1",
        "operator_id": "OP-MECCANO-1",
        "complete_job_id": "JOB-M-1",
        "video_id": video_id,
    }
    unit_id = make_unit_identity(
        source_dataset=identity["source_dataset"],
        canonical_root_id=identity["canonical_root_id"],
        operator_id=identity["operator_id"],
        complete_job_id=identity["complete_job_id"],
        video_id=identity["video_id"],
    )
    units = _units()
    units[0].update(
        unit_id=unit_id,
        canonical_root_id=identity["canonical_root_id"],
        canonical_group_id=identity["canonical_group_id"],
        operator_id=identity["operator_id"],
        complete_job_id=identity["complete_job_id"],
        video_id=identity["video_id"],
        video_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
    )
    auth_root = tmp_path / "authority"
    auth_root.mkdir()
    ledger = (tmp_path / "ledger.jsonl").resolve()
    destination = {
        "schema_version": "g3-result-destination-1.0.0",
        "ledger_id": "ledger:g3-paired-results",
        "ledger_path_sha256": hashlib.sha256(str(ledger).encode("utf-8")).hexdigest(),
    }
    now = datetime.now(timezone.utc)
    decision = _evaluate(
        auth_root,
        unit_inventory=units,
        result_destination=destination,
        issued_at=_iso(now - timedelta(minutes=1)),
        expires_at=_iso(now + timedelta(hours=1)),
        now=now,
    )
    receipt = decision["batch_receipt"]
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    tokens = issue_unit_execution_tokens(receipt, now=now)
    token = next(item for item in tokens if item["unit"]["unit_id"] == unit_id)
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps(token), encoding="utf-8")
    metadata = {
        "experiment_profile": "DEVELOPMENT_G3_EVALUATION",
        "task_config_hash": units[0]["task_config_hash"],
        "split_manifest_sha256": units[0]["split_sha256"],
        "dataset_ledger_sha256": units[0]["ledger_sha256"],
        "paired_ledger_id": destination["ledger_id"],
    }
    return video, identity, receipt_path, token_path, token, metadata


def test_industreal_complete_job_identity_matches_admission_unit():
    expected = "fc2a24dfcda8edfa9d19b5d6e1b6a1b2cec42038c367c29544b6b792cb066136"
    assert make_unit_identity(
        source_dataset="IndustReal",
        canonical_root_id="INDUSTREAL-05_assy_0_1",
        operator_id="UNKNOWN",
        complete_job_id="INDUSTREAL-RECORDING-05_assy_0_1",
        video_id="05_assy_0_1",
    ) == expected


def test_g3_runner_rejects_legacy_joint_gate_and_missing_receipt(tmp_path: Path):
    profile = {"experiment_profile": "DEVELOPMENT_G3_EVALUATION"}
    with pytest.raises(ValueError, match="requires a batch admission receipt"):
        PairedExperimentRunner(
            {"fixed_streak": fixture_success},
            frozen_schedule=["fixed_streak"],
            metadata=profile,
        )
    gate = tmp_path / "gate.json"
    gate.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="rejects legacy"):
        PairedExperimentRunner(
            {"fixed_streak": fixture_success},
            frozen_schedule=["fixed_streak"],
            metadata=profile,
            capability=gate,
            admission_receipt=gate,
        )


def test_invalid_unit_token_blocks_before_decode_or_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    video, identity, receipt_path, token_path, token, metadata = _admitted_case(
        tmp_path
    )
    methods = {name: fixture_success for name in token["method_schedule"]}
    ledger = tmp_path / "ledger.jsonl"
    runner = PairedExperimentRunner(
        methods,
        frozen_schedule=token["method_schedule"],
        metadata=metadata,
        ledger_path=ledger,
        admission_receipt=receipt_path,
    )
    token["unit"]["video_sha256"] = "0" * 64
    token_path.write_text(json.dumps(token), encoding="utf-8")

    def forbidden_decode(path):
        raise AssertionError("decode occurred before token validation")

    monkeypatch.setattr(core, "_decode_video", forbidden_decode)
    with pytest.raises(ValueError, match="unit execution token invalid"):
        runner.run_video(
            video,
            task_id="meccano",
            unit_token=token_path,
            **identity,
        )
    assert not ledger.exists()
    assert not ledger.with_suffix(".jsonl.registration.jsonl").exists()


def test_runner_schedule_must_equal_unit_randomized_schedule(tmp_path: Path):
    video, identity, receipt_path, token_path, token, metadata = _admitted_case(
        tmp_path
    )
    reversed_schedule = list(reversed(token["method_schedule"]))
    if reversed_schedule == token["method_schedule"]:
        reversed_schedule = token["method_schedule"][1:] + token["method_schedule"][:1]
    runner = PairedExperimentRunner(
        {name: fixture_success for name in reversed_schedule},
        frozen_schedule=reversed_schedule,
        metadata=metadata,
        admission_receipt=receipt_path,
    )
    with pytest.raises(ValueError, match="schedule differs"):
        runner.run_video(
            video,
            task_id="meccano",
            unit_token=token_path,
            **identity,
        )


def test_valid_token_is_bound_into_registration_before_attempts(tmp_path: Path):
    video, identity, receipt_path, token_path, token, metadata = _admitted_case(
        tmp_path
    )
    ledger = tmp_path / "ledger.jsonl"
    runner = PairedExperimentRunner(
        {name: fixture_success for name in token["method_schedule"]},
        frozen_schedule=token["method_schedule"],
        metadata=metadata,
        ledger_path=ledger,
        admission_receipt=receipt_path,
        timeout_s=5,
    )
    run = runner.run_video(
        video,
        task_id="meccano",
        unit_token=token_path,
        expected_frames=5,
        **identity,
    )
    assert len(run.attempts) == 3
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    registrations = [
        json.loads(line)
        for line in sidecar.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(registrations) == 3
    assert {row["scorer_metadata"]["unit_token_id"] for row in registrations} == {
        token["token_id"]
    }
    assert {
        row["scorer_metadata"]["admission_receipt_sha256"]
        for row in registrations
    } == {json.loads(receipt_path.read_text())["artifact_sha256"]}
    claim_ledger = ledger.with_suffix(ledger.suffix + ".token-claims.jsonl")
    claims = [
        json.loads(line)
        for line in claim_ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["state"] for row in claims] == [
        "PRIMARY_ATTEMPT_REGISTERING",
        "TERMINAL_RECORDED",
    ]
    assert [
        row["method"] for row in claims[-1]["primary_method_statuses"]
    ] == token["method_schedule"]
    receipt = json.loads(receipt_path.read_text())
    validated, reasons = validate_g3_token_claim_records(
        claim_ledger,
        expected_token=token,
        expected_receipt=receipt,
    )
    assert len(validated) == 2
    assert reasons == []


def test_missing_terminal_token_record_is_not_complete(tmp_path: Path):
    video, identity, receipt_path, token_path, token, metadata = _admitted_case(
        tmp_path
    )
    ledger = tmp_path / "ledger.jsonl"
    runner = PairedExperimentRunner(
        {name: fixture_success for name in token["method_schedule"]},
        frozen_schedule=token["method_schedule"],
        metadata=metadata,
        ledger_path=ledger,
        admission_receipt=receipt_path,
        timeout_s=5,
    )
    runner.run_video(video, task_id="meccano", unit_token=token_path, **identity)
    claim_ledger = ledger.with_suffix(ledger.suffix + ".token-claims.jsonl")
    first_record = claim_ledger.read_bytes().splitlines(keepends=True)[0]
    claim_ledger.write_bytes(first_record)
    _rows, reasons = validate_g3_token_claim_records(
        claim_ledger,
        expected_token=token,
        expected_receipt=json.loads(receipt_path.read_text()),
    )
    assert "TOKEN_LIFECYCLE_INCOMPLETE" in reasons


def test_g3_token_is_single_use_even_after_success(tmp_path: Path):
    video, identity, receipt_path, token_path, token, metadata = _admitted_case(
        tmp_path
    )
    ledger = tmp_path / "ledger.jsonl"
    methods = {name: fixture_success for name in token["method_schedule"]}
    runner = PairedExperimentRunner(
        methods,
        frozen_schedule=token["method_schedule"],
        metadata=metadata,
        ledger_path=ledger,
        admission_receipt=receipt_path,
        timeout_s=5,
    )
    runner.run_video(video, task_id="meccano", unit_token=token_path, **identity)
    with pytest.raises(ValueError, match="already consumed"):
        runner.run_video(video, task_id="meccano", unit_token=token_path, **identity)


def test_g3_receipt_cannot_write_to_a_different_ledger(tmp_path: Path):
    video, identity, receipt_path, token_path, token, metadata = _admitted_case(
        tmp_path
    )
    wrong_ledger = tmp_path / "other-ledger.jsonl"
    runner = PairedExperimentRunner(
        {name: fixture_success for name in token["method_schedule"]},
        frozen_schedule=token["method_schedule"],
        metadata=metadata,
        ledger_path=wrong_ledger,
        admission_receipt=receipt_path,
    )
    with pytest.raises(ValueError, match="ledger path differs"):
        runner.run_video(video, task_id="meccano", unit_token=token_path, **identity)
    assert not wrong_ledger.exists()
