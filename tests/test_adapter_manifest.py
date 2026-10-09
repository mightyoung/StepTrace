from __future__ import annotations

from pathlib import Path

import pytest

import display_project.experiments.adapter_manifest as manifest_module
from display_project.experiments.adapter_manifest import (
    G3_METHOD_NAMES,
    canonical_sha256,
    compile_adapter_manifest,
)
from display_project.experiments.paired_runner import METHOD_NAMES
from display_project.risk import RiskCalibrator

TASK = {"schema_version": 1, "task_id": "t", "steps": [{"id": "a"}, {"id": "b"}]}


def manifest(**updates):
    value = {
        "schema_version": 1,
        "profile": "SOFTWARE_FIXTURE",
        "task_config": TASK,
        "fixed_streak": {"confirm_frames": 4, "cooldown_frames": 12},
        "methods": [
            {"name": name, "factory": "unavailable", "config": {}}
            for name in METHOD_NAMES
        ],
    }
    value.update(updates)
    return value


def replace_method(value, name, **replacement):
    value["methods"] = [
        ({"name": name, **replacement} if item["name"] == name else item)
        for item in value["methods"]
    ]
    return value


def calibration_artifact(tmp_path: Path):
    partition_hash = "1" * 64
    calibrator = RiskCalibrator().fit([0.2, 0.8], [0, 1])
    path = tmp_path / "calibration.json"
    artifact = calibrator.write_artifact(path, partition_hash)
    return path, artifact["artifact_hash"], partition_hash


def test_manifest_hash_is_order_independent():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_report_contains_manifest_task_and_per_method_hashes():
    compiled = compile_adapter_manifest(manifest())
    anchors = compiled.report()["frozen_anchors"]
    assert len(anchors["manifest_hash"]) == 64
    assert len(anchors["task_source_hash"]) == 64
    assert len(anchors["task_config_hash"]) == 64
    assert all(len(item.config_hash) == 64 for item in compiled.preflight)


def test_forbidden_profiles_and_asset_references_fail_closed():
    with pytest.raises(ValueError):
        compile_adapter_manifest(manifest(profile="PILOT"))
    with pytest.raises(ValueError, match="pilot/formal"):
        compile_adapter_manifest(
            manifest(task_config={**TASK, "task_id": "formal_test_asset"})
        )


def test_hmm_without_fit_artifact_and_proposed_without_calibration_are_blocked():
    value = replace_method(
        manifest(),
        "proposed",
        factory="render_sop",
        config={
            "policy": "proposed",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        },
    )
    compiled = compile_adapter_manifest(value)
    status = {item.name: item for item in compiled.preflight}
    assert status["hmm_or_crf"].error_code == "HMM_FIT_ARTIFACT_MISSING"
    assert status["proposed"].error_code == "CALIBRATION_ARTIFACT_MISSING"


def test_proposed_rejects_uppercase_and_missing_hash_anchors(tmp_path: Path):
    path, artifact_hash, partition_hash = calibration_artifact(tmp_path)
    config = {
        "policy": "proposed",
        "detector_kind": "grounding_dino",
        "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        "calibration_artifact": str(path),
        "calibration_artifact_hash": artifact_hash.upper(),
        "calibration_partition_hash": partition_hash,
    }
    compiled = compile_adapter_manifest(
        replace_method(manifest(), "proposed", factory="render_sop", config=config)
    )
    proposed = {item.name: item for item in compiled.preflight}["proposed"]
    assert proposed.error_code == "CALIBRATION_ARTIFACT_HASH_INVALID"
    del config["calibration_artifact_hash"]
    compiled = compile_adapter_manifest(
        replace_method(manifest(), "proposed", factory="render_sop", config=config)
    )
    proposed = {item.name: item for item in compiled.preflight}["proposed"]
    assert proposed.error_code == "CALIBRATION_ARTIFACT_HASH_INVALID"


def test_proposed_validates_calibration_artifact_preflight(tmp_path: Path):
    path, artifact_hash, partition_hash = calibration_artifact(tmp_path)
    config = {
        "policy": "proposed",
        "detector_kind": "grounding_dino",
        "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        "calibration_artifact": str(path),
        "calibration_artifact_hash": artifact_hash,
        "calibration_partition_hash": partition_hash,
    }
    compiled = compile_adapter_manifest(
        replace_method(manifest(), "proposed", factory="render_sop", config=config)
    )
    proposed = {item.name: item for item in compiled.preflight}["proposed"]
    assert proposed.status == "READY"
    assert "proposed" in compiled.adapters


