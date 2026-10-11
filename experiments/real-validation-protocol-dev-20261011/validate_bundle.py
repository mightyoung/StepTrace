"""Fail-closed offline intake and descriptive scoring for StepTrace real runs.

The validator consumes a frozen bundle of media/frame manifests, blinded
reference labels, and the three real arm outputs. It never calls a model.
Synthetic unit fixtures exercise this contract but are not experiment data.
"""

from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "steptrace-real-validation-input/1.1"
SOP_SCHEMA_VERSION = "steptrace-sop-evidence-rules/1.1"
SCORE_SCHEMA_VERSION = "steptrace-real-validation-score/1.1"
SCOPE = "DEVELOPMENT_ONLY_REAL_VALIDATION"
ARM_SPECS = {
    "full_frame_batch": ("full_frame", "batch", "per_opportunity"),
    "local_crop_batch": ("local_crop", "batch", "per_opportunity"),
    "temporal_memory": ("full_frame", "incremental", "per_source_event"),
}
REQUIRED_STRATA = {"occlusion_hold", "true_removal", "identity_change"}
STEP_LABELS = {"complete", "incomplete", "unknown"}
RELATIONS = {"connected", "disconnected", "unknown"}
IDENTITIES = {"same", "different", "uncertain"}
VISIBILITY = {"visible", "occluded", "partly_occluded", "unknown"}
EVENT_TRUTHS = {"relation_held", "relation_separated", "identity_changed", "unknown"}
STEP_EVIDENCE_BASES = {"process_observed", "sop_rule_supported"}
RESPONSE_FIELDS = (
    "opportunity_id",
    "cutoff",
    "direct_relation",
    "current_estimate",
    "basis",
    "assumptions",
    "historical_record_ids",
    "history_applicability",
    "process_evidence_frame_ids",
    "step_completion",
    "step_evidence_basis",
    "step_evidence_rule_id",
    "step_evidence_frame_ids",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: Any, field: str) -> str:
    _need(isinstance(value, str) and bool(value.strip()), f"{field} must be non-empty")
    return value.strip()


def _enum(value: Any, choices: set[str], field: str) -> str:
    result = _text(value, field)
    _need(result in choices, f"{field} must be one of {sorted(choices)}")
    return result


def _sha(value: Any, field: str) -> str:
    result = _text(value, field)
    _need(bool(SHA256_RE.fullmatch(result)), f"{field} must be lowercase SHA-256")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(root: Path, value: Any, field: str) -> tuple[Path, str]:
    _need(isinstance(value, Mapping), f"{field} must contain path and sha256")
    raw_path = _text(value.get("path"), f"{field}.path")
    relative = Path(raw_path)
    _need(not relative.is_absolute(), f"{field}.path must be relative to --root")
    path = (root / relative).resolve()
    _need(path.is_relative_to(root), f"{field}.path escapes --root")
    _need(path.is_file(), f"{field} missing: {raw_path}")
    expected = _sha(value.get("sha256"), f"{field}.sha256")
    actual = sha256_file(path)
    _need(actual == expected, f"{field} sha256 mismatch: {raw_path}")
    return path, actual


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in result, f"JSON object repeats field {key!r}")
        result[key] = value
    return result


def _read_json_artifact(root: Path, value: Any, field: str) -> tuple[dict[str, Any], str]:
    path, digest = _artifact(root, value, field)
    try:
        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must be valid UTF-8 JSON") from exc
    _need(isinstance(document, dict), f"{field} must be a JSON object")
    return document, digest


def _load_sop_rules(
    root: Path,
    sop_artifact: Any,
    shared_contract: Any,
) -> dict[str, dict[str, dict[str, Any]]]:
    _need(isinstance(shared_contract, Mapping), "shared_run_contract is required")
    expected_digest = _sha(shared_contract.get("sop_sha256"), "shared_run_contract.sop_sha256")
    sop, digest = _read_json_artifact(root, sop_artifact, "sop_artifact")
    _need(digest == expected_digest, "SOP artifact hash differs from shared_run_contract.sop_sha256")
    _need(
        sop.get("schema_version") == SOP_SCHEMA_VERSION,
        f"SOP artifact schema_version must be {SOP_SCHEMA_VERSION}",
    )
    steps = sop.get("steps")
    _need(isinstance(steps, list) and bool(steps), "SOP artifact must contain a non-empty steps list")
    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for step_index, step in enumerate(steps):
        _need(isinstance(step, Mapping), f"SOP steps[{step_index}] must be an object")
        step_id = _text(step.get("step_id"), f"SOP steps[{step_index}].step_id")
        _need(step_id not in indexed, f"duplicate SOP step_id: {step_id}")
        rules = step.get("rules")
        _need(isinstance(rules, list), f"SOP step {step_id} rules must be a list")
        rules_by_id: dict[str, dict[str, Any]] = {}
        for rule_index, rule in enumerate(rules):
            _need(isinstance(rule, Mapping), f"SOP step {step_id} rules[{rule_index}] must be an object")
            rule_id = _text(rule.get("rule_id"), f"SOP step {step_id} rule_id")
            _need(rule_id not in rules_by_id, f"duplicate SOP rule_id {rule_id} under step {step_id}")
            criterion = _text(rule.get("criterion"), f"SOP step {step_id} rule {rule_id} criterion")
            bases = rule.get("allowed_evidence_bases")
            _need(isinstance(bases, list) and bool(bases), f"SOP rule {rule_id} must list allowed evidence bases")
            normalized_bases = frozenset(
                _enum(base, STEP_EVIDENCE_BASES, f"SOP rule {rule_id} allowed evidence basis")
                for base in bases
            )
            _need(len(normalized_bases) == len(bases), f"SOP rule {rule_id} repeats an evidence basis")
            rules_by_id[rule_id] = {
                "criterion": criterion,
                "allowed_evidence_bases": normalized_bases,
            }
        indexed[step_id] = rules_by_id
    return indexed


