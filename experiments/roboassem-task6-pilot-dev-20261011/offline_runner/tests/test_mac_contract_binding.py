import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


RUNNER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNNER_DIR))

from offline_preflight import (  # noqa: E402
    authorization_blockers,
    validate_plan,
    validate_response_content,
    validate_response_envelopes,
)


with (RUNNER_DIR / "request_plan.json").open("r", encoding="utf-8") as _stream:
    PLAN = json.load(_stream)


def synthetic_f_output():
    return {
        "schema_version": "1",
        "condition": "F",
        "authority": "development_state_estimation_only",
        "state_write_allowed": False,
        "identity_continuity": "not_observed",
        "current_relations": [
            {"part_id": "white_u_part", "seat_id": "blue_seat_left", "appearance": "unknown", "evidence_input_ids": ["k04"], "reason": "synthetic fixture"},
            {"part_id": "white_u_part", "seat_id": "blue_seat_right", "appearance": "unknown", "evidence_input_ids": ["k04"], "reason": "synthetic fixture"},
        ],
        "historical_events": [],
        "history_current_applicability": "not_applicable",
        "assumptions": [],
        "SOP_completion": "unknown",
        "mechanical_lock": "unknown",
        "force_state": "unknown",
        "limitations": [],
    }


def synthetic_hf_output():
    return {
        "schema_version": "1",
        "condition": "HF",
        "authority": "development_state_estimation_only",
        "state_write_allowed": False,
        "identity_continuity": "unknown",
        "current_relations": [
            {"part_id": "white_u_part", "seat_id": "blue_seat_left", "appearance": "unknown", "evidence_input_ids": ["k04"], "reason": "synthetic fixture"},
            {"part_id": "white_u_part", "seat_id": "blue_seat_right", "appearance": "unknown", "evidence_input_ids": ["k04"], "reason": "synthetic fixture"},
        ],
        "historical_events": [
            {"part_id": "white_u_part", "seat_id": "unknown", "event": "unknown", "evidence_input_ids": ["k01"], "uncertainty": "synthetic fixture"}
        ],
        "history_current_applicability": "not_applicable in synthetic fixture",
        "assumptions": [],
        "SOP_completion": "unknown",
        "mechanical_lock": "unknown",
        "force_state": "unknown",
        "limitations": [],
    }


class PerArmJSONSchemaTests(unittest.TestCase):
    def test_synthetic_valid_f_and_hf_instances_validate_against_separate_schemas(self):
        f_result = validate_response_content(PLAN, "F", synthetic_f_output(), RUNNER_DIR)
        hf_result = validate_response_content(PLAN, "HF", synthetic_hf_output(), RUNNER_DIR)
        self.assertTrue(f_result["valid"], f_result["errors"])
        self.assertTrue(hf_result["valid"], hf_result["errors"])
        self.assertEqual(f_result["schema_file"], PLAN["response_contract"]["schemas"]["F"]["path"])
        self.assertEqual(hf_result["schema_file"], PLAN["response_contract"]["schemas"]["HF"]["path"])

    def test_synthetic_duplicate_seat_relations_are_rejected(self):
        for condition, output in (("F", synthetic_f_output()), ("HF", synthetic_hf_output())):
            output["current_relations"][1]["seat_id"] = "blue_seat_left"
            result = validate_response_content(PLAN, condition, output, RUNNER_DIR)
            self.assertFalse(result["valid"])
            self.assertTrue(result["errors"])

    def test_f_rejects_synthetic_fabricated_history(self):
        output = synthetic_f_output()
        output["historical_events"] = [synthetic_hf_output()["historical_events"][0]]
        result = validate_response_content(PLAN, "F", output, RUNNER_DIR)
        self.assertFalse(result["valid"])
        self.assertTrue(any("historical_events" in error for error in result["errors"]))

    def test_each_schema_rejects_missing_required_fields(self):
        for condition, output in (("F", synthetic_f_output()), ("HF", synthetic_hf_output())):
            del output["limitations"]
            result = validate_response_content(PLAN, condition, output, RUNNER_DIR)
            self.assertFalse(result["valid"])
            self.assertTrue(result["errors"])