def test_missing_dependency_path_is_blocked(tmp_path: Path):
    value = replace_method(
        manifest(),
        "vlm_only",
        factory="vlm_audit_only",
        config={"review_json": str(tmp_path / "missing.json")},
    )
    compiled = compile_adapter_manifest(value)
    item = {check.name: check for check in compiled.preflight}["vlm_only"]
    assert item.error_code == "REVIEW_JSON_INVALID"


def test_nonexistent_output_root_is_validated_without_preflight_creation(
    tmp_path: Path,
):
    output_root = tmp_path / "new" / "fixed-output"
    value = replace_method(
        manifest(),
        "fixed_streak",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
            "output_root": str(output_root),
        },
    )
    compiled = compile_adapter_manifest(value)
    fixed = {item.name: item for item in compiled.preflight}["fixed_streak"]
    assert fixed.status == "READY"
    assert compiled.adapters["fixed_streak"].output_root == str(output_root.resolve())
    assert not output_root.exists()


def test_output_root_cannot_be_an_existing_file(tmp_path: Path):
    output_root = tmp_path / "output-file"
    output_root.write_text("not a directory", encoding="utf-8")
    value = replace_method(
        manifest(),
        "fixed_streak",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
            "output_root": str(output_root),
        },
    )
    compiled = compile_adapter_manifest(value)
    fixed = {item.name: item for item in compiled.preflight}["fixed_streak"]
    assert fixed.error_code == "DEPENDENCY_PATH_INVALID"
    assert "not a directory" in fixed.reason


def test_fixed_streak_is_explicit_and_frozen():
    with pytest.raises(ValueError, match="explicitly"):
        compile_adapter_manifest(manifest(fixed_streak={}))
    with pytest.raises(ValueError, match="frozen"):
        compile_adapter_manifest(
            manifest(fixed_streak={"confirm_frames": 3, "cooldown_frames": 12})
        )


def test_non_fixture_requires_exact_full_schedule_and_lowercase_anchors(tmp_path: Path):
    task = tmp_path / "task.yaml"
    task.write_text(
        "schema_version: 1\ntask_id: t\nsteps: [{id: a}]\n", encoding="utf-8"
    )
    value = manifest(
        profile="DEVELOPMENT_ONLY",
        task_config=str(task),
        schedule=list(METHOD_NAMES[:-1]),
        anchors={key: "a" * 64 for key in manifest_module.ANCHOR_KEYS},
    )
    with pytest.raises(ValueError, match="complete profile method set"):
        compile_adapter_manifest(value)
    value["schedule"] = list(METHOD_NAMES)
    value["anchors"]["code_hash"] = "A" * 64
    with pytest.raises(ValueError, match="lowercase"):
        compile_adapter_manifest(value)


def test_g3_evaluation_profile_is_exact_and_non_fixture(tmp_path: Path):
    task = tmp_path / "task.yaml"
    task.write_text(
        "schema_version: 1\ntask_id: t\nsteps: [{id: a}]\n", encoding="utf-8"
    )
    value = manifest(
        profile="DEVELOPMENT_G3_EVALUATION",
        task_config=str(task),
        schedule=list(G3_METHOD_NAMES),
        anchors={key: "a" * 64 for key in manifest_module.ANCHOR_KEYS},
    )
    value["methods"] = [
        item for item in value["methods"] if item["name"] in G3_METHOD_NAMES
    ]
    compiled = compile_adapter_manifest(value)
    assert compiled.profile == "DEVELOPMENT_G3_EVALUATION"
    assert compiled.blocked is True
    value["profile"] = "G3_EVALUATION"
    with pytest.raises(ValueError, match="profile is not allowed"):
        compile_adapter_manifest(value)


