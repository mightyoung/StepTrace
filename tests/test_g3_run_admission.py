from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess

from display_project.current_gate_snapshot import build_current_gate_snapshot
from display_project.g3_run_admission import (
    evaluate_g3_run_admission,
    issue_unit_execution_tokens,
    validate_batch_receipt,
    validate_unit_execution_token,
)
from display_project.pre_g3_authority import (
    build_authority_envelope,
    build_dirty_tree_receipt,
    build_external_attestation,
)


NOW = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)


def _hashed(value):
    import json

    body = dict(value)
    body["artifact_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return body


def _authority(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "code.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    receipt = build_dirty_tree_receipt(root, included_paths=[], excluded_paths=[])
    envelope = build_authority_envelope(
        logical_id="authority:pre-g3",
        implementation_commit={
            "commit_sha": receipt["base_head"],
            "tree_sha": receipt["base_tree"],
            "implementation_branch_id": "codex/pre-g3",
        },
        environment_commitment={
            "logical_id": "environment:g3",
            "schema_version": "environment-1.0.0",
            "sha256": "a" * 64,
        },
        evidence_bundle_commitment=[
            {
                "logical_id": "gate:g1-g2",
                "schema_version": "gate-1.0.0",
                "sha256": "b" * 64,
                "source_timestamp": "2026-08-20T11:00:00Z",
                "lineage": None,
            }
        ],
        dirty_tree_receipt=receipt,
    )
    attestation = build_external_attestation(
        logical_id="attestation:pre-g3",
        envelope=envelope,
        reviewer_role="independent_verifier",
        attested_at="2026-08-20T12:00:00Z",
        nonce="authority-nonce-0001",
        storage_logical_id="handoff:pre-g3-attestation",
    )
    return envelope, attestation


def _snapshot():
    source = b"gate"
    initial = build_current_gate_snapshot(
        snapshot_id="snapshot:0001",
        observed_at="2026-08-20T10:00:00Z",
        effective_at="2026-08-20T10:00:00Z",
        expires_at="2026-08-22T10:00:00Z",
        transition_event="INITIAL_BLOCKED",
        reason_codes=["INITIAL"],
        predicates={
            "g1_prerequisite": "BLOCKED",
            "g2_prerequisite": "BLOCKED",
            "implementation_readiness": "BLOCKED",
            "g3_run_admission": "BLOCKED",
            "g3_decision": "NOT_RUN",
            "downstream_admission": "BLOCKED",
        },
        sources=[
            {
                "logical_id": "gate:g1-g2",
                "schema_version": "gate-1.0.0",
                "sha256": hashlib.sha256(source).hexdigest(),
                "source_timestamp": "2026-08-20T10:00:00Z",
            }
        ],
        now=NOW,
    )
    prerequisites = build_current_gate_snapshot(
        snapshot_id="snapshot:0002",
        observed_at="2026-08-20T11:00:00Z",
        effective_at="2026-08-20T11:00:00Z",
        expires_at="2026-08-22T11:00:00Z",
        transition_event="PREREQUISITE_REFRESH",
        reason_codes=["G1_G2_PASS"],
        predicates={
            **initial["predicates"],
            "g1_prerequisite": "PASS",
            "g2_prerequisite": "PASS",
        },
        sources=initial["sources"],
        predecessor=initial,
        now=NOW,
    )
    ready = build_current_gate_snapshot(
        snapshot_id="snapshot:0003",
        observed_at="2026-08-20T12:00:00Z",
        effective_at="2026-08-20T12:00:00Z",
        expires_at="2026-08-22T12:00:00Z",
        transition_event="IMPLEMENTATION_ADVANCE",
        reason_codes=["IMPLEMENTATION_ATTESTED"],
        predicates={
            **prerequisites["predicates"],
            "implementation_readiness": "READY",
        },
        sources=initial["sources"],
        predecessor=prerequisites,
        now=NOW,
    )
    return prerequisites, ready


def _bindings():
    return [
        {"task_id": "meccano", "dataset_id": "MECCANO", "task_config_hash": "c" * 64},
        {"task_id": "industreal", "dataset_id": "INDUSTREAL", "task_config_hash": "d" * 64},
    ]


def _units(operator="OP-INDUSTREAL-1"):
    rows = []
    for task, dataset, config, suffix, unit_id in (
        ("meccano", "MECCANO", "c" * 64, "m", "7" * 64),
        ("industreal", "INDUSTREAL", "d" * 64, "i", "8" * 64),
    ):
        rows.append(
            {
                "unit_id": unit_id,
                "task_id": task,
                "dataset_id": dataset,
                "operator_id": operator if task == "industreal" else "OP-MECCANO-1",
                "complete_job_id": f"JOB-{suffix.upper()}-1",
                "canonical_root_id": f"ROOT-{suffix.upper()}-1",
                "canonical_group_id": f"GROUP-{suffix.upper()}-1",
                "video_id": f"{dataset.lower()}-{suffix}-001",
                "video_sha256": "e" * 64,
                "task_config_hash": config,
                "split_sha256": "f" * 64,
                "ledger_sha256": "1" * 64,
                "cluster_level": "operator",
                "operator_identity_status": "VERIFIED",
            }
        )
    return rows


def _methods():
    return _hashed(
        {
            "schema_version": "g3-method-manifest-1.0.0",
            "profile": "DEVELOPMENT_G3_EVALUATION",
            "schedule": ["fixed_streak", "hmm_or_crf", "proposed"],
            "randomization_seed": 20260820,
            "method_hashes": {
                "fixed_streak": "2" * 64,
                "hmm_or_crf": "3" * 64,
                "proposed": "4" * 64,
            },
        }
    )


def _producer():
    source_hashes = ["5" * 64]
    import json

    return _hashed(
        {
            "schema_version": "g3-statistical-producer-1.1.0",
            "producer_id": "stats:g3-primary",
            "producer_version": "1.0.0",
            "runtime": "statsmodels",
            "fixture": False,
            "real_input_enabled": True,
            "source_sha256": source_hashes,
            "runtime_asset_sha256": {"aft_dll": "9" * 64},
            "code_sha256": hashlib.sha256(
                json.dumps(source_hashes, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "environment_sha256": "6" * 64,
            "bootstrap_count": 10_000,
        }
    )


def _destination(tmp_path: Path):
    ledger = (tmp_path / "paired-results.jsonl").resolve()
    return {
        "schema_version": "g3-result-destination-1.0.0",
        "ledger_id": "ledger:g3-paired-results",
        "ledger_path_sha256": hashlib.sha256(str(ledger).encode("utf-8")).hexdigest(),
    }


def _evaluate(tmp_path: Path, **updates):
    envelope, attestation = _authority(tmp_path)
    predecessor, snapshot = _snapshot()
    values = {
        "authority_envelope": envelope,
        "authority_attestation": attestation,
        "current_snapshot": snapshot,
        "snapshot_predecessor": predecessor,
        "task_bindings": _bindings(),
        "method_manifest": _methods(),
        "unit_inventory": _units(),
        "statistical_producer": _producer(),
        "result_destination": _destination(tmp_path),
        "profile": "DEVELOPMENT_G3_EVALUATION",
        "consumer_id": "runner:g3-paired-panel",
        "nonce": "admission-nonce-0001",
        "issued_at": "2026-08-20T13:00:00Z",
        "expires_at": "2026-08-20T15:00:00Z",
        "now": NOW,
    }
    values.update(updates)
    return evaluate_g3_run_admission(**values)


def test_complete_fixture_contract_issues_narrow_batch_receipt(tmp_path: Path):
    decision = _evaluate(tmp_path)
    assert decision["status"] == "G3_RUN_ADMISSION_READY"
    receipt = decision["batch_receipt"]
    valid, reasons = validate_batch_receipt(
        receipt,
        expected_consumer_id="runner:g3-paired-panel",
        now=NOW,
    )
    assert valid, reasons
    assert receipt["downstream_authorized"] is False


def test_unknown_industreal_operator_and_missing_stats_block(tmp_path: Path):
    decision = _evaluate(
        tmp_path,
        unit_inventory=_units(operator="UNKNOWN"),
        statistical_producer=None,
    )
    assert decision["ready"] is False
    assert "BLOCKED_UNKNOWN_INDUSTREAL_OPERATOR_ID" in decision["reason_codes"]
    assert "STATISTICAL_ENGINE_MISSING" in decision["reason_codes"]
    assert decision["batch_receipt"] is None


def _complete_job_fallback_units():
    rows = _units(operator="UNKNOWN")
    industreal = rows[-1]
    industreal.update(
        cluster_level="complete_job",
        operator_identity_status="UNKNOWN_DISCLOSED",
    )
    second = dict(industreal)
    second.update(
        unit_id="9" * 64,
        complete_job_id="JOB-I-2",
        canonical_root_id="ROOT-I-2",
        canonical_group_id="GROUP-I-2",
        video_id="industreal-i-002",
        video_sha256="a" * 64,
    )
    rows.append(second)
    return rows


def test_disclosed_industreal_complete_job_fallback_is_admissible(tmp_path: Path):
    decision = _evaluate(tmp_path, unit_inventory=_complete_job_fallback_units())
    assert decision["status"] == "G3_RUN_ADMISSION_READY"
    assert decision["reason_codes"] == []
    assert len(decision["batch_receipt"]["unit_inventory"]) == 3


def test_complete_job_fallback_requires_two_unique_jobs(tmp_path: Path):
    units = _complete_job_fallback_units()[:-1]
    decision = _evaluate(tmp_path, unit_inventory=units)
    assert decision["ready"] is False
    assert "COMPLETE_JOB_FALLBACK_SUPPORT_INVALID" in decision["reason_codes"]


def test_unknown_operator_cannot_fake_verified_operator_contract(tmp_path: Path):
    units = _complete_job_fallback_units()
    units[-1]["operator_identity_status"] = "VERIFIED"
    decision = _evaluate(tmp_path, unit_inventory=units)
    assert decision["ready"] is False
    assert "BLOCKED_UNKNOWN_INDUSTREAL_OPERATOR_ID" in decision["reason_codes"]
    assert "UNIT_OPERATOR_CLUSTER_CONTRACT_INVALID" in decision["reason_codes"]


def test_profile_alias_and_joint_prerequisites_cannot_bypass_admission(tmp_path: Path):
    decision = _evaluate(tmp_path, profile="DEVELOPMENT_ONLY")
    assert decision["ready"] is False
    assert "EVALUATION_PROFILE_INVALID" in decision["reason_codes"]


def test_tampered_or_wrong_consumer_receipt_is_rejected(tmp_path: Path):
    receipt = _evaluate(tmp_path)["batch_receipt"]
    receipt["consumer_id"] = "runner:other"
    valid, reasons = validate_batch_receipt(
        receipt,
        expected_consumer_id="runner:g3-paired-panel",
        now=NOW,
    )
    assert valid is False
    assert "BATCH_RECEIPT_HASH_INVALID" in reasons
    assert "BATCH_RECEIPT_CONSUMER_MISMATCH" in reasons


def test_precheck_cannot_be_relabelled_as_batch_receipt():
    valid, reasons = validate_batch_receipt(
        {
            "schema_version": "g3-mechanism-precheck-1.0.0",
            "decision_type": "PRECHECK_ONLY",
        },
        now=NOW,
    )
    assert valid is False
    assert "BATCH_RECEIPT_SCHEMA_VERSION_INVALID" in reasons


def test_batch_issues_one_token_per_complete_unit(tmp_path: Path):
    receipt = _evaluate(tmp_path)["batch_receipt"]
    tokens = issue_unit_execution_tokens(receipt, now=NOW)
    assert len(tokens) == len(receipt["unit_inventory"]) == 2
    assert len({token["token_id"] for token in tokens}) == 2
    for token in tokens:
        valid, reasons = validate_unit_execution_token(
            token,
            receipt,
            expected_consumer_id="runner:g3-paired-panel",
            expected_unit_id=token["unit"]["unit_id"],
            now=NOW,
        )
        assert valid, reasons
        assert set(token["method_schedule"]) == {
            "fixed_streak",
            "hmm_or_crf",
            "proposed",
        }


def test_unit_randomization_is_deterministic_and_unit_bound(tmp_path: Path):
    receipt = _evaluate(tmp_path)["batch_receipt"]
    first = issue_unit_execution_tokens(receipt, now=NOW)
    second = issue_unit_execution_tokens(receipt, now=NOW)
    assert first == second
    assert [token["unit"]["unit_id"] for token in first] == [
        row["unit_id"] for row in receipt["unit_inventory"]
    ]


def test_unit_token_replay_and_changed_unit_binding_fail_closed(tmp_path: Path):
    receipt = _evaluate(tmp_path)["batch_receipt"]
    token = issue_unit_execution_tokens(receipt, now=NOW)[0]
    valid, reasons = validate_unit_execution_token(
        token,
        receipt,
        expected_consumer_id="runner:g3-paired-panel",
        expected_unit_id=token["unit"]["unit_id"],
        consumed_token_ids={token["token_id"]},
        now=NOW,
    )
    assert valid is False
    assert "BLOCKED_TOKEN_REPLAY" in reasons
    token["unit"]["video_sha256"] = "9" * 64
    valid, reasons = validate_unit_execution_token(
        token,
        receipt,
        expected_consumer_id="runner:g3-paired-panel",
        expected_unit_id=token["unit"]["unit_id"],
        now=NOW,
    )
    assert valid is False
    assert "UNIT_TOKEN_HASH_INVALID" in reasons
    assert "UNIT_TOKEN_UNIT_BINDING_INVALID" in reasons


def test_unit_token_cannot_be_used_by_wrong_consumer(tmp_path: Path):
    receipt = _evaluate(tmp_path)["batch_receipt"]
    token = issue_unit_execution_tokens(receipt, now=NOW)[0]
    valid, reasons = validate_unit_execution_token(
        token,
        receipt,
        expected_consumer_id="runner:wrong",
        expected_unit_id=token["unit"]["unit_id"],
        now=NOW,
    )
    assert valid is False
    assert any("CONSUMER" in reason for reason in reasons)