class ActualResponseBytesTests(unittest.TestCase):
    def _write_synthetic_response(self, root: Path, content: dict):
        raw = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response_path = root / "hf-response.json"
        response_path.write_bytes(raw)
        record = {
            "request_id": "HF",
            "attempt": 1,
            "finish_reason": "stop",
            "context_truncated": False,
            "raw_response_path": "hf-response.json",
            "raw_response_bytes": len(raw),
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
            "synthetic_test_fixture": True,
        }
        return raw, record

    def test_truncated_synthetic_response_is_rejected_even_with_valid_content_and_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, record = self._write_synthetic_response(root, synthetic_hf_output())
            record["context_truncated"] = True
            result = validate_response_envelopes(PLAN, [record], root, RUNNER_DIR)
        self.assertFalse(result["envelope_integrity_ok"])
        self.assertTrue(any("context_truncated must be false" in error for error in result["errors"]))

    def test_raw_response_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, record = self._write_synthetic_response(root, synthetic_hf_output())
            record["raw_response_sha256"] = "0" * 64
            result = validate_response_envelopes(PLAN, [record], root, RUNNER_DIR)
        self.assertFalse(result["envelope_integrity_ok"])
        self.assertTrue(any("raw response SHA256 does not match" in error for error in result["errors"]))

    def test_actual_synthetic_response_bytes_are_parsed_and_schema_validated_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, record = self._write_synthetic_response(root, synthetic_hf_output())
            result = validate_response_envelopes(PLAN, [record], root, RUNNER_DIR)
        self.assertTrue(result["content_validation"]["HF"]["valid"])
        self.assertFalse(result["envelope_integrity_ok"])  # F is deliberately absent.


class IndependentAuthorizationAndBackendGatesTests(unittest.TestCase):
    def test_upload_download_and_inference_authorizations_are_independent(self):
        statuses = {
            "kaggle_upload": {"status": "not_authorized", "evidence": None},
            "model_download": {"status": "not_authorized", "evidence": None},
            "inference": {"status": "not_authorized", "evidence": None},
        }
        blockers = authorization_blockers(statuses)
        self.assertEqual(len(blockers), 3)
        statuses["kaggle_upload"] = {"status": "authorized", "evidence": "synthetic-test-only"}
        blockers = authorization_blockers(statuses)
        self.assertEqual(len(blockers), 2)
        self.assertTrue(any("model download" in blocker.lower() for blocker in blockers))
        self.assertTrue(any("inference" in blocker.lower() for blocker in blockers))

    def test_current_plan_remains_blocked_when_backend_schema_support_is_unverified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = validate_plan(PLAN, Path(temp_dir), RUNNER_DIR)
        blockers = "\n".join(report["readiness_blockers"])
        self.assertFalse(report["execution_ready"])
        self.assertIn("backend strict JSON Schema support is not confirmed", blockers)
        self.assertIn("top_p is unresolved", blockers)
        self.assertIn("top_k is unresolved", blockers)
        self.assertIn("Kaggle upload authorization is not confirmed", blockers)
        self.assertIn("model download authorization is not confirmed", blockers)
        self.assertIn("inference authorization is not confirmed", blockers)

    def test_current_imported_contract_has_no_integrity_errors_even_though_it_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = validate_plan(PLAN, Path(temp_dir), RUNNER_DIR)
        self.assertEqual(report["errors"], [])
        self.assertFalse(report["execution_ready"])

    def test_original_schema_hashes_are_recorded_as_source_identities_not_local_file_hashes(self):
        schemas = PLAN["response_contract"]["schemas"]
        self.assertEqual(
            schemas["F"]["mac_original_schema_file_sha256"],
            "77240e2c680ae3d5b4ec6f28083c32bdb54eb8e757b47e0678b63ac40c751b4d",
        )
        self.assertEqual(
            schemas["HF"]["mac_original_schema_file_sha256"],
            "45075bdcf0eb9144ddca918c74600901f4660f4b4a2d06b0601277b5b2193753",
        )
        self.assertTrue(all(spec["byte_identity_with_mac_source_claimed"] is False for spec in schemas.values()))

    def test_prompt_and_known_generation_parameter_drift_are_detected(self):
        prompt_plan = json.loads(json.dumps(PLAN))
        prompt_plan["task6_prompt"]["system_text"] += " changed"
        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_report = validate_plan(prompt_plan, Path(temp_dir), RUNNER_DIR)
        self.assertTrue(any("system prompt SHA256" in error for error in prompt_report["errors"]))

        parameter_plan = json.loads(json.dumps(PLAN))
        parameter_plan["generation_parameters"]["parameters"]["temperature"] = 0.1
        with tempfile.TemporaryDirectory() as temp_dir:
            parameter_report = validate_plan(parameter_plan, Path(temp_dir), RUNNER_DIR)
        self.assertTrue(any("temperature differs from the Mac-reported value" in error for error in parameter_report["errors"]))


if __name__ == "__main__":
    unittest.main()
