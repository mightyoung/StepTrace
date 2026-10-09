import json
from pathlib import Path
import pytest
from display_project.data.industreal_errors import (
    EXPECTED_LABELS_SHA256,
    EXPECTED_MD5,
    build_error_extension_registry,
    validate_error_extension,
    verify_registry_canonical,
)

ROOT = Path("datasets/IndustReal/official")
TAR = ROOT / "assembly_states_errors.tar"
LABELS = ROOT / "assembly_states_errors/labels.json"


def test_extension_validates_official_asset():
    report = validate_error_extension(
        TAR,
        LABELS,
        expected_md5=EXPECTED_MD5,
        expected_labels_sha256=EXPECTED_LABELS_SHA256,
    )
    assert report["status"] == "PASS"
    assert report["row_count"] == 3347
    assert report["recording_count"] == 82
    assert report["error_category_counts"] == {"1": 1796, "2": 120, "3": 523, "4": 908}


def test_extension_registry_never_infers_identity():
    report = validate_error_extension(
        TAR,
        LABELS,
        expected_md5=EXPECTED_MD5,
        expected_labels_sha256=EXPECTED_LABELS_SHA256,
    )
    registry = build_error_extension_registry(report)
    assert {r["operator_id"] for r in registry["records"]} == {"UNKNOWN"}
    assert {r["complete_job_id"] for r in registry["records"]} == {"UNKNOWN"}
    assert registry["psr_labels_with_errors"] == "MISSING_FROM_DOWNLOADED_LABEL_ASSET"


def test_registry_hash_covers_added_cli_field():
    report = validate_error_extension(
        TAR,
        LABELS,
        expected_md5=EXPECTED_MD5,
        expected_labels_sha256=EXPECTED_LABELS_SHA256,
    )
    registry = build_error_extension_registry(report)
    registry["cli_status"] = (
        "BLOCKED_EXTENSION_AVAILABLE_RGB_VIDEO_OPERATOR_JOB_AND_PSR_BLOCKED"
    )
    registry["canonical_sha256"] = ""
    import hashlib

    canonical = (
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    registry["canonical_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert verify_registry_canonical(registry)
    registry["cli_status"] = "tampered"
    assert not verify_registry_canonical(registry)


def _mutated(tmp_path, transform):
    payload = json.loads(LABELS.read_text(encoding="utf-8"))
    transform(payload)
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p["split"].__setitem__(0, "wat"),
        lambda p: p["error_cat"].__setitem__(0, 99),
        lambda p: p["images"].__setitem__(0, "../escape.jpg"),
    ],
)
def test_extension_rejects_malformed_labels(tmp_path, mutator):
    report = validate_error_extension(TAR, _mutated(tmp_path, mutator))
    assert report["status"] == "BLOCKED"


def test_extension_rejects_cross_split_recording(tmp_path):
    report = validate_error_extension(
        TAR,
        _mutated(tmp_path, lambda p: p["split"].__setitem__(1, "test")),
        expected_md5=EXPECTED_MD5,
    )
    assert any("crosses split" in error for error in report["errors"])


def test_extension_rejects_bad_archive_digest():
    report = validate_error_extension(TAR, LABELS, expected_md5="0" * 32)
    assert report["status"] == "BLOCKED"
    assert "tar MD5 mismatch" in report["errors"]
