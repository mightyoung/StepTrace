"""Synthetic-only regression tests; no Kaggle, model, or network calls."""

from __future__ import annotations

import hashlib
import ast
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


SOURCE_DIR = Path(__file__).resolve().parents[1] / "sources"


def _load_module(name: str, path: Path):
    if not path.is_file():
        raise AssertionError(f"expected implementation file is missing: {path.name}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load implementation file: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_pair():
    return [
        ("F", {"condition": "F", "synthetic_test_fixture": True, "messages": [{"role": "user", "content": "synthetic F"}]}),
        ("HF", {"condition": "HF", "synthetic_test_fixture": True, "messages": [{"role": "user", "content": "synthetic HF"}]}),
    ]


class PreparedRunnerCrashSafetyTests(unittest.TestCase):
    def test_termination_confirmation_requires_kill_request_and_reap(self):
        safety = _load_module("task6_runner_safety_termination_evidence_test", SOURCE_DIR / "runner_safety.py")
        self.assertTrue(safety.sender_termination_confirmed(kill_requested=True, reaped=True))
        self.assertFalse(safety.sender_termination_confirmed(kill_requested=False, reaped=True))
        self.assertFalse(safety.sender_termination_confirmed(kill_requested=True, reaped=False))

    def test_empty_or_malformed_worker_stdin_never_calls_urlopen(self):
        runner_tree = ast.parse((SOURCE_DIR / "prepared-run_pair-crashsafe.py").read_text())
        worker_source = None
        for node in ast.walk(runner_tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "generation_worker"
                for target in node.targets
            ):
                worker_source = ast.literal_eval(node.value)
                break
        self.assertIsInstance(worker_source, str)

        for raw_input in (b"", b"not-json", b"[]", b"{}", b'{"messages":[]}'):
            with self.subTest(raw_input=raw_input), patch("sys.argv", ["worker", "60"]), patch(
                "sys.stdin", type("Input", (), {"buffer": io.BytesIO(raw_input)})()
            ), patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()), patch(
                "urllib.request.urlopen"
            ) as urlopen:
                with self.assertRaises(SystemExit):
                    exec(worker_source, {})
                urlopen.assert_not_called()

    def test_working_copy_uses_private_input_mount_not_its_script_parent(self):
        safety = _load_module("task6_runner_safety_path_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_root = root / "kaggle-input"
            script_root = root / "kaggle-working" / "steptrace-task6"
            input_root.mkdir()
            script_root.mkdir(parents=True)
            resolved = safety.resolve_input_root(
                script_path=script_root / "run_pair.py",
                env={"TASK6_INPUT_ROOT": str(input_root)},
            )
            self.assertEqual(resolved, input_root.resolve())

    def test_shared_message_context_is_rejected_before_any_send(self):
        safety = _load_module("task6_runner_safety_shared_context_test", SOURCE_DIR / "runner_safety.py")
        pair = _synthetic_pair()
        pair[1][1]["messages"] = pair[0][1]["messages"]
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            with self.assertRaisesRegex(ValueError, "independent request contexts"):
                safety.execute_pair(
                    pair,
                    output_dir=Path(temp) / "results",
                    send_request=lambda raw, **kwargs: (calls.append(raw), b"{}")[1],
                    parse_response=lambda *_args: {"synthetic_test_fixture": True},
                )
            self.assertEqual(calls, [])

    def test_f_success_hf_failure_is_recorded_once_and_never_retried(self):
        safety = _load_module("task6_runner_safety_pair_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "results"
            calls = []

            def mock_send(raw_request: bytes, *, timeout_seconds: int) -> bytes:
                request = json.loads(raw_request)
                calls.append((request["condition"], timeout_seconds))
                self.assertEqual(len((output / "attempts.jsonl").read_text().splitlines()), len(calls))
                self.assertEqual(timeout_seconds, 60)
                if request["condition"] == "F":
                    return b'{"synthetic_test_fixture":true,"condition":"F"}'
                raise TimeoutError("synthetic HF transport failure")

            def parse(arm: str, raw_response: bytes, _attempt: dict):
                value = json.loads(raw_response)
                self.assertTrue(value["synthetic_test_fixture"])
                return {"condition": arm, "synthetic_test_fixture": True}

            original_fsync = safety.os.fsync
            fsync_calls = []

            def track_fsync(fd: int):
                fsync_calls.append(fd)
                return original_fsync(fd)

            with patch.object(safety.os, "fsync", side_effect=track_fsync):
                with self.assertRaisesRegex(TimeoutError, "synthetic HF"):
                    safety.execute_pair(
                        _synthetic_pair(),
                        output_dir=output,
                        send_request=mock_send,
                        parse_response=parse,
                        request_id_factory=iter(("synthetic-F-id", "synthetic-HF-id")).__next__,
                    )

            self.assertEqual(calls, [("F", 60), ("HF", 60)])
            self.assertGreaterEqual(len(fsync_calls), 7)
            attempts = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
            self.assertEqual([row["condition"] for row in attempts], ["F", "HF"])
            self.assertEqual(len({row["request_id"] for row in attempts}), 2)
            self.assertTrue(all(row["attempt"] == 1 for row in attempts))
            self.assertTrue(all("start_epoch" in row for row in attempts))
            self.assertEqual(safety.count_send_intents(output / "attempts.jsonl"), 2)
            self.assertEqual(
                (output / "response-F-raw.json").read_bytes(),
                b'{"synthetic_test_fixture":true,"condition":"F"}',
            )
            events = [json.loads(line) for line in (output / "attempt-events.jsonl").read_text().splitlines()]
            self.assertEqual(
                [(row["condition"], row["state"]) for row in events],
                [("F", "send_intent"), ("F", "response_received"), ("HF", "send_intent"), ("HF", "response_unknown")],
            )
            failures = [json.loads(line) for line in (output / "failures.jsonl").read_text().splitlines()]
            self.assertEqual(failures[0]["condition"], "HF")
            self.assertEqual(failures[0]["error_type"], "TimeoutError")

            with self.assertRaises(safety.PriorAttemptExists):
                safety.execute_pair(
                    _synthetic_pair(),
                    output_dir=output,
                    send_request=lambda *_args, **_kwargs: self.fail("must not resend"),
                    parse_response=parse,
                )

    def test_crash_after_durable_send_intent_blocks_a_second_run(self):
        safety = _load_module("task6_runner_safety_crash_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "results"
            network_send_count = 0

            def crash_before_transport(_raw_request: bytes, *, timeout_seconds: int):
                nonlocal network_send_count
                self.assertEqual(timeout_seconds, 60)
                row = json.loads((output / "attempts.jsonl").read_text().splitlines()[0])
                self.assertEqual(row["condition"], "F")
                self.assertEqual(row["attempt"], 1)
                raise KeyboardInterrupt("synthetic process crash before transport")

            with self.assertRaises(KeyboardInterrupt):
                safety.execute_pair(
                    _synthetic_pair(),
                    output_dir=output,
                    send_request=crash_before_transport,
                    parse_response=lambda *_args: {},
                    request_id_factory=lambda: "synthetic-crash-id",
                )

            self.assertEqual(network_send_count, 0)
            events = [json.loads(line) for line in (output / "attempt-events.jsonl").read_text().splitlines()]
            self.assertEqual([(row["condition"], row["state"]) for row in events], [("F", "send_intent")])
            with self.assertRaises(safety.PriorAttemptExists):
                safety.execute_pair(
                    _synthetic_pair(),
                    output_dir=output,
                    send_request=lambda *_args, **_kwargs: self.fail("must not resend after ambiguous crash"),
                    parse_response=lambda *_args: {},
                )

    def test_sender_wall_clock_deadline_records_unknown_delivery_and_stops_pair(self):
        safety = _load_module("task6_runner_safety_deadline_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "results"
            calls = []
            real_monotonic = time.monotonic
            virtual_clock = {"now": 500.0}

            def virtual_monotonic():
                return virtual_clock["now"]

            def slow_sender(raw_request: bytes, *, timeout_seconds: float) -> bytes:
                calls.append(json.loads(raw_request)["condition"])
                self.assertEqual(timeout_seconds, 60)
                virtual_clock["now"] += 61
                return b'{"synthetic_test_fixture":true}'

            started = real_monotonic()
            with patch.object(safety.time, "monotonic", side_effect=virtual_monotonic):
                with self.assertRaisesRegex(TimeoutError, "wall-clock"):
                    safety.execute_pair(
                        _synthetic_pair(),
                        output_dir=output,
                        send_request=slow_sender,
                        parse_response=lambda *_args: {"synthetic_test_fixture": True},
                    )

            self.assertLess(real_monotonic() - started, 0.15)
            self.assertEqual(calls, ["F"])
            intent = json.loads((output / "attempts.jsonl").read_text().splitlines()[0])
            self.assertEqual(intent["timeout_seconds"], 60)
            self.assertEqual(intent["timeout_mode"], "wall_clock")
            self.assertEqual(intent["timeout_scope"], "per_request_from_send_intent_timestamp")
            self.assertIn("send_deadline_epoch", intent)
            self.assertGreater(intent["cancellation_reserve_seconds"], 0)
            self.assertLess(intent["sender_cancel_start_epoch"], intent["send_deadline_epoch"])
            failure = json.loads((output / "failures.jsonl").read_text().splitlines()[-1])
            self.assertEqual(failure["condition"], "F")
            self.assertEqual(failure["state"], "response_unknown")
            self.assertEqual(failure["timeout_mode"], "wall_clock")
            self.assertEqual(failure["timeout_scope"], "per_request_from_send_intent_timestamp")
            self.assertIs(failure.get("deadline_exceeded"), True)
            self.assertTrue(failure["delivery_unknown"])

    def test_wall_clock_deadline_terminates_hung_sender_before_next_arm(self):
        safety = _load_module("task6_runner_cancel_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "results"

            class HungSender:
                def __init__(self):
                    self.cancel_requested = threading.Event()
                    self.calls = []

                def __call__(self, raw_request: bytes, *, timeout_seconds: float) -> bytes:
                    self.calls.append(json.loads(raw_request)["condition"])
                    self.cancel_requested.wait(5)
                    return b'{"synthetic_test_fixture":true}'

                def cancel(self) -> bool:
                    self.cancel_requested.set()
                    return True

            sender = HungSender()
            cancelled_by_runner = False
            started = time.monotonic()
            try:
                with patch.object(safety, "REQUEST_TIMEOUT_SECONDS", 0.03):
                    with self.assertRaisesRegex(TimeoutError, "wall-clock"):
                        safety.execute_pair(
                            _synthetic_pair(),
                            output_dir=output,
                            send_request=sender,
                            parse_response=lambda *_args: {"synthetic_test_fixture": True},
                        )
                cancelled_by_runner = sender.cancel_requested.is_set()
            finally:
                sender.cancel()

            self.assertTrue(cancelled_by_runner)
            self.assertLess(time.monotonic() - started, 0.15)
            self.assertEqual(sender.calls, ["F"])
            failure = json.loads((output / "failures.jsonl").read_text().splitlines()[-1])
            self.assertEqual(failure["state"], "response_unknown")
            self.assertTrue(failure["delivery_unknown"])
            self.assertTrue(failure["sender_terminated"])

    def test_http_error_body_is_preserved_as_a_failure_artifact(self):
        import io
        import urllib.error

        safety = _load_module("task6_runner_safety_http_error_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "results"
            raw_error = b'{"synthetic_test_fixture":true,"error":"synthetic HTTP failure"}'

            def mock_http_error(_raw_request: bytes, *, timeout_seconds: int):
                self.assertEqual(timeout_seconds, 60)
                raise urllib.error.HTTPError(
                    "http://127.0.0.1:8080/v1/chat/completions",
                    503,
                    "synthetic service unavailable",
                    hdrs=None,
                    fp=io.BytesIO(raw_error),
                )

            with self.assertRaises(urllib.error.HTTPError):
                safety.execute_pair(
                    _synthetic_pair(),
                    output_dir=output,
                    send_request=mock_http_error,
                    parse_response=lambda *_args: {},
                    request_id_factory=lambda: "synthetic-http-id",
                )

            self.assertEqual((output / "response-F-http-error-raw.bin").read_bytes(), raw_error)
            event = json.loads((output / "attempt-events.jsonl").read_text().splitlines()[-1])
            self.assertEqual(event["state"], "http_error")
            self.assertEqual(event["http_status"], 503)
            failure = json.loads((output / "failures.jsonl").read_text().splitlines()[-1])
            self.assertEqual(failure["condition"], "F")
            self.assertEqual(failure["response_sha256"], hashlib.sha256(raw_error).hexdigest())

    def test_active_run_lock_blocks_a_second_sender(self):
        safety = _load_module("task6_runner_safety_lock_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "results"
            guard = safety.OneShotRunGuard(output).acquire()
            calls = []
            try:
                with self.assertRaisesRegex(RuntimeError, "another Task 6 runner is active"):
                    safety.execute_pair(
                        _synthetic_pair(),
                        output_dir=output,
                        send_request=lambda *_args, **_kwargs: calls.append("sent"),
                        parse_response=lambda *_args: {},
                    )
            finally:
                guard.release()
            self.assertEqual(calls, [])

    def test_bootstrap_verifies_and_copies_the_prepared_source_without_running_it(self):
        bootstrap = _load_module(
            "task6_runner_bootstrap_test",
            SOURCE_DIR / "prepared-execute-pair-bootstrap-crashsafe.py",
        )
        self.assertEqual(
            bootstrap.PREPARED_RUNNER_SHA256,
            hashlib.sha256((SOURCE_DIR / "prepared-run_pair-crashsafe.py").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            bootstrap.SAFETY_MODULE_SHA256,
            hashlib.sha256((SOURCE_DIR / "runner_safety.py").read_bytes()).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "private-input"
            working = root / "working"
            package.mkdir()
            working.mkdir()
            runner = b"# synthetic prepared runner\n"
            helper = b"# synthetic safety helper\n"
            (package / "prepared-run_pair-crashsafe.py").write_bytes(runner)
            (package / "runner_safety.py").write_bytes(helper)
            prepared = bootstrap.prepare_runner(
                input_root=package,
                working_root=working,
                expected_runner_sha256=hashlib.sha256(runner).hexdigest(),
                expected_safety_sha256=hashlib.sha256(helper).hexdigest(),
            )
            self.assertEqual(prepared, working / "prepared-run_pair-crashsafe.py")
            self.assertEqual(prepared.read_bytes(), runner)
            self.assertEqual((working / "runner_safety.py").read_bytes(), helper)
            self.assertFalse((working / "results" / "attempts.jsonl").exists())

    def test_bootstrap_copies_the_exact_bytes_it_hash_verified(self):
        bootstrap = _load_module(
            "task6_runner_bootstrap_source_race_test",
            SOURCE_DIR / "prepared-execute-pair-bootstrap-crashsafe.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "private-input"
            working = root / "working"
            package.mkdir()
            working.mkdir()
            runner_source = package / "prepared-run_pair-crashsafe.py"
            helper_source = package / "runner_safety.py"
            runner_bytes = b"# verified synthetic runner\n"
            helper_bytes = b"# verified synthetic helper\n"
            runner_source.write_bytes(runner_bytes)
            helper_source.write_bytes(helper_bytes)
            original_read_bytes = Path.read_bytes
            source_mutated = False

            def mutate_source_after_first_read(path):
                nonlocal source_mutated
                data = original_read_bytes(path)
                if path == runner_source and not source_mutated:
                    source_mutated = True
                    path.write_bytes(b"# changed after hash verification\n")
                return data

            with patch.object(Path, "read_bytes", new=mutate_source_after_first_read):
                prepared = bootstrap.prepare_runner(
                    input_root=package,
                    working_root=working,
                    expected_runner_sha256=hashlib.sha256(runner_bytes).hexdigest(),
                    expected_safety_sha256=hashlib.sha256(helper_bytes).hexdigest(),
                )

            self.assertEqual(prepared.read_bytes(), runner_bytes)

    def test_bootstrap_rejects_a_target_changed_during_copy(self):
        bootstrap = _load_module(
            "task6_runner_bootstrap_target_hash_test",
            SOURCE_DIR / "prepared-execute-pair-bootstrap-crashsafe.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "private-input"
            working = root / "working"
            package.mkdir()
            working.mkdir()
            runner = b"# verified synthetic runner\n"
            helper = b"# verified synthetic helper\n"
            (package / "prepared-run_pair-crashsafe.py").write_bytes(runner)
            (package / "runner_safety.py").write_bytes(helper)
            real_replace = bootstrap.os.replace

            def replace_then_tamper(source, destination):
                real_replace(source, destination)
                if Path(destination).name == "prepared-run_pair-crashsafe.py":
                    Path(destination).write_bytes(b"# altered target\n")

            with patch.object(bootstrap.os, "replace", side_effect=replace_then_tamper):
                with self.assertRaisesRegex(ValueError, "copied runner SHA256"):
                    bootstrap.prepare_runner(
                        input_root=package,
                        working_root=working,
                        expected_runner_sha256=hashlib.sha256(runner).hexdigest(),
                        expected_safety_sha256=hashlib.sha256(helper).hexdigest(),
                    )

    def test_bootstrap_refuses_to_replace_working_code_when_run_lock_is_held(self):
        bootstrap = _load_module(
            "task6_runner_bootstrap_lock_test",
            SOURCE_DIR / "prepared-execute-pair-bootstrap-crashsafe.py",
        )
        safety = _load_module("task6_runner_bootstrap_lock_guard_test", SOURCE_DIR / "runner_safety.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "private-input"
            working = root / "working"
            package.mkdir()
            working.mkdir()
            results = working / "results"
            results.mkdir()
            runner_source = b"# verified new runner\n"
            helper_source = b"# verified new helper\n"
            (package / "prepared-run_pair-crashsafe.py").write_bytes(runner_source)
            (package / "runner_safety.py").write_bytes(helper_source)
            runner_destination = working / "prepared-run_pair-crashsafe.py"
            runner_destination.write_bytes(b"# currently active runner\n")
            guard = safety.OneShotRunGuard(results).acquire()
            try:
                with self.assertRaisesRegex(RuntimeError, "another Task 6 runner is active"):
                    bootstrap.prepare_runner(
                        input_root=package,
                        working_root=working,
                        expected_runner_sha256=hashlib.sha256(runner_source).hexdigest(),
                        expected_safety_sha256=hashlib.sha256(helper_source).hexdigest(),
                    )
            finally:
                guard.release()
            self.assertEqual(runner_destination.read_bytes(), b"# currently active runner\n")

    def _exercise_prepared_working_copy(
        self,
        *,
        invalid_f_schema=False,
        lock_held=False,
        bootstrap_entrypoint=False,
        runtime_commit="8345f333951c661d166b00e6f9362e553768f292",
        invalid_frame_hash=False,
        finish_reason="stop",
        response_truncated=False,
        hung_generation=False,
        pending_generation_spawn=False,
        wall_timeout_seconds=60,
    ):
        import base64
        import os
        import runpy
        import sys

        from PIL import Image

        bootstrap = _load_module(
            "task6_runner_bootstrap_integration_test",
            SOURCE_DIR / "prepared-execute-pair-bootstrap-crashsafe.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_root = root / "private-input"
            working_root = root / "working" / "steptrace-task6"
            input_root.mkdir()
            (input_root / "prepared-run_pair-crashsafe.py").write_bytes(
                (SOURCE_DIR / "prepared-run_pair-crashsafe.py").read_bytes()
            )
            (input_root / "runner_safety.py").write_bytes(
                (SOURCE_DIR / "runner_safety.py").read_bytes()
            )

            frame_specs = []
            for index, input_id in enumerate(("k01", "k02", "k03", "k04")):
                filename = input_id + ".png"
                frame_path = input_root / filename
                Image.new("RGB", (640, 480), color=(index * 30, 10, 20)).save(frame_path)
                frame_bytes = frame_path.read_bytes()
                frame_specs.append(
                    {
                        "input_id": input_id,
                        "filename": filename,
                        "bytes": len(frame_bytes),
                        "sha256": hashlib.sha256(frame_bytes).hexdigest(),
                    }
                )
            if invalid_frame_hash:
                frame_specs[0]["sha256"] = "0" * 64

            schema = {
                "type": "object",
                "required": ["current_relations"],
                "properties": {
                    "current_relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["seat_id"],
                            "properties": {"seat_id": {"type": "string"}},
                        },
                    }
                },
            }
            plan = {
                "model_input_frames": frame_specs,
                "requests": [
                    {
                        "condition": "F",
                        "input_ids": ["k04"],
                        "exact_user_content_sequence": [
                            {"type": "text", "text": "synthetic F"},
                            {"type": "image", "input_id": "k04"},
                        ],
                    },
                    {
                        "condition": "HF",
                        "input_ids": ["k01", "k02", "k03", "k04"],
                        "exact_user_content_sequence": [
                            {"type": "text", "text": "synthetic HF"},
                            {"type": "image", "input_id": "k01"},
                            {"type": "image", "input_id": "k02"},
                            {"type": "image", "input_id": "k03"},
                            {"type": "image", "input_id": "k04"},
                        ],
                    },
                ],
                "content_schemas": {"F": schema, "HF": schema},
                "generation_parameters_exact": {
                    "temperature": 0,
                    "seed": 1,
                    "max_tokens": 2048,
                    "stream": False,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                "system_prompt_exact": "synthetic test prompt",
            }
            (input_root / "execution-input-contract.json").write_text(
                json.dumps(plan), encoding="utf-8"
            )

            if bootstrap_entrypoint:
                working_root.mkdir(parents=True, exist_ok=True)
                runner = working_root / "prepared-run_pair-crashsafe.py"
            else:
                runner = bootstrap.prepare_runner(
                    input_root=input_root,
                    working_root=working_root,
                    expected_runner_sha256=bootstrap.PREPARED_RUNNER_SHA256,
                    expected_safety_sha256=bootstrap.SAFETY_MODULE_SHA256,
                )
            (working_root / "runtime-verification.json").write_text(
                json.dumps({"commit": runtime_commit}),
                encoding="utf-8",
            )

            generation_calls = []
            generation_processes = []
            all_urls = []
            generation_spawn_entered = threading.Event()
            generation_spawn_gate = threading.Event()
            generation_process_spawned = threading.Event()

            class MockResponse:
                def __init__(self, body: bytes, status: int = 200):
                    self.body = body
                    self.status = status

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self):
                    return self.body

            def mock_urlopen(request, timeout):
                url = request.full_url
                all_urls.append((url, timeout))
                self.assertTrue(url.startswith("http://127.0.0.1:8080/"))
                path = url.removeprefix("http://127.0.0.1:8080")
                if path == "/health":
                    return MockResponse(b"{}")
                if path == "/props":
                    return MockResponse(b'{"n_ctx":16384,"n_ctx_per_seq":16384}')
                if path == "/apply-template":
                    return MockResponse(b'{"prompt":"synthetic prompt"}')
                if path == "/tokenize":
                    return MockResponse(b'{"tokens":[1,2,3]}')
                if path == "/v1/chat/completions":
                    body = json.loads(request.data)
                    content = body["messages"][1]["content"]
                    arm = "F" if sum(part["type"] == "image_url" for part in content) == 1 else "HF"
                    generation_calls.append((arm, timeout, body))
                    output = (
                        {}
                        if invalid_f_schema and arm == "F"
                        else {
                            "current_relations": [
                                {"seat_id": "blue_seat_left"},
                                {"seat_id": "blue_seat_right"},
                            ]
                        }
                    )
                    response = {
                        "choices": [
                            {
                                "message": {"content": json.dumps(output)},
                                "finish_reason": finish_reason,
                            }
                        ],
                        "usage": {"prompt_tokens": 100},
                        "timings": {"synthetic_test_fixture": True},
                        "truncated": response_truncated,
                    }
                    return MockResponse(json.dumps(response).encode("utf-8"))
                self.fail(f"unexpected mocked URL path: {path}")

            class MockServer:
                def __init__(self, *_args, **_kwargs):
                    self.returncode = None

                def poll(self):
                    return self.returncode

                def terminate(self):
                    self.returncode = -15

                def wait(self, timeout=None):
                    return self.returncode

                def kill(self):
                    self.returncode = -9

            class MockGenerationProcess:
                def __init__(self, *, hang):
                    self.returncode = None
                    self.hang = hang
                    self.killed = threading.Event()
                    self.wait_called = threading.Event()
                    self.communicate_called = threading.Event()

                def communicate(self, input=None, timeout=None):
                    self.communicate_called.set()
                    body = json.loads(input)
                    content = body["messages"][1]["content"]
                    arm = "F" if sum(part["type"] == "image_url" for part in content) == 1 else "HF"
                    generation_calls.append((arm, timeout, body))
                    if self.hang:
                        self.killed.wait(5)
                        self.returncode = -9
                        return b"", b"synthetic sender process killed"
                    output = (
                        {}
                        if invalid_f_schema and arm == "F"
                        else {
                            "current_relations": [
                                {"seat_id": "blue_seat_left"},
                                {"seat_id": "blue_seat_right"},
                            ]
                        }
                    )
                    response = {
                        "choices": [
                            {
                                "message": {"content": json.dumps(output)},
                                "finish_reason": finish_reason,
                            }
                        ],
                        "usage": {"prompt_tokens": 100},
                        "timings": {"synthetic_test_fixture": True},
                        "truncated": response_truncated,
                    }
                    envelope = {
                        "status": 200,
                        "reason": "OK",
                        "body_b64": base64.b64encode(
                            json.dumps(response).encode("utf-8")
                        ).decode("ascii"),
                    }
                    self.returncode = 0
                    return json.dumps(envelope).encode("utf-8"), b""

                def poll(self):
                    return self.returncode

                def kill(self):
                    self.killed.set()
                    self.returncode = -9

                def terminate(self):
                    self.kill()

                def wait(self, timeout=None):
                    self.wait_called.set()
                    return self.returncode

            def mock_popen(command, **_kwargs):
                if isinstance(command, (list, tuple)) and len(command) > 1 and command[1] == "-c":
                    if pending_generation_spawn:
                        generation_spawn_entered.set()
                        generation_spawn_gate.wait(3)
                    process = MockGenerationProcess(
                        hang=hung_generation or pending_generation_spawn
                    )
                    generation_processes.append(process)
                    generation_process_spawned.set()
                    return process
                return MockServer()

            safety_module = _load_module(
                "task6_runner_safety_integration_timeout_test",
                SOURCE_DIR / "runner_safety.py",
            )
            safety_module.REQUEST_TIMEOUT_SECONDS = wall_timeout_seconds

            external_guard = None
            config_marker = None
            marker_bytes = b"previous-run-evidence\n"
            if lock_held:
                result_dir = working_root / "results"
                result_dir.mkdir(parents=True, exist_ok=True)
                config_marker = result_dir / "execution-config.json"
                config_marker.write_bytes(marker_bytes)
                guard_module = _load_module(
                    "task6_runner_external_lock_test", SOURCE_DIR / "runner_safety.py"
                )
                external_guard = guard_module.OneShotRunGuard(result_dir).acquire()

            lock_probe = {}
            execution_error = None
            try:
                with patch.dict(
                    os.environ,
                    {
                        "TASK6_INPUT_ROOT": str(input_root),
                        "TASK6_RUN_ROOT": str(working_root),
                    },
                ), patch.dict(sys.modules, {"runner_safety": safety_module}), patch("subprocess.Popen", side_effect=mock_popen), patch(
                    "subprocess.check_output", return_value="0,10,100,1"
                ), patch("urllib.request.urlopen", side_effect=mock_urlopen):
                    if bootstrap_entrypoint:
                        real_run_path = bootstrap.runpy.run_path

                        def probe_bootstrap_lock(path, run_name):
                            helper_module = _load_module(
                                "task6_runner_handoff_lock_test",
                                Path(path).parent / "runner_safety.py",
                            )
                            contender = helper_module.OneShotRunGuard(
                                working_root / "results"
                            )
                            try:
                                contender.acquire()
                            except RuntimeError as exc:
                                lock_probe["held"] = "another Task 6 runner is active" in str(exc)
                            else:
                                lock_probe["held"] = False
                                contender.release()
                            return real_run_path(path, run_name=run_name)

                        with patch.object(
                            bootstrap.runpy, "run_path", side_effect=probe_bootstrap_lock
                        ):
                            bootstrap.main()
                    else:
                        runpy.run_path(str(runner), run_name="__main__")
            except Exception as exc:
                execution_error = exc
            finally:
                if pending_generation_spawn:
                    self.assertTrue(generation_spawn_entered.wait(1))
                    generation_spawn_gate.set()
                    self.assertTrue(generation_process_spawned.wait(1))
                    self.assertTrue(generation_processes[0].killed.wait(1))
                    self.assertTrue(generation_processes[0].wait_called.wait(1))
                    self.assertFalse(generation_processes[0].communicate_called.is_set())
                if external_guard is not None:
                    external_guard.release()

            if lock_held:
                self.assertIsNotNone(execution_error)
                self.assertEqual(generation_calls, [])
                self.assertEqual(config_marker.read_bytes(), marker_bytes)
                return

            if bootstrap_entrypoint:
                self.assertIsNone(execution_error)
                self.assertIs(lock_probe.get("held"), True)

            if hung_generation:
                self.assertIsInstance(execution_error, TimeoutError)
                self.assertEqual([arm for arm, _timeout, _body in generation_calls], ["F"])
                self.assertEqual(len(generation_processes), 1)
                self.assertTrue(generation_processes[0].killed.is_set())
                self.assertTrue(generation_processes[0].wait_called.is_set())
                self.assertEqual(generation_processes[0].returncode, -9)
                failure = json.loads(
                    (working_root / "results" / "failures.jsonl").read_text().splitlines()[-1]
                )
                self.assertEqual(failure["state"], "response_unknown")
                self.assertTrue(failure["delivery_unknown"])
                self.assertTrue(failure["sender_terminated"])
                self.assertEqual(failure["sender_termination_status"], "terminated")
                return

            if pending_generation_spawn:
                self.assertIsInstance(execution_error, TimeoutError)
                self.assertEqual(len(generation_processes), 1)
                self.assertEqual(generation_calls, [])
                attempts = [
                    json.loads(line)
                    for line in (working_root / "results" / "attempts.jsonl").read_text().splitlines()
                ]
                self.assertEqual([row["condition"] for row in attempts], ["F"])
                failure = json.loads(
                    (working_root / "results" / "failures.jsonl").read_text().splitlines()[-1]
                )
                self.assertEqual(failure["state"], "response_unknown")
                self.assertTrue(failure["delivery_unknown"])
                self.assertFalse(failure["sender_terminated"])
                self.assertEqual(failure["sender_termination_status"], "pending_spawn")
                safety = _load_module("task6_runner_pending_spawn_replay_test", SOURCE_DIR / "runner_safety.py")
                retry_calls = []
                with self.assertRaises(safety.PriorAttemptExists):
                    safety.execute_pair(
                        _synthetic_pair(),
                        output_dir=working_root / "results",
                        send_request=lambda *_args, **_kwargs: retry_calls.append("sent"),
                        parse_response=lambda *_args: {},
                    )
                self.assertEqual(retry_calls, [])
                return

            if invalid_frame_hash or runtime_commit != "8345f333951c661d166b00e6f9362e553768f292":
                self.assertIsInstance(execution_error, ValueError)
                self.assertEqual(generation_calls, [])
                return

            if finish_reason != "stop" or response_truncated:
                self.assertIsInstance(execution_error, ValueError)
                self.assertEqual([arm for arm, _timeout, _body in generation_calls], ["F"])
                failures = [
                    json.loads(line)
                    for line in (working_root / "results" / "failures.jsonl").read_text().splitlines()
                ]
                self.assertEqual(failures[-1]["state"], "processing_failed")
                return

            if invalid_f_schema:
                self.assertIsNotNone(execution_error, "invalid F schema must fail the pair")
                self.assertEqual([arm for arm, _timeout, _body in generation_calls], ["F"])
                attempts = [
                    json.loads(line)
                    for line in (working_root / "results" / "attempts.jsonl").read_text().splitlines()
                ]
                self.assertEqual([row["condition"] for row in attempts], ["F"])
                failures = [
                    json.loads(line)
                    for line in (working_root / "results" / "failures.jsonl").read_text().splitlines()
                ]
                self.assertEqual(failures[-1]["condition"], "F")
                self.assertEqual(failures[-1]["state"], "processing_failed")
                self.assertFalse((working_root / "results" / "results.json").exists())
                return

            self.assertIsNone(execution_error)

            self.assertEqual([arm for arm, _timeout, _body in generation_calls], ["F", "HF"])
            self.assertEqual([timeout for _arm, timeout, _body in generation_calls], [60, 60])
            self.assertIsNot(generation_calls[0][2]["messages"], generation_calls[1][2]["messages"])
            self.assertEqual(
                generation_calls[0][2]["messages"][1]["content"][-1],
                generation_calls[1][2]["messages"][1]["content"][-1],
            )
            attempts = [
                json.loads(line)
                for line in (working_root / "results" / "attempts.jsonl").read_text().splitlines()
            ]
            self.assertEqual([row["condition"] for row in attempts], ["F", "HF"])
            self.assertEqual([row["attempt"] for row in attempts], [1, 1])
            self.assertEqual(len({row["request_id"] for row in attempts}), 2)
            self.assertTrue((working_root / "task6-results.zip").is_file())
            self.assertTrue(all(url.startswith("http://127.0.0.1:8080/") for url, _timeout in all_urls))

    def test_invalid_f_schema_is_persisted_and_hf_is_not_sent(self):
        self._exercise_prepared_working_copy(invalid_f_schema=True)

    def test_runner_lock_contention_does_not_overwrite_execution_config(self):
        self._exercise_prepared_working_copy(lock_held=True)

    def test_bootstrap_holds_run_lock_through_runner_start(self):
        self._exercise_prepared_working_copy(bootstrap_entrypoint=True)

    def test_wall_clock_deadline_kills_the_mocked_local_sender_process(self):
        self._exercise_prepared_working_copy(hung_generation=True, wall_timeout_seconds=0.03)

    def test_deadline_pending_spawn_is_marked_pending_then_killed_before_request_write(self):
        self._exercise_prepared_working_copy(
            pending_generation_spawn=True,
            wall_timeout_seconds=0.2,
        )

    def test_optimized_mode_rejects_input_frame_hash_mismatch(self):
        self._exercise_prepared_working_copy(invalid_frame_hash=True)

    def test_optimized_mode_rejects_runtime_commit_mismatch(self):
        self._exercise_prepared_working_copy(runtime_commit="synthetic-wrong-commit")

    def test_optimized_mode_rejects_incomplete_finish_reason(self):
        self._exercise_prepared_working_copy(finish_reason="length")

    def test_optimized_mode_rejects_backend_truncation(self):
        self._exercise_prepared_working_copy(response_truncated=True)

    def test_prepared_working_copy_sends_f_then_hf_through_mock_only(self):
        self._exercise_prepared_working_copy()


if __name__ == "__main__":
    unittest.main()
