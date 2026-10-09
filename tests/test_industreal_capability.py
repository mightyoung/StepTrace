import json
import subprocess
import sys

from display_project.data.industreal_capability import (
    audit_industreal_capability,
    build_industreal_capability_artifact,
    canonical_sha256,
)


SIX_CLASSES = [
    "hand",
    "metal bar",
    "partial assembly",
    "screw",
    "bolt",
    "screwdriver",
]


def test_od_partial_provenance_is_blocked():
    result = audit_industreal_capability(
        {
            "od_provenance": {
                "provenance_verified": True,
                "schema_verified": True,
                "hash_verified": False,
                "coverage_verified": True,
                "class_count": 6,
                "classes": [],
            }
        }
    )
    assert result["capability_status"] == "ACTION_STEP_STATE_ONLY"
    assert result["detection_metric_policy"] == "PROHIBITED"


def test_self_reported_od_provenance_is_blocked():
    result = audit_industreal_capability(
        {
            "od_provenance": {
                "provenance_verified": True,
                "schema_verified": True,
                "hash_verified": True,
                "coverage_verified": True,
                "class_count": 6,
                "classes": SIX_CLASSES,
            }
        }
    )
    assert result["capability_status"] == "ACTION_STEP_STATE_ONLY"
    assert result["detection_metrics_allowed"] is False


def test_six_unapproved_classes_do_not_enable_detection_metrics():
    result = audit_industreal_capability(
        {
            "od_provenance": {
                "provenance_verified": True,
                "schema_verified": True,
                "hash_verified": True,
                "coverage_verified": True,
                "class_count": 6,
                "classes": ["a", "b", "c", "d", "e", "f"],
            }
        }
    )
    assert result["capability_status"] == "ACTION_STEP_STATE_ONLY"
    assert result["detection_metric_policy"] == "PROHIBITED"


def test_registry_without_od_provenance_emits_action_step_state_only_artifact(
    tmp_path,
):
    registry = {
        "canonical_sha256": "a" * 64,
        "status": "BLOCKED",
        "video_count": 86,
        "action_labeled_recording_count": 84,
    }
    artifact = build_industreal_capability_artifact(
        registry, registry_path=tmp_path / "registry.json"
    )
    assert artifact["capability_status"] == "ACTION_STEP_STATE_ONLY"
    assert artifact["detection_metric_policy"] == "PROHIBITED"
    assert artifact["six_class_detection_allowed"] is False
    assert artifact["prohibited_detection_metrics"] == [
        "six_class_detection_mAP",
        "six_class_detection_Recall",
    ]
    assert artifact["source_registry"]["canonical_sha256"] == "a" * 64
    assert artifact["artifact_sha256"] == canonical_sha256(
        {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    )


def test_fully_verified_registry_artifact_enables_exact_six_class_branch(
    tmp_path,
):
    import hashlib

    files = {}
    for name, content in {
        "schema": json.dumps(
            {
                "schema_version": "industreal-od-schema-1.0.0",
                "status": "VERIFIED",
                "task": "six_class_detection",
                "classes": SIX_CLASSES,
            }
        ),
        "source": json.dumps({"records": [{"recording_id": "r1", "frames": [{"boxes": [{"class": c} for c in SIX_CLASSES]}]}]}),
    }.items():
        f = tmp_path / f"{name}.dat"
        f.write_text(content, encoding="utf-8")
        files[name] = (str(f), hashlib.sha256(f.read_bytes()).hexdigest())
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps(
            {
                "schema_version": "industreal-od-coverage-1.0.0",
                "status": "PASS",
                "task": "six_class_detection",
                "expected_recording_ids": ["r1"],
                "covered_recording_ids": ["r1"],
                "expected_count": 1,
                "covered_count": 1,
                "labeled_frame_count": 1,
                "box_count": 6,
            }
        ),
        encoding="utf-8",
    )
    files["coverage"] = (
        str(coverage),
        hashlib.sha256(coverage.read_bytes()).hexdigest(),
    )
    provenance = tmp_path / "provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "schema_version": "industreal-od-provenance-1.0.0",
                "status": "VERIFIED",
                "task": "six_class_detection",
                "source": {"path": files["source"][0], "sha256": files["source"][1]},
                "schema": {"path": files["schema"][0], "sha256": files["schema"][1]},
                "coverage": {
                    "path": files["coverage"][0],
                    "sha256": files["coverage"][1],
                },
            }
        ),
        encoding="utf-8",
    )
    files["provenance"] = (
        str(provenance),
        hashlib.sha256(provenance.read_bytes()).hexdigest(),
    )
    registry = {
        "canonical_sha256": "",
        "status": "PASS",
        "od_provenance": {
            "provenance_verified": True,
            "schema_verified": True,
            "hash_verified": True,
            "coverage_verified": True,
            "provenance_path": files["provenance"][0],
            "provenance_sha256": files["provenance"][1],
            "schema_path": files["schema"][0],
            "schema_sha256": files["schema"][1],
            "coverage_path": files["coverage"][0],
            "coverage_sha256": files["coverage"][1],
            "source_path": files["source"][0],
            "source_sha256": files["source"][1],
        },
    }
    from display_project.data.industreal_videos import canonical_sha256 as registry_hash

    registry["canonical_sha256"] = registry_hash(registry)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifact = build_industreal_capability_artifact(
        registry, registry_path=registry_path
    )
    assert artifact["capability_status"] == "SIX_CLASS_DETECTION"
    assert artifact["action_step_state_only"] is False
    assert artifact["detection_metric_policy"] == "ALLOWED"
    assert artifact["detection_metrics_allowed"] is True
    assert artifact["prohibited_detection_metrics"] == []
    assert artifact["od_labels"]["status"] == "VERIFIED"


def test_capability_cli_writes_deterministic_artifact(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"canonical_sha256": "b" * 64, "status": "BLOCKED"}),
        encoding="utf-8",
        newline="\n",
    )
    output = tmp_path / "capability.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/audit_industreal_capability.py",
            str(registry),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["detection_metric_policy"] == "PROHIBITED"
    assert payload["artifact_sha256"] == canonical_sha256(
        {k: v for k, v in payload.items() if k != "artifact_sha256"}
    )
