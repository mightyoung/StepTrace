from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from display_project.gate_audit import (
    JOINT_GATE_SCHEMA,
    _canonical_sha256,
    write_decision,
)
from scripts import freeze_paired_metrics as freeze_cli
from scripts import run_paired_experiment as run_cli


def _write_blocked_gate(path: Path) -> Path:
    decision = {
        "schema_version": JOINT_GATE_SCHEMA,
        "status": "BLOCKED",
        "action_state": "CONDITION_UNMET_AND_ACTIONS_NOT_PERFORMED",
        "g1": {
            "status": "BLOCKED",
            "checks": {"artifact_valid": False},
            "failed_checks": ["artifact_valid"],
        },
        "g2": {
            "status": "BLOCKED",
            "checks": {"identity_complete": False},
            "failed_checks": ["identity_complete"],
        },
        "joint_g1_g2_pass": False,
        "freeze_paired_metrics_allowed": False,
        "freeze_method_inventory_allowed": False,
        "freeze_thresholds_allowed": False,
        "freeze_mechanism_gate_manifest_allowed": False,
        "execute_g3_method_panel_allowed": False,
        "pilot_power_formal_test_cross_task_allowed": False,
    }
    decision["decision_sha256"] = _canonical_sha256(decision)
    write_decision(path, decision)
    return path


def test_freeze_cli_cannot_bypass_blocked_joint_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")
    operating_points = tmp_path / "points.json"
    operating_points.write_text("{}", encoding="utf-8")
    output = tmp_path / "freeze.json"
    gate = _write_blocked_gate(tmp_path / "joint-gate.json")

    monkeypatch.setattr(freeze_cli, "verify_ledger_integrity", lambda raw: ([], None))
    monkeypatch.setattr(freeze_cli, "validate_ledger", lambda path: [])
    monkeypatch.setattr(freeze_cli, "validate_anchor", lambda *args, **kwargs: [])

    def forbidden_freeze(*args, **kwargs):
        raise AssertionError("blocked gate reached metric freezing")

    monkeypatch.setattr(freeze_cli, "freeze_paired_metrics", forbidden_freeze)
    result = freeze_cli.main(
        [
            "--ledger",
            str(ledger),
            "--operating-points",
            str(operating_points),
            "--input-ledger-hash",
            hashlib.sha256(ledger.read_bytes()).hexdigest(),
            "--anchor", str(tmp_path / "anchor.json"), "--expected-anchor-hash", "a" * 64,
            "--output",
            str(output),
            "--joint-gate",
            str(gate),
        ]
    )
    assert result == 2
    assert "joint G1/G2 input commitments missing or invalid" in capsys.readouterr().err
    assert not output.exists()


def _ready_non_fixture_compiled():
    report = {
        "schema_version": 1,
        "profile": "DEVELOPMENT_ONLY",
        "status": "READY",
        "blocked": False,
        "frozen_anchors": {},
        "methods": [],
    }
    return SimpleNamespace(
        profile="DEVELOPMENT_ONLY",
        blocked=False,
        task=SimpleNamespace(task_id="task"),
        report=lambda: dict(report),
    )


def test_real_paired_run_cannot_bypass_blocked_joint_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gate = _write_blocked_gate(tmp_path / "joint-gate.json")
    report = tmp_path / "preflight.json"
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(
        run_cli, "load_adapter_manifest", lambda path: _ready_non_fixture_compiled()
    )

    class ForbiddenRunner:
        def __init__(self, *args, **kwargs):
            raise AssertionError("blocked gate constructed the paired runner")

    monkeypatch.setattr(run_cli, "PairedExperimentRunner", ForbiddenRunner)
    with pytest.raises(SystemExit) as caught:
        run_cli.main(
            [
                str(tmp_path / "video.mp4"),
                "--adapter-manifest",
                str(tmp_path / "manifest.yaml"),
                "--ledger",
                str(ledger),
                "--joint-gate",
                str(gate),
                "--preflight-report",
                str(report),
            ]
        )
    assert caught.value.code == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["error_code"] == "JOINT_G1_G2_GATE_BLOCKED"
    assert not ledger.exists()


def test_non_fixture_preflight_remains_available_while_gate_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        run_cli, "load_adapter_manifest", lambda path: _ready_non_fixture_compiled()
    )
    report = tmp_path / "preflight.json"
    run_cli.main(
        [
            "--adapter-manifest",
            str(tmp_path / "manifest.yaml"),
            "--preflight-only",
            "--joint-gate",
            str(tmp_path / "missing-gate.json"),
            "--preflight-report",
            str(report),
        ]
    )
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "READY"

@pytest.mark.parametrize("anchor_error", [
    "ledger anchor unreadable",
    "ledger anchor ledger hash mismatch",
    "ledger anchor artifact hash mismatch",
    "ledger anchor external hash mismatch",
])
def test_freeze_cli_rejects_anchor_before_gate(tmp_path: Path, monkeypatch, capsys, anchor_error):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")
    points = tmp_path / "points.json"
    points.write_text("{}", encoding="utf-8")
    gate = _write_blocked_gate(tmp_path / "joint-gate.json")
    monkeypatch.setattr(freeze_cli, "validate_anchor", lambda *args, **kwargs: [anchor_error])
    monkeypatch.setattr(freeze_cli, "verify_joint_gate_permission", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gate must not run")))
    result = freeze_cli.main([
        "--ledger", str(ledger), "--operating-points", str(points),
        "--input-ledger-hash", hashlib.sha256(ledger.read_bytes()).hexdigest(),
        "--anchor", str(tmp_path / "anchor.json"), "--expected-anchor-hash", "a" * 64,
        "--output", str(tmp_path / "freeze.json"), "--joint-gate", str(gate),
    ])
    assert result == 2
    assert "ledger anchor validation failed" in capsys.readouterr().err


def test_freeze_cli_requires_anchor_and_external_hash(tmp_path: Path):
    common = ["--ledger", str(tmp_path / "ledger.jsonl"), "--operating-points", str(tmp_path / "points.json"), "--input-ledger-hash", "a" * 64, "--output", str(tmp_path / "freeze.json")]
    with pytest.raises(SystemExit):
        freeze_cli.main(common)
    with pytest.raises(SystemExit):
        freeze_cli.main(common + ["--anchor", str(tmp_path / "anchor.json")])