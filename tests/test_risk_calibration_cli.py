import hashlib
import json

import pytest

from scripts.build_risk_calibration_artifact import build


def _input(path, **updates):
    data = {
        "split_role": "CALIBRATION",
        "partition_hash": "a" * 64,
        "source_digest": "b" * 64,
        "probabilities": [0.1, 0.8, 0.4],
        "labels": [0, 1, 1],
        "truth_store_used": False,
    }
    data.update(updates)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_build_writes_frozen_artifact_and_deterministic_replay(tmp_path):
    source = tmp_path / "input.json"
    _input(source)
    first = build(source, tmp_path / "artifact.json", tmp_path / "replay.json")
    assert first["artifact"]["fitted"] is True
    assert first["artifact"]["frozen"] is True
    assert first["replay"]["fixed_streak"] == {
        "confirm_frames": 4,
        "cooldown_frames": 12,
    }
    replay = json.loads((tmp_path / "replay.json").read_text(encoding="utf-8"))
    expected = dict(replay)
    expected.pop("replay_hash")
    assert (
        replay["replay_hash"]
        == hashlib.sha256(
            json.dumps(
                expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"split_role": "TEST"},
        {"truth_store_used": True},
        {"truth_store_used": None},
        {"video_id": "09"},
    ],
)
def test_build_rejects_non_calibration_or_identity_inputs(tmp_path, updates):
    source = tmp_path / "input.json"
    _input(source, **updates)
    with pytest.raises(ValueError):
        build(source, tmp_path / "artifact.json", tmp_path / "replay.json")