def _truth_rule_id(
    step_reference: str,
    rule_id: Any,
    step_rules: Mapping[str, Mapping[str, Any]],
    field: str,
) -> str | None:
    if step_reference == "unknown":
        if rule_id is None:
            return None
        normalized_id = _text(rule_id, field)
        _need(normalized_id in step_rules, f"{field} references unknown SOP rule {normalized_id}")
        return normalized_id

    normalized_id = _text(rule_id, f"{field} is required for complete/incomplete truth")
    _need(
        normalized_id in step_rules,
        f"{field} references unknown SOP rule {normalized_id}; truth cannot be included without a frozen step criterion",
    )
    _text(step_rules[normalized_id].get("criterion"), f"{field} SOP criterion")
    return normalized_id


def _validate_response_artifact(
    root: Path,
    row: Mapping[str, Any],
    op_id: str,
    expected_cutoff: Mapping[str, Any],
) -> None:
    artifact = row.get("response_artifact")
    path, _ = _artifact(root, artifact, f"{op_id} response artifact")
    parse_status = _enum(
        row.get("response_parse_status"),
        {"parsed", "malformed"},
        f"{op_id}.response_parse_status",
    )
    if parse_status == "malformed":
        try:
            parsed_malformed = json.loads(
                path.read_text(encoding="utf-8"),
            )
        except (UnicodeDecodeError, ValueError):
            parsed_malformed = None
        _need(
            not (
                isinstance(parsed_malformed, Mapping)
                and set(parsed_malformed) == set(RESPONSE_FIELDS)
            ),
            f"{op_id} response is marked malformed but contains a complete normalized response object",
        )
        _need(row.get("opportunity_id") == op_id, f"{op_id} malformed-response row opportunity_id mismatch")
        _need(row.get("cutoff") == expected_cutoff, f"{op_id} malformed-response row cutoff mismatch")
        expected_abstention = {
            "direct_relation": "unknown",
            "current_estimate": "unknown",
            "basis": "insufficient_current_evidence",
            "assumptions": [],
            "historical_record_ids": [],
            "history_applicability": "none",
            "process_evidence_frame_ids": [],
            "step_completion": "unknown",
            "step_evidence_basis": "insufficient_evidence",
            "step_evidence_rule_id": None,
            "step_evidence_frame_ids": [],
        }
        _need(
            all(row.get(key) == value for key, value in expected_abstention.items()),
            f"{op_id} malformed response must normalize to an abstention before scoring",
        )
        return

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{op_id} response is marked parsed but is not valid UTF-8 JSON") from exc
    _need(isinstance(parsed, Mapping), f"{op_id} parsed response must be a JSON object")
    _need(
        set(parsed) == set(RESPONSE_FIELDS),
        f"{op_id} parsed response fields do not match the normalized score schema",
    )
    expected = {field: row.get(field) for field in RESPONSE_FIELDS}
    _need(
        parsed == expected,
        f"{op_id} parsed response mismatch with scored output row",
    )


def _verify_local_crop_pixels(
    root: Path,
    presentation: Mapping[str, Any],
    expected_frame: Mapping[str, Any],
    source_frame: Mapping[str, Any],
    crop_rule: Mapping[str, Any],
    field: str,
) -> None:
    _need(
        presentation.get("crop_source_frame_id") == expected_frame["frame_id"],
        f"{field} crop source frame does not match its cutoff-prefix frame",
    )
    source_hash = _sha(presentation.get("crop_source_sha256"), f"{field}.crop_source_sha256")
    _need(
        source_hash == expected_frame["sha256"],
        f"{field} crop source hash does not match its cutoff-prefix frame",
    )
    _need(
        crop_rule.get("schema_version") == "steptrace-local-crop-rule/1.0"
        and crop_rule.get("coordinate_system") == "pixel_xywh"
        and crop_rule.get("coordinate_source") == "cutoff_prefix_only"
        and crop_rule.get("source_frame_binding") == "opportunity_prefix_frame"
        and crop_rule.get("color_mode") == "RGB"
        and crop_rule.get("output_format") == "PNG",
        f"{field} frozen crop rule does not define supported pixel reconstruction",
    )

    box = presentation.get("crop_box_xywh")
    _need(
        isinstance(box, list)
        and len(box) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) for item in box),
        f"{field} crop_box_xywh must contain four integers",
    )
    x, y, crop_width, crop_height = box
    _need(
        x >= 0
        and y >= 0
        and crop_width > 0
        and crop_height > 0
        and x + crop_width <= expected_frame["width"]
        and y + crop_height <= expected_frame["height"],
        f"{field} crop box is outside source frame bounds",
    )
    output_size = presentation.get("output_size")
    _need(
        isinstance(output_size, list)
        and len(output_size) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in output_size),
        f"{field} crop output_size must be two positive integers",
    )

    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError(
            "Pillow is required to verify local crop pixels; local-crop scoring is blocked"
        ) from exc

    try:
        pillow_version = getattr(Image, "__version__", None)
        _need(
            crop_rule.get("pixel_decoder") == "Pillow"
            and crop_rule.get("pixel_decoder_version") == pillow_version,
            f"{field} Pillow version differs from the frozen crop rule",
        )
        source_path, actual_source_hash = _artifact(
            root,
            {"path": source_frame.get("path"), "sha256": source_frame.get("sha256")},
            f"{field} source frame asset",
        )
        _need(
            actual_source_hash == expected_frame["sha256"],
            f"{field} source frame asset SHA differs from cutoff-prefix frame",
        )
        presented_path, _ = _artifact(root, presentation, field)
        with Image.open(source_path) as source_image:
            source_image.load()
            _need(
                source_image.size == (expected_frame["width"], expected_frame["height"]),
                f"{field} source frame decoded dimensions disagree with its manifest",
            )
            _need(source_image.mode == crop_rule["color_mode"], f"{field} source frame mode differs from frozen color mode")
            source_pixels = source_image.copy()
        with Image.open(presented_path) as presented_image:
            presented_image.load()
            _need(
                presented_image.format == crop_rule["output_format"],
                f"{field} crop format differs from frozen output format",
            )
            _need(presented_image.mode == crop_rule["color_mode"], f"{field} crop mode differs from frozen color mode")
            actual_pixels = presented_image.copy()
        resampling = getattr(Image, "Resampling", Image)
        filters = {
            "nearest": resampling.NEAREST,
            "bilinear": resampling.BILINEAR,
            "bicubic": resampling.BICUBIC,
            "lanczos": resampling.LANCZOS,
        }
        filter_name = _text(crop_rule.get("resize_filter"), f"{field} crop resize_filter")
        _need(filter_name in filters, f"{field} frozen crop rule has unsupported resize filter {filter_name}")
        expected_pixels = source_pixels.crop((x, y, x + crop_width, y + crop_height))
        if expected_pixels.size != tuple(output_size):
            expected_pixels = expected_pixels.resize(tuple(output_size), resample=filters[filter_name])
        _need(
            actual_pixels.size == tuple(output_size),
            f"{field} presented crop dimensions differ from frozen output_size",
        )
        _need(
            actual_pixels.tobytes() == expected_pixels.tobytes(),
            f"{field} crop pixels are not derived from the declared cutoff-prefix source frame",
        )
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("local_crop_batch", "Pillow is required")):
            raise
        raise ValueError(f"{field} crop pixel verification failed: {exc}") from exc