def test_g3_profile_rejects_non_candidate_method_set(tmp_path: Path):
    task = tmp_path / "task.yaml"
    task.write_text(
        "schema_version: 1\ntask_id: t\nsteps: [{id: a}]\n", encoding="utf-8"
    )
    value = manifest(
        profile="DEVELOPMENT_G3_EVALUATION",
        task_config=str(task),
        schedule=list(METHOD_NAMES),
        anchors={key: "a" * 64 for key in manifest_module.ANCHOR_KEYS},
    )
    with pytest.raises(ValueError, match="exactly"):
        compile_adapter_manifest(value)


def test_import_error_is_not_masked(monkeypatch):
    def fail_import():
        raise ImportError("adapters unavailable")

    monkeypatch.setattr(manifest_module, "_default_registry", fail_import)
    with pytest.raises(ImportError, match="adapters unavailable"):
        compile_adapter_manifest(manifest())


def test_constructor_type_error_is_not_retried_positionally():
    calls = []

    def positional_only(value, /):
        calls.append(value)
        return value

    compiled = compile_adapter_manifest(
        manifest(), registry={"unavailable": positional_only}
    )
    fixed = {item.name: item for item in compiled.preflight}["fixed_streak"]
    assert fixed.error_code == "ADAPTER_CONSTRUCTION_FAILED"
    assert calls == []


def test_render_requires_explicit_detector_kind():
    value = replace_method(
        manifest(),
        "fixed_streak",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        },
    )
    compiled = compile_adapter_manifest(value)
    fixed = {item.name: item for item in compiled.preflight}["fixed_streak"]
    assert fixed.error_code == "DETECTOR_KIND_INVALID"


def test_manifest_cannot_expand_processor_allowlist():
    value = replace_method(
        manifest(),
        "fixed_streak",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "tests.fake:processor",
            "processor_allowlist": ["tests.fake:processor"],
        },
    )
    compiled = compile_adapter_manifest(value)
    fixed = {item.name: item for item in compiled.preflight}["fixed_streak"]
    assert fixed.error_code == "METHOD_CONFIG_INVALID"


def test_yolo_detector_freeze_requires_explicit_numeric_settings():
    value = replace_method(
        manifest(),
        "yolo",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "yolo",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        },
    )
    compiled = compile_adapter_manifest(value)
    yolo = {item.name: item for item in compiled.preflight}["yolo"]
    assert yolo.error_code == "YOLO_CONFIG_MISSING"


def test_duplicate_output_roots_are_blocked(tmp_path: Path):
    shared = str(tmp_path / "shared")
    value = replace_method(
        manifest(),
        "fixed_streak",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
            "output_root": shared,
        },
    )
    value = replace_method(
        value,
        "yolo",
        factory="render_sop",
        config={
            "policy": "no_calibration",
            "detector_kind": "yolo",
            "yolo_image_size": 1280,
            "yolo_iou_threshold": 0.5,
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
            "output_root": shared,
        },
    )
    compiled = compile_adapter_manifest(value)
    checks = {item.name: item for item in compiled.preflight}
    assert checks["fixed_streak"].error_code == "OUTPUT_ROOT_REUSED"
    assert checks["yolo"].error_code == "OUTPUT_ROOT_REUSED"


def test_output_and_evidence_paths_may_not_overlap(tmp_path: Path):
    output = tmp_path / "fixed-output"
    review = output / "review.json"
    value = replace_method(
        manifest(),
        "fixed_streak",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
            "output_root": str(output),
        },
    )
    value = replace_method(
        value,
        "vlm_only",
        factory="vlm_audit_only",
        config={"review_json": str(review)},
    )
    compiled = compile_adapter_manifest(value)
    checks = {item.name: item for item in compiled.preflight}
    assert checks["fixed_streak"].error_code == "OUTPUT_EVIDENCE_PATH_OVERLAP"
    assert checks["vlm_only"].error_code == "OUTPUT_EVIDENCE_PATH_OVERLAP"


def test_hmm_fixture_processor_is_allowed_only_in_fixture_profile():
    value = replace_method(
        manifest(),
        "hmm_or_crf",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        },
    )
    compiled = compile_adapter_manifest(value)
    hmm = {item.name: item for item in compiled.preflight}["hmm_or_crf"]
    assert hmm.status == "READY"


