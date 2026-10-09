from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from display_project.g3_capability import (
    G3_CAPABILITY_SCHEMA,
    build_g3_capability_artifact,
    canonical_sha256,
    require_g3_capability,
    validate_g3_capability,
    write_g3_capability,
)


TASKS = ("meccano-task", "industreal-task")


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _inputs(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    singles = {
        name: _write_json(tmp_path / f"{name}.json", {"source": name})
        for name in (
            "joint_gate",
            "ledger_anchor",
            "split_manifest",
            "paired_metrics_freeze",
            "thresholds_manifest",
            "operating_point_manifest",
            "method_manifest",
            "paired_support_manifest",
        )
    }
    ledger = tmp_path / "paired.jsonl"
    ledger.write_text('{"record":"diagnostic"}\n', encoding="utf-8", newline="\n")
    singles["paired_ledger"] = ledger
    results = {
        task: _write_json(tmp_path / f"{task}.json", {"task_id": task})
        for task in TASKS
    }
    return singles, results


def _build(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Path], dict[str, Path]]:
    singles, results = _inputs(tmp_path)
    artifact = build_g3_capability_artifact(
        results,
        thresholds_manifest_path=singles["thresholds_manifest"],
        operating_point_manifest_path=singles["operating_point_manifest"],
        method_manifest_path=singles["method_manifest"],
        paired_support_manifest_path=singles["paired_support_manifest"],
        ledger_path=singles["paired_ledger"],
        ledger_anchor_path=singles["ledger_anchor"],
        expected_anchor_hash="a" * 64,
        split_manifest_path=singles["split_manifest"],
        paired_metrics_freeze_path=singles["paired_metrics_freeze"],
        scorer_config_hash="b" * 64,
        task_config_hash="c" * 64,
        task_ids=TASKS,
        fixture=False,
        joint_gate_path=singles["joint_gate"],
    )
    return artifact, singles, results


def _rehash(artifact: dict[str, Any]) -> None:
    artifact["artifact_sha256"] = canonical_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )


def _assert_denied(artifact: dict[str, Any]) -> None:
    valid, reasons = validate_g3_capability(artifact)
    assert valid is False
    assert "precheck_only_not_authorizing" in reasons
    for stage in ("pilot", "power_analysis", "formal_test", "cross_task"):
        with pytest.raises(PermissionError, match="authorization denied"):
            require_g3_capability(artifact, stage)


def test_complete_inputs_produce_only_a_hash_bound_blocked_precheck(
    tmp_path: Path,
) -> None:
    artifact, _singles, _results = _build(tmp_path)

    assert artifact["schema_version"] == G3_CAPABILITY_SCHEMA
    assert artifact["decision_type"] == "PRECHECK_ONLY"
    assert artifact["status"] == "BLOCKED"
    assert artifact["downstream_authorized"] is False
    assert artifact["precheck_checks"] == {
        "exactly_two_unique_tasks": True,
        "all_supplied_sources_readable": True,
        "actual_g3_implemented": False,
    }
    assert {item["id"] for item in artifact["deferred_requirements"]} == {
        "production_partition_contract",
        "canonical_identity_validation",
        "per_task_task_config_hashes",
        "full_statistical_gate",
        "out_of_band_pin",
    }
    assert all(
        item["status"] == "BLOCKING" for item in artifact["deferred_requirements"]
    )
    expected = canonical_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    assert artifact["artifact_sha256"] == expected
    _assert_denied(artifact)

    output = tmp_path / "precheck.json"
    write_g3_capability(output, artifact)
    assert json.loads(output.read_text(encoding="utf-8")) == artifact


def test_missing_inputs_still_emit_a_blocked_precheck(tmp_path: Path) -> None:
    artifact = build_g3_capability_artifact(
        {},
        thresholds_manifest_path=None,
        operating_point_manifest_path=None,
        method_manifest_path=None,
        paired_support_manifest_path=None,
        task_ids=TASKS,
    )

    assert artifact["status"] == "BLOCKED"
    assert artifact["downstream_authorized"] is False
    assert "missing_source:mechanism_results" in artifact["errors"]
    assert "missing_source:joint_gate" in artifact["errors"]
    _assert_denied(artifact)
    with pytest.raises(PermissionError, match="absent or unreadable"):
        require_g3_capability(tmp_path / "missing.json", "pilot")


def test_self_hashed_pass_claim_never_authorizes(tmp_path: Path) -> None:
    artifact, _singles, _results = _build(tmp_path)
    artifact.update(
        decision_type="ACTUAL_G3",
        status="PASS",
        downstream_authorized=True,
    )
    _rehash(artifact)

    _assert_denied(artifact)
    with pytest.raises(ValueError, match="blocked G3 precheck"):
        write_g3_capability(tmp_path / "forged.json", artifact)


