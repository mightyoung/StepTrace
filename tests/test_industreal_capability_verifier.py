from pathlib import Path
import hashlib
import json

from display_project.data.industreal_capability import (
    build_industreal_capability_artifact,
    canonical_sha256,
    validate_industreal_capability,
)

CLASSES = ["hand", "metal bar", "partial assembly", "screw", "bolt", "screwdriver"]


def _valid(tmp_path):
    schema = tmp_path / "schema.json"
    source = tmp_path / "source.json"
    coverage = tmp_path / "coverage.json"
    provenance = tmp_path / "provenance.json"
    schema.write_text(
        json.dumps(
            {
                "schema_version": "industreal-od-schema-1.0.0",
                "status": "VERIFIED",
                "task": "six_class_detection",
                "classes": CLASSES,
            }
        ),
        encoding="utf-8",
    )
    source.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "recording_id": "r1",
                        "frames": [{"boxes": [{"class": c} for c in CLASSES]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
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

    def h(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    provenance.write_text(
        json.dumps(
            {
                "schema_version": "industreal-od-provenance-1.0.0",
                "status": "VERIFIED",
                "task": "six_class_detection",
                "source": {"path": str(source), "sha256": h(source)},
                "schema": {"path": str(schema), "sha256": h(schema)},
                "coverage": {"path": str(coverage), "sha256": h(coverage)},
            }
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    reg = {"canonical_sha256": "", "status": "PASS", "od_provenance": {}}
    od = reg["od_provenance"]
    for n, p in (
        ("provenance", provenance),
        ("schema", schema),
        ("coverage", coverage),
        ("source", source),
    ):
        od[f"{n}_path"] = str(p)
        od[f"{n}_sha256"] = h(p)
    reg["canonical_sha256"] = canonical_sha256(reg)
    # registry canonical form uses the industreal registry convention
    body = dict(reg)
    body["canonical_sha256"] = ""
    reg["canonical_sha256"] = hashlib.sha256(
        (json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()
    registry.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return registry, build_industreal_capability_artifact(reg, registry_path=registry)


def test_verifier_reopens_and_rebuilds_allowed_artifact(tmp_path):
    _, artifact = _valid(tmp_path)
    assert artifact["capability_status"] == "SIX_CLASS_DETECTION"
    assert validate_industreal_capability(artifact) == (True, [])


def test_verifier_rejects_self_hashed_mutated_allowed_artifact(tmp_path):
    _, artifact = _valid(tmp_path)
    forged = dict(artifact, detection_evaluation_reason="forged")
    forged["artifact_sha256"] = canonical_sha256(
        {k: v for k, v in forged.items() if k != "artifact_sha256"}
    )
    ok, reasons = validate_industreal_capability(forged)
    assert not ok and "source_rebuild_mismatch" in reasons


def test_verifier_rejects_bound_path_substitution(tmp_path):
    _, artifact = _valid(tmp_path)
    forged = json.loads(json.dumps(artifact))
    forged["source_evidence"]["source"]["path"] = forged["source_evidence"]["schema"][
        "path"
    ]
    forged["artifact_sha256"] = canonical_sha256(
        {k: v for k, v in forged.items() if k != "artifact_sha256"}
    )
    assert validate_industreal_capability(forged)[0] is False


def test_verifier_rejects_bound_source_mutation_and_missing_registry(tmp_path):
    registry, artifact = _valid(tmp_path)
    source = Path(artifact["source_evidence"]["source"]["path"])
    source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert validate_industreal_capability(artifact)[0] is False
    registry.unlink()
    assert validate_industreal_capability(artifact)[0] is False


def test_action_step_state_only_is_honest_and_verifiable(tmp_path):
    registry = tmp_path / "registry.json"
    reg = {"canonical_sha256": "", "status": "BLOCKED"}
    body = dict(reg)
    body["canonical_sha256"] = ""
    reg["canonical_sha256"] = hashlib.sha256(
        (json.dumps(body, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()
    registry.write_text(
        json.dumps(reg, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    artifact = build_industreal_capability_artifact(reg, registry_path=registry)
    assert artifact["capability_status"] == "ACTION_STEP_STATE_ONLY"
    assert validate_industreal_capability(artifact) == (True, [])
