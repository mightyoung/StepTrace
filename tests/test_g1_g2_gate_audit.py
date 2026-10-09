from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from display_project.experiments.hmm_fit import fit_hmm_artifact

torch = pytest.importorskip("torch", reason="gate-audit YOLO checks require torch")

from display_project import gate_audit  # noqa: E402
from display_project.gate_audit import (  # noqa: E402
    METHOD_NAMES,
    _canonical_sha256,
    _validate_yolo_artifact,
    build_joint_gate_decision,
    verify_joint_gate_permission,
)
from display_project.risk import RiskCalibrator  # noqa: E402


class Detect(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.ones(1))


class FakeGraph(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.detect = Detect()
        self.names = {
            0: "hand",
            1: "metal bar",
            2: "partial assembly",
            3: "screw",
            4: "bolt",
            5: "screwdriver",
        }


class FakeYOLO:
    def __init__(self, _path):
        self.model = FakeGraph()
        self.task = "detect"
        self.names = self.model.names


def _enable_fake_yolo(monkeypatch):
    monkeypatch.setattr(
        gate_audit, "_load_yolo_model", lambda path: (FakeYOLO(path), torch)
    )
    monkeypatch.setattr(gate_audit, "_candidate_manifest_valid", lambda *args: True)
    monkeypatch.setattr(
        gate_audit,
        "validate_meccano_annotation_sources",
        lambda *args, **kwargs: ({"status": "PASS", "source_count": 22}, []),
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _preflight(ready: bool = True) -> dict:
    status = "READY" if ready else "BLOCKED"
    return {
        "status": status,
        "blocked": not ready,
        "frozen_anchors": {
            "fixed_streak": {"confirm_frames": 4, "cooldown_frames": 12},
            "manifest_hash": _digest("manifest"),
            "task_source_hash": _digest("task-source"),
            "task_config_hash": _digest("task-config"),
            "code_hash": _digest("code"),
            "environment_hash": _digest("environment"),
            "annotation_hash": _digest("annotation"),
            "partition_hash": _digest("partition"),
            "schedule": list(METHOD_NAMES),
            "task_id": "task",
        },
        "methods": [
            {
                "name": name,
                "status": status,
                "error_code": None if ready else "DEPENDENCY_MISSING",
                "config_hash": _digest(f"config:{name}"),
                "model_hash": _digest(f"model:{name}"),
            }
            for name in METHOD_NAMES
        ],
    }


def _qa() -> dict:
    return {
        "schema_version": "1.0.0",
        "status": "PASS",
        "passed": True,
        "errors": [],
        "decode_failures": 0,
        "invalid_labels": 0,
        "missing_label_pairs": 0,
        "missing_image_pairs": 0,
        "duplicate_image_hash_count": 0,
        "video_cross_split": {},
        "conversion_crosscheck": {"status": "PASS", "mismatches": []},
        "content_sha256": _digest("dataset-content"),
        "file_manifest_sha256": _digest("file-manifest"),
        "held_out_videos": ["09"],
        "held_out_test_assets": [],
        "videos": {"train": ["01"], "val": ["07"], "test": ["08", "10"]},
    }


def _candidate(qa: dict, admissible: bool = True) -> dict:
    payload = {
        "status": (
            "DEVELOPMENT_CANDIDATE" if admissible else "BLOCKED_UNKNOWN_IDENTITY"
        ),
        "formal_admission": "ADMITTED" if admissible else "BLOCKED_UNTIL_G2",
        "formal_admission_scope": (
            "ELIGIBLE_FOR_FUTURE_FORMAL_PARTITION_NOT_ASSIGNED"
        ),
        "formal_use_currently_allowed": False,
        "schema_version": "1.0.0",
        "source_identity_manifest": "research/split-manifest.json",
        "source_identity_manifest_sha256": _digest("source-identity"),
        "conversion_report_sha256": _digest("conversion-report"),
        "source_identity_manifest_status": (
            "READY" if admissible else "BLOCKED_MISSING_EXTERNAL_IDENTITY"
        ),
        "dataset_content_sha256": qa["content_sha256"],
        "file_manifest_sha256": qa["file_manifest_sha256"],
        "checks": {
            "video_isolation": "PASS",
            "adjacent_frame_isolation": "PASS",
            "operator_identity_completeness": (
                "PASS" if admissible else "BLOCKED_UNKNOWN_IDENTITY"
            ),
            "complete_job_identity_completeness": "PASS",
        },
    }
    payload["records"] = [
        {
            "dataset_video_id": "08",
            "dataset_split": "test",
            "identity_values_invented": False,
            "adjacent_frames_inherit_video_split": True,
        }
    ]
    payload["candidate_payload_sha256"] = _canonical_sha256(payload)
    return payload


def _industreal() -> dict:
    payload = {
        "schema_version": "industreal-capability-1.0.0",
        "status": "PASS",
        "capability_status": "ACTION_STEP_STATE_ONLY",
        "detection_metric_policy": "PROHIBITED",
        "six_class_detection_allowed": False,
        "detection_metrics_allowed": False,
        "action_step_state_only": True,
        "decision_checks": {
            "provenance_verified": False,
            "schema_verified": False,
            "hash_verified": False,
            "coverage_verified": False,
            "six_class_schema": False,
        },
    }
    payload["artifact_sha256"] = _canonical_sha256(payload)
    return payload


def _write_valid_artifacts(tmp_path: Path, qa: dict) -> tuple[Path, Path, Path]:
    calibration = tmp_path / "calibration.json"
    RiskCalibrator().fit([0.2, 0.8], [0, 1]).write_artifact(
        calibration, _digest("calibration-partition")
    )

    hmm = tmp_path / "hmm.json"
    records = [
        {
            "group_hash": _digest("group-a"),
            "partition": "DEVELOPMENT",
            "state": "s0",
            "features": [1.0, 2.0, 0.0],
        },
        {
            "group_hash": _digest("group-a"),
            "partition": "DEVELOPMENT",
            "state": "s1",
            "features": [2.0, 3.0, 1.0],
        },
    ]
    hmm.write_text(
        json.dumps(
            fit_hmm_artifact(
                records,
                task_id="task",
                task_config_hash=_digest("task-config"),
                partition_manifest_hash=_digest("partition"),
                states=["s0", "s1"],
                fit_code_hash=_digest("fit-code"),
            )
        ),
        encoding="utf-8",
    )

    yolo = tmp_path / "best.pt"
    with zipfile.ZipFile(yolo, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("archive/data.pkl", b"x" * 2048)
        archive.writestr("archive/version", "3")
    yolo_hash = hashlib.sha256(yolo.read_bytes()).hexdigest()
    Path(f"{yolo}.provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "display_project.yolo_training_artifact-1.0.0",
                "status": "TRAINED",
                "artifact_sha256": yolo_hash,
                "dataset_content_sha256": qa["content_sha256"],
                "base_model_sha256": _digest("base-model"),
                "training_run_id": "development-training-001",
                "split_role": "TRAIN",
                "training_completed": True,
            }
        ),
        encoding="utf-8",
    )
    return calibration, hmm, yolo


def _source_paths(tmp_path: Path, *, preflight, qa, candidate, capability, cvat):
    tmp_path.mkdir(parents=True, exist_ok=True)
    values = {
        "preflight": preflight,
        "dataset_qa": qa,
        "candidate_manifest": candidate,
        "industreal_capability": capability,
        "cvat_audit": cvat,
    }
    result = {}
    for name, value in values.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        result[f"{name}_path"] = path
    return result


def _decision(
    tmp_path: Path,
    *,
    ready: bool = True,
    admissible: bool = True,
    artifacts: tuple[Path, Path, Path] | None = None,
) -> dict:
    qa = _qa()
    preflight = _preflight(ready=ready)
    candidate = _candidate(qa, admissible=admissible)
    capability = _industreal()
    cvat = {"labels": 0, "audit_status": "BLOCKED"}
    if artifacts is None:
        artifacts = _write_valid_artifacts(tmp_path, qa)
    return build_joint_gate_decision(
        preflight=preflight,
        dataset_qa=qa,
        candidate_manifest=candidate,
        industreal_capability=capability,
        cvat_audit=cvat,
        calibration_artifact=artifacts[0],
        hmm_artifact=artifacts[1],
        yolo_artifact=artifacts[2],
        **_source_paths(
            tmp_path,
            preflight=preflight,
            qa=qa,
            candidate=candidate,
            capability=capability,
            cvat=cvat,
        ),
    )


def test_joint_gate_blocks_every_action_when_inputs_are_not_ready(tmp_path: Path):
    missing = tuple(tmp_path / name for name in ("cal.json", "hmm.json", "best.pt"))
    decision = _decision(
        tmp_path,
        ready=False,
        admissible=False,
        artifacts=missing,
    )

    assert decision["status"] == "BLOCKED"
    assert decision["action_state"] == "CONDITION_UNMET_AND_ACTIONS_NOT_PERFORMED"
    assert decision["g1"]["status"] == "BLOCKED"
    assert decision["g2"]["status"] == "BLOCKED"
    for permission in (
        "freeze_paired_metrics_allowed",
        "freeze_method_inventory_allowed",
        "freeze_thresholds_allowed",
        "freeze_mechanism_gate_manifest_allowed",
        "execute_g3_method_panel_allowed",
        "pilot_power_formal_test_cross_task_allowed",
    ):
        assert decision[permission] is False


def test_missing_formal_admission_fails_closed(tmp_path: Path):
    qa = _qa()
    candidate = _candidate(qa)
    candidate.pop("formal_admission")
    candidate["candidate_payload_sha256"] = _canonical_sha256(
        {
            key: value
            for key, value in candidate.items()
            if key != "candidate_payload_sha256"
        }
    )
    calibration, hmm, yolo = _write_valid_artifacts(tmp_path, qa)
    decision = build_joint_gate_decision(
        preflight=_preflight(),
        dataset_qa=qa,
        candidate_manifest=candidate,
        industreal_capability=_industreal(),
        cvat_audit={"labels": 0, "audit_status": "BLOCKED"},
        calibration_artifact=calibration,
        hmm_artifact=hmm,
        yolo_artifact=yolo,
        **_source_paths(
            tmp_path,
            preflight=_preflight(),
            qa=qa,
            candidate=candidate,
            capability=_industreal(),
            cvat={"labels": 0, "audit_status": "BLOCKED"},
        ),
    )
    assert decision["g2"]["status"] == "BLOCKED"
    assert decision["g2"]["checks"]["candidate_formally_admissible"] is False


def test_arbitrary_files_cannot_impersonate_fitted_artifacts(tmp_path: Path):
    artifacts = tuple(tmp_path / name for name in ("cal.json", "hmm.json", "best.pt"))
    for artifact in artifacts:
        artifact.write_bytes(b"verified")
    decision = _decision(tmp_path, artifacts=artifacts)
    assert decision["g1"]["status"] == "BLOCKED"
    assert decision["g1"]["checks"]["risk_calibration_artifact_valid"] is False
    assert decision["g1"]["checks"]["hmm_fit_artifact_valid"] is False
    assert decision["g1"]["checks"]["yolo_best_weight_valid"] is False


def test_yolo_gate_accepts_strict_task_profile_provenance_1_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_yolo(monkeypatch)
    qa = _qa()
    _, _, yolo = _write_valid_artifacts(tmp_path, qa)
    provenance_path = Path(f"{yolo}.provenance.json")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance.update(
        schema_version="display_project.yolo_training_artifact-1.1.0",
        task_profile="fasteners",
        class_names=["screw", "bolt", "screwdriver"],
        ultralytics_version="8.3.test",
        training_parameters={
            "imgsz": 1280,
            "epochs": 80,
            "batch": 8,
            "workers": 4,
            "device": "cuda",
            "seed": 17,
            "deterministic": True,
        },
    )
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    result = _validate_yolo_artifact(
        yolo, expected_dataset_hash=qa["content_sha256"]
    )

    assert result["status"] == "PASS"
    assert result["provenance_schema_version"].endswith("1.1.0")
    assert result["task_profile"] == "fasteners"


def test_yolo_gate_rejects_malformed_task_profile_provenance_1_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_yolo(monkeypatch)
    qa = _qa()
    _, _, yolo = _write_valid_artifacts(tmp_path, qa)
    provenance_path = Path(f"{yolo}.provenance.json")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance.update(
        schema_version="display_project.yolo_training_artifact-1.1.0",
        task_profile="fasteners",
        class_names=["screw", "screw"],
        ultralytics_version="8.3.test",
        training_parameters={
            "imgsz": 1280,
            "epochs": 80,
            "batch": 8,
            "workers": 4,
            "device": "cuda",
            "seed": 17,
            "deterministic": True,
        },
    )
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    result = _validate_yolo_artifact(
        yolo, expected_dataset_hash=qa["content_sha256"]
    )

    assert result["status"] == "FAIL"
    assert "task-profile provenance" in result["error"]


def test_industreal_detection_branch_requires_all_verified_od_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _enable_fake_yolo(monkeypatch)
    qa = _qa()
    calibration, hmm, yolo = _write_valid_artifacts(tmp_path, qa)
    capability = _industreal()
    capability.update(
        capability_status="SIX_CLASS_DETECTION",
        detection_metric_policy="ALLOWED",
        six_class_detection_allowed=True,
        detection_metrics_allowed=True,
        action_step_state_only=False,
        decision_checks={
            "provenance_verified": True,
            "schema_verified": True,
            "hash_verified": True,
            "coverage_verified": True,
            "six_class_schema": True,
        },
    )
    capability["artifact_sha256"] = _canonical_sha256(
        {key: value for key, value in capability.items() if key != "artifact_sha256"}
    )
    kwargs = {
        "preflight": _preflight(),
        "dataset_qa": qa,
        "candidate_manifest": _candidate(qa),
        "cvat_audit": {"labels": 0, "audit_status": "BLOCKED"},
        "calibration_artifact": calibration,
        "hmm_artifact": hmm,
        "yolo_artifact": yolo,
        **_source_paths(
            tmp_path,
            preflight=_preflight(),
            qa=qa,
            candidate=_candidate(qa),
            capability=capability,
            cvat={"labels": 0, "audit_status": "BLOCKED"},
        ),
    }
    blocked = build_joint_gate_decision(industreal_capability=capability, **kwargs)[
        "status"
    ]
    assert blocked == "BLOCKED"

    capability["decision_checks"]["coverage_verified"] = False
    capability["artifact_sha256"] = _canonical_sha256(
        {key: value for key, value in capability.items() if key != "artifact_sha256"}
    )
    kwargs.update(
        _source_paths(
            tmp_path,
            preflight=kwargs["preflight"],
            qa=qa,
            candidate=kwargs["candidate_manifest"],
            capability=capability,
            cvat=kwargs["cvat_audit"],
        )
    )
    blocked = build_joint_gate_decision(industreal_capability=capability, **kwargs)
    assert blocked["g2"]["checks"]["industreal_capability_audited"] is False


def test_joint_gate_passes_only_with_valid_bound_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_yolo(monkeypatch)
    monkeypatch.setattr(gate_audit, "_industreal_capability_valid", lambda *args: True)
    decision = _decision(tmp_path)
    assert decision["status"] == "PASS"
    assert decision["joint_g1_g2_pass"] is True
    assert decision["pilot_power_formal_test_cross_task_allowed"] is False
    for permission in (
        "freeze_paired_metrics_allowed",
        "freeze_method_inventory_allowed",
        "freeze_thresholds_allowed",
        "freeze_mechanism_gate_manifest_allowed",
    ):
        assert verify_joint_gate_permission(decision, permission=permission) == decision
    assert decision["execute_g3_method_panel_allowed"] is False
    with pytest.raises(ValueError, match="does not allow"):
        verify_joint_gate_permission(
            decision, permission="execute_g3_method_panel_allowed"
        )


def test_tampered_or_blocked_decision_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_yolo(monkeypatch)
    decision = _decision(tmp_path)
    decision["execute_g3_method_panel_allowed"] = False
    with pytest.raises(ValueError, match="hash mismatch|G[12] is not PASS"):
        verify_joint_gate_permission(
            decision, permission="execute_g3_method_panel_allowed"
        )

    blocked = _decision(tmp_path / "blocked", ready=False, admissible=False)
    with pytest.raises(ValueError, match="G1 is not PASS"):
        verify_joint_gate_permission(
            blocked, permission="freeze_paired_metrics_allowed"
        )


def test_persisted_decision_rejects_missing_or_extra_check_keys(tmp_path: Path):
    decision = _decision(tmp_path, ready=False, admissible=False)
    decision["g1"]["checks"].pop("yolo_best_weight_valid")
    decision["decision_sha256"] = _canonical_sha256(
        {k: v for k, v in decision.items() if k != "decision_sha256"}
    )
    with pytest.raises(ValueError, match="G1 is not PASS|schema|artifact evidence"):
        verify_joint_gate_permission(
            decision, permission="freeze_paired_metrics_allowed"
        )
    decision = _decision(tmp_path / "extra", ready=False, admissible=False)
    decision["g2"]["checks"]["unexpected"] = True
    decision["decision_sha256"] = _canonical_sha256(
        {k: v for k, v in decision.items() if k != "decision_sha256"}
    )
    with pytest.raises(ValueError, match="G[12] is not PASS|schema|artifact evidence"):
        verify_joint_gate_permission(
            decision, permission="freeze_paired_metrics_allowed"
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda c: c.pop("records"),
        lambda c: c.update(records=[]),
        lambda c: c.update(status="PASS"),
        lambda c: c.update(source_identity_manifest_sha256="0" * 64),
    ],
)
def test_candidate_manifest_integrity_is_fail_closed(tmp_path: Path, mutator):
    qa = _qa()
    candidate = _candidate(qa)
    mutator(candidate)
    calibration, hmm, yolo = _write_valid_artifacts(tmp_path, qa)
    result = build_joint_gate_decision(
        preflight=_preflight(),
        dataset_qa=qa,
        candidate_manifest=candidate,
        industreal_capability=_industreal(),
        cvat_audit={"labels": 0, "audit_status": "BLOCKED"},
        calibration_artifact=calibration,
        hmm_artifact=hmm,
        yolo_artifact=yolo,
        **_source_paths(
            tmp_path,
            preflight=_preflight(),
            qa=qa,
            candidate=candidate,
            capability=_industreal(),
            cvat={"labels": 0, "audit_status": "BLOCKED"},
        ),
    )
    assert result["g2"]["status"] == "BLOCKED"
    assert result["g2"]["checks"]["candidate_payload_hash_valid"] is False


