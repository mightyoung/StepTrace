"""Read-only derivation of frozen G1 calibration and HMM inputs.

This module intentionally keeps annotation/scoring provenance separate from the
runtime trace.  It accepts only frozen manifests and exported observations;
video files and truth stores are never opened.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from display_project.provenance import canonical_json, sha256_file

DEV_IDS = {f"{i:04d}" for i in (1, 2, 3, 4, 5, 6, 13, 14, 15, 16, 18)}
CAL_IDS = {"0007", "0017"}
REQUIRED_ACTION_COLUMNS = {"video_id", "action_id", "action_name", "start_frame", "end_frame"}
TRACE_FIELDS = {
    "observation_id", "video_id", "group_hash", "sequence_index",
    "frame_start", "frame_end", "candidate_step", "probability",
    "luma_mean", "luma_std", "frame_motion",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_obj(value: Any) -> str:
    return _hash_bytes(canonical_json(value).encode("utf-8"))


def canonical_video_id(value: Any) -> str:
    text = str(value).strip()
    if text.isdigit() and 1 <= int(text) <= 99:
        return f"{int(text):04d}"
    if len(text) == 4 and text.isdigit():
        return text
    raise ValueError(f"invalid MECCANO video_id: {value!r}")


def parse_frame_reference(value: Any) -> int:
    """Parse an official MECCANO frame reference such as ``00010.jpg``."""
    text = str(value).strip()
    match = re.fullmatch(r"(\d+)(?:\.[A-Za-z0-9]+)?", text)
    if match is None:
        raise ValueError(f"invalid MECCANO frame reference: {value!r}")
    return int(match.group(1))



def parse_action_csv(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not REQUIRED_ACTION_COLUMNS.issubset(reader.fieldnames):
            raise ValueError("official action CSV schema requires video_id/action_id/action_name/start_frame/end_frame")
        rows = []
        for row in reader:
            video_id = canonical_video_id(row["video_id"])
            start = parse_frame_reference(row["start_frame"])
            end = parse_frame_reference(row["end_frame"])
            if start < 0 or end < start:
                raise ValueError("invalid action frame interval")
            rows.append({"video_id": video_id, "action_id": str(row["action_id"]).strip(), "action_name": str(row["action_name"]).strip(), "start_frame": start, "end_frame": end})
    return rows


def load_action_state_map(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("action-state map must be an object")
    required = {
        "schema_version", "task_id", "states", "action_to_state", "proxy_labels"
    }
    if set(payload) != required or payload["schema_version"] != 1:
        raise ValueError("action-state map schema mismatch")
    states = payload["states"]
    mappings = payload["action_to_state"]
    if (
        not isinstance(states, list)
        or not states
        or len(states) != len(set(states))
        or any(not isinstance(state, str) or not state for state in states)
        or not isinstance(mappings, Mapping)
        or any(
            not isinstance(action, str)
            or not action
            or state not in states
            for action, state in mappings.items()
        )
    ):
        raise ValueError("action-state map contents are invalid")
    return dict(payload)



def find_complete_action_chains(
    rows: list[dict[str, Any]], states: list[str]
) -> list[list[dict[str, Any]]]:
    ordered = sorted(
        rows,
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


def _manifest_rows(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("records", payload) if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise ValueError("frozen identity/split manifest must contain records")
    result = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("manifest record must be an object")
        vid = canonical_video_id(raw.get("video_id"))
        split = str(raw.get("project_split", ""))
        if vid == "0009" or split in {"FORMAL_TEST_SEALED", "TEST", "FORMAL_TEST"} or str(raw.get("source_split", "")).lower() == "test":
            raise ValueError(f"forbidden test/0009 video in derivation: {vid}")
        expected = "DEVELOPMENT" if vid in DEV_IDS else "CALIBRATION" if vid in CAL_IDS else None
        if expected is None or split not in {expected, ""}:
            raise ValueError(f"video {vid} is not an allowed frozen {expected or 'partition'} video")
        group_hash = raw.get("group_hash")
        if not isinstance(group_hash, str) or HASH_RE.fullmatch(group_hash) is None:
            raise ValueError(f"video {vid} has invalid group_hash")
        episode_start = int(raw.get("episode_start", 0))
        episode_end = int(raw.get("episode_end", 2**63 - 1))
        if episode_start < 0 or episode_end < episode_start:
            raise ValueError(f"video {vid} has invalid episode bounds")
        if vid in result and result[vid].get("group_hash") != raw.get("group_hash"):
            raise ValueError(f"duplicate manifest identity: {vid}")

        result[vid] = dict(raw) | {
            "video_id": vid,
            "partition": expected,
            "episode_start": episode_start,
            "episode_end": episode_end,
        }
    return result


def derive_inputs(
    manifest_path: str | Path,
    action_csv: str | Path,
    trace_path: str | Path,
    *,
    action_state_map: str | Path,
    code_hash: str,
    model_hash: str,
    config_hash: str,
    scorer_rule: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = _manifest_rows(manifest_path)
    actions = parse_action_csv(action_csv)
    action_map: dict[str, list[dict[str, Any]]] = {}
    mapping_payload = load_action_state_map(action_state_map)
    states = list(mapping_payload["states"])
    state_index = {state: index for index, state in enumerate(states)}
    action_to_state = dict(mapping_payload["action_to_state"])
    for action in actions:
        if action["video_id"] not in manifest:
            raise ValueError(f"action references unassigned video {action['video_id']}")
        if action["action_name"] in action_to_state:
            action_map.setdefault(action["video_id"], []).append(
                action | {"state": action_to_state[action["action_name"]]}
            )
    selected_action_map: dict[str, list[dict[str, Any]]] = {}
    for video_id, manifest_row in manifest.items():
        eligible = [
            action
            for action in action_map.get(video_id, ())
            if manifest_row["episode_start"]
            <= action["start_frame"]
            <= action["end_frame"]
            <= manifest_row["episode_end"]
        ]
        chains = find_complete_action_chains(eligible, states)
        if not chains:
            raise ValueError(f"no complete mapped action chain for {video_id}")
        chain = chains[0]
        commitment = _hash_obj(
            [
                {
                    "state": item["state"],
                    "action_name": item["action_name"],
                    "start_frame": item["start_frame"],
                    "end_frame": item["end_frame"],
                }
                for item in chain
            ]
        )
        expected_commitment = manifest_row.get("chain_annotation_digest")
        if (
            expected_commitment is not None
            and expected_commitment != commitment
        ):
            raise ValueError(f"frozen action-chain digest mismatch for {video_id}")
        selected_action_map[video_id] = chain
    action_map = selected_action_map
    records = []
    seen: set[str] = set()
    previous: dict[str, int] = {}
    for line_no, line in enumerate(Path(trace_path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if set(raw) != TRACE_FIELDS:
            raise ValueError(f"trace line {line_no} schema mismatch")
        vid = canonical_video_id(raw["video_id"])
        if vid not in manifest:
            raise ValueError(f"trace video not in frozen manifest: {vid}")
        oid, group = str(raw["observation_id"]), str(raw["group_hash"])
        if oid in seen:
            raise ValueError("duplicate observation_id")
        seen.add(oid)
        idx, frame = int(raw["sequence_index"]), int(raw["frame_start"])
        if int(raw["frame_end"]) < frame or idx < 0:
            raise ValueError("invalid trace window")
        if not (
            manifest[vid]["episode_start"]
            <= frame
            <= int(raw["frame_end"])
            <= manifest[vid]["episode_end"]
        ):
            raise ValueError("trace frame falls outside frozen episode bounds")
        if group != str(manifest[vid].get("group_hash", group)):
            raise ValueError("trace group_hash disagrees with manifest")

        if group in previous and idx <= previous[group]:
            raise ValueError("non-monotonic sequence within group")
        previous[group] = idx
        features = [float(raw[k]) for k in ("luma_mean", "luma_std", "frame_motion")]
        if any(not (-float("inf") < v < float("inf")) for v in features):
            raise ValueError("non-finite trace feature")
        probability = float(raw["probability"])
        if not 0.0 <= probability <= 1.0:
            raise ValueError("trace probability must be in [0,1]")
        candidate = raw["candidate_step"]
        if candidate is not None and candidate not in state_index:
            raise ValueError("trace candidate_step is not in the frozen task")
        records.append(
            (
                raw,
                manifest[vid]["partition"],
                group,
                idx,
                frame,
                probability,
                features,
            )
        )
    if not records:
        raise ValueError("empty runtime inference trace")
    # A positive label means a candidate advancement opportunity was false.
    # Runtime inference never reads the action annotations or the mapping.
    rule = dict(
        scorer_rule
        or {
            "name": "false_advancement_against_mapped_official_state_v1",
            "positive": "candidate_step_differs_from_mapped_official_state",
            "no_candidate": "excluded_from_calibration_opportunities",
            "overlap_resolution": "lowest_frozen_state_index",
        }
    )
    rule_hash = _hash_obj(rule)
    probs, labels = [], []
    hmm = []
    previous_state: dict[str, int] = {}
    development_state_coverage: set[str] = set()
    for raw, partition, group, idx, frame, prob, features in records:
        covering = [
            action
            for action in action_map.get(raw["video_id"], ())
            if action["start_frame"] <= frame <= action["end_frame"]
        ]
        truth_state = (
            min(covering, key=lambda action: state_index[action["state"]])["state"]
            if covering
            else None
        )
        candidate = raw["candidate_step"]
        if partition == "CALIBRATION" and candidate is not None:
            probs.append(prob)
            labels.append(int(candidate != truth_state))
        # HMM fitting is development-only; calibration observations must never
        # leak into sequence-model training.
        if partition == "DEVELOPMENT" and truth_state is not None:
            current = state_index[truth_state]
            prior = previous_state.get(group)
            if prior is not None and current not in (prior, prior + 1):
                raise ValueError(
                    "mapped DEVELOPMENT state sequence is not monotonic self/next"
                )
            previous_state[group] = current
            development_state_coverage.add(truth_state)
            hmm.append(
                {
                    "group_hash": group,
                    "partition": partition,
                    "state": truth_state,
                    "features": features,
                }
            )
    # Fail closed: never emit a degenerate artifact that downstream fitting
    # would accept syntactically but that cannot support calibration.
    if not probs:
        raise ValueError(
            "calibration partition produced no observations; refusing to emit "
            "an empty calibration artifact"
        )
    if len(set(labels)) < 2:
        raise ValueError(
            "calibration labels are single-class; risk calibration is not "
            "identifiable from a degenerate calibration partition"
        )
    if not hmm:
        raise ValueError(
            "development partition produced no observations; refusing to emit "
            "an empty HMM training export"
        )
    assignments = sorted({vid: {"partition": row["partition"], "group_hash": row.get("group_hash")} for vid, row in manifest.items()}.items())
    if development_state_coverage != set(states):
        missing = sorted(set(states) - development_state_coverage)
        raise ValueError(f"development observations do not cover task states: {missing}")
    partition_hash = _hash_obj(assignments)
    source_digest = _hash_obj(
        {
            "manifest": sha256_file(manifest_path),
            "actions": sha256_file(action_csv),
            "trace": sha256_file(trace_path),
            "action_state_map": sha256_file(action_state_map),
        }
    )
    sidecar = {
        "schema": "display_project.g1_derivation_sidecar",
        "schema_version": 2,
        "manifest_hash": sha256_file(manifest_path),
        "action_annotation_hash": sha256_file(action_csv),
        "action_state_map_hash": sha256_file(action_state_map),
        "trace_hash": sha256_file(trace_path),
        "partition_hash": partition_hash,
        "source_digest": source_digest,
        "runtime_truth_store_used": False,
        "offline_scorer_source": str(Path(action_csv)),
        "scorer_rule_hash": rule_hash,
        "code_hash": code_hash,
        "model_hash": model_hash,
        "config_hash": config_hash,
    }
    calibration = {"split_role": "CALIBRATION", "truth_store_used": False, "probabilities": probs, "labels": labels, "partition_hash": partition_hash, "source_digest": source_digest, "offline_scorer_source": str(Path(action_csv)), "annotation_hash": sha256_file(action_csv), "scorer_rule_hash": rule_hash}
    return {"calibration": calibration, "hmm_records": hmm, "sidecar": sidecar}


def atomic_write(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(canonical_json(value) + "\n", encoding="utf-8")
    os.replace(tmp, target)
