import ast
import json
import sys
import unittest
from pathlib import Path


RUNNER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNNER_DIR))

from offline_preflight import validate_plan, validate_plan_shape, validate_response_envelopes  # noqa: E402


def plan_fixture():
    return {
        "schema_version": "steptrace.roboassem-task6-request-plan.v1",
        "run_policy": {
            "max_requests": 2,
            "max_retries": 0,
            "parallelism": 1,
            "bind_host": "127.0.0.1",
        },
        "requests": [
            {
                "request_id": "F",
                "frames": [
                    {
                        "frame_id": "pts_510",
                        "pts": 510,
                        "time_base": "1/30",
                        "path": "private_inputs/pts_510.png",
                        "size_bytes": None,
                        "sha256": None,
                    }
                ],
            },
            {
                "request_id": "HF",
                "frames": [
                    {
                        "frame_id": "pts_0",
                        "pts": 0,
                        "time_base": "1/30",
                        "path": "private_inputs/pts_0.png",
                        "size_bytes": None,
                        "sha256": None,
                    },
                    {
                        "frame_id": "pts_300",
                        "pts": 300,
                        "time_base": "1/30",
                        "path": "private_inputs/pts_300.png",
                        "size_bytes": None,
                        "sha256": None,
                    },
                    {
                        "frame_id": "pts_450",
                        "pts": 450,
                        "time_base": "1/30",
                        "path": "private_inputs/pts_450.png",
                        "size_bytes": None,
                        "sha256": None,
                    },
                    {
                        "frame_id": "pts_510",
                        "pts": 510,
                        "time_base": "1/30",
                        "path": "private_inputs/pts_510.png",
                        "size_bytes": None,
                        "sha256": None,
                    },
                ],
            },
        ],
    }


class PlanShapeTests(unittest.TestCase):
    def test_accepts_reported_f_hf_layout_without_claiming_readiness(self):
        report = validate_plan_shape(plan_fixture())
        self.assertEqual(report["errors"], [])
        self.assertFalse(report["execution_ready"])

    def test_rejects_wrong_hf_order(self):
        plan = plan_fixture()
        plan["requests"][1]["frames"][0], plan["requests"][1]["frames"][1] = (
            plan["requests"][1]["frames"][1],
            plan["requests"][1]["frames"][0],
        )
        report = validate_plan_shape(plan)
        self.assertIn("HF frames must use reported PTS order [0, 300, 450, 510]", report["errors"])

    def test_rejects_different_or_nonshared_current_frame(self):
        plan = plan_fixture()
        plan["requests"][1]["frames"][-1]["path"] = "private_inputs/other.png"
        report = validate_plan_shape(plan)
        self.assertIn("F and HF must reference the same current-frame bytes", report["errors"])

    def test_rejects_more_than_one_attempt_or_nonlocal_bind(self):
        plan = plan_fixture()
        plan["run_policy"]["max_retries"] = 1
        plan["run_policy"]["bind_host"] = "0.0.0.0"
        report = validate_plan_shape(plan)
        self.assertIn("max_retries must be 0", report["errors"])
        self.assertIn("bind_host must be 127.0.0.1", report["errors"])

    def test_rejects_windows_absolute_private_paths(self):
        plan = plan_fixture()
        plan["requests"][0]["frames"][0]["path"] = r"C:\Users\private\frame.png"
        report = validate_plan_shape(plan)
        self.assertIn(
            "F frame 0 path must stay relative to the private input root",
            report["errors"],
        )


class ResponseEnvelopeTests(unittest.TestCase):
    def test_reports_missing_actual_responses_without_filling_them_in(self):
        report = validate_response_envelopes(plan_fixture(), [])
        self.assertEqual(report["valid_request_ids"], [])
        self.assertEqual(set(report["missing_request_ids"]), {"F", "HF"})
        self.assertFalse(report["content_schema_validated"])

    def test_rejects_retry_duplicates_unknown_ids_and_truncated_context(self):
        # Synthetic integrity fixtures only; these are not model outputs or study results.
        records = [
            {
                "request_id": "F",
                "attempt": 2,
                "finish_reason": "length",
                "context_truncated": True,
                "raw_response_sha256": "a" * 64,
                "synthetic_test_fixture": True,
            },
            {
                "request_id": "H",
                "attempt": 1,
                "finish_reason": "stop",
                "context_truncated": False,
                "raw_response_sha256": "b" * 64,
                "synthetic_test_fixture": True,
            },
        ]
        report = validate_response_envelopes(plan_fixture(), records)
        self.assertTrue(any("attempt must be 1" in error for error in report["errors"]))
        self.assertTrue(any("unknown request_id" in error for error in report["errors"]))
        self.assertTrue(any("context_truncated must be false" in error for error in report["errors"]))
        self.assertFalse(report["envelope_integrity_ok"])

    def test_requires_finish_reason_and_valid_raw_digest(self):
        records = [
            {
                "request_id": "F",
                "attempt": 1,
                "context_truncated": False,
                "raw_response_sha256": "not-a-hash",
                "synthetic_test_fixture": True,
            }
        ]
        report = validate_response_envelopes(plan_fixture(), records)
        self.assertTrue(any("finish_reason is required" in error for error in report["errors"]))
        self.assertTrue(any("raw_response_sha256 must be a 64-character hex digest" in error for error in report["errors"]))


class PackageArtifactTests(unittest.TestCase):
    def test_checked_in_plan_stays_blocked_without_private_inputs_and_authorization(self):
        plan_path = RUNNER_DIR / "request_plan.json"
        with plan_path.open("r", encoding="utf-8") as stream:
            plan = json.load(stream)
        report = validate_plan(plan, RUNNER_DIR / "private_inputs")
        self.assertFalse(report["execution_ready"])
        self.assertEqual(report["model_calls_made"], 0)
        self.assertFalse(report["network_used"])
        blockers = "\n".join(report["readiness_blockers"])
        self.assertIn("task6-specific fixed prompt record is missing", blockers)
        self.assertIn("existing model/configuration/cost authorization is not confirmed", blockers)
        self.assertIn("compute authorization is not confirmed", blockers)
        self.assertIn("task6 response content schema is missing", blockers)

    def test_model_candidate_revision_and_file_hashes_are_pinned_without_downloads(self):
        with (RUNNER_DIR / "request_plan.json").open("r", encoding="utf-8") as stream:
            plan = json.load(stream)
        candidate = plan["model_candidate"]
        self.assertEqual(candidate["revision"], "3885219b6810b007914f3a7950a8d1b469d598a5")
        self.assertEqual(len(candidate["artifacts"]), 2)
        for artifact in candidate["artifacts"]:
            self.assertEqual(len(artifact["sha256"]), 64)
            self.assertFalse(artifact["downloaded"])

    def test_no_frame_digest_is_fabricated_from_the_mac_summary(self):
        with (RUNNER_DIR / "request_plan.json").open("r", encoding="utf-8") as stream:
            plan = json.load(stream)
        frame_descriptors = [frame for request in plan["requests"] for frame in request["frames"]]
        self.assertTrue(all(frame["sha256"] is None for frame in frame_descriptors))

    def test_notebook_is_valid_json_and_code_cells_parse_without_network_clients(self):
        notebook_path = RUNNER_DIR / "kaggle_private_draft.ipynb"
        with notebook_path.open("r", encoding="utf-8") as stream:
            notebook = json.load(stream)
        modules = set()
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module.split(".")[0])
        self.assertTrue({"requests", "urllib", "subprocess", "socket", "kaggle"}.isdisjoint(modules))


if __name__ == "__main__":
    unittest.main()
