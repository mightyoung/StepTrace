"""Bind each verified IndustReal RGB recording to its complete-job identity.

This migration preserves every previously audited video byte/hash/decode field.
It only replaces the unknown complete-job placeholder with the authoritative
complete-recording unit and recomputes the artifact's canonical hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from display_project.data.industreal_videos import canonical_sha256, write_inventory


def _registry_hash(payload: dict) -> str:
    body = dict(payload)
    body["canonical_sha256"] = ""
    encoded = (
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def refresh_registry(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("canonical_sha256") != _registry_hash(payload):
        raise ValueError("IndustReal registry canonical hash mismatch")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("IndustReal registry records are missing")
    for record in records:
        recording_id = str(record.get("recording_id", ""))
        if not recording_id:
            raise ValueError("IndustReal registry recording_id is missing")
        record["complete_job_id"] = f"INDUSTREAL-RECORDING-{recording_id}"
        record["complete_job_identity_status"] = "COMPLETE_RECORDING_BOUND"
        record["complete_job_token"] = recording_id
    payload["complete_job_id_status"] = "COMPLETE_RECORDING_BOUND"
    payload["operator_mapping_status"] = "UNKNOWN"
    payload["canonical_sha256"] = _registry_hash(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def refresh(input_path: Path, output_path: Path) -> dict:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if payload.get("status") != "PASS":
        raise ValueError("IndustReal RGB audit status must be PASS")
    if payload.get("canonical_sha256") != canonical_sha256(payload):
        raise ValueError("IndustReal RGB audit canonical hash mismatch")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 86:
        raise ValueError("IndustReal RGB audit must contain exactly 86 recordings")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("IndustReal RGB audit record must be an object")
        recording_id = str(record.get("source_recording_id", ""))
        if not recording_id:
            raise ValueError("IndustReal source_recording_id is missing")
        record["complete_job_id"] = f"INDUSTREAL-RECORDING-{recording_id}"
        record["unit_status"] = "AUTHORITATIVE_COMPLETE_RECORDING_UNIT"
    payload["complete_job_identity_status"] = "COMPLETE_RECORDING_BOUND"
    payload["operator_identity_status"] = "BLOCKED_UNKNOWN_OPERATOR_ID"
    payload["canonical_sha256"] = canonical_sha256(payload)
    write_inventory(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("research/audits/industreal-rgb-videos.json"),
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("research/audits/industreal-registry.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("research/audits/industreal-rgb-videos.json"),
    )
    args = parser.parse_args()
    payload = refresh(args.input, args.output)
    registry = refresh_registry(args.registry)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "record_count": len(payload["records"]),
                "complete_job_identity_status": payload[
                    "complete_job_identity_status"
                ],
                "operator_identity_status": payload["operator_identity_status"],
                "canonical_sha256": payload["canonical_sha256"],
                "registry_canonical_sha256": registry["canonical_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
