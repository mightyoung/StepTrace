"""Synthetic regression cases from independent review of commit 7f919e5.

These fixtures are temporary contract examples, not videos, model results, or
research samples.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tests.test_steptrace_real_validation_bundle import (
    PACKAGE,
    ROOT,
    _api,
    _build_bundle,
    _frame_pixels,
    _png_bytes,
    _write,
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


def _write_parsed_response(root: Path, output: dict, relative: str = "outputs/revised-response.json"):
    payload = {field: output[field] for field in RESPONSE_FIELDS}
    output["response_artifact"] = _write(
        root,
        relative,
        (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(),
    )
    output["response_parse_status"] = "parsed"


class IndependentReviewRegressionTests(unittest.TestCase):
    def test_future_frame_pixels_cannot_be_relabelled_as_a_prefix_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            source = bundle["source_events"][0]
            frame_rows = [
                json.loads(line)
                for line in (root / source["frame_manifest"]["path"]).read_text().splitlines()
            ]
            future = frame_rows[-1]
            local_output = bundle["arms"][1]["outputs"][0]
            presentation = local_output["presented_frames"][0]
            future_pixels = _frame_pixels(event_index=0, frame_number=2)
            future_crop = [row[2:8] for row in future_pixels[2:8]]
            crop_bytes = _png_bytes(6, 6, future_crop)
            crop_asset = _write(root, "crops/attacker/future-frame.png", crop_bytes)
            presentation.update(path=crop_asset["path"], sha256=crop_asset["sha256"])
            # Keep the scored source-frame id and hash at the cutoff prefix.
            self.assertNotEqual(future["frame_id"], presentation["crop_source_frame_id"])

            with self.assertRaisesRegex(ValueError, "crop.*pixel|pixel.*source|derived.*frame"):
                api.validate_and_score(bundle, root=root)

    def test_raw_parsed_answer_cannot_disagree_with_scored_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            raw = {field: output[field] for field in RESPONSE_FIELDS}
            raw["step_completion"] = "incomplete"
            output["response_artifact"] = _write(
                root,
                "outputs/contrary-response.json",
                (json.dumps(raw, sort_keys=True) + "\n").encode(),
            )
            output["response_parse_status"] = "parsed"

            with self.assertRaisesRegex(ValueError, "response.*mismatch|parsed.*score"):
                api.validate_and_score(bundle, root=root)

    def test_nonexistent_sop_rule_id_is_rejected_after_response_consistency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            output["step_evidence_rule_id"] = "ABSENT-RULE-NEVER-FROZEN"
            _write_parsed_response(root, output)

            with self.assertRaisesRegex(ValueError, "SOP.*rule|unknown.*rule|rule.*step"):
                api.validate_and_score(bundle, root=root)

    def test_sop_artifact_sha_must_match_the_frozen_shared_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            bundle["shared_run_contract"]["sop_sha256"] = "a" * 64
            for arm in bundle["arms"]:
                arm["run_contract"]["sop_sha256"] = "a" * 64

            with self.assertRaisesRegex(ValueError, "SOP artifact hash"):
                api.validate_and_score(bundle, root=root)

    def test_conditional_history_complete_is_scored_as_false_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            output["step_evidence_basis"] = "conditional_history"
            output["step_evidence_rule_id"] = None
            output["step_evidence_frame_ids"] = []
            _write_parsed_response(root, output)

            result = api.validate_and_score(bundle, root=root)

        metrics = result["arms"]["full_frame_batch"]
        self.assertEqual(metrics["false_complete"], 1)
        self.assertEqual(metrics["incomplete_truth_n"], 1)
        self.assertEqual(metrics["fcr"], 1.0)
        self.assertEqual(metrics["coverage"], 0.5)

    def test_malformed_response_is_normalized_to_abstention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            output.update(
                direct_relation="unknown",
                current_estimate="unknown",
                basis="insufficient_current_evidence",
                assumptions=[],
                historical_record_ids=[],
                history_applicability="none",
                process_evidence_frame_ids=[],
                step_completion="unknown",
                step_evidence_basis="insufficient_evidence",
                step_evidence_rule_id=None,
                step_evidence_frame_ids=[],
                response_parse_status="malformed",
            )
            output["response_artifact"] = _write(root, "outputs/malformed-response.txt", b"not valid JSON")

            result = api.validate_and_score(bundle, root=root)

        metrics = result["arms"]["full_frame_batch"]
        self.assertEqual(metrics["false_complete"], 0)
        self.assertEqual(metrics["coverage"], 0.0)

    def test_malformed_status_cannot_hide_a_complete_normalized_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            output = bundle["arms"][0]["outputs"][0]
            valid_response = {field: output[field] for field in RESPONSE_FIELDS}
            output.update(
                direct_relation="unknown",
                current_estimate="unknown",
                basis="insufficient_current_evidence",
                assumptions=[],
                historical_record_ids=[],
                history_applicability="none",
                process_evidence_frame_ids=[],
                step_completion="unknown",
                step_evidence_basis="insufficient_evidence",
                step_evidence_rule_id=None,
                step_evidence_frame_ids=[],
                response_parse_status="malformed",
            )
            output["response_artifact"] = _write(
                root,
                "outputs/falsely-malformed-response.json",
                (json.dumps(valid_response, ensure_ascii=False, sort_keys=True) + "\n").encode(),
            )

            with self.assertRaisesRegex(ValueError, "marked malformed.*normalized response"):
                api.validate_and_score(bundle, root=root)

    def test_current_relation_adjudication_cannot_cite_only_future_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            opportunity = bundle["opportunities"][1]
            source = next(
                row for row in bundle["source_events"]
                if row["source_event_id"] == opportunity["source_event_id"]
            )
            frame_rows = [
                json.loads(line)
                for line in (root / source["frame_manifest"]["path"]).read_text().splitlines()
            ]
            future_id = frame_rows[-1]["frame_id"]
            self.assertNotIn(future_id, {frame["frame_id"] for frame in opportunity["prefix_frames"]})
            opportunity["blind_review"]["adjudication"]["labels"][
                "current_state_support_frame_ids"
            ] = [future_id]

            with self.assertRaisesRegex(ValueError, "current.*support|current.*cutoff|future.*current"):
                api.validate_and_score(bundle, root=root)

    def test_full_video_event_truth_may_cite_future_frames_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = _api()
            bundle = _build_bundle(root)
            opportunity = bundle["opportunities"][0]
            source = next(
                row for row in bundle["source_events"]
                if row["source_event_id"] == opportunity["source_event_id"]
            )
            frame_rows = [
                json.loads(line)
                for line in (root / source["frame_manifest"]["path"]).read_text().splitlines()
            ]
            references = api._validate_review(opportunity, frame_rows)
            future_id = frame_rows[-1]["frame_id"]
            self.assertIn(future_id, references["event_truth_support_frame_ids"])
            self.assertIn(future_id, references["step_reference_support_frame_ids"])
            self.assertNotIn(future_id, references["current_state_support_frame_ids"])
            result = api.validate_and_score(bundle, root=root)

        self.assertEqual(result["status"], "DEVELOPMENT_DESCRIPTIVE_ONLY")


if __name__ == "__main__":
    unittest.main()
