"""CPU-only tests. Every payload in this module is synthetic test data."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from task6_bundle import (  # noqa: E402
    audit_request_pair,
    record_runtime,
    run_gpu_preflight,
    run_preflight,
    safe_artifact_path,
    summarize_v5_run,
    validate_runtime_record,
    validate_rights_attestation,
    write_return_files,
)


SYNTHETIC_EXPERIMENT = "synthetic-task6-bundle-test-only"
SYNTHETIC_CONTRACT_SHA = "a" * 64
SYNTHETIC_PROVIDER = "synthetic-provider-test-only"


def synthetic_contract() -> dict:
    frames = []
    for input_id in ("k01", "k02", "k03", "k04"):
        payload = f"synthetic-{input_id}".encode("ascii")
        frames.append(
            {
                "input_id": input_id,
                "filename": f"{input_id}.png",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return {
        "model_input_frames": frames,
        "requests": [
            {"condition": "F", "input_ids": ["k04"]},
            {"condition": "HF", "input_ids": ["k01", "k02", "k03", "k04"]},
        ],
    }


def synthetic_request(input_ids: list[str]) -> dict:
    contract = synthetic_contract()
    frame_map = {frame["input_id"]: frame for frame in contract["model_input_frames"]}
    content = []
    for input_id in input_ids:
        payload = f"synthetic-{input_id}".encode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
                },
            }
        )
    return {
        "model": "synthetic-model-test-only",
        "temperature": 0,
        "seed": 1,
        "max_tokens": 2048,
        "stream": False,
        "top_p": 0.95,
        "top_k": 40,
        "cache_prompt": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "roboassem_task6_visual_state",
                "strict": True,
                "schema": {"synthetic_fixture_only": True},
            },
        },
        "messages": [
            {"role": "system", "content": "synthetic system prompt"},
            {"role": "user", "content": content},
        ],
    }


def write_synthetic_success_pair(results: Path, contract: dict) -> None:
    """Build only temporary synthetic v5 artifacts; never calls a model/server."""
    seats = ["blue_seat_left", "blue_seat_right"]
    relation_schema = {
        "type": "object",
        "required": ["seat_id"],
        "properties": {"seat_id": {"type": "string", "enum": seats}},
        "additionalProperties": False,
    }
    content_schema = {
        "type": "object",
        "required": ["current_relations"],
        "properties": {
            "current_relations": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": relation_schema,
            }
        },
        "additionalProperties": False,
    }
    contract["content_schemas"] = {arm: content_schema for arm in ("F", "HF")}
    content = {"current_relations": [{"seat_id": seat} for seat in seats]}
    attempts = []
    events = []
    records = []
    for arm, input_ids in (("F", ["k04"]), ("HF", ["k01", "k02", "k03", "k04"])):
        request_id = f"synthetic-request-{arm}"
        request_bytes = json.dumps(synthetic_request(input_ids)).encode("utf-8")
        request_path = f"request-{arm}.json"
        (results / request_path).write_bytes(request_bytes)
        attempts.append(
            {
                "condition": arm,
                "attempt": 1,
                "request_id": request_id,
                "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
                "request_path": request_path,
                "state": "send_intent",
            }
        )
        raw_response = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100},
            }
        ).encode("utf-8")
        response_path = f"response-{arm}-raw.json"
        (results / response_path).write_bytes(raw_response)
        response_sha256 = hashlib.sha256(raw_response).hexdigest()
        events.append(
            {
                "condition": arm,
                "request_id": request_id,
                "state": "response_received",
                "response_path": response_path,
                "response_bytes": len(raw_response),
                "response_sha256": response_sha256,
            }
        )
        records.append(
            {
                "condition": arm,
                "request_id": request_id,
                "response_sha256": response_sha256,
                "response_bytes": len(raw_response),
                "schema_valid": True,
                "schema_errors": [],
                "content": content,
            }
        )
    (results / "attempts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in attempts), encoding="utf-8"
    )
    (results / "attempt-events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    (results / "results.json").write_text(json.dumps(records), encoding="utf-8")
    (results / "server-stopped.json").write_text(
        json.dumps({"returncode": 0}), encoding="utf-8"
    )


class RightsGateTests(unittest.TestCase):
    def test_exact_real_data_attestation_shape_is_accepted(self) -> None:
        attestation = {
            "schema_version": "steptrace.data-rights-attestation.v1",
            "experiment_id": SYNTHETIC_EXPERIMENT,
            "input_contract_sha256": SYNTHETIC_CONTRACT_SHA,
            "authorization_status": "authorized",
            "rights_basis": "synthetic fixture only",
            "license_id": "synthetic-only",
            "evidence_reference": "synthetic-evidence-reference",
            "evidence_file": "synthetic-evidence.txt",
            "inference_authorized": True,
            "transfer_authorized": True,
            "provider_id": SYNTHETIC_PROVIDER,
            "expires_utc": "2999-01-01T00:00:00Z",
            "public_redistribution_authorized": False,
            "synthetic_template_only": False,
        }
        issues = validate_rights_attestation(
            attestation,
            experiment_id=SYNTHETIC_EXPERIMENT,
            input_contract_sha256=SYNTHETIC_CONTRACT_SHA,
            provider_id=SYNTHETIC_PROVIDER,
        )
        self.assertEqual(issues, [])

    def test_missing_or_unverified_dataset_rights_block_execution(self) -> None:
        issues = validate_rights_attestation(
            {"schema_version": "steptrace.data-rights-attestation.v1"},
            experiment_id=SYNTHETIC_EXPERIMENT,
            input_contract_sha256=SYNTHETIC_CONTRACT_SHA,
            provider_id=SYNTHETIC_PROVIDER,
        )
        self.assertTrue(any("authorization_status" in issue for issue in issues))
        self.assertTrue(any("inference_authorized" in issue for issue in issues))
        self.assertTrue(any("transfer_authorized" in issue for issue in issues))

    def test_authorized_attestation_must_bind_the_exact_contract_and_provider(self) -> None:
        attestation = {
            "schema_version": "steptrace.data-rights-attestation.v1",
            "experiment_id": SYNTHETIC_EXPERIMENT,
            "input_contract_sha256": "b" * 64,
            "authorization_status": "authorized",
            "rights_basis": "synthetic fixture only",
            "license_id": "synthetic-only",
            "evidence_reference": "synthetic-evidence-reference",
            "inference_authorized": True,
            "transfer_authorized": True,
            "provider_id": "different-provider",
            "public_redistribution_authorized": False,
            "synthetic_template_only": False,
        }
        issues = validate_rights_attestation(
            attestation,
            experiment_id=SYNTHETIC_EXPERIMENT,
            input_contract_sha256=SYNTHETIC_CONTRACT_SHA,
            provider_id=SYNTHETIC_PROVIDER,
        )
        self.assertTrue(any("input_contract_sha256" in issue for issue in issues))
        self.assertTrue(any("provider_id" in issue for issue in issues))

    def test_completed_rights_fields_do_not_override_synthetic_template_marker(self) -> None:
        attestation = {
            "schema_version": "steptrace.data-rights-attestation.v1",
            "experiment_id": SYNTHETIC_EXPERIMENT,
            "input_contract_sha256": SYNTHETIC_CONTRACT_SHA,
            "authorization_status": "authorized",
            "rights_basis": "synthetic fixture only",
            "license_id": "synthetic-only",
            "evidence_reference": "synthetic-evidence-reference",
            "evidence_file": "synthetic-evidence.txt",
            "inference_authorized": True,
            "transfer_authorized": True,
            "provider_id": SYNTHETIC_PROVIDER,
            "expires_utc": "2999-01-01T00:00:00Z",
            "public_redistribution_authorized": False,
            "synthetic_template_only": True,
        }
        issues = validate_rights_attestation(
            attestation,
            experiment_id=SYNTHETIC_EXPERIMENT,
            input_contract_sha256=SYNTHETIC_CONTRACT_SHA,
            provider_id=SYNTHETIC_PROVIDER,
        )
        self.assertTrue(any("synthetic_template_only" in issue for issue in issues))


class RequestAuditTests(unittest.TestCase):
    def test_audit_accepts_the_pinned_synthetic_f_hf_pair(self) -> None:
        contract = synthetic_contract()
        errors = audit_request_pair(
            synthetic_request(["k04"]),
            synthetic_request(["k01", "k02", "k03", "k04"]),
            contract,
        )
        self.assertEqual(errors, [])

    def test_audit_rejects_hf_current_frame_mismatch(self) -> None:
        contract = synthetic_contract()
        hf = synthetic_request(["k01", "k02", "k03", "k04"])
        wrong = b"synthetic-different-current-frame"
        hf["messages"][1]["content"][-1]["image_url"]["url"] = (
            "data:image/png;base64," + base64.b64encode(wrong).decode("ascii")
        )
        errors = audit_request_pair(synthetic_request(["k04"]), hf, contract)
        self.assertTrue(any("HF image" in issue for issue in errors))


class ResultStateTests(unittest.TestCase):
    def test_response_success_does_not_attest_gpu_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            contract = synthetic_contract()
            write_synthetic_success_pair(results, contract)
            report = summarize_v5_run(results, contract)

        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["inference_status"], "success")
        self.assertEqual(report.get("gpu_execution_status"), "not_verified")

    def test_execution_error_without_attempt_journal_is_unknown_and_replay_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "execution-error.json").write_text(
                json.dumps({"error": "synthetic exception", "epoch": 123}),
                encoding="utf-8",
            )
            report = summarize_v5_run(results, synthetic_contract())

        self.assertFalse(report["valid"])
        self.assertEqual(report["inference_status"], "unknown")
        self.assertEqual(report["arms"]["F"]["status"], "unknown")
        self.assertEqual(report["arms"]["HF"]["status"], "unknown")
        self.assertTrue(report["replay_blocked"])

    def test_empty_results_array_without_attempt_journal_is_unknown_and_replay_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "results.json").write_text("[]", encoding="utf-8")
            report = summarize_v5_run(results, synthetic_contract())

        self.assertFalse(report["valid"])
        self.assertEqual(report["inference_status"], "unknown")
        self.assertEqual(report["arms"]["F"]["status"], "unknown")
        self.assertEqual(report["arms"]["HF"]["status"], "unknown")
        self.assertTrue(report["replay_blocked"])

    def test_corrupt_result_ledgers_without_attempts_are_unknown_and_replay_blocked(self) -> None:
        for filename, payload in (
            ("results.json", "{"),
            ("failures.jsonl", "{"),
            ("attempt-events.jsonl", "{"),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                results = Path(tmp)
                (results / filename).write_text(payload, encoding="utf-8")
                report = summarize_v5_run(results, synthetic_contract())

            self.assertFalse(report["valid"])
            self.assertEqual(report["inference_status"], "unknown")
            self.assertEqual(report["arms"]["F"]["status"], "unknown")
            self.assertEqual(report["arms"]["HF"]["status"], "unknown")
            self.assertTrue(report["replay_blocked"])

    def test_uninterpretable_attempt_row_with_execution_error_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "attempts.jsonl").write_text("{}\n", encoding="utf-8")
            (results / "execution-error.json").write_text(
                json.dumps({"error": "synthetic exception"}), encoding="utf-8"
            )
            report = summarize_v5_run(results, synthetic_contract())

        self.assertFalse(report["valid"])
        self.assertEqual(report["inference_status"], "unknown")
        self.assertEqual(report["arms"]["F"]["status"], "unknown")
        self.assertEqual(report["arms"]["HF"]["status"], "unknown")
        self.assertTrue(report["replay_blocked"])

    def test_hf_result_artifacts_without_hf_attempt_are_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            contract = synthetic_contract()
            write_synthetic_success_pair(results, contract)
            attempt_rows = [
                json.loads(line)
                for line in (results / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            (results / "attempts.jsonl").write_text(
                json.dumps(attempt_rows[0]) + "\n", encoding="utf-8"
            )
            report = summarize_v5_run(results, contract)

        self.assertFalse(report["valid"])
        self.assertEqual(report["arms"]["F"]["status"], "completed")
        self.assertEqual(report["arms"]["HF"]["status"], "unknown")
        self.assertEqual(report["inference_status"], "unknown")
        self.assertTrue(report["replay_blocked"])

    def test_invalid_attempt_journal_is_unknown_and_replay_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "attempts.jsonl").write_text(
                '{"condition":"F","state":"send_intent"\n', encoding="utf-8"
            )
            report = summarize_v5_run(results, synthetic_contract())

        self.assertFalse(report["valid"])
        self.assertEqual(report["inference_status"], "unknown")
        self.assertEqual(report["arms"]["F"]["status"], "unknown")
        self.assertEqual(report["arms"]["HF"]["status"], "unknown")
        self.assertTrue(report["replay_blocked"])
        self.assertTrue(any("attempts.jsonl:1" in error for error in report["errors"]))

    def test_return_artifacts_without_attempt_journal_are_unknown_and_replay_blocked(self) -> None:
        content = {
            "current_relations": [
                {"seat_id": "blue_seat_left"},
                {"seat_id": "blue_seat_right"},
            ]
        }
        raw_response = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100},
            }
        ).encode("utf-8")
        response_sha256 = hashlib.sha256(raw_response).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "results"
            results.mkdir()
            (results / "response-F-raw.json").write_bytes(raw_response)
            (results / "attempt-events.jsonl").write_text(
                json.dumps(
                    {
                        "condition": "F",
                        "state": "response_received",
                        "response_path": "response-F-raw.json",
                        "response_bytes": len(raw_response),
                        "response_sha256": response_sha256,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (results / "results.json").write_text(
                json.dumps(
                    [
                        {
                            "condition": "F",
                            "response_sha256": response_sha256,
                            "response_bytes": len(raw_response),
                            "schema_valid": True,
                            "schema_errors": [],
                            "content": content,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            returned = write_return_files(
                results, synthetic_contract(), root / "return"
            )
            public_summary = json.loads((root / "return/redacted-summary.json").read_text())

        report = returned["audit"]
        self.assertFalse(report["valid"])
        self.assertEqual(report["inference_status"], "unknown")
        self.assertEqual(report["arms"]["F"]["status"], "unknown")
        self.assertEqual(report["arms"]["HF"]["status"], "unknown")
        self.assertEqual(report["request_count"], 0)
        self.assertTrue(report["replay_blocked"])
        self.assertTrue(any("attempts.jsonl" in error for error in report["errors"]))
        self.assertIn("unknown", public_summary["inference_status"])
        self.assertTrue(public_summary["arm_status"]["F"] == "unknown")
        self.assertTrue(public_summary["arm_status"]["HF"] == "unknown")

    def test_send_intent_without_response_is_unknown_and_replay_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "attempts.jsonl").write_text(
                json.dumps(
                    {
                        "condition": "F",
                        "attempt": 1,
                        "request_id": "synthetic-request-f",
                        "request_sha256": "c" * 64,
                        "request_path": "request-F.json",
                        "state": "send_intent",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = summarize_v5_run(results, synthetic_contract())
        self.assertEqual(report["arms"]["F"]["status"], "unknown")
        self.assertEqual(report["arms"]["HF"]["status"], "not_attempted")
        self.assertTrue(report["replay_blocked"])
        self.assertEqual(report["request_count"], 1)

    def test_attempt_budget_or_order_violation_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            rows = [
                {"condition": arm, "attempt": 1, "request_id": arm, "state": "send_intent"}
                for arm in ("HF", "F", "HF")
            ]
            (results / "attempts.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            report = summarize_v5_run(results, synthetic_contract())
        self.assertFalse(report["valid"])
        self.assertTrue(any("order" in issue or "maximum" in issue for issue in report["errors"]))
        self.assertTrue(report["replay_blocked"])

    def test_response_path_cannot_escape_private_result_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                safe_artifact_path(root, "../../outside.json")

    def test_provider_session_is_unknown_without_private_stop_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = summarize_v5_run(Path(tmp), synthetic_contract())
        self.assertEqual(report["resource_cleanup"]["provider_session"], "unknown")

    def test_provider_stop_claim_requires_timestamp_and_private_evidence_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = summarize_v5_run(
                Path(tmp),
                synthetic_contract(),
                {"status": "stopped", "provider_id": "synthetic-provider"},
            )
        self.assertEqual(report["resource_cleanup"]["provider_session"], "unknown")
        self.assertTrue(any("evidence_reference" in issue for issue in report["errors"]))

    def test_provider_stop_claim_needs_session_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = summarize_v5_run(
                Path(tmp),
                synthetic_contract(),
                {
                    "status": "stopped",
                    "resource_type": "cloud-vm",
                    "provider_id": "synthetic-provider",
                    "stopped_at_utc": "2999-01-01T00:00:00Z",
                    "evidence_reference": "synthetic-stop-receipt",
                },
            )
        self.assertEqual(report["resource_cleanup"]["provider_session"], "unknown")
        self.assertTrue(any("instance_reference" in issue for issue in report["errors"]))

    def test_provider_stop_timestamp_must_be_iso_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = summarize_v5_run(
                Path(tmp),
                synthetic_contract(),
                {
                    "status": "stopped",
                    "resource_type": "cloud-vm",
                    "provider_id": "synthetic-provider",
                    "instance_reference": "synthetic-instance-id",
                    "stopped_at_utc": "yesterday",
                    "evidence_reference": "synthetic-stop-receipt",
                },
            )
        self.assertEqual(report["resource_cleanup"]["provider_session"], "unknown")
        self.assertTrue(any("ISO-8601 UTC" in issue for issue in report["errors"]))

    def test_complete_provider_stop_evidence_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = summarize_v5_run(
                Path(tmp),
                synthetic_contract(),
                {
                    "status": "stopped",
                    "resource_type": "cloud-vm",
                    "provider_id": "synthetic-provider",
                    "instance_reference": "synthetic-instance-id",
                    "stopped_at_utc": "2999-01-01T00:00:00Z",
                    "evidence_reference": "synthetic-stop-receipt",
                },
            )
        self.assertEqual(report["resource_cleanup"]["provider_session"], "verified_stopped")

    def test_response_event_hash_must_match_the_private_raw_bytes(self) -> None:
        raw_response = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "current_relations": [
                                        {"seat_id": "blue_seat_left"},
                                        {"seat_id": "blue_seat_right"},
                                    ]
                                }
                            )
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100},
            }
        ).encode("utf-8")
        content = json.loads(
            json.loads(raw_response.decode("utf-8"))["choices"][0]["message"]["content"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            request = b'{"synthetic":true}'
            (results / "request-F.json").write_bytes(request)
            (results / "response-F-raw.json").write_bytes(raw_response)
            (results / "attempts.jsonl").write_text(
                json.dumps(
                    {
                        "condition": "F",
                        "attempt": 1,
                        "request_id": "synthetic-request-f",
                        "request_sha256": hashlib.sha256(request).hexdigest(),
                        "request_path": "request-F.json",
                        "state": "send_intent",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (results / "attempt-events.jsonl").write_text(
                json.dumps(
                    {
                        "condition": "F",
                        "state": "response_received",
                        "response_path": "response-F-raw.json",
                        "response_bytes": len(raw_response),
                        "response_sha256": "0" * 64,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (results / "results.json").write_text(
                json.dumps(
                    [
                        {
                            "condition": "F",
                            "response_sha256": hashlib.sha256(raw_response).hexdigest(),
                            "response_bytes": len(raw_response),
                            "schema_valid": True,
                            "schema_errors": [],
                            "content": content,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            report = summarize_v5_run(
                results,
                {
                    "requests": [
                        {"condition": "F", "input_ids": ["k04"]},
                        {"condition": "HF", "input_ids": ["k01", "k02", "k03", "k04"]},
                    ],
                    "model_input_frames": [],
                    "content_schemas": {"F": {"type": "object"}},
                },
            )
        self.assertFalse(report["valid"])
        self.assertTrue(any("raw response bytes/hash" in issue for issue in report["errors"]))

    def test_response_receipt_is_checked_even_when_v5_records_processing_failure(self) -> None:
        raw_response = b"synthetic raw response bytes"
        request = b'{"synthetic":true}'
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "request-F.json").write_bytes(request)
            (results / "response-F-raw.json").write_bytes(raw_response)
            (results / "attempts.jsonl").write_text(
                json.dumps(
                    {
                        "condition": "F",
                        "attempt": 1,
                        "request_id": "synthetic-request-f",
                        "request_sha256": hashlib.sha256(request).hexdigest(),
                        "request_path": "request-F.json",
                        "state": "send_intent",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (results / "attempt-events.jsonl").write_text(
                json.dumps(
                    {
                        "condition": "F",
                        "state": "response_received",
                        "response_path": "response-F-raw.json",
                        "response_bytes": len(raw_response),
                        "response_sha256": "0" * 64,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (results / "failures.jsonl").write_text(
                json.dumps(
                    {
                        "condition": "F",
                        "state": "processing_failed",
                        "request_id": "synthetic-request-f",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = summarize_v5_run(results, synthetic_contract())
        self.assertEqual(report["arms"]["F"]["status"], "failed")
        self.assertTrue(any("raw response bytes/hash" in issue for issue in report["errors"]))


class RuntimeLockTests(unittest.TestCase):
    def test_runtime_capture_records_cmake_compiler_and_os_release_without_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "llama.cpp"
            binary = source / "build/bin/llama-server"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"synthetic llama server binary")
            command_outputs = {
                "git": "8345f333951c661d166b00e6f9362e553768f292",
                str(binary): "llama.cpp synthetic pinned build",
                "nvidia-smi": "Synthetic GPU, 7.5, 16384, 555.42.06",
                "nvcc": "Cuda compilation tools, release 12.5, V12.5.82",
                "cmake": "cmake version 3.31.0",
                "c++": "c++ (GCC) synthetic compiler 14.2.0",
            }

            def fake_capture(command: list[str], *, timeout: int = 20) -> str:
                if command[0] == str(binary) and command[-1] == "--help":
                    from task6_bundle import REQUIRED_SERVER_FLAGS

                    return "\n".join(REQUIRED_SERVER_FLAGS)
                return command_outputs[command[0]]

            with mock.patch("task6_bundle._capture", side_effect=fake_capture):
                with mock.patch("task6_bundle.platform.freedesktop_os_release", return_value={"ID": "synthetic-linux", "VERSION_ID": "1"}):
                    runtime = record_runtime(root)

        self.assertEqual(runtime["cmake_version"], "cmake version 3.31.0")
        self.assertEqual(runtime["compiler_version"], "c++ (GCC) synthetic compiler 14.2.0")
        self.assertEqual(runtime["os_release"]["ID"], "synthetic-linux")
        self.assertEqual(runtime["generation_calls"], 0)
        self.assertFalse(runtime["server_started"])

    def test_runtime_record_satisfying_candidate_lock_is_accepted(self) -> None:
        runtime = {
            "commit": "8345f333951c661d166b00e6f9362e553768f292",
            "version": "llama.cpp pinned test build",
            "binary_bytes": 123,
            "binary_sha256": "d" * 64,
            "cuda_release": "12.5",
            "compute_capability": 7.5,
            "gpu_vram_mib": 16384,
            "gpu_name": "synthetic GPU test fixture",
            "server_flags_verified": [
                "--no-mmproj-offload", "--image-max-tokens", "--no-context-shift",
                "--no-cache-prompt", "--top-k", "--top-p", "--temp", "--seed",
            ],
            "python_version": "3.12.14",
            "jsonschema_version": "4.26.0",
            "pillow_version": "12.3.0",
                "os_release": {"ID": "synthetic-linux", "VERSION_ID": "1"},
                "cmake_version": "synthetic cmake version",
                "compiler_version": "synthetic compiler version",
        }
        environment = {
            "cuda_release": "12.5",
            "minimum_compute_capability": 7.5,
            "minimum_vram_mib": 16384,
            "python_version": "3.12.14",
            "jsonschema_version": "4.26.0",
            "pillow_version": "12.3.0",
                "os_release": {"ID": "synthetic-linux", "VERSION_ID": "1"},
                "cmake_version": "synthetic cmake version",
                "compiler_version": "synthetic compiler version",
        }
        self.assertEqual(validate_runtime_record(runtime, environment), [])

    def test_runtime_record_with_insufficient_vram_is_rejected(self) -> None:
        runtime = {
            "commit": "8345f333951c661d166b00e6f9362e553768f292",
            "version": "llama.cpp pinned test build",
            "binary_bytes": 123,
            "binary_sha256": "d" * 64,
            "cuda_release": "12.5",
            "compute_capability": 7.5,
            "gpu_vram_mib": 8192,
            "gpu_name": "synthetic GPU test fixture",
            "server_flags_verified": [
                "--no-mmproj-offload", "--image-max-tokens", "--no-context-shift",
                "--no-cache-prompt", "--top-k", "--top-p", "--temp", "--seed",
            ],
            "python_version": "3.12.14",
            "jsonschema_version": "4.26.0",
            "pillow_version": "12.3.0",
                "os_release": {"ID": "synthetic-linux", "VERSION_ID": "1"},
                "cmake_version": "synthetic cmake version",
                "compiler_version": "synthetic compiler version",
        }
        environment = {
            "cuda_release": "12.5",
            "minimum_compute_capability": 7.5,
            "minimum_vram_mib": 16384,
            "python_version": "3.12.14",
            "jsonschema_version": "4.26.0",
            "pillow_version": "12.3.0",
        }
        self.assertTrue(any("gpu_vram_mib" in issue for issue in validate_runtime_record(runtime, environment)))

    def test_runtime_lock_checks_host_memory_disk_and_cuda_driver_floor(self) -> None:
        runtime = {
            "commit": "8345f333951c661d166b00e6f9362e553768f292",
            "version": "llama.cpp pinned test build",
            "binary_bytes": 123,
            "binary_sha256": "d" * 64,
            "cuda_release": "12.5",
            "compute_capability": 7.5,
            "gpu_vram_mib": 16384,
            "gpu_name": "synthetic GPU test fixture",
            "driver_version": "555.42.06",
            "host_ram_mib": 32768,
            "free_disk_gib": 20,
            "server_flags_verified": [
                "--no-mmproj-offload", "--image-max-tokens", "--no-context-shift",
                "--no-cache-prompt", "--top-k", "--top-p", "--temp", "--seed",
            ],
            "python_version": "3.12.14",
            "jsonschema_version": "4.26.0",
            "pillow_version": "12.3.0",
        }
        environment = {
            "cuda_release": "12.5",
            "minimum_compute_capability": 7.5,
            "minimum_vram_mib": 16384,
            "minimum_host_ram_mib": 32768,
            "minimum_free_disk_gib": 20,
            "minimum_driver_version": "555.42.06",
            "python_version": "3.12.14",
            "jsonschema_version": "4.26.0",
            "pillow_version": "12.3.0",
        }
        runtime.update(driver_version="525.60.13", host_ram_mib=16384, free_disk_gib=10)
        issues = validate_runtime_record(runtime, environment)
        self.assertTrue(any("driver_version" in issue for issue in issues))
        self.assertTrue(any("host_ram_mib" in issue for issue in issues))
        self.assertTrue(any("free_disk_gib" in issue for issue in issues))


class GpuPreflightTests(unittest.TestCase):
    class FakeServerProcess:
        def __init__(self, command: list[str], **kwargs) -> None:
            self.command = command
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        def wait(self, timeout=None):
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    def test_gpu_preflight_reads_props_and_token_bounds_then_stops_without_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = Path(__file__).resolve().parents[1]
            paths = []
            fake_processes = []
            run_root = root / "private-run"
            run_root.mkdir()
            (run_root / "runtime-verification.json").write_text(
                json.dumps({"commit": "8345f333951c661d166b00e6f9362e553768f292"}),
                encoding="utf-8",
            )

            def api_response(path, body=None, *, timeout=10):
                paths.append(path)
                if path == "/props":
                    return {"model_alias": "synthetic-only", "default_generation_settings": {"n_ctx": 16384}}
                if path == "/apply-template":
                    return {"prompt": "synthetic prompt"}
                if path == "/tokenize":
                    return {"tokens": list(range(10))}
                return {"status": "ok"}

            def fake_popen(command, **kwargs):
                process = self.FakeServerProcess(command, **kwargs)
                fake_processes.append(process)
                return process

            with mock.patch("task6_bundle.run_preflight", return_value={"execution_ready": True, "blockers": [], "network_calls": 0, "generation_calls": 0}):
                with mock.patch("task6_bundle.subprocess.Popen", side_effect=fake_popen) as popen:
                    with mock.patch("task6_bundle._ensure_loopback_port_free"):
                        with mock.patch("task6_bundle._local_json_request", side_effect=api_response):
                            with mock.patch("task6_bundle.time.sleep"):
                                output = root / "gpu-preflight-private"
                                report = run_gpu_preflight(
                                    manifest_path=package / "experiment-manifest.json",
                                    contract_path=package / "execution-input-contract.json",
                                    input_root=root / "private-inputs",
                                    run_root=run_root,
                                    rights_path=root / "private-inputs/rights-attestation.json",
                                    provider_id=SYNTHETIC_PROVIDER,
                                    output_dir=output,
                                )
                                backend_props_written = (output / "backend-props.json").is_file()
                                stopped_record_written = (output / "server-stopped.json").is_file()

        self.assertTrue(report["preflight_passed"], report["errors"])
        self.assertEqual(report["generation_calls"], 0)
        self.assertEqual(report["external_network_calls"], 0)
        self.assertEqual(report["arms"]["F"]["conservative_context_bound"], 4234)
        self.assertEqual(report["arms"]["HF"]["conservative_context_bound"], 10378)
        self.assertNotIn("/v1/chat/completions", paths)
        self.assertEqual(report["local_server"], "verified_stopped")
        self.assertTrue(backend_props_written)
        self.assertTrue(stopped_record_written)
        self.assertEqual(popen.call_count, 1)
        self.assertTrue(fake_processes[0].terminated)

    def test_gpu_preflight_does_not_start_backend_when_cpu_gate_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = Path(__file__).resolve().parents[1]
            with mock.patch("task6_bundle.run_preflight", return_value={"execution_ready": False, "blockers": ["synthetic rights gate"], "network_calls": 0, "generation_calls": 0}):
                with mock.patch("task6_bundle.subprocess.Popen") as popen:
                    report = run_gpu_preflight(
                        manifest_path=package / "experiment-manifest.json",
                        contract_path=package / "execution-input-contract.json",
                        input_root=root / "private-inputs",
                        run_root=root / "private-run",
                        rights_path=root / "private-inputs/rights-attestation.json",
                        provider_id=SYNTHETIC_PROVIDER,
                        output_dir=root / "blocked-preflight",
                    )
        popen.assert_not_called()
        self.assertFalse(report["preflight_passed"])
        self.assertEqual(report["generation_calls"], 0)

    def test_gpu_preflight_refuses_an_already_bound_loopback_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = Path(__file__).resolve().parents[1]
            run_root = root / "private-run"
            run_root.mkdir()
            (run_root / "runtime-verification.json").write_text(
                json.dumps({"commit": "8345f333951c661d166b00e6f9362e553768f292"}),
                encoding="utf-8",
            )
            with mock.patch("task6_bundle.run_preflight", return_value={"execution_ready": True, "blockers": [], "network_calls": 0, "generation_calls": 0}):
                with mock.patch("task6_bundle._ensure_loopback_port_free", side_effect=OSError("synthetic port occupied")):
                    with mock.patch("task6_bundle.subprocess.Popen") as popen:
                        report = run_gpu_preflight(
                            manifest_path=package / "experiment-manifest.json",
                            contract_path=package / "execution-input-contract.json",
                            input_root=root / "private-inputs",
                            run_root=run_root,
                            rights_path=root / "private-inputs/rights-attestation.json",
                            provider_id=SYNTHETIC_PROVIDER,
                            output_dir=root / "port-conflict-output",
                        )
        popen.assert_not_called()
        self.assertFalse(report["preflight_passed"])
        self.assertEqual(report["local_server"], "not_started")
        self.assertTrue(any("port occupied" in issue for issue in report["errors"]))

    def test_gpu_preflight_blocks_overlong_context_and_stops_without_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = Path(__file__).resolve().parents[1]
            run_root = root / "private-run"
            run_root.mkdir()
            (run_root / "runtime-verification.json").write_text(
                json.dumps({"commit": "8345f333951c661d166b00e6f9362e553768f292"}),
                encoding="utf-8",
            )
            paths = []
            fake_processes = []

            def api_response(path, body=None, *, timeout=10):
                paths.append(path)
                if path == "/props":
                    return {"default_generation_settings": {"n_ctx": 16384}}
                if path == "/apply-template":
                    return {"prompt": "synthetic prompt"}
                if path == "/tokenize":
                    return {"tokens": list(range(13000))}
                return {"status": "ok"}

            def fake_popen(command, **kwargs):
                process = self.FakeServerProcess(command, **kwargs)
                fake_processes.append(process)
                return process

            with mock.patch("task6_bundle.run_preflight", return_value={"execution_ready": True, "blockers": [], "network_calls": 0, "generation_calls": 0}):
                with mock.patch("task6_bundle.subprocess.Popen", side_effect=fake_popen):
                    with mock.patch("task6_bundle._ensure_loopback_port_free"):
                        with mock.patch("task6_bundle._local_json_request", side_effect=api_response):
                            with mock.patch("task6_bundle.time.sleep"):
                                report = run_gpu_preflight(
                                    manifest_path=package / "experiment-manifest.json",
                                    contract_path=package / "execution-input-contract.json",
                                    input_root=root / "private-inputs",
                                    run_root=run_root,
                                    rights_path=root / "private-inputs/rights-attestation.json",
                                    provider_id=SYNTHETIC_PROVIDER,
                                    output_dir=root / "gpu-preflight-too-long",
                                )

        self.assertFalse(report["preflight_passed"])
        self.assertEqual(report["generation_calls"], 0)
        self.assertEqual(report["local_server"], "verified_stopped")
        self.assertEqual(report["arms"]["F"]["status"], "failed")
        self.assertNotIn("/v1/chat/completions", paths)
        self.assertTrue(fake_processes[0].terminated)


class PackageIntegrationTests(unittest.TestCase):
    def test_missing_private_inputs_and_rights_stay_blocked_without_network_or_model_calls(self) -> None:
        package = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_preflight(
                manifest_path=package / "experiment-manifest.json",
                input_root=root / "private-inputs",
                run_root=root / "run",
                rights_path=root / "private-inputs" / "rights-attestation.json",
                provider_id="synthetic-local-test-only",
                phase="inputs",
            )
        self.assertFalse(report["execution_ready"])
        self.assertFalse(report["input_ready"])
        self.assertEqual(report["network_calls"], 0)
        self.assertEqual(report["generation_calls"], 0)
        self.assertTrue(any("rights" in blocker for blocker in report["blockers"]))

    def test_preflight_blocks_replay_when_any_prior_result_artifact_exists(self) -> None:
        package = Path(__file__).resolve().parents[1]
        artifact_cases = (
            ("results.json", "[]"),
            ("results.json", "{"),
            ("failures.jsonl", "{"),
            ("attempt-events.jsonl", "{"),
            ("response-HF-raw.json", "synthetic response bytes"),
            ("execution-error.json", '{"synthetic":true}'),
        )
        for filename, payload in artifact_cases:
            with self.subTest(filename=filename, payload=payload), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                input_root = root / "inputs"
                input_root.mkdir()
                run_root = root / "run"
                results_root = run_root / "results"
                results_root.mkdir(parents=True)
                (results_root / filename).write_text(payload, encoding="utf-8")

                contract_bytes = b'{"synthetic":true}'
                (input_root / "execution-input-contract.json").write_bytes(contract_bytes)
                contract_digest = hashlib.sha256(contract_bytes).hexdigest()
                manifest = {
                    "experiment_id": SYNTHETIC_EXPERIMENT,
                    "input_contract": {
                        "bytes": len(contract_bytes),
                        "sha256": contract_digest,
                    },
                    "inputs": {
                        "frames": [
                            {
                                "input_id": "synthetic-frame",
                                "filename": "synthetic.png",
                                "bytes": 1,
                                "sha256": "0" * 64,
                            }
                        ],
                        "source_video": {
                            "filename": "synthetic.mp4",
                            "bytes": 1,
                            "sha256": "1" * 64,
                        },
                    },
                    "model": {"files": [{"filename": "synthetic.gguf", "bytes": 1, "sha256": "2" * 64}]},
                    "runner_files": [{"filename": "synthetic-runner.py", "bytes": 1, "sha256": "3" * 64}],
                    "environment": {},
                }
                manifest_path = root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                rights_path = input_root / "rights-attestation.json"
                rights_path.write_text("{}", encoding="utf-8")
                runtime_path = run_root / "runtime-verification.json"
                runtime_path.parent.mkdir(parents=True, exist_ok=True)
                runtime_path.write_text(
                    json.dumps(
                        {
                            "binary_path": "llama.cpp/build/bin/llama-server",
                            "binary_bytes": 1,
                            "binary_sha256": "4" * 64,
                        }
                    ),
                    encoding="utf-8",
                )

                with mock.patch("task6_bundle._file_check", return_value=("verified", {})):
                    with mock.patch("task6_bundle.validate_rights_attestation", return_value=[]):
                        with mock.patch("task6_bundle.validate_runtime_record", return_value=[]):
                            report = run_preflight(
                                manifest_path=manifest_path,
                                input_root=input_root,
                                run_root=run_root,
                                rights_path=rights_path,
                                provider_id=SYNTHETIC_PROVIDER,
                                phase="execution",
                            )

                one_shot = next(check for check in report["checks"] if check["name"] == "one_shot_root")
                self.assertEqual(one_shot["status"], "blocked")
                self.assertFalse(report["execution_ready"])
                self.assertTrue(any("prior result artifacts" in blocker for blocker in report["blockers"]))

    def test_empty_results_directory_and_setup_evidence_allow_first_preflight(self) -> None:
        package = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_root = root / "inputs"
            input_root.mkdir()
            run_root = root / "run"
            results_root = run_root / "results"
            (results_root / "setup-evidence" / "gpu-preflight").mkdir(parents=True)
            (results_root / "setup-evidence" / "runtime-verification.json").write_text(
                '{"synthetic":true}', encoding="utf-8"
            )

            contract_bytes = b'{"synthetic":true}'
            (input_root / "execution-input-contract.json").write_bytes(contract_bytes)
            manifest = {
                "experiment_id": SYNTHETIC_EXPERIMENT,
                "input_contract": {
                    "bytes": len(contract_bytes),
                    "sha256": hashlib.sha256(contract_bytes).hexdigest(),
                },
                "inputs": {
                    "frames": [],
                    "source_video": {"filename": "synthetic.mp4", "bytes": 1, "sha256": "1" * 64},
                },
                "model": {"files": []},
                "runner_files": [],
                "environment": {},
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            rights_path = input_root / "rights-attestation.json"
            rights_path.write_text("{}", encoding="utf-8")
            runtime_path = run_root / "runtime-verification.json"
            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            runtime_path.write_text(
                json.dumps(
                    {
                        "binary_path": "llama.cpp/build/bin/llama-server",
                        "binary_bytes": 1,
                        "binary_sha256": "4" * 64,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("task6_bundle._file_check", return_value=("verified", {})):
                with mock.patch("task6_bundle.validate_rights_attestation", return_value=[]):
                    with mock.patch("task6_bundle.validate_runtime_record", return_value=[]):
                        report = run_preflight(
                            manifest_path=manifest_path,
                            input_root=input_root,
                            run_root=run_root,
                            rights_path=rights_path,
                            provider_id=SYNTHETIC_PROVIDER,
                            phase="execution",
                        )

        one_shot = next(check for check in report["checks"] if check["name"] == "one_shot_root")
        self.assertEqual(one_shot["status"], "clear")
        self.assertTrue(report["execution_ready"])

    def test_pinned_runner_source_bytes_match_the_current_manifest(self) -> None:
        package = Path(__file__).resolve().parents[1]
        manifest = json.loads((package / "experiment-manifest.json").read_text(encoding="utf-8"))
        for artifact in manifest["runner_files"]:
            with self.subTest(filename=artifact["filename"]):
                path = package / "runner_sources" / artifact["filename"]
                payload = path.read_bytes()
                self.assertEqual(len(payload), artifact["bytes"])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), artifact["sha256"])

    def test_real_cli_blocks_empty_prior_artifacts_and_corrupt_ledgers(self) -> None:
        package = Path(__file__).resolve().parents[1]
        tool = package / "tools/task6_bundle.py"
        cases = (
            ("results.json", "[]"),
            ("attempt-events.jsonl", ""),
            ("failures.jsonl", ""),
            ("results.json", "{"),
            ("attempt-events.jsonl", "{"),
            ("failures.jsonl", "{"),
        )
        for filename, payload in cases:
            with self.subTest(filename=filename, payload=payload), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                results = root / "results"
                results.mkdir()
                contract = root / "synthetic-contract.json"
                contract.write_text(json.dumps(synthetic_contract()), encoding="utf-8")
                (results / filename).write_text(payload, encoding="utf-8")
                validate = subprocess.run(
                    [
                        sys.executable,
                        str(tool),
                        "validate-results",
                        "--results-dir",
                        str(results),
                        "--contract",
                        str(contract),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                returned = subprocess.run(
                    [
                        sys.executable,
                        str(tool),
                        "write-return",
                        "--results-dir",
                        str(results),
                        "--contract",
                        str(contract),
                        "--return-dir",
                        str(root / "return"),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(validate.returncode, 2, validate.stdout + validate.stderr)
                self.assertEqual(returned.returncode, 2, returned.stdout + returned.stderr)
                audit = json.loads(validate.stdout)
                private_manifest = json.loads((root / "return/private-return-manifest.json").read_text())
                self.assertEqual(audit["inference_status"], "unknown")
                self.assertTrue(audit["replay_blocked"])
                self.assertEqual(private_manifest["audit"]["inference_status"], "unknown")
                self.assertTrue(private_manifest["audit"]["replay_blocked"])

    def test_private_and_redacted_manifests_match_the_declared_output_schema(self) -> None:
        from jsonschema import Draft202012Validator

        package = Path(__file__).resolve().parents[1]
        schema = json.loads((package / "output-manifest.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            return_dir = root / "return"
            write_return_files(root / "empty-results", synthetic_contract(), return_dir)
            for filename in ("private-return-manifest.json", "redacted-summary.json"):
                value = json.loads((return_dir / filename).read_text(encoding="utf-8"))
                Draft202012Validator(schema).validate(value)
        example = json.loads((package / "examples/synthetic/redacted-summary.synthetic.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(example)
        self.assertIs(example["synthetic"], True)


if __name__ == "__main__":
    unittest.main()