def test_fake_zip_and_missing_ultralytics_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    qa = _qa()
    yolo = tmp_path / "fake.pt"
    with zipfile.ZipFile(yolo, "w") as archive:
        archive.writestr("archive/data.pkl", b"x" * 2048)
        archive.writestr("archive/version", "3")
    digest = hashlib.sha256(yolo.read_bytes()).hexdigest()
    Path(f"{yolo}.provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "display_project.yolo_training_artifact-1.0.0",
                "status": "TRAINED",
                "artifact_sha256": digest,
                "dataset_content_sha256": qa["content_sha256"],
                "base_model_sha256": _digest("base"),
                "training_run_id": "run",
                "split_role": "TRAIN",
                "training_completed": True,
            }
        )
    )
    monkeypatch.setattr(
        gate_audit,
        "_load_yolo_model",
        lambda path: (_ for _ in ()).throw(
            ValueError("Ultralytics/torch runtime unavailable")
        ),
    )
    result = gate_audit._validate_yolo_artifact(
        yolo, expected_dataset_hash=qa["content_sha256"]
    )
    assert result["status"] == "FAIL"


def test_persisted_gate_rejects_missing_swapped_or_mutated_input_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_yolo(monkeypatch)
    monkeypatch.setattr(gate_audit, "_industreal_capability_valid", lambda *args: True)
    decision = _decision(tmp_path)
    preflight_path = Path(decision["input_commitments"]["preflight"]["path"])
    original = preflight_path.read_bytes()
    preflight_path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="source evidence hash mismatch"):
        verify_joint_gate_permission(
            decision, permission="execute_g3_method_panel_allowed"
        )
    preflight_path.write_bytes(original)
    decision["input_commitments"]["preflight"]["path"] = decision["input_commitments"][
        "dataset_qa"
    ]["path"]
    decision["decision_sha256"] = _canonical_sha256(
        {k: v for k, v in decision.items() if k != "decision_sha256"}
    )
    with pytest.raises(ValueError, match="source evidence hash mismatch"):
        verify_joint_gate_permission(
            decision, permission="execute_g3_method_panel_allowed"
        )
    decision = _decision(tmp_path / "missing")
    Path(decision["input_commitments"]["cvat_audit"]["path"]).unlink()
    with pytest.raises(OSError):
        verify_joint_gate_permission(
            decision, permission="execute_g3_method_panel_allowed"
        )


