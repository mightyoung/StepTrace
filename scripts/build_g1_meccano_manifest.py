"""Build a frozen, leakage-auditable MECCANO G1 micro-episode manifest.

Official action annotations select complete five-stage micro-episodes and are
later consumed by the offline scorer. The runtime trace extractor receives
only episode bounds and never receives action names or labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from display_project.experiments.g1_derivation import (  # noqa: E402
    load_action_state_map,
    parse_action_csv,
)
from display_project.provenance import canonical_json, sha256_file  # noqa: E402


DEVELOPMENT_VIDEOS = ("0014", "0016", "0018")
CALIBRATION_VIDEOS = ("0007", "0017")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _find_complete_chains(
    rows: list[dict[str, Any]],
    *,
    states: list[str],
    action_to_state: dict[str, str],
) -> list[list[dict[str, Any]]]:
    ordered = sorted(
        (
            row | {"state": action_to_state[row["action_name"]]}
            for row in rows
            if row["action_name"] in action_to_state
        ),
        key=lambda row: (
            row["start_frame"],
            row["end_frame"],
            row["action_name"],
        ),
    )
    chains: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    stage_index = 0
    for row in ordered:
        state = row["state"]
        if state == states[stage_index]:
            current.append(row)
            stage_index += 1
            if stage_index == len(states):
                chains.append(current)
                current = []
                stage_index = 0
        elif stage_index and state == states[0]:
            current = [row]
            stage_index = 1
    return chains


def _source_records(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not (
        isinstance(payload, dict)
        and payload.get("source_identity_manifest_status") == "READY"
        and isinstance(payload.get("records"), list)
    ):
        raise ValueError("candidate identity manifest is not READY")
    records: dict[str, dict[str, Any]] = {}
    for raw in payload["records"]:
        if not isinstance(raw, dict):
            raise ValueError("candidate identity record must be an object")
        video_id = str(raw.get("video_id", ""))
        if video_id in records:
            raise ValueError(f"duplicate candidate identity: {video_id}")
        records[video_id] = raw
    return records


def _write_merged_actions(
    rows: list[dict[str, Any]], output: Path
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "video_id",
                "action_id",
                "action_name",
                "start_frame",
                "end_frame",
            ),
            lineterminator="\n",
        )
        writer.writeheader()
        for row in sorted(
            rows,
            key=lambda item: (
                item["video_id"],
                item["start_frame"],
                item["end_frame"],
                item["action_name"],
            ),
        ):
            writer.writerow(
                {
                    "video_id": row["video_id"],
                    "action_id": row["action_id"],
                    "action_name": row["action_name"],
                    "start_frame": row["start_frame"],
                    "end_frame": row["end_frame"],
                }
            )
    temporary.replace(output)


def build(
    *,
    candidate_manifest: Path,
    train_actions: Path,
    val_actions: Path,
    action_state_map: Path,
    output: Path,
    merged_actions_output: Path,
    margin_frames: int = 30,
    trace_stride: int = 3,
) -> dict[str, Any]:
    if margin_frames < 0 or trace_stride < 1:
        raise ValueError("invalid episode margin or trace stride")
    source_records = _source_records(candidate_manifest)
    mapping = load_action_state_map(action_state_map)
    states = list(mapping["states"])
    action_to_state = dict(mapping["action_to_state"])
    actions = parse_action_csv(train_actions) + parse_action_csv(val_actions)
    allowed = set(DEVELOPMENT_VIDEOS) | set(CALIBRATION_VIDEOS)
    selected_actions = [row for row in actions if row["video_id"] in allowed]
    by_video: dict[str, list[dict[str, Any]]] = {}
    for row in selected_actions:
        by_video.setdefault(row["video_id"], []).append(row)

    records: list[dict[str, Any]] = []
    for video_id in (*DEVELOPMENT_VIDEOS, *CALIBRATION_VIDEOS):
        source = source_records.get(video_id)
        if source is None:
            raise ValueError(f"missing source identity for {video_id}")
        expected_split = (
            "DEVELOPMENT"
            if video_id in DEVELOPMENT_VIDEOS
            else "CALIBRATION"
        )
        expected_source = "train" if expected_split == "DEVELOPMENT" else "val"
        if (
            source.get("dataset_split") != expected_source
            or source.get("tombstone_id") != "NONE"
            or source.get("identity_values_invented") is not False
        ):
            raise ValueError(f"source identity is not admissible for {video_id}")
        chains = _find_complete_chains(
            by_video.get(video_id, []),
            states=states,
            action_to_state=action_to_state,
        )
        if not chains:
            raise ValueError(f"no complete frozen action chain for {video_id}")
        chain = chains[0]
        canonical_group_id = str(source.get("canonical_group_id", ""))
        if not canonical_group_id:
            raise ValueError(f"missing canonical group for {video_id}")
        records.append(
            {
                "video_id": video_id,
                "project_split": expected_split,
                "source_split": expected_source,
                "canonical_root_id": source["canonical_root_id"],
                "canonical_group_id": canonical_group_id,
                "operator_id": source["operator_id"],
                "complete_job_id": source["complete_job_id"],
                "group_hash": hashlib.sha256(
                    canonical_group_id.encode("utf-8")
                ).hexdigest(),
                "episode_start": max(
                    1, int(chain[0]["start_frame"]) - margin_frames
                ),
                "episode_end": int(chain[-1]["end_frame"]) + margin_frames,
                "trace_stride": trace_stride,
                "chain_annotation_digest": _digest(
                    [
                        {
                            "state": item["state"],
                            "action_name": item["action_name"],
                            "start_frame": item["start_frame"],
                            "end_frame": item["end_frame"],
                        }
                        for item in chain
                    ]
                ),
                "runtime_truth_store_allowed": False,
                "formal_test": False,
                "tombstone_id": "NONE",
            }
        )

    _write_merged_actions(selected_actions, merged_actions_output)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "FROZEN_DEVELOPMENT_CALIBRATION",
        "task_id": mapping["task_id"],
        "states": states,
        "selection_policy": {
            "kind": "annotation_assisted_complete_micro_episode_selection",
            "runtime_receives_action_labels": False,
            "runtime_truth_store_allowed": False,
            "one_episode_per_video": True,
            "development_videos": list(DEVELOPMENT_VIDEOS),
            "calibration_videos": list(CALIBRATION_VIDEOS),
            "formal_test_videos": [],
            "tombstone_0009_excluded": True,
            "margin_frames": margin_frames,
            "trace_stride": trace_stride,
        },
        "source_hashes": {
            "candidate_manifest": sha256_file(candidate_manifest),
            "train_actions": sha256_file(train_actions),
            "val_actions": sha256_file(val_actions),
            "action_state_map": sha256_file(action_state_map),
            "merged_selected_actions": sha256_file(merged_actions_output),
        },
        "records": records,
    }
    payload["manifest_sha256"] = _digest(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-manifest",
        type=Path,
        default=Path(
            "research/audits/meccano-yolo-candidate-split-manifest.json"
        ),
    )
    parser.add_argument(
        "--train-actions",
        type=Path,
        default=Path(
            "data/g1/official-actions/MECCANO_train_actions.csv"
        ),
    )
    parser.add_argument(
        "--val-actions",
        type=Path,
        default=Path("data/g1/official-actions/MECCANO_val_actions.csv"),
    )
    parser.add_argument(
        "--action-state-map",
        type=Path,
        default=Path("configs/g1/meccano-action-state-map.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/g1/meccano-g1-episode-manifest.json"),
    )
    parser.add_argument(
        "--merged-actions-output",
        type=Path,
        default=Path(
            "data/g1/official-actions/MECCANO_train_val_selected_actions.csv"
        ),
    )
    parser.add_argument("--margin-frames", type=int, default=30)
    parser.add_argument("--trace-stride", type=int, default=3)
    args = parser.parse_args()
    result = build(
        candidate_manifest=args.candidate_manifest,
        train_actions=args.train_actions,
        val_actions=args.val_actions,
        action_state_map=args.action_state_map,
        output=args.output,
        merged_actions_output=args.merged_actions_output,
        margin_frames=args.margin_frames,
        trace_stride=args.trace_stride,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "manifest_sha256": result["manifest_sha256"],
                "records": len(result["records"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