def _jsonl(path: Path, field: str) -> list[dict[str, Any]]:
    rows = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} invalid JSON on line {line_number}") from exc
        _need(isinstance(row, dict), f"{field} line {line_number} must be an object")
        rows.append(row)
    return rows


def _time_base(value: Any, field: str) -> tuple[int, int]:
    raw = _text(value, field)
    parts = raw.split("/")
    _need(len(parts) == 2, f"{field} must have numerator/denominator form")
    try:
        numerator, denominator = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"{field} must have integer numerator/denominator") from exc
    _need(numerator > 0 and denominator > 0, f"{field} must be positive")
    return numerator, denominator


def _integer(value: Any, field: str) -> int:
    _need(isinstance(value, int) and not isinstance(value, bool), f"{field} must be an integer")
    return value


def _frame_manifest(
    root: Path, source: Mapping[str, Any], *, source_video_sha256: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest_path, _ = _artifact(root, source.get("frame_manifest"), "frame_manifest")
    rows = _jsonl(manifest_path, "frame_manifest")
    _need(bool(rows), "frame_manifest must not be empty")
    indexed: dict[str, dict[str, Any]] = {}
    ordered_pts: list[int] = []
    time_bases: set[tuple[int, int]] = set()
    for index, row in enumerate(rows):
        frame_id = _text(row.get("frame_id"), f"frame_manifest[{index}].frame_id")
        _need(frame_id not in indexed, f"duplicate frame_id in frame_manifest: {frame_id}")
        frame_source_hash = _sha(
            row.get("source_video_sha256"),
            f"frame_manifest[{index}].source_video_sha256",
        )
        _need(
            frame_source_hash == source_video_sha256,
            f"frame {frame_id} is not bound to the verified source video hash",
        )
        pts = _integer(row.get("pts"), f"frame_manifest[{index}].pts")
        base = _time_base(row.get("time_base"), f"frame_manifest[{index}].time_base")
        time_bases.add(base)
        _need(base[0] > 0, "frame time base is invalid")
        width = _integer(row.get("width"), f"frame_manifest[{index}].width")
        height = _integer(row.get("height"), f"frame_manifest[{index}].height")
        _need(width > 0 and height > 0, f"frame {frame_id} dimensions must be positive")
        asset = {"path": row.get("path"), "sha256": row.get("sha256")}
        _, actual = _artifact(root, asset, f"frame {frame_id}")
        _need(actual == row.get("sha256"), f"frame {frame_id} hash mismatch")
        indexed[frame_id] = {
            **row,
            "pts": pts,
            "time_base": row["time_base"],
            "width": width,
            "height": height,
        }
        ordered_pts.append(pts)
    _need(ordered_pts == sorted(ordered_pts), "frame_manifest must be ordered by nondecreasing PTS")
    _need(len(time_bases) == 1, "frame_manifest must use one consistent time_base per source event")
    return rows, indexed


def _validate_sources(root: Path, value: Any) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    _need(isinstance(value, list) and bool(value), "source_events must be a non-empty list")
    by_event: dict[str, dict[str, Any]] = {}
    by_group_split: dict[str, str] = {}
    manifests: dict[str, list[dict[str, Any]]] = {}
    for index, source in enumerate(value):
        _need(isinstance(source, Mapping), f"source_events[{index}] must be an object")
        group_id = _text(source.get("source_group_id"), f"source_events[{index}].source_group_id")
        event_id = _text(source.get("source_event_id"), f"source_events[{index}].source_event_id")
        split = _enum(source.get("split"), {"development", "calibration", "validation"}, f"source_events[{index}].split")
        _need(event_id not in by_event, f"duplicate source_event_id: {event_id}")
        previous_split = by_group_split.setdefault(group_id, split)
        _need(previous_split == split, f"source_group split leakage for {group_id}")
        origin = source.get("origin")
        _need(isinstance(origin, Mapping), f"source_events[{index}].origin must be an object")
        _text(origin.get("description"), f"source_events[{index}].origin.description")
        _text(origin.get("license_or_access_basis"), f"source_events[{index}].origin.license_or_access_basis")
        _, video_sha256 = _artifact(root, source.get("media"), f"source event {event_id} media")
        rows, _ = _frame_manifest(root, source, source_video_sha256=video_sha256)
        by_event[event_id] = {**source, "source_group_id": group_id, "split": split}
        manifests[event_id] = rows
    return by_event, manifests


def _frame_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "frame_id": row["frame_id"],
        "pts": row["pts"],
        "time_base": row["time_base"],
        "sha256": row["sha256"],
        "width": row["width"],
        "height": row["height"],
    }


def _checked_frame_refs(value: Any, field: str, allowed_ids: set[str]) -> list[str]:
    _need(isinstance(value, list) and bool(value), f"{field} must cite one or more evidence frames")
    result = [_text(frame_id, f"{field} frame id") for frame_id in value]
    _need(len(result) == len(set(result)), f"{field} must not repeat frame ids")
    _need(set(result).issubset(allowed_ids), f"{field} cites a frame outside its allowed evidence view")
    return result


