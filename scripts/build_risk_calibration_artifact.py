"""Build an auditable development-only risk-calibration artifact.

The command deliberately requires an explicit calibration partition and rejects
test/formal/truth-store inputs.  It never infers partition identity from video
or frame names.  A replay descriptor is emitted alongside the frozen artifact
so deterministic replays can be checked without retaining labels in outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from display_project.provenance import canonical_json, sha256_file
from display_project.risk import RiskCalibrator


def _digest(value: str, field: str) -> str:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{field} must be lowercase sha256")
    return value


def build(
    input_path: str | Path, artifact_path: str | Path, replay_path: str | Path
) -> dict[str, Any]:
    source = Path(input_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("calibration input must be an object")
    if payload.get("split_role") != "CALIBRATION":
        raise ValueError("calibration input split_role must be CALIBRATION")
    if payload.get("truth_store_used") is not False:
        raise ValueError("truth_store_used must be explicitly false")
    if any(
        key in payload for key in ("video_id", "frame_id", "test_video", "formal_test")
    ):
        raise ValueError(
            "calibration input must not contain video/frame or formal-test identity"
        )
    partition_hash = _digest(str(payload.get("partition_hash", "")), "partition_hash")
    source_digest = _digest(str(payload.get("source_digest", "")), "source_digest")
    probabilities, labels = payload.get("probabilities"), payload.get("labels")
    if not isinstance(probabilities, list) or not isinstance(labels, list):
        raise ValueError("probabilities and labels are required lists")
    calibrator = RiskCalibrator(
        bins=int(payload.get("bins", 10)),
        calibration_version=str(
            payload.get("calibration_version", "development-cal-v1")
        ),
        risk_threshold=float(payload.get("risk_threshold", 0.5)),
    ).fit(probabilities, labels, split="calibration")
    artifact = calibrator.write_artifact(artifact_path, partition_hash)
    replay_payload = {
        "schema": "display_project.risk_calibration_replay",
        "schema_version": 1,
        "artifact_hash": artifact["artifact_hash"],
        "calibration_input_hash": sha256_file(source),
        "source_digest": source_digest,
        "split_role": "CALIBRATION",
        "fixed_streak": {"confirm_frames": 4, "cooldown_frames": 12},
    }
    replay_payload["replay_hash"] = hashlib.sha256(
        canonical_json(replay_payload).encode("utf-8")
    ).hexdigest()
    replay_target = Path(replay_path)
    replay_target.parent.mkdir(parents=True, exist_ok=True)
    replay_target.write_text(canonical_json(replay_payload) + "\n", encoding="utf-8")
    return {"artifact": artifact, "replay": replay_payload}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--replay", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.input, args.artifact, args.replay),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