def test_evidence_path_cannot_be_reused_by_methods(tmp_path: Path):
    shared = tmp_path / "shared-evidence.json"
    shared.write_text("{}", encoding="utf-8")
    value = replace_method(
        manifest(),
        "hmm_or_crf",
        factory="unavailable",
        config={
            "fit_artifact": str(shared),
            "fit_artifact_hash": "a" * 64,
        },
    )
    value = replace_method(
        value,
        "proposed",
        factory="render_sop",
        config={
            "policy": "proposed",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
            "calibration_artifact": str(shared),
            "calibration_artifact_hash": "b" * 64,
            "calibration_partition_hash": "c" * 64,
        },
    )
    compiled = compile_adapter_manifest(value)
    checks = {item.name: item for item in compiled.preflight}
    assert checks["hmm_or_crf"].error_code == "EVIDENCE_PATH_REUSED"
    assert checks["proposed"].error_code == "EVIDENCE_PATH_REUSED"


def test_non_builtin_processor_path_is_blocked():
    value = replace_method(
        manifest(),
        "fixed_streak",
        factory="render_sop",
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "pathlib:Path",
        },
    )
    compiled = compile_adapter_manifest(value)
    fixed = {item.name: item for item in compiled.preflight}["fixed_streak"]
    assert fixed.error_code == "PROCESSOR_NOT_ALLOWLISTED"


def test_no_calibration_certificate_requires_uncalibrated_policy_and_raw_threshold():
    base = {
        "detector_kind": "grounding_dino",
        "processor_name": "display_project.experiments.adapters:software_fixture_processor",
    }
    value = replace_method(
        manifest(),
        "no_calibration_certificate",
        factory="render_sop",
        config={**base, "policy": "no_calibration"},
    )
    compiled = compile_adapter_manifest(value)
    item = {check.name: check for check in compiled.preflight}[
        "no_calibration_certificate"
    ]
    assert item.error_code == "RENDER_POLICY_INVALID"

    value = replace_method(
        manifest(),
        "no_calibration_certificate",
        factory="render_sop",
        config={**base, "policy": "uncalibrated_certificate"},
    )
    compiled = compile_adapter_manifest(value)
    item = {check.name: check for check in compiled.preflight}[
        "no_calibration_certificate"
    ]
    assert item.error_code == "RAW_RISK_THRESHOLD_INVALID"

    for threshold in (-0.01, 1.01, True):
        value = replace_method(
            manifest(),
            "no_calibration_certificate",
            factory="render_sop",
            config={
                **base,
                "policy": "uncalibrated_certificate",
                "raw_risk_threshold": threshold,
            },
        )
        compiled = compile_adapter_manifest(value)
        item = {check.name: check for check in compiled.preflight}[
            "no_calibration_certificate"
        ]
        assert item.error_code == "RAW_RISK_THRESHOLD_INVALID"


def test_uncalibrated_certificate_rejects_calibration_anchors(tmp_path: Path):
    artifact = tmp_path / "calibration.json"
    artifact.write_text("{}", encoding="utf-8")
    value = replace_method(
        manifest(),
        "no_calibration_certificate",
        factory="render_sop",
        config={
            "policy": "uncalibrated_certificate",
            "raw_risk_threshold": 0.5,
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
            "calibration_artifact": str(artifact),
            "calibration_artifact_hash": "a" * 64,
            "calibration_partition_hash": "b" * 64,
        },
    )
    compiled = compile_adapter_manifest(value)
    item = {check.name: check for check in compiled.preflight}[
        "no_calibration_certificate"
    ]
    assert item.error_code == "UNCALIBRATED_CALIBRATION_FORBIDDEN"


def test_perception_ablations_are_fixed_streak_with_matching_detector_kind():
    builtin = "display_project.experiments.adapters:software_fixture_processor"
    cases = (
        ("yolo", "no_calibration", "yolo", "RENDER_POLICY_INVALID"),
        ("yolo", "fixed_streak", "grounding_dino", "DETECTOR_KIND_MISMATCH"),
        (
            "grounding_dino",
            "uncalibrated_certificate",
            "grounding_dino",
            "RENDER_POLICY_MISMATCH",
        ),
        ("grounding_dino", "fixed_streak", "yolo", "DETECTOR_KIND_MISMATCH"),
    )
    for method, policy, detector, error_code in cases:
        config = {
            "policy": policy,
            "detector_kind": detector,
            "processor_name": builtin,
        }
        if detector == "yolo":
            config.update(yolo_image_size=1280, yolo_iou_threshold=0.5)
        if policy == "uncalibrated_certificate":
            config["raw_risk_threshold"] = 0.5
        compiled = compile_adapter_manifest(
            replace_method(manifest(), method, factory="render_sop", config=config)
        )
        item = {check.name: check for check in compiled.preflight}[method]
        assert item.error_code == error_code