def _validate_review(
    opportunity: Mapping[str, Any],
    manifest: Sequence[Mapping[str, Any]],
    step_rules: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    review = opportunity.get("blind_review")
    _need(isinstance(review, Mapping), "blind_review is required for every opportunity")
    frames_by_id = {str(row["frame_id"]): row for row in manifest}
    prefix = opportunity["prefix_frames"]
    prefix_ids = {str(row["frame_id"]) for row in prefix}
    all_ids = set(frames_by_id)

    role_specs = (
        ("prefix_only", 2),
        ("full_video", 2),
    )
    reviewer_ids: set[str] = set()
    for role, minimum in role_specs:
        rows = review.get(role)
        _need(isinstance(rows, list) and len(rows) >= minimum, f"at least two {role} blinded annotations are required")
        seen = set()
        for index, row in enumerate(rows):
            _need(isinstance(row, Mapping), f"{role} annotation {index} must be an object")
            annotator = _text(row.get("annotator_id"), f"{role}[{index}].annotator_id")
            _need(annotator not in seen, f"duplicate {role} annotator: {annotator}")
            _need(annotator not in reviewer_ids, f"annotator cannot serve both blind label streams: {annotator}")
            seen.add(annotator)
            reviewer_ids.add(annotator)
            _need(row.get("blind_to_methods") is True, f"{role} annotation must be blinded to methods")
            _checked_frame_refs(
                row.get("support_frame_ids"),
                f"{role} support_frame_ids",
                prefix_ids if role == "prefix_only" else all_ids,
            )
            if role == "prefix_only":
                _enum(row.get("current_relation"), RELATIONS, f"{role}.current_relation")
                _enum(row.get("identity_status"), IDENTITIES, f"{role}.identity_status")
                _enum(row.get("interface_visibility"), VISIBILITY, f"{role}.interface_visibility")
            else:
                step_reference = _enum(row.get("step_reference"), STEP_LABELS, f"{role}.step_reference")
                _truth_rule_id(
                    step_reference,
                    row.get("step_reference_rule_id"),
                    step_rules,
                    f"{role} step_reference_rule_id",
                )
                _enum(row.get("challenge_type"), REQUIRED_STRATA | {"other"}, f"{role}.challenge_type")
                _enum(row.get("event_truth"), EVENT_TRUTHS, f"{role}.event_truth")
                _need(isinstance(row.get("process_observed"), bool), f"{role}.process_observed must be explicitly labeled")

    adjudication = review.get("adjudication")
    _need(isinstance(adjudication, Mapping), "adjudication is required; unresolved or unlabeled truth blocks metrics")
    _need(adjudication.get("status") == "resolved", "adjudication status must be resolved")
    adjudicator_id = _text(adjudication.get("adjudicator_id"), "adjudication.adjudicator_id")
    _need(adjudicator_id not in reviewer_ids, "adjudicator must be independent from both blind streams")
    _need(adjudication.get("blind_to_methods") is True, "adjudicator must be blinded to method outputs")
    labels = adjudication.get("labels")
    _need(isinstance(labels, Mapping), "adjudication.labels are required")
    relation = _enum(labels.get("current_relation"), RELATIONS, "adjudicated current_relation")
    identity = _enum(labels.get("identity_status"), IDENTITIES, "adjudicated identity_status")
    visibility = _enum(labels.get("interface_visibility"), VISIBILITY, "adjudicated interface_visibility")
    _need(isinstance(labels.get("process_observed"), bool), "adjudicated process_observed must be explicitly labeled")
    step = _enum(labels.get("step_reference"), STEP_LABELS, "adjudicated step_reference")
    step_rule_id = _truth_rule_id(
        step,
        labels.get("step_reference_rule_id"),
        step_rules,
        "adjudicated step_reference_rule_id",
    )
    challenge = _enum(labels.get("challenge_type"), REQUIRED_STRATA | {"other"}, "adjudicated challenge_type")
    event_truth = _enum(labels.get("event_truth"), EVENT_TRUTHS, "adjudicated event_truth")
    current_support = _checked_frame_refs(
        labels.get("current_state_support_frame_ids"),
        "adjudicated current_state_support_frame_ids",
        prefix_ids,
    )
    step_support = _checked_frame_refs(
        labels.get("step_reference_support_frame_ids"),
        "adjudicated step_reference_support_frame_ids",
        all_ids,
    )
    event_support = _checked_frame_refs(
        labels.get("event_truth_support_frame_ids"),
        "adjudicated event_truth_support_frame_ids",
        all_ids,
    )
    # Retain the old broad reference field only as a general source pointer.
    # It is never used to establish the current state at cutoff.
    support = labels.get("support_frame_ids")
    if support is not None:
        support = _checked_frame_refs(support, "adjudicated support_frame_ids", all_ids)

    if challenge == "occlusion_hold":
        _need(event_truth == "relation_held", "occlusion_hold requires full-video evidence that relation_held through the gap")
        _need(relation == "unknown", "occlusion_hold must keep current relation unknown without direct support")
        _need(identity == "same", "occlusion_hold requires adjudicated same-object identity")
        _need(visibility in {"occluded", "partly_occluded"}, "occlusion_hold requires occluded interface")
    elif challenge == "true_removal":
        _need(event_truth == "relation_separated", "true_removal requires full-video evidence of relation_separated")
        _need(relation == "disconnected", "true_removal requires a directly supported disconnected label")
        _need(identity == "same", "true_removal must refer to the same target identity")
    elif challenge == "identity_change":
        _need(event_truth == "identity_changed", "identity_change requires full-video evidence of identity_changed")
        _need(identity == "different", "identity_change requires adjudicated different-object identity")

    return {
        "current_relation": relation,
        "identity_status": identity,
        "interface_visibility": visibility,
        "process_observed": labels["process_observed"],
        "step_reference": step,
        "step_reference_rule_id": step_rule_id,
        "challenge_type": challenge,
        "event_truth": event_truth,
        "current_state_support_frame_ids": current_support,
        "step_reference_support_frame_ids": step_support,
        "event_truth_support_frame_ids": event_support,
        "support_frame_ids": support,
    }


def _validate_opportunities(
    value: Any,
    source_events: Mapping[str, Mapping[str, Any]],
    frame_manifests: Mapping[str, Sequence[Mapping[str, Any]]],
    sop_rules: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    _need(isinstance(value, list) and bool(value), "opportunities must be a non-empty list")
    opportunities: dict[str, dict[str, Any]] = {}
    references: dict[str, dict[str, Any]] = {}
    validation_strata: set[str] = set()
    for index, opportunity in enumerate(value):
        _need(isinstance(opportunity, Mapping), f"opportunities[{index}] must be an object")
        op_id = _text(opportunity.get("opportunity_id"), f"opportunities[{index}].opportunity_id")
        _need(op_id not in opportunities, f"duplicate opportunity_id: {op_id}")
        event_id = _text(opportunity.get("source_event_id"), f"{op_id}.source_event_id")
        _need(event_id in source_events, f"{op_id} references unknown source event")
        source = source_events[event_id]
        group_id = _text(opportunity.get("source_group_id"), f"{op_id}.source_group_id")
        _need(group_id == source["source_group_id"], f"{op_id} source group does not match source event")
        _need(source["split"] == "validation", "real evaluation scoring requires validation-split source events")
        target = opportunity.get("target")
        _need(isinstance(target, Mapping), f"{op_id}.target must be an object")
        for key in ("host", "part", "interface"):
            _text(target.get(key), f"{op_id}.target.{key}")
        sop_step_id = _text(opportunity.get("sop_step_id"), f"{op_id}.sop_step_id")
        _need(sop_step_id in sop_rules, f"{op_id} references unknown SOP step {sop_step_id}")
        cutoff = opportunity.get("cutoff")
        _need(isinstance(cutoff, Mapping), f"{op_id}.cutoff must be an object")
        cutoff_pts = _integer(cutoff.get("pts"), f"{op_id}.cutoff.pts")
        cutoff_base = _time_base(cutoff.get("time_base"), f"{op_id}.cutoff.time_base")
        _ = cutoff_base
        source_rows = list(frame_manifests[event_id])
        source_base = _time_base(source_rows[0]["time_base"], "source frame time_base")
        _need(cutoff_base == source_base, f"{op_id} cutoff time_base differs from source frame time_base")
        eligible = [
            row
            for row in source_rows
            if int(row["pts"]) <= cutoff_pts
        ]
        prefix = opportunity.get("prefix_frames")
        _need(isinstance(prefix, list) and bool(prefix), f"{op_id}.prefix_frames must be non-empty")
        expected_prefix = [_frame_projection(row) for row in eligible]
        actual_prefix = []
        for frame_index, frame in enumerate(prefix):
            _need(isinstance(frame, Mapping), f"{op_id}.prefix_frames[{frame_index}] must be an object")
            actual_prefix.append(
                {
                    "frame_id": _text(frame.get("frame_id"), f"{op_id}.prefix frame id"),
                    "pts": _integer(frame.get("pts"), f"{op_id}.prefix frame pts"),
                    "time_base": _text(frame.get("time_base"), f"{op_id}.prefix frame time_base"),
                    "sha256": _sha(frame.get("sha256"), f"{op_id}.prefix frame sha256"),
                    "width": _integer(frame.get("width"), f"{op_id}.prefix frame width"),
                    "height": _integer(frame.get("height"), f"{op_id}.prefix frame height"),
                }
            )
        _need(actual_prefix == expected_prefix, f"{op_id} prefix does not exactly match all source frames through cutoff")
        challenge = _enum(opportunity.get("challenge_type"), REQUIRED_STRATA | {"other"}, f"{op_id}.challenge_type")
        reference = _validate_review(opportunity, source_rows, sop_rules[sop_step_id])
        _need(challenge == reference["challenge_type"], f"{op_id} challenge type differs from adjudicated truth")
        if source["split"] == "validation":
            validation_strata.add(challenge)
        opportunities[op_id] = {
            **opportunity,
            "source_event_id": event_id,
            "source_group_id": group_id,
            "sop_step_id": sop_step_id,
            "cutoff": {"pts": cutoff_pts, "time_base": cutoff["time_base"]},
            "prefix_frames": actual_prefix,
            "challenge_type": challenge,
        }
        references[op_id] = reference
    _need(REQUIRED_STRATA.issubset(validation_strata), "validation split lacks one or more required real challenge strata")
    return opportunities, references


def _validate_output_relation(output: Mapping[str, Any], op_id: str) -> None:
    direct = _enum(output.get("direct_relation"), RELATIONS, f"{op_id}.direct_relation")
    estimate = _enum(output.get("current_estimate"), RELATIONS, f"{op_id}.current_estimate")
    basis = _enum(output.get("basis"), {"direct_current", "conditional_history", "insufficient_current_evidence"}, f"{op_id}.basis")
    applicability = _enum(output.get("history_applicability"), {"none", "applicable", "conditional", "revoked"}, f"{op_id}.history_applicability")
    records = output.get("historical_record_ids")
    _need(isinstance(records, list), f"{op_id}.historical_record_ids must be a list")
    record_ids = [_text(item, f"{op_id}.historical_record_id") for item in records]
    assumptions = output.get("assumptions")
    _need(isinstance(assumptions, list), f"{op_id}.assumptions must be a list")
    assumption_text = [_text(item, f"{op_id}.assumption") for item in assumptions]
    if direct == "unknown" and estimate == "disconnected":
        raise ValueError(f"{op_id} unknown direct relation cannot be converted to disconnected")
    if direct == "disconnected":
        _need(
            estimate in {"disconnected", "unknown"},
            f"{op_id} explicit separation cannot retain a connected current estimate",
        )
        if record_ids:
            _need(
                applicability == "revoked",
                f"{op_id} explicit separation must revoke prior history applicability",
            )
    if direct == "unknown" and estimate == "connected":
        _need(
            basis == "conditional_history" and applicability == "conditional" and bool(assumption_text) and bool(record_ids),
            f"{op_id} unknown-to-connected estimate must remain explicitly conditional on history",
        )
    if basis == "conditional_history":
        _need(applicability == "conditional" and bool(assumption_text) and bool(record_ids), f"{op_id} conditional history must retain assumptions and record ids")
    if basis == "direct_current":
        _need(direct in {"connected", "disconnected"}, f"{op_id} direct_current basis requires direct relation support")
        _need(estimate in {direct, "unknown"}, f"{op_id} direct current estimate conflicts with direct relation")
    if basis == "insufficient_current_evidence":
        _need(estimate == "unknown", f"{op_id} insufficient evidence must leave current estimate unknown")
    if applicability == "none":
        _need(not record_ids, f"{op_id} history marked none cannot carry historical record ids")
    else:
        _need(bool(record_ids), f"{op_id} history applicability requires historical record ids")


def _cutoff_value(cutoff: Mapping[str, Any]) -> Fraction:
    numerator, denominator = _time_base(cutoff.get("time_base"), "temporal cutoff time_base")
    return Fraction(_integer(cutoff.get("pts"), "temporal cutoff pts") * numerator, denominator)


def _validate_temporal_runs(
    arm: Mapping[str, Any],
    outputs: Mapping[str, Mapping[str, Any]],
    opportunities: Mapping[str, Mapping[str, Any]],
) -> None:
    runs = arm.get("temporal_runs")
    _need(isinstance(runs, list), "temporal_memory.temporal_runs must describe one reset per source event")
    event_ids = {opportunity["source_event_id"] for opportunity in opportunities.values()}
    indexed: dict[str, Mapping[str, Any]] = {}
    sessions: set[str] = set()
    for run in runs:
        _need(isinstance(run, Mapping), "temporal run must be an object")
        event_id = _text(run.get("source_event_id"), "temporal run source_event_id")
        _need(event_id in event_ids, f"temporal run references unknown source event {event_id}")
        _need(event_id not in indexed, f"duplicate temporal run for source event {event_id}")
        session_id = _text(run.get("temporal_session_id"), f"temporal run {event_id} session id")
        _need(session_id not in sessions, "temporal session id must be unique per source event")
        sessions.add(session_id)
        _need(run.get("reset_before_first_frame") is True, f"temporal memory must reset once before source event {event_id}")
        indexed[event_id] = run
    _need(set(indexed) == event_ids, "temporal run set must contain every source event exactly once")

    for event_id, run in indexed.items():
        event_op_ids = [
            op_id
            for op_id, opportunity in opportunities.items()
            if opportunity["source_event_id"] == event_id
        ]
        session_id = run["temporal_session_id"]
        for op_id in event_op_ids:
            _need(
                outputs[op_id].get("temporal_session_id") == session_id,
                f"temporal output {op_id} is not bound to its event session",
            )
        event_opportunities = [opportunities[op_id] for op_id in event_op_ids]
        latest_value = max(_cutoff_value(row["cutoff"]) for row in event_opportunities)
        latest = [row for row in event_opportunities if _cutoff_value(row["cutoff"]) == latest_value]
        latest_prefix_ids = [frame["frame_id"] for frame in latest[0]["prefix_frames"]]
        _need(
            all([frame["frame_id"] for frame in row["prefix_frames"]] == latest_prefix_ids for row in latest),
            f"temporal outputs at a shared cutoff have inconsistent prefixes for {event_id}",
        )
        _need(run.get("frame_ids") == latest_prefix_ids, f"temporal frame ingestion is not the exact chronological prefix for {event_id}")

        emissions = run.get("emissions")
        _need(isinstance(emissions, list), f"temporal run {event_id} emissions must be a list")
        by_emission: dict[str, Mapping[str, Any]] = {}
        emission_order: list[str] = []
        for emission in emissions:
            _need(isinstance(emission, Mapping), f"temporal run {event_id} emission must be an object")
            op_id = _text(emission.get("opportunity_id"), "temporal emission opportunity_id")
            _need(op_id in event_op_ids and op_id not in by_emission, f"invalid or duplicate temporal emission {op_id}")
            by_emission[op_id] = emission
            emission_order.append(op_id)
            opportunity = opportunities[op_id]
            _need(emission.get("cutoff") == opportunity["cutoff"], f"temporal emission cutoff mismatch for {op_id}")
            expected_ids = [frame["frame_id"] for frame in opportunity["prefix_frames"]]
            _need(emission.get("processed_frame_ids") == expected_ids, f"temporal emission for {op_id} used a non-prefix or future frame")
        _need(set(by_emission) == set(event_op_ids), f"temporal run {event_id} is missing an output emission")
        expected_order = sorted(
            event_op_ids,
            key=lambda op_id: (_cutoff_value(opportunities[op_id]["cutoff"]), op_id),
        )
        _need(emission_order == expected_order, f"temporal emissions are not chronological for {event_id}")


def _validate_arms(
    root: Path,
    arm_rows: Any,
    opportunities: Mapping[str, Mapping[str, Any]],
    shared_contract: Any,
    frame_manifests: Mapping[str, Sequence[Mapping[str, Any]]],
    sop_rules: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    _need(isinstance(shared_contract, Mapping), "shared_run_contract is required")
    required_contract_keys = {
        "protocol_sha256",
        "sop_sha256",
        "prompt_sha256",
        "output_schema_sha256",
        "model_fingerprint_sha256",
        "decode_config_sha256",
    }
    _need(set(shared_contract) == required_contract_keys, "shared_run_contract fields must be frozen and complete")
    for key, contract_value in shared_contract.items():
        _sha(contract_value, f"shared_run_contract.{key}")
    _need(isinstance(arm_rows, list), "arms must be a list")
    indexed_arms: dict[str, Mapping[str, Any]] = {}
    for arm in arm_rows:
        _need(isinstance(arm, Mapping), "each arm must be an object")
        method_id = _text(arm.get("method_id"), "arm.method_id")
        _need(method_id in ARM_SPECS, f"unknown method_id: {method_id}")
        _need(method_id not in indexed_arms, f"duplicate method_id: {method_id}")
        indexed_arms[method_id] = arm
    _need(set(indexed_arms) == set(ARM_SPECS), "all three real arm outputs are required")

    all_op_ids = set(opportunities)
    validated: dict[str, dict[str, dict[str, Any]]] = {}
    for method_id, spec in ARM_SPECS.items():
        arm = indexed_arms[method_id]
        expected_view, expected_update, expected_reset = spec
        _need(arm.get("view_mode") == expected_view, f"{method_id} view_mode mismatch")
        _need(arm.get("update_mode") == expected_update, f"{method_id} update_mode mismatch")
        _need(arm.get("reset_policy") == expected_reset, f"{method_id} reset_policy mismatch")
        _need(arm.get("run_contract") == shared_contract, f"{method_id} run contract differs across arms")
        crop_rule: Mapping[str, Any] | None = None
        if method_id == "local_crop_batch":
            crop_rule_sha256 = _sha(
                arm.get("crop_rule_sha256"), "local_crop_batch.crop_rule_sha256"
            )
            crop_rule, crop_rule_digest = _read_json_artifact(
                root,
                arm.get("crop_rule_artifact"),
                "local_crop_batch.crop_rule_artifact",
            )
            _need(crop_rule_digest == crop_rule_sha256, "local crop rule artifact hash mismatch")
            _need(
                crop_rule.get("schema_version") == "steptrace-local-crop-rule/1.0",
                "local crop rule artifact schema_version is unsupported",
            )
            _need(
                arm.get("crop_rule_frozen_before_outputs") is True,
                "local crop rule must be frozen before outputs",
            )
        outputs = arm.get("outputs")
        _need(isinstance(outputs, list), f"{method_id}.outputs must be a list")
        by_id: dict[str, dict[str, Any]] = {}
        for row in outputs:
            _need(isinstance(row, Mapping), f"{method_id} output must be an object")
            op_id = _text(row.get("opportunity_id"), f"{method_id}.opportunity_id")
            _need(op_id in opportunities, f"{method_id} output references unknown opportunity {op_id}")
            _need(op_id not in by_id, f"{method_id} duplicate output for opportunity {op_id}")
            by_id[op_id] = dict(row)
        _need(set(by_id) == all_op_ids, f"{method_id} output opportunity set is incomplete or unexpected")

        for op_id, opportunity in opportunities.items():
            row = by_id[op_id]
            _need(row.get("cutoff") == opportunity["cutoff"], f"{method_id}/{op_id} cutoff mismatch")
            expected_frames = opportunity["prefix_frames"]
            source_frame_rows = frame_manifests[opportunity["source_event_id"]]
            source_frames_by_id = {str(frame["frame_id"]): frame for frame in source_frame_rows}
            input_frames = row.get("input_frames")
            _need(isinstance(input_frames, list), f"{method_id}/{op_id}.input_frames must be a list")
            normalized_inputs = []
            for item in input_frames:
                _need(isinstance(item, Mapping), f"{method_id}/{op_id} input frame must be an object")
                normalized_inputs.append(
                    {
                        "frame_id": _text(item.get("frame_id"), "input frame id"),
                        "pts": _integer(item.get("pts"), "input frame pts"),
                        "time_base": _text(item.get("time_base"), "input frame time_base"),
                        "sha256": _sha(item.get("sha256"), "input frame sha256"),
                        "width": _integer(item.get("width"), "input frame width"),
                        "height": _integer(item.get("height"), "input frame height"),
                    }
                )
            _need(normalized_inputs == expected_frames, f"{method_id}/{op_id} input frames do not match the frozen prefix")

            presented = row.get("presented_frames")
            _need(isinstance(presented, list) and len(presented) == len(expected_frames), f"{method_id}/{op_id} presented frame set is incomplete")
            for expected, presentation in zip(expected_frames, presented):
                _need(isinstance(presentation, Mapping), f"{method_id}/{op_id} presented frame must be an object")
                _need(presentation.get("frame_id") == expected["frame_id"], f"{method_id}/{op_id} presented frame order mismatch")
                _, digest = _artifact(root, presentation, f"{method_id}/{op_id} presented frame")
                if method_id != "local_crop_batch":
                    matching_source = source_frames_by_id[expected["frame_id"]]
                    _need(digest == matching_source["sha256"], f"{method_id}/{op_id} full-frame presentation changed source pixels")
                else:
                    _need(
                        presentation.get("crop_rule_sha256") == crop_rule_sha256,
                        f"{method_id}/{op_id} crop row does not match frozen crop rule",
                    )
                    source_frame = source_frames_by_id[expected["frame_id"]]
                    _verify_local_crop_pixels(
                        root,
                        presentation,
                        expected,
                        source_frame,
                        crop_rule,
                        f"{method_id}/{op_id} presented crop",
                    )

            _validate_response_artifact(root, row, op_id, opportunity["cutoff"])
            if method_id == "temporal_memory":
                expected_order = [frame["frame_id"] for frame in expected_frames]
                _need(row.get("ordered_frame_ids") == expected_order, f"{method_id}/{op_id} temporal frame order differs from prefix")
                _text(row.get("temporal_session_id"), f"{method_id}/{op_id}.temporal_session_id")

            _validate_output_relation(row, op_id)
            step = _enum(row.get("step_completion"), STEP_LABELS, f"{method_id}/{op_id}.step_completion")
            step_basis = _enum(
                row.get("step_evidence_basis"),
                {"process_observed", "sop_rule_supported", "conditional_history", "insufficient_evidence"},
                f"{method_id}/{op_id}.step_evidence_basis",
            )
            step_rule_id = row.get("step_evidence_rule_id")
            process_refs = row.get("process_evidence_frame_ids")
            step_refs = row.get("step_evidence_frame_ids")
            _need(isinstance(process_refs, list), f"{method_id}/{op_id}.process_evidence_frame_ids must be a list")
            _need(isinstance(step_refs, list), f"{method_id}/{op_id}.step_evidence_frame_ids must be a list")
            prefix_ids = {frame["frame_id"] for frame in expected_frames}
            for ref in process_refs + step_refs:
                _need(_text(ref, f"{method_id}/{op_id} evidence frame id") in prefix_ids, f"{method_id}/{op_id} evidence reference is outside cutoff prefix")
            step_id = opportunity["sop_step_id"]
            _need(step_id in sop_rules, f"{method_id}/{op_id} references unknown SOP step {step_id}")
            rules_for_step = sop_rules[step_id]
            step_rule_basis_valid = None
            if step_rule_id is not None:
                step_rule_id = _text(step_rule_id, f"{method_id}/{op_id}.step_evidence_rule_id")
                _need(
                    step_rule_id in rules_for_step,
                    f"{method_id}/{op_id} cites unknown SOP rule {step_rule_id} for step {step_id}",
                )
                step_rule_basis_valid = step_basis in rules_for_step[step_rule_id]["allowed_evidence_bases"]
            if step in {"complete", "incomplete"}:
                if step_basis == "process_observed":
                    _need(bool(step_refs), f"{method_id}/{op_id} definitive step output requires evidence references")
                    _need(bool(process_refs), f"{method_id}/{op_id} process-observed step output requires process evidence frames")
                    _need(step_rule_id is not None, f"{method_id}/{op_id} process-observed step output requires a frozen SOP rule id")
                elif step_basis == "sop_rule_supported":
                    _need(bool(step_refs), f"{method_id}/{op_id} SOP-rule step output requires evidence references")
                    _need(step_rule_id is not None, f"{method_id}/{op_id} SOP-rule step output requires a frozen SOP rule id")
                # A conditional-history completion is a model error, but the
                # protocol scores it as a completion and exposes it to FCR.
            else:
                _need(
                    step_basis == "insufficient_evidence",
                    f"{method_id}/{op_id} unknown step completion must be marked insufficient_evidence",
                )
            row["step_completion"] = step
            row["step_rule_basis_valid"] = step_rule_basis_valid
        if method_id == "temporal_memory":
            _validate_temporal_runs(arm, by_id, opportunities)
        validated[method_id] = by_id
    return validated


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _metric_rows(
    selected_ids: Sequence[str],
    references: Mapping[str, Mapping[str, Any]],
    predictions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    counts = Counter(references[op_id]["step_reference"] for op_id in selected_ids)
    known_ids = [op_id for op_id in selected_ids if references[op_id]["step_reference"] in {"complete", "incomplete"}]
    incomplete_ids = [op_id for op_id in selected_ids if references[op_id]["step_reference"] == "incomplete"]
    false_complete = sum(predictions[op_id]["step_completion"] == "complete" for op_id in incomplete_ids)
    decisive = sum(predictions[op_id]["step_completion"] in {"complete", "incomplete"} for op_id in known_ids)
    rule_basis_violation_n = sum(
        predictions[op_id].get("step_rule_basis_valid") is False
        and predictions[op_id]["step_completion"] in {"complete", "incomplete"}
        for op_id in selected_ids
    )
    known_count = len(known_ids)
    coverage_value = _safe_ratio(decisive, known_count)
    return {
        "opportunity_n": len(selected_ids),
        "complete_truth_n": counts["complete"],
        "incomplete_truth_n": counts["incomplete"],
        "unknown_truth_n": counts["unknown"],
        "false_complete": false_complete,
        "fcr": _safe_ratio(false_complete, len(incomplete_ids)),
        "rule_basis_violation_n": rule_basis_violation_n,
        "decisive_output_n": decisive,
        "coverage": coverage_value,
        "abstention_rate": None if coverage_value is None else 1.0 - coverage_value,
        "inferential_claim_allowed": False,
    }


def validate_and_score(bundle: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    """Validate a frozen real-input bundle, then emit descriptive FCR/coverage.

    The function raises before scoring if media, labels, frame prefixes, or any
    arm output are missing or inconsistent. It does not estimate power or test
    an inferential hypothesis.
    """
    _need(isinstance(bundle, Mapping), "bundle must be an object")
    _need(bundle.get("schema_version") == SCHEMA_VERSION, f"schema_version must be {SCHEMA_VERSION}")
    _need(bundle.get("scope") == SCOPE, f"scope must be {SCOPE}")
    evaluation_id = _text(bundle.get("evaluation_id"), "evaluation_id")
    resolved_root = root.resolve()
    source_events, frame_manifests = _validate_sources(resolved_root, bundle.get("source_events"))
    shared_contract = bundle.get("shared_run_contract")
    sop_rules = _load_sop_rules(
        resolved_root,
        bundle.get("sop_artifact"),
        shared_contract,
    )
    opportunities, references = _validate_opportunities(
        bundle.get("opportunities"),
        source_events,
        frame_manifests,
        sop_rules,
    )
    arms = _validate_arms(
        resolved_root,
        bundle.get("arms"),
        opportunities,
        shared_contract,
        frame_manifests,
        sop_rules,
    )

    truth_counts = Counter(reference["step_reference"] for reference in references.values())
    per_arm: dict[str, Any] = {}
    by_stratum: dict[str, Any] = {}
    for method_id, predictions in arms.items():
        per_arm[method_id] = _metric_rows(
            list(opportunities), references, predictions
        )
        stratum_rows = {}
        for stratum in sorted(REQUIRED_STRATA):
            selected = [
                op_id
                for op_id, opportunity in opportunities.items()
                if opportunity["challenge_type"] == stratum
            ]
            stratum_rows[stratum] = _metric_rows(selected, references, predictions)
        by_stratum[method_id] = stratum_rows

    canonical_bundle = json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "scope": SCOPE,
        "status": "DEVELOPMENT_DESCRIPTIVE_ONLY",
        "evaluation_id": evaluation_id,
        "input_bundle_sha256": hashlib.sha256(canonical_bundle).hexdigest(),
        "source_event_n": len(source_events),
        "source_group_n": len({row["source_group_id"] for row in source_events.values()}),
        "opportunity_n": len(opportunities),
        "reference_counts": {
            label: truth_counts[label] for label in ("complete", "incomplete", "unknown")
        },
        "required_validation_strata": sorted(REQUIRED_STRATA),
        "arms": per_arm,
        "by_stratum": by_stratum,
        "claims": {
            "confirmatory_comparison": False,
            "accuracy_or_generalization_claim": False,
            "sample_size_or_power_claim": False,
            "coverage_match_rule_frozen": False,
            "reason": "descriptive challenge-set accounting only; protocol denominators and coverage-match rule remain TBD",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and score a frozen StepTrace real-validation bundle offline")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path, help="root for relative source and output artifact paths")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        report = validate_and_score(bundle, root=args.root)
        payload = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "score_sha256": hashlib.sha256(payload).hexdigest()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
