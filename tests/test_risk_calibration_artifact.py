import json

import pytest

from display_project.risk.calibrator import RiskCalibrator

HASH = "a" * 64


def fitted():
    return RiskCalibrator(bins=4, calibration_version="cal-v2", risk_threshold=0.4).fit(
        [0.1, 0.8, 0.4], [0, 1, 1]
    )


def test_artifact_roundtrip_and_atomic_utf8_write(tmp_path):
    original = fitted()
    artifact = original.write_artifact(tmp_path / "calibration.json", HASH)
    assert artifact["split_role"] == "CALIBRATION"
    assert artifact["fitted"] is True and artifact["frozen"] is True
    assert "labels" not in json.dumps(artifact)
    assert (tmp_path / "calibration.json").read_bytes()[:3] != b"\xef\xbb\xbf"
    loaded = RiskCalibrator.from_artifact(
        tmp_path / "calibration.json", expected_partition_hash=HASH
    )
    assert loaded.evaluate(0.25) == original.evaluate(0.25)
    with pytest.raises(RuntimeError):
        loaded.fit([0.1], [0])


@pytest.mark.parametrize(
    "mutation",
    [
        {"split_role": "TEST"},
        {"offset": 1.01},
        {"risk_threshold": -0.1},
        {"fitted": False},
    ],
)
def test_artifact_tamper_and_wrong_split_fail_closed(mutation):
    artifact = fitted().to_artifact(HASH)
    artifact.update(mutation)
    with pytest.raises(ValueError):
        RiskCalibrator.from_artifact(artifact)


def test_artifact_hash_and_partition_binding_are_checked():
    artifact = fitted().to_artifact(HASH)
    artifact["artifact_hash"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        RiskCalibrator.from_artifact(artifact)
    with pytest.raises(ValueError, match="partition"):
        RiskCalibrator.from_artifact(
            fitted().to_artifact(HASH), expected_partition_hash="b" * 64
        )


def test_artifact_requires_lowercase_sha256_partition_hash():
    cal = fitted()
    for value in ("short", "A" * 64, "g" * 64):
        with pytest.raises(ValueError):
            cal.to_artifact(value)


def test_write_is_idempotent_but_never_overwrites(tmp_path):
    path = tmp_path / "calibration.json"
    cal = fitted()
    first = cal.write_artifact(path, HASH)
    assert cal.write_artifact(path, HASH) == first
    other = RiskCalibrator(calibration_version="other").fit([0.3], [1])
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        other.write_artifact(path, HASH)
    assert path.read_bytes() == before


def test_external_artifact_hash_rejects_rehashed_payload():
    artifact = fitted().to_artifact(HASH)
    expected = artifact["artifact_hash"]
    artifact["offset"] = 0.25
    payload = {key: value for key, value in artifact.items() if key != "artifact_hash"}
    artifact["artifact_hash"] = RiskCalibrator._artifact_hash(payload)
    with pytest.raises(ValueError, match="hash"):
        RiskCalibrator.from_artifact(
            artifact, expected_partition_hash=HASH, expected_artifact_hash=expected
        )


def test_artifact_rejects_noncanonical_calibration_version_whitespace():
    artifact = fitted().to_artifact(HASH)
    payload = {key: value for key, value in artifact.items() if key != "artifact_hash"}
    payload["calibration_version"] = " cal-v2 "
    artifact = {**payload, "artifact_hash": RiskCalibrator._artifact_hash(payload)}
    with pytest.raises(ValueError, match="calibration_version"):
        RiskCalibrator.from_artifact(artifact)