def test_decision_ablations_require_identical_perception_anchors():
    builtin = "display_project.experiments.adapters:software_fixture_processor"
    value = manifest()
    value = replace_method(
        value,
        "fixed_streak",
        factory="render_sop",
        model_hash="a" * 64,
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "device": "cpu",
            "processor_name": builtin,
        },
    )
    value = replace_method(
        value,
        "no_calibration_certificate",
        factory="render_sop",
        model_hash="a" * 64,
        config={
            "policy": "uncalibrated_certificate",
            "raw_risk_threshold": 0.5,
            "detector_kind": "grounding_dino",
            "device": "cpu",
            "processor_name": builtin,
        },
    )
    value = replace_method(
        value,
        "proposed",
        factory="render_sop",
        model_hash="b" * 64,
        config={
            "policy": "proposed",
            "detector_kind": "grounding_dino",
            "device": "cpu",
            "processor_name": builtin,
        },
    )
    compiled = compile_adapter_manifest(value)
    checks = {item.name: item for item in compiled.preflight}
    for name in ("fixed_streak", "no_calibration_certificate", "proposed"):
        assert checks[name].error_code == "PERCEPTION_ANCHOR_MISMATCH"


def test_fixed_policy_method_hash_includes_frozen_streak():
    item = {
        "name": "fixed_streak",
        "factory": "render_sop",
        "config": {
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        },
    }
    value = replace_method(
        manifest(), "fixed_streak", **{k: v for k, v in item.items() if k != "name"}
    )
    compiled = compile_adapter_manifest(value)
    check = {entry.name: entry for entry in compiled.preflight}["fixed_streak"]
    assert check.config_hash == canonical_sha256(
        {"method": item, "fixed_streak": value["fixed_streak"]}
    )


def test_no_calibration_cannot_use_zero_authority_vlm_factory(tmp_path: Path):
    task = tmp_path / "task.yaml"
    task.write_text(
        "schema_version: 1\ntask_id: t\nsteps: [{id: a}]\n", encoding="utf-8"
    )
    value = manifest(
        profile="DEVELOPMENT_ONLY",
        task_config=str(task),
        schedule=list(METHOD_NAMES),
        anchors={key: "a" * 64 for key in manifest_module.ANCHOR_KEYS},
    )
    value = replace_method(
        value,
        "no_calibration_certificate",
        factory="vlm_audit_only",
        model_hash="b" * 64,
        config={},
    )
    compiled = compile_adapter_manifest(value)
    item = {check.name: check for check in compiled.preflight}[
        "no_calibration_certificate"
    ]
    assert item.error_code == "METHOD_FACTORY_MISMATCH"


def test_hmm_fixture_escape_is_rejected_outside_fixture_profile(tmp_path: Path):
    task = tmp_path / "task.yaml"
    task.write_text(
        "schema_version: 1\ntask_id: t\nsteps: [{id: a}]\n", encoding="utf-8"
    )
    value = manifest(
        profile="DEVELOPMENT_ONLY",
        task_config=str(task),
        schedule=list(METHOD_NAMES),
        anchors={key: "a" * 64 for key in manifest_module.ANCHOR_KEYS},
    )
    value = replace_method(
        value,
        "hmm_or_crf",
        factory="render_sop",
        model_hash="b" * 64,
        config={
            "policy": "fixed_streak",
            "detector_kind": "grounding_dino",
            "device": "cpu",
            "processor_name": "display_project.experiments.adapters:software_fixture_processor",
        },
    )
    compiled = compile_adapter_manifest(value)
    hmm = {item.name: item for item in compiled.preflight}["hmm_or_crf"]
    assert hmm.error_code == "PROCESSOR_NOT_ALLOWLISTED"