def test_forged_all_true_decision_without_input_sources_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_yolo(monkeypatch)
    monkeypatch.setattr(gate_audit, "_industreal_capability_valid", lambda *args: True)
    decision = _decision(tmp_path)
    decision.pop("input_commitments")
    decision["decision_sha256"] = _canonical_sha256(
        {k: v for k, v in decision.items() if k != "decision_sha256"}
    )
    with pytest.raises(ValueError, match="input commitments"):
        verify_joint_gate_permission(
            decision, permission="execute_g3_method_panel_allowed"
        )


def test_persisted_pass_rejects_self_consistent_empty_committed_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _enable_fake_yolo(monkeypatch)
    monkeypatch.setattr(gate_audit, "_industreal_capability_valid", lambda *args: True)
    decision = _decision(tmp_path)
    preflight_path = Path(decision["input_commitments"]["preflight"]["path"])
    preflight_path.write_text("{}", encoding="utf-8")
    decision["input_commitments"]["preflight"] = gate_audit._input_source_evidence(
        preflight_path, {}
    )
    decision["decision_sha256"] = _canonical_sha256(
        {key: value for key, value in decision.items() if key != "decision_sha256"}
    )
    with pytest.raises(
        ValueError,
        match="input document is empty|persisted decision does not match reconstruction",
    ):
        verify_joint_gate_permission(
            decision, permission="execute_g3_method_panel_allowed"
        )
