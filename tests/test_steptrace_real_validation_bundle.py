"""Offline contract tests for future real-video StepTrace evaluations.

All files created here are synthetic unit fixtures. They are never experiment
observations and must not be counted as real validation samples.
"""

from __future__ import annotations

import copy
import binascii
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import shutil
import struct
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "experiments" / "real-validation-protocol-dev-20261011"


def _load_module(name: str, path: Path):
    assert path.is_file(), f"missing offline contract implementation: {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(root: Path, relative: str, data: bytes) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "sha256": _hash(data)}


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _rewrite_parsed_response(root: Path, output: dict) -> None:
    payload = {field: output[field] for field in RESPONSE_FIELDS}
    path = output["response_artifact"]["path"]
    output["response_artifact"] = _write(root, path, _json_bytes(payload))
    output["response_parse_status"] = "parsed"


def _frame_pixels(event_index: int, frame_number: int, width: int = 16, height: int = 12):
    pixels = []
    base = event_index * 40 + frame_number * 10
    for y in range(height):
        pixels.append(
            [
                ((base + x) % 256, (base * 3 + y) % 256, (base + x + y) % 256)
                for x in range(width)
            ]
        )
    return pixels


def _png_bytes(width: int, height: int, pixels) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)

    scanlines = b"".join(
        b"\x00" + bytes(channel for pixel in row for channel in pixel)
        for row in pixels
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


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


def _build_bundle(root: Path) -> dict:
    """Create a small, explicitly synthetic three-stratum contract fixture."""
    from PIL import __version__ as pillow_version

    strata = ("occlusion_hold", "true_removal", "identity_change")
    source_events = []
    opportunities = []
    expected_frames = {}
    for index, stratum in enumerate(strata):
        group_id = f"synthetic-group-{index}"
        event_id = f"synthetic-event-{index}"
        video = _write(root, f"media/{event_id}.bin", f"synthetic:{event_id}".encode())
        frame_rows = []
        for frame_number in range(3):
            frame_id = f"{event_id}-f{frame_number}"
            frame = _write(
                root,
                f"frames/{event_id}/{frame_id}.png",
                _png_bytes(16, 12, _frame_pixels(index, frame_number)),
            )
            frame_rows.append(
                {
                    "frame_id": frame_id,
                    "pts": frame_number,
                    "time_base": "1/30",
                    "path": frame["path"],
                    "sha256": frame["sha256"],
                    "source_video_sha256": video["sha256"],
                    "width": 16,
                    "height": 12,
                }
            )
        manifest_bytes = b"".join(_json_bytes(row) for row in frame_rows)
        frame_manifest = _write(root, f"manifests/{event_id}.jsonl", manifest_bytes)
        source_events.append(
            {
                "source_group_id": group_id,
                "source_event_id": event_id,
                "split": "validation",
                "origin": {
                    "description": "synthetic unit fixture",
                    "license_or_access_basis": "synthetic only",
                },
                "media": video,
                "frame_manifest": frame_manifest,
            }
        )

        prefix = frame_rows[:2]
        opportunity_id = f"synthetic-opportunity-{index}"
        step_truth = ("incomplete", "complete", "unknown")[index]
        relation = ("unknown", "disconnected", "unknown")[index]
        identity = ("same", "same", "different")[index]
        visibility = ("occluded", "visible", "visible")[index]
        event_truth = ("relation_held", "relation_separated", "identity_changed")[index]
        opportunity = {
            "opportunity_id": opportunity_id,
            "source_group_id": group_id,
            "source_event_id": event_id,
            "target": {"host": "host-1", "part": "part-1", "interface": "interface-1"},
            "sop_step_id": "synthetic-step-1",
            "challenge_type": stratum,
            "cutoff": {"pts": 1, "time_base": "1/30"},
            "prefix_frames": [
                {
                    key: row[key]
                    for key in ("frame_id", "pts", "time_base", "sha256", "width", "height")
                }
                for row in prefix
            ],
            "blind_review": {
                "prefix_only": [
                    {
                        "annotator_id": "prefix-a",
                        "blind_to_methods": True,
                        "current_relation": relation,
                        "identity_status": identity,
                        "interface_visibility": visibility,
                        "support_frame_ids": [prefix[-1]["frame_id"]],
                    },
                    {
                        "annotator_id": "prefix-b",
                        "blind_to_methods": True,
                        "current_relation": relation,
                        "identity_status": identity,
                        "interface_visibility": visibility,
                        "support_frame_ids": [prefix[-1]["frame_id"]],
                    },
                ],
                "full_video": [
                    {
                        "annotator_id": "full-a",
                        "blind_to_methods": True,
                        "step_reference": step_truth,
                        "step_reference_rule_id": (
                            "synthetic-step-rule-1" if step_truth != "unknown" else None
                        ),
                        "challenge_type": stratum,
                        "event_truth": event_truth,
                        "support_frame_ids": [frame_rows[-1]["frame_id"]],
                        "process_observed": False,
                    },
                    {
                        "annotator_id": "full-b",
                        "blind_to_methods": True,
                        "step_reference": step_truth,
                        "step_reference_rule_id": (
                            "synthetic-step-rule-1" if step_truth != "unknown" else None
                        ),
                        "challenge_type": stratum,
                        "event_truth": event_truth,
                        "support_frame_ids": [frame_rows[-1]["frame_id"]],
                        "process_observed": False,
                    },
                ],
                "adjudication": {
                    "status": "resolved",
                    "adjudicator_id": "adjudicator-c",
                    "blind_to_methods": True,
                    "labels": {
                        "current_relation": relation,
                        "identity_status": identity,
                        "interface_visibility": visibility,
                        "process_observed": False,
                        "step_reference": step_truth,
                        "step_reference_rule_id": (
                            "synthetic-step-rule-1" if step_truth != "unknown" else None
                        ),
                        "challenge_type": stratum,
                        "event_truth": event_truth,
                        "current_state_support_frame_ids": [prefix[-1]["frame_id"]],
                        "step_reference_support_frame_ids": [frame_rows[-1]["frame_id"]],
                        "event_truth_support_frame_ids": [frame_rows[-1]["frame_id"]],
                        "support_frame_ids": [frame_rows[-1]["frame_id"]],
                    },
                },
            },
        }
        opportunities.append(opportunity)
        expected_frames[opportunity_id] = prefix

    sop_document = {
        "schema_version": "steptrace-sop-evidence-rules/1.1",
        "steps": [
            {
                "step_id": "synthetic-step-1",
                "rules": [
                    {
                        "rule_id": "synthetic-step-rule-1",
                        "criterion": "Synthetic unit-test criterion; not a real SOP step.",
                        "allowed_evidence_bases": ["process_observed", "sop_rule_supported"],
                    }
                ],
            }
        ],
    }
    sop_artifact = _write(root, "rules/frozen-sop-rules.json", _json_bytes(sop_document))
    shared_contract = {
        "protocol_sha256": "1" * 64,
        "sop_sha256": sop_artifact["sha256"],
        "prompt_sha256": "3" * 64,
        "output_schema_sha256": "4" * 64,
        "model_fingerprint_sha256": "5" * 64,
        "decode_config_sha256": "6" * 64,
    }
    arms = []
    crop_rule_document = {
        "schema_version": "steptrace-local-crop-rule/1.0",
        "coordinate_system": "pixel_xywh",
        "coordinate_source": "cutoff_prefix_only",
        "source_frame_binding": "opportunity_prefix_frame",
        "color_mode": "RGB",
        "output_format": "PNG",
        "pixel_decoder": "Pillow",
        "pixel_decoder_version": pillow_version,
        "resize_filter": "nearest",
    }
    crop_rule_artifact = _write(root, "configs/frozen-local-crop-rule.json", _json_bytes(crop_rule_document))
    arm_specs = (
        ("full_frame_batch", "full_frame", "batch", "per_opportunity"),
        ("local_crop_batch", "local_crop", "batch", "per_opportunity"),
        ("temporal_memory", "full_frame", "incremental", "per_source_event"),
    )
    for method_id, view_mode, update_mode, reset_policy in arm_specs:
        outputs = []
        for index, opportunity in enumerate(opportunities):
            op_id = opportunity["opportunity_id"]
            rows = expected_frames[op_id]
            presented = []
            for frame_row in rows:
                if view_mode == "local_crop":
                    crop_box = [2, 2, 6, 6]
                    x, y, crop_width, crop_height = crop_box
                    frame_number = int(frame_row["frame_id"].rsplit("f", 1)[1])
                    source_pixels = _frame_pixels(index, frame_number)
                    crop_pixels = [row[x : x + crop_width] for row in source_pixels[y : y + crop_height]]
                    crop = _write(
                        root,
                        f"crops/{method_id}/{frame_row['frame_id']}.png",
                        _png_bytes(crop_width, crop_height, crop_pixels),
                    )
                    presented.append(
                        {
                            "frame_id": frame_row["frame_id"],
                            **crop,
                            "crop_source_frame_id": frame_row["frame_id"],
                            "crop_source_sha256": frame_row["sha256"],
                            "crop_box_xywh": crop_box,
                            "output_size": [crop_width, crop_height],
                            "crop_rule_sha256": crop_rule_artifact["sha256"],
                        }
                    )
                else:
                    presented.append(
                        {
                            "frame_id": frame_row["frame_id"],
                            "path": frame_row["path"],
                            "sha256": frame_row["sha256"],
                        }
                    )
            step_completion = ("complete", "unknown", "unknown")[index]
            output = {
                "opportunity_id": op_id,
                "cutoff": copy.deepcopy(opportunity["cutoff"]),
                "response_parse_status": "parsed",
                "input_frames": [
                    {
                        key: row[key]
                        for key in ("frame_id", "pts", "time_base", "sha256", "width", "height")
                    }
                    for row in rows
                ],
                "presented_frames": presented,
                "direct_relation": "unknown",
                "current_estimate": "connected",
                "basis": "conditional_history",
                "assumptions": ["same target identity and no unobserved separation"],
                "historical_record_ids": ["synthetic-prior-record"],
                "history_applicability": "conditional",
                "process_evidence_frame_ids": (
                    [rows[-1]["frame_id"]] if step_completion != "unknown" else []
                ),
                "step_completion": step_completion,
                "step_evidence_basis": (
                    "sop_rule_supported" if step_completion != "unknown" else "insufficient_evidence"
                ),
                "step_evidence_rule_id": (
                    "synthetic-step-rule-1" if step_completion != "unknown" else None
                ),
                "step_evidence_frame_ids": (
                    [rows[-1]["frame_id"]] if step_completion != "unknown" else []
                ),
            }
            response_payload = {field: output[field] for field in RESPONSE_FIELDS}
            output["response_artifact"] = _write(
                root,
                f"outputs/{method_id}/{op_id}.json",
                _json_bytes(response_payload),
            )
            if method_id == "temporal_memory":
                output["ordered_frame_ids"] = [row["frame_id"] for row in rows]
                output["temporal_session_id"] = f"synthetic-session-{opportunity['source_event_id']}"
            outputs.append(output)
        arm = {
            "method_id": method_id,
            "view_mode": view_mode,
            "update_mode": update_mode,
            "reset_policy": reset_policy,
            "run_contract": copy.deepcopy(shared_contract),
            "outputs": outputs,
        }
        if method_id == "local_crop_batch":
            arm["crop_rule_artifact"] = crop_rule_artifact
            arm["crop_rule_sha256"] = crop_rule_artifact["sha256"]
            arm["crop_rule_frozen_before_outputs"] = True
        if method_id == "temporal_memory":
            arm["temporal_runs"] = [
                {
                    "source_event_id": source["source_event_id"],
                    "temporal_session_id": f"synthetic-session-{source['source_event_id']}",
                    "reset_before_first_frame": True,
                    "frame_ids": [
                        row["frame_id"]
                        for row in expected_frames[
                            next(
                                opportunity["opportunity_id"]
                                for opportunity in opportunities
                                if opportunity["source_event_id"] == source["source_event_id"]
                            )
                        ]
                    ],
                    "emissions": [
                        {
                            "opportunity_id": opportunity["opportunity_id"],
                            "cutoff": copy.deepcopy(opportunity["cutoff"]),
                            "processed_frame_ids": [
                                row["frame_id"]
                                for row in expected_frames[opportunity["opportunity_id"]]
                            ],
                        }
                        for opportunity in opportunities
                        if opportunity["source_event_id"] == source["source_event_id"]
                    ],
                }
                for source in source_events
            ]
        arms.append(arm)

    return {
        "schema_version": "steptrace-real-validation-input/1.1",
        "scope": "DEVELOPMENT_ONLY_REAL_VALIDATION",
        "evaluation_id": "synthetic-contract-test",
        "shared_run_contract": shared_contract,
        "sop_artifact": sop_artifact,
        "source_events": source_events,
        "opportunities": opportunities,
        "arms": arms,
    }


def _api():
    return _load_module("steptrace_real_validation", PACKAGE / "validate_bundle.py")


class RealValidationBundleTests(unittest.TestCase):
    def test_valid_bundle_reports_descriptive_metrics_without_collapsing_unknown_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            result = api.validate_and_score(_build_bundle(root), root=root)

        self.assertEqual(result["status"], "DEVELOPMENT_DESCRIPTIVE_ONLY")
        self.assertEqual(result["schema_version"], "steptrace-real-validation-score/1.1")
        self.assertEqual(
            result["reference_counts"], {"complete": 1, "incomplete": 1, "unknown": 1}
        )
        self.assertEqual(
            set(result["arms"]),
            {"full_frame_batch", "local_crop_batch", "temporal_memory"},
        )
        for metrics in result["arms"].values():
            self.assertEqual(metrics["false_complete"], 1)
            self.assertEqual(metrics["incomplete_truth_n"], 1)
            self.assertEqual(metrics["fcr"], 1.0)
            self.assertEqual(metrics["coverage"], 0.5)
            self.assertEqual(metrics["abstention_rate"], 0.5)
            self.assertEqual(metrics["unknown_truth_n"], 1)
            self.assertFalse(metrics["inferential_claim_allowed"])

    def test_v1_0_bundle_is_not_silently_accepted_as_v1_1(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["schema_version"] = "steptrace-real-validation-input/1.0"

            with self.assertRaisesRegex(ValueError, "schema_version must be steptrace-real-validation-input/1.1"):
                api.validate_and_score(bundle, root=root)

    def test_missing_media_fails_closed_before_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            media_path = root / bundle["source_events"][0]["media"]["path"]
            media_path.unlink()

            with self.assertRaisesRegex(ValueError, "media.*missing|missing.*media"):
                api.validate_and_score(bundle, root=root)

    def test_frame_manifest_rows_are_bound_to_the_verified_source_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            source = bundle["source_events"][0]
            manifest_path = root / source["frame_manifest"]["path"]
            rows = [json.loads(line) for line in manifest_path.read_text().splitlines()]
            rows[0]["source_video_sha256"] = "0" * 64
            rewritten = b"".join(_json_bytes(row) for row in rows)
            source["frame_manifest"] = _write(
                root, source["frame_manifest"]["path"], rewritten
            )

            with self.assertRaisesRegex(ValueError, "source video|video hash|frame manifest"):
                api.validate_and_score(bundle, root=root)

    def test_mixed_frame_time_bases_are_rejected_even_beyond_a_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            source = bundle["source_events"][0]
            manifest_path = root / source["frame_manifest"]["path"]
            rows = [json.loads(line) for line in manifest_path.read_text().splitlines()]
            rows[-1]["time_base"] = "1/60"
            rewritten = b"".join(_json_bytes(row) for row in rows)
            source["frame_manifest"] = _write(
                root, source["frame_manifest"]["path"], rewritten
            )

            with self.assertRaisesRegex(ValueError, "time_base|time base"):
                api.validate_and_score(bundle, root=root)

    def test_missing_real_arm_opportunity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["arms"][1]["outputs"].pop()

            with self.assertRaisesRegex(ValueError, "output.*opportunit|opportunit.*output"):
                api.validate_and_score(bundle, root=root)

    def test_future_frame_in_any_arm_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][2]["outputs"][0]
            source = bundle["source_events"][0]
            frame_rows = [
                json.loads(line)
                for line in (root / source["frame_manifest"]["path"])
                .read_text()
                .splitlines()
            ]
            frame = frame_rows[-1]
            output["input_frames"].append(
                {
                    key: frame[key]
                    for key in ("frame_id", "pts", "time_base", "sha256", "width", "height")
                }
            )
            output["presented_frames"].append(
                {"frame_id": frame["frame_id"], "path": frame["path"], "sha256": frame["sha256"]}
            )
            output["ordered_frame_ids"].append(frame["frame_id"])

            with self.assertRaisesRegex(ValueError, "prefix|cutoff|future"):
                api.validate_and_score(bundle, root=root)

    def test_local_crop_rule_and_per_frame_geometry_are_frozen_and_in_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            presentation = bundle["arms"][1]["outputs"][0]["presented_frames"][0]
            presentation["crop_box_xywh"] = [600, 10, 100, 100]

            with self.assertRaisesRegex(ValueError, "crop|bounds|frozen"):
                api.validate_and_score(bundle, root=root)

    def test_temporal_state_resets_once_at_each_source_event_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["arms"][2]["temporal_runs"][0]["reset_before_first_frame"] = False

            with self.assertRaisesRegex(ValueError, "temporal.*reset|reset.*source event"):
                api.validate_and_score(bundle, root=root)

    def test_history_cannot_become_unqualified_current_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            output["basis"] = "direct_current"
            output["history_applicability"] = "none"
            output["assumptions"] = []
            _rewrite_parsed_response(root, output)

            with self.assertRaisesRegex(
                ValueError, "conditional|unknown.*connected|unqualified"
            ):
                api.validate_and_score(bundle, root=root)

    def test_explicit_separation_revokes_prior_connection_applicability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            output["direct_relation"] = "disconnected"
            output["current_estimate"] = "connected"
            output["basis"] = "conditional_history"
            output["history_applicability"] = "conditional"
            _rewrite_parsed_response(root, output)

            with self.assertRaisesRegex(ValueError, "separation|disconnected|revok"):
                api.validate_and_score(bundle, root=root)

    def test_conditional_history_complete_is_retained_as_a_false_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            output["step_evidence_basis"] = "conditional_history"
            output["step_evidence_rule_id"] = None
            output["step_evidence_frame_ids"] = []
            _rewrite_parsed_response(root, output)

            result = api.validate_and_score(bundle, root=root)

        metrics = result["arms"]["full_frame_batch"]
        self.assertEqual(metrics["false_complete"], 1)
        self.assertEqual(metrics["incomplete_truth_n"], 1)
        self.assertEqual(metrics["fcr"], 1.0)
        self.assertEqual(metrics["coverage"], 0.5)

    def test_unlabeled_step_truth_is_rejected_instead_of_scored_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["opportunities"][0]["blind_review"]["adjudication"]["labels"][
                "step_reference"
            ] = None

            with self.assertRaisesRegex(ValueError, "step_reference|truth.*label|adjudicat"):
                api.validate_and_score(bundle, root=root)

    def test_blinded_truth_requires_evidence_frame_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["opportunities"][0]["blind_review"]["full_video"][0][
                "support_frame_ids"
            ] = []

            with self.assertRaisesRegex(ValueError, "support.*frame|evidence.*frame"):
                api.validate_and_score(bundle, root=root)

    def test_adjudicator_must_also_be_blinded_to_method_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["opportunities"][0]["blind_review"]["adjudication"][
                "blind_to_methods"
            ] = False

            with self.assertRaisesRegex(ValueError, "adjudicator.*blind|blinded"):
                api.validate_and_score(bundle, root=root)

    def test_occlusion_hold_requires_evidence_that_relation_persisted_through_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["opportunities"][0]["blind_review"]["adjudication"]["labels"][
                "event_truth"
            ] = "unknown"

            with self.assertRaisesRegex(ValueError, "occlusion_hold|relation_held|challenge event"):
                api.validate_and_score(bundle, root=root)

    def test_source_group_cannot_leak_across_splits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["source_events"][1]["source_group_id"] = bundle["source_events"][0][
                "source_group_id"
            ]
            bundle["source_events"][1]["split"] = "calibration"
            bundle["opportunities"][1]["source_group_id"] = bundle["source_events"][0][
                "source_group_id"
            ]

            with self.assertRaisesRegex(ValueError, "split|leak"):
                api.validate_and_score(bundle, root=root)

    def test_cached_comparison_is_field_replay_with_source_hashes_and_no_fake_images(self):
        renderer = _load_module(
            "steptrace_cache_comparison", PACKAGE / "render_cache_comparison.py"
        )
        html = renderer.render_comparison(
            ROOT / "experiments/process-visible-state-dev-20261011/results.json",
            ROOT / "experiments/process-visible-state-dev-20261011/MANIFEST.json",
        )

        self.assertIn("缓存字段回放", html)
        self.assertIn("q31", html)
        self.assertIn("q68", html)
        self.assertIn("q92", html)
        self.assertIn("conditional-history", html)
        self.assertIn("unknown", html)
        self.assertIn(
            "4c7638d6dd90119915513af511f19c3b8890e32106a9b911b7a850c9bfebc38c",
            html,
        )
        self.assertIn("955d2bae7f047c5e699caa90ac094b74b9b738e3", html)
        self.assertNotIn("<img", html.lower())
        self.assertIn("新模型实验", html)

    def test_cached_comparison_rejects_tampered_replay_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_dir = root / "experiments/process-visible-state-dev-20261011"
            package_dir.mkdir(parents=True)
            results_source = ROOT / "experiments/process-visible-state-dev-20261011/results.json"
            manifest_source = ROOT / "experiments/process-visible-state-dev-20261011/MANIFEST.json"
            results_path = package_dir / "results.json"
            manifest_path = package_dir / "MANIFEST.json"
            shutil.copyfile(results_source, results_path)
            shutil.copyfile(manifest_source, manifest_path)
            cache_dir = root / "experiments/real-reappearance-dev-20261010"
            (cache_dir / "model_outputs").mkdir(parents=True)
            cache_source_dir = ROOT / "experiments/real-reappearance-dev-20261010"
            shutil.copyfile(cache_source_dir / "public_manifest.json", cache_dir / "public_manifest.json")
            for packet_id in ("q31", "q68", "q92"):
                shutil.copyfile(
                    cache_source_dir / "model_outputs" / f"{packet_id}.json",
                    cache_dir / "model_outputs" / f"{packet_id}.json",
                )
            tampered = json.loads(results_path.read_text(encoding="utf-8"))
            tampered["records"]["q68"]["current_visible_state"][
                "direct_relation_by_record"
            ] = "external_connection_supported"
            results_path.write_text(json.dumps(tampered), encoding="utf-8")
            renderer = _load_module(
                "steptrace_cache_comparison_tamper", PACKAGE / "render_cache_comparison.py"
            )

            with self.assertRaisesRegex(ValueError, "results.*SHA|result.*hash"):
                renderer.render_comparison(results_path, manifest_path)


if __name__ == "__main__":
    unittest.main()
