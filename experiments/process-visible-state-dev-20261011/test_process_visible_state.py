"""Offline contract checks for process evidence and cached current-state labels."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
RUNNER = EXPERIMENT / "process_visible_state.py"
SOURCE_COMMIT = "955d2bae7f047c5e699caa90ac094b74b9b738e3"


class ProcessVisibleState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory(prefix="steptrace-process-visible-")
        cls.output_path = Path(cls.tempdir.name) / "results.json"
        cls.command = [sys.executable, "-B", str(RUNNER), "--output", str(cls.output_path)]
        cls.completed = subprocess.run(
            cls.command, cwd=ROOT, capture_output=True, text=True, check=False
        )
        cls.report = None
        if cls.completed.returncode == 0 and cls.output_path.is_file():
            cls.report = json.loads(cls.output_path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def result(self):
        self.assertEqual(
            self.completed.returncode,
            0,
            msg=f"offline control failed: {self.completed.stderr}",
        )
        self.assertIsNotNone(self.report)
        return self.report

    def test_public_cache_is_pinned_and_byte_verified(self):
        report = self.result()
        self.assertEqual(report["cache_source_commit"], SOURCE_COMMIT)
        self.assertEqual(report["verified_manifest_entries"], 23)
        self.assertEqual(
            report["cache_manifest_sha256"],
            "4c7638d6dd90119915513af511f19c3b8890e32106a9b911b7a850c9bfebc38c",
        )

    def test_direct_endpoint_record_does_not_fill_missing_process_evidence(self):
        row = self.result()["records"]["q31"]
        self.assertEqual(row["process_evidence"]["input_scope"], "process_not_supplied")
        self.assertEqual(row["process_evidence"]["step_completion"], "unknown")
        self.assertEqual(
            row["current_visible_state"]["claim_applicability"],
            "direct-supported-by-record",
        )
        self.assertEqual(
            row["combined_control"]["process_step_status"], "unknown"
        )
        self.assertFalse(row["ground_truth"]["human_verified"])

    def test_occluded_current_state_keeps_history_conditional_and_explicit(self):
        row = self.result()["records"]["q68"]
        current = row["current_visible_state"]
        self.assertEqual(current["direct_relation_by_record"], "occluded_or_unknown")
        self.assertEqual(current["claim_applicability"], "conditional-history")
        self.assertFalse(current["current_visible_confirmation_eligible"])
        self.assertEqual(current["estimate_basis"], "conditional_history")
        self.assertTrue(current["assumptions"])
        self.assertEqual(len(current["historical_connections"]), 1)
        self.assertEqual(row["process_evidence"]["step_completion"], "unknown")

    def test_later_reappearance_does_not_relabel_the_occluded_frame(self):
        row = self.result()["records"]["q92"]
        frames = {item["frame_id"]: item for item in row["frame_history"]}
        self.assertEqual(frames["f1746"]["relation_state"], "occluded_or_unknown")
        self.assertEqual(frames["f1752"]["relation_state"], "external_connection_supported")
        self.assertEqual(row["current_visible_state"]["frame_id"], "f1752")
        self.assertEqual(row["process_evidence"]["step_completion"], "unknown")

    def test_rich_combined_control_separates_cases_that_lossy_projection_collides(self):
        contrast = self.result()["comparison"]
        self.assertTrue(contrast["q31_q68_lossy_projection_collision"])
        self.assertFalse(contrast["q31_q68_evidence_qualified_collision"])
        self.assertTrue(contrast["process_completion_unknown_for_both"])

    def test_synthetic_occlusion_removal_and_identity_change_are_not_real_negatives(self):
        rows = self.result()["synthetic_controls"]
        self.assertEqual(
            {row["case_id"] for row in rows},
            {"synthetic_occlusion", "synthetic_removal", "synthetic_identity_change"},
        )
        self.assertTrue(all(row["kind"] == "synthetic_logic_only" for row in rows))
        self.assertTrue(all(row["counted_as_real_sample"] is False for row in rows))
        by_id = {row["case_id"]: row for row in rows}
        self.assertEqual(by_id["synthetic_occlusion"]["current_direct_state"], "unknown")
        self.assertEqual(by_id["synthetic_occlusion"]["history_status"], "retained_conditional")
        self.assertEqual(by_id["synthetic_removal"]["current_direct_state"], "separation_supported")
        self.assertEqual(by_id["synthetic_removal"]["history_status"], "retained_revoked")
        self.assertEqual(by_id["synthetic_identity_change"]["current_direct_state"], "unknown")
        self.assertEqual(by_id["synthetic_identity_change"]["history_status"], "retained_not_transferred")
        self.assertTrue(all(row["process_step_status"] == "unknown" for row in rows))


if __name__ == "__main__":
    unittest.main()