def test_coordinated_full_chain_reseal_never_authorizes(tmp_path: Path) -> None:
    artifact, singles, results = _build(tmp_path)
    for path in [*singles.values(), *results.values()]:
        path.write_text('{"coordinated":"replacement"}\n', encoding="utf-8")

    forged = copy.deepcopy(artifact)
    for label, evidence in forged["source_evidence"].items():
        if label == "mechanism_results":
            for task, result_evidence in evidence.items():
                raw = results[task].read_bytes()
                result_evidence["file_sha256"] = hashlib.sha256(raw).hexdigest()
                result_evidence["size"] = len(raw)
        else:
            raw = singles[label].read_bytes()
            evidence["file_sha256"] = hashlib.sha256(raw).hexdigest()
            evidence["size"] = len(raw)
    forged.update(status="PASS", decision_type="ACTUAL_G3", downstream_authorized=True)
    _rehash(forged)

    _assert_denied(forged)


@pytest.mark.parametrize(
    "identity",
    [
        "N/A",
        "NOT_APPLICABLE",
        "UNKNOWN_EXTERNAL_IDENTITY",
        "UNASSIGNED_BLOCKED_IDENTITY",
    ],
)
def test_reserved_identity_claim_cannot_authorize(
    tmp_path: Path, identity: str
) -> None:
    artifact, _singles, _results = _build(tmp_path)
    artifact["claimed_identity"] = {
        "operator_id": identity,
        "complete_job_id": identity,
    }
    artifact.update(
        status="PASS", decision_type="ACTUAL_G3", downstream_authorized=True
    )
    _rehash(artifact)
    _assert_denied(artifact)


def test_yes_yes_arbitrary_profile_cannot_authorize(tmp_path: Path) -> None:
    artifact, _singles, _results = _build(tmp_path)
    artifact["claimed_partition"] = {
        "profile": "ARBITRARY_DEVELOPMENT",
        "project_split": "DEVELOPMENT",
        "formal_use_allowed": "YES",
        "independent_unit_eligible": "YES",
    }
    artifact.update(
        status="PASS", decision_type="ACTUAL_G3", downstream_authorized=True
    )
    _rehash(artifact)
    _assert_denied(artifact)


@pytest.mark.parametrize(
    "task_hashes",
    [
        {"legacy_single_task_config_hash": "d" * 64},
        {TASKS[0]: "d" * 64, TASKS[1]: "d" * 64},
    ],
)
def test_single_or_duplicate_task_config_hashes_cannot_authorize(
    tmp_path: Path, task_hashes: dict[str, str]
) -> None:
    artifact, _singles, _results = _build(tmp_path)
    artifact["claimed_task_config_hashes"] = task_hashes
    artifact.update(
        status="PASS", decision_type="ACTUAL_G3", downstream_authorized=True
    )
    _rehash(artifact)
    _assert_denied(artifact)


@pytest.mark.parametrize(
    "branch", ["A_FSAR_AT_MATCHED_COVERAGE", "B_COVERAGE_AT_MATCHED_FSAR"]
)
def test_point_estimate_only_branch_cannot_authorize(
    tmp_path: Path, branch: str
) -> None:
    artifact, _singles, _results = _build(tmp_path)
    artifact["claimed_point_estimates"] = {
        "branch": branch,
        "proposed": {"coverage": 1.0, "fsar": 0.0},
        "fixed_streak": {"coverage": 1.0, "fsar": 1.0},
        "hmm_or_crf": {"coverage": 1.0, "fsar": 1.0},
    }
    artifact.update(
        status="PASS", decision_type="ACTUAL_G3", downstream_authorized=True
    )
    _rehash(artifact)
    _assert_denied(artifact)


def test_unknown_downstream_stage_is_rejected(tmp_path: Path) -> None:
    artifact, _singles, _results = _build(tmp_path)
    with pytest.raises(ValueError, match="unknown downstream stage"):
        require_g3_capability(artifact, "manuscript")


def test_require_cli_never_prints_authorized(tmp_path: Path) -> None:
    artifact, _singles, _results = _build(tmp_path)
    capability = tmp_path / "precheck.json"
    write_g3_capability(capability, artifact)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/require_downstream_capability.py",
            "--capability",
            str(capability),
            "--stage",
            "pilot",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "AUTHORIZED" not in result.stdout
    assert "AUTHORIZED" not in result.stderr
    assert "precheck_only_not_authorizing" in result.stderr
