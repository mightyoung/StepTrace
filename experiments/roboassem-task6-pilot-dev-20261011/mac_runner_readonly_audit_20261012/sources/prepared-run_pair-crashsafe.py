"""Prepared Task 6 F/HF sender with durable one-shot request accounting.

This file is an unfrozen preparation artifact. It must not be executed until
the input/backend/configuration gates are independently reviewed. Tests use
synthetic requests and a mock sender only.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

from jsonschema import Draft202012Validator
from PIL import Image


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from runner_safety import (  # noqa: E402
    CANCELLATION_RESERVE_SECONDS,
    PriorAttemptExists,
    REQUEST_TIMEOUT_SECONDS,
    SenderCancellationResult,
    acquire_run_guard,
    count_send_intents,
    execute_pair,
    resolve_input_root,
    sender_termination_confirmed,
)


P = resolve_input_root(script_path=__file__)
R = Path(os.environ.get("TASK6_RUN_ROOT", "/kaggle/working/steptrace-task6")).resolve()
O = R / "results"
O.mkdir(parents=True, exist_ok=True)
if (O / "attempts.jsonl").exists():
    raise PriorAttemptExists(
        "a prior send-intent journal exists; delivery may be unknown, do not retry"
    )

C = json.loads((P / "execution-input-contract.json").read_text(encoding="utf-8"))
V = json.loads((R / "runtime-verification.json").read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


require(
    V.get("commit") == "8345f333951c661d166b00e6f9362e553768f292",
    "runtime commit does not match the pinned prepared runner",
)

inputs = {}
for frame in C["model_input_frames"]:
    image_bytes = (P / frame["filename"]).read_bytes()
    require(len(image_bytes) == frame["bytes"], f"input byte length mismatch: {frame['filename']}")
    require(
        hashlib.sha256(image_bytes).hexdigest() == frame["sha256"],
        f"input SHA256 mismatch: {frame['filename']}",
    )
    with Image.open(io.BytesIO(image_bytes)) as image:
        require(image.size == (640, 480), f"input frame dimensions invalid: {frame['filename']}")
    inputs[frame["input_id"]] = image_bytes

require(
    [request["input_ids"] for request in C["requests"]]
    == [["k04"], ["k01", "k02", "k03", "k04"]],
    "prepared F/HF input frame order does not match the pinned contract",
)

requests = []
schema_enforcement = {}
for spec in C["requests"]:
    arm = spec["condition"]
    schema = C["content_schemas"][arm]
    Draft202012Validator.check_schema(schema)
    generation_schema = copy.deepcopy(schema)
    omitted_keywords = []

    def remove_unsupported_keywords(value, path=""):
        if isinstance(value, dict):
            for key in list(value):
                if key in ("contains", "minContains", "maxContains", "uniqueItems", "$schema"):
                    omitted_keywords.append(path + "/" + key)
                    del value[key]
                else:
                    remove_unsupported_keywords(value[key], path + "/" + key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                remove_unsupported_keywords(item, path + "/" + str(index))

    remove_unsupported_keywords(generation_schema)
    schema_enforcement[arm] = {
        "generation_omitted_keywords": omitted_keywords,
        "full_schema_offline_validation_required": True,
    }

    content = []
    for part in spec["exact_user_content_sequence"]:
        if part["type"] == "text":
            content.append(part)
        else:
            image_url = "data:image/png;base64," + base64.b64encode(
                inputs[part["input_id"]]
            ).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": image_url}})

    body = copy.deepcopy(C["generation_parameters_exact"])
    body.update(top_p=0.95, top_k=40, cache_prompt=False)
    body["messages"] = [
        {"role": "system", "content": C["system_prompt_exact"]},
        {"role": "user", "content": content},
    ]
    body["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "roboassem_task6_visual_state",
            "strict": True,
            "schema": generation_schema,
        },
    }
    requests.append((arm, body))

require(tuple(arm for arm, _body in requests) == ("F", "HF"), "request order must be F then HF")
require(requests[0][1] is not requests[1][1], "F and HF requests must be independent objects")
require(
    requests[0][1]["messages"][1]["content"][-1]
    == requests[1][1]["messages"][1]["content"][-1],
    "F and HF must share the same current image bytes",
)
request_parameters = [
    {key: value for key, value in body.items() if key not in ("messages", "response_format")}
    for _arm, body in requests
]
require(request_parameters[0] == request_parameters[1], "F and HF request parameters differ")

server_command = [
    str(R / "llama.cpp/build/bin/llama-server"),
    "-m",
    str(R / "Qwen3.5-9B-Q4_K_M.gguf"),
    "--mmproj",
    str(R / "mmproj-BF16.gguf"),
    "--no-mmproj-offload",
    "--device",
    "CUDA0",
    "-ngl",
    "99",
    "-c",
    "16384",
    "--parallel",
    "1",
    "--threads",
    "4",
    "--flash-attn",
    "off",
    "--image-min-tokens",
    "1024",
    "--image-max-tokens",
    "2048",
    "--no-context-shift",
    "--no-cache-prompt",
    "--host",
    "127.0.0.1",
    "--port",
    "8080",
    "--top-k",
    "40",
    "--top-p",
    "0.95",
    "--temp",
    "0",
    "--seed",
    "1",
    "--jinja",
]
execution_config = {
    "runtime": V,
    "server_args": server_command,
    "request_parameters": request_parameters[0],
    "schema_enforcement": schema_enforcement,
    "protocol_commit": "a2cdc0b712ccc70d929a3d0e6b00d6b414dbde98",
    "budget": {
        "max_requests": 2,
        "retries": 0,
        "parallel": 1,
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "sender_cancellation_reserve_seconds": CANCELLATION_RESERVE_SECONDS,
    },
    "state_write_allowed": False,
}
def http(path, body=None, timeout=5):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8080" + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=timeout)


stop_monitor = threading.Event()


def monitor_gpu():
    with (O / "gpu-memory.csv").open("w", encoding="utf-8") as stream:
        stream.write("epoch,index,memory_used_MiB,memory_total_MiB,utilization_percent\n")
        while not stop_monitor.is_set():
            try:
                output = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    text=True,
                )
                for line in output.splitlines():
                    stream.write(str(time.time()) + "," + line + "\n")
                stream.flush()
            except Exception as exc:
                stream.write("monitor_error," + type(exc).__name__ + "\n")
                stream.flush()
            stop_monitor.wait(1)


def write_execution_error(error: Exception) -> None:
    (O / "execution-error.json").write_text(
        json.dumps({"error": repr(error), "epoch": time.time()}), encoding="utf-8"
    )


def make_response_parser(results):
    def parse_response(arm, raw_response, attempt):
        response = json.loads(raw_response.decode("utf-8"))
        choice = response["choices"][0]
        content_text = choice["message"]["content"]
        parsed = json.loads(content_text)
        errors = [
            error.message
            for error in Draft202012Validator(C["content_schemas"][arm]).iter_errors(parsed)
        ]
        seats = {
            relation.get("seat_id")
            for relation in parsed.get("current_relations", [])
        }
        if seats != {"blue_seat_left", "blue_seat_right"}:
            errors.append("Require exactly one relation for each seat")

        record = dict(attempt)
        record.update(
            response_sha256=hashlib.sha256(raw_response).hexdigest(),
            response_bytes=len(raw_response),
            finish_reason=choice["finish_reason"],
            usage=response.get("usage"),
            timings=response.get("timings"),
            schema_valid=not errors,
            schema_errors=errors,
            content=parsed,
            truncated=response.get("truncated", "inspect_server_log"),
        )
        require(choice.get("finish_reason") == "stop", "First answer incomplete; no retry")
        prompt_tokens = response.get("usage", {}).get("prompt_tokens")
        require(
            isinstance(prompt_tokens, int) and prompt_tokens + 2048 <= 16384,
            "Prompt token/no truncation check failed",
        )
        require(not response.get("truncated", False), "Backend reports truncation")
        if errors:
            raise ValueError(
                "Response failed offline schema validation: " + "; ".join(errors)
            )
        results.append(record)
        (O / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(arm, json.dumps(record, ensure_ascii=False), flush=True)
        return record

    return parse_response


guard = acquire_run_guard(O)
server = None
monitor_thread = None
server_log = None
monitor_started = False
try:
    # All result artifacts, including execution-config.json, are written only
    # after the shared one-shot lock is held.
    (O / "execution-config.json").write_text(
        json.dumps(execution_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    server_log = (O / "server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        server_command, stdout=server_log, stderr=subprocess.STDOUT
    )
    monitor_thread = threading.Thread(target=monitor_gpu, name="task6-gpu-monitor")
    monitor_thread.start()
    monitor_started = True
    results = []

    try:
        started = time.monotonic()
        while True:
            if server.poll() is not None:
                raise RuntimeError("Server exited while loading; inspect server.log")
            try:
                with http("/health") as response:
                    if response.status == 200:
                        break
            except Exception:
                pass
            if time.monotonic() - started > 240:
                raise TimeoutError("Loading exceeded 240 seconds; stopping")
            time.sleep(2)

        with http("/props") as response:
            props_raw = response.read()
        (O / "backend-props.json").write_bytes(props_raw)
        props = json.loads(props_raw)
        contexts = []

        def collect_context_values(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("n_ctx", "n_ctx_per_seq") and isinstance(item, int):
                        contexts.append(item)
                    collect_context_values(item)
            elif isinstance(value, list):
                for item in value:
                    collect_context_values(item)

        collect_context_values(props)
        require(
            bool(contexts) and min(contexts) >= 16384,
            f"Effective backend context unverified or too small: {contexts}",
        )
        execution_config["backend_context_values"] = contexts
        execution_config["backend_schema_support"] = (
            "Generation subset only; original contains and uniqueness strictly checked offline"
        )
        (O / "execution-config.json").write_text(
            json.dumps(execution_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Non-generating text-token preflight; no F/HF completion is sent here.
        for arm, body in requests:
            text_body = copy.deepcopy(body)
            text_body["messages"][1]["content"] = [
                block
                for block in text_body["messages"][1]["content"]
                if block["type"] == "text"
            ]
            with http(
                "/apply-template",
                {
                    "messages": text_body["messages"],
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            ) as response:
                template = json.loads(response.read())
            prompt = template.get("prompt", template.get("content"))
            require(isinstance(prompt, str), f"template response has no prompt string: {template!r}")
            with http("/tokenize", {"content": prompt, "add_special": True}) as response:
                token_count = len(json.loads(response.read())["tokens"])
            request_spec = next(item for item in C["requests"] if item["condition"] == arm)
            bound = token_count + len(request_spec["input_ids"]) * 2048 + 2048 + 128
            require(bound <= 16384, f"{arm} conservative context bound exceeds 16384: {bound}")
            (O / f"preflight-{arm}.json").write_text(
                json.dumps(
                    {
                        "text_tokens": token_count,
                        "conservative_context_bound": bound,
                        "context": 16384,
                        "image_max_tokens_each": 2048,
                        "generation_calls": 0,
                    }
                ),
                encoding="utf-8",
            )
        print("PAIR_PREFLIGHT_PASSED_NO_GENERATION_CALLS", flush=True)

        generation_worker = r'''import base64
import json
import sys
import urllib.error
import urllib.request

timeout = float(sys.argv[1])
raw_request = sys.stdin.buffer.read()
if not raw_request.strip():
    raise SystemExit("generation request stdin is empty")
try:
    parsed_request = json.loads(raw_request)
except (UnicodeDecodeError, json.JSONDecodeError) as error:
    raise SystemExit("generation request stdin is not valid JSON") from error
if (
    not isinstance(parsed_request, dict)
    or not isinstance(parsed_request.get("messages"), list)
    or not parsed_request["messages"]
):
    raise SystemExit("generation request stdin is not a chat completion request")
request = urllib.request.Request(
    "http://127.0.0.1:8080/v1/chat/completions",
    data=raw_request,
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        envelope = {
            "status": response.status,
            "reason": response.reason,
            "body_b64": base64.b64encode(response.read()).decode("ascii"),
        }
except urllib.error.HTTPError as error:
    envelope = {
        "status": error.code,
        "reason": error.reason,
        "body_b64": base64.b64encode(error.read()).decode("ascii"),
    }
except BaseException as error:
    envelope = {"error_type": type(error).__name__, "error": repr(error)}
sys.stdout.write(json.dumps(envelope))
'''
        generation_process_condition = threading.Condition()
        generation_process_state = {
            "process": None,
            "cancelled": False,
            "phase": "idle",
            "cancel_handler_active": False,
            "termination_confirmed": False,
            "completed": False,
            "deadline_monotonic": None,
        }

        def begin_generation_request(*, deadline_monotonic: float) -> None:
            with generation_process_condition:
                generation_process_state.update(
                    process=None,
                    cancelled=False,
                    phase="idle",
                    cancel_handler_active=False,
                    termination_confirmed=False,
                    completed=False,
                    deadline_monotonic=deadline_monotonic,
                )
                generation_process_condition.notify_all()

        def finish_generation_process(
            process, *, reaped: bool, kill_requested: bool, completed: bool
        ) -> None:
            with generation_process_condition:
                if generation_process_state["process"] is process:
                    generation_process_state["process"] = None
                generation_process_state["phase"] = "done"
                generation_process_state["cancel_handler_active"] = False
                generation_process_state["termination_confirmed"] = sender_termination_confirmed(
                    kill_requested=kill_requested,
                    reaped=reaped,
                )
                generation_process_state["completed"] = completed
                generation_process_condition.notify_all()

        def kill_and_reap_generation_process(process, *, deadline_monotonic=None):
            kill_requested = False
            try:
                if process.poll() is None:
                    process.kill()
                    kill_requested = True
            except ProcessLookupError:
                pass
            except Exception:
                return False, False
            wait_timeout = 5.0 if deadline_monotonic is None else max(
                0.0, deadline_monotonic - time.monotonic()
            )
            try:
                process.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                return False, kill_requested
            return process.poll() is not None, kill_requested

        def send_generation_request(raw_request: bytes, *, timeout_seconds: int) -> bytes:
            with generation_process_condition:
                if generation_process_state["cancelled"]:
                    generation_process_state["phase"] = "done"
                    generation_process_state["completed"] = False
                    generation_process_condition.notify_all()
                    raise TimeoutError("generation sender cancelled before process start")
                generation_process_state["phase"] = "spawning"
                generation_process_condition.notify_all()
            try:
                process = subprocess.Popen(
                    [sys.executable, "-c", generation_worker, str(timeout_seconds)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except BaseException:
                with generation_process_condition:
                    generation_process_state["phase"] = "done"
                    generation_process_state["completed"] = False
                    generation_process_condition.notify_all()
                raise

            terminate_here = False
            with generation_process_condition:
                generation_process_state["process"] = process
                if generation_process_state["cancelled"]:
                    if generation_process_state["cancel_handler_active"]:
                        generation_process_state["phase"] = "cancel_requested"
                    else:
                        generation_process_state["phase"] = "terminating"
                        generation_process_state["cancel_handler_active"] = True
                        terminate_here = True
                else:
                    generation_process_state["phase"] = "communicating"
                generation_process_condition.notify_all()
                cancelled = generation_process_state["cancelled"]

            if cancelled:
                if terminate_here:
                    reaped, kill_requested = kill_and_reap_generation_process(process)
                    finish_generation_process(
                        process,
                        reaped=reaped,
                        kill_requested=kill_requested,
                        completed=reaped and not kill_requested,
                    )
                raise TimeoutError("generation sender cancelled before request bytes were written")

            try:
                stdout, stderr = process.communicate(input=raw_request, timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                with generation_process_condition:
                    already_cancelling = generation_process_state["cancelled"]
                    if not already_cancelling:
                        generation_process_state["cancelled"] = True
                        generation_process_state["phase"] = "terminating"
                        generation_process_state["cancel_handler_active"] = True
                if not already_cancelling:
                    reaped, kill_requested = kill_and_reap_generation_process(
                        process,
                        deadline_monotonic=generation_process_state["deadline_monotonic"],
                    )
                    finish_generation_process(
                        process,
                        reaped=reaped,
                        kill_requested=kill_requested,
                        completed=reaped and not kill_requested,
                    )
                raise TimeoutError("local generation sender process exceeded timeout") from exc
            except BaseException:
                with generation_process_condition:
                    already_cancelling = generation_process_state["cancelled"]
                    if not already_cancelling:
                        generation_process_state["cancelled"] = True
                        generation_process_state["phase"] = "terminating"
                        generation_process_state["cancel_handler_active"] = True
                if not already_cancelling:
                    reaped, kill_requested = kill_and_reap_generation_process(
                        process,
                        deadline_monotonic=generation_process_state["deadline_monotonic"],
                    )
                    finish_generation_process(
                        process,
                        reaped=reaped,
                        kill_requested=kill_requested,
                        completed=reaped and not kill_requested,
                    )
                raise
            else:
                with generation_process_condition:
                    if not generation_process_state["cancelled"]:
                        if generation_process_state["process"] is process:
                            generation_process_state["process"] = None
                        generation_process_state["phase"] = "done"
                        generation_process_state["completed"] = True
                        generation_process_condition.notify_all()
                    else:
                        raise TimeoutError("generation sender was cancelled while communicating")

            if process.returncode != 0:
                raise RuntimeError(
                    f"local generation sender process exited {process.returncode}: "
                    f"{stderr.decode('utf-8', errors='replace')}"
                )
            try:
                envelope = json.loads(stdout.decode("utf-8"))
            except Exception as exc:
                raise RuntimeError("local generation sender returned an invalid envelope") from exc
            if "error_type" in envelope:
                raise RuntimeError(
                    f"local generation sender failed: {envelope['error_type']}: {envelope['error']}"
                )
            response_body = base64.b64decode(envelope["body_b64"], validate=True)
            status = int(envelope["status"])
            if status >= 400:
                raise urllib.error.HTTPError(
                    "http://127.0.0.1:8080/v1/chat/completions",
                    status,
                    str(envelope.get("reason", "HTTP error")),
                    hdrs=None,
                    fp=io.BytesIO(response_body),
                )
            return response_body

        def cancel_generation_request() -> SenderCancellationResult:
            with generation_process_condition:
                phase = generation_process_state["phase"]
                deadline = generation_process_state["deadline_monotonic"]
                process = generation_process_state["process"]
                if phase == "done":
                    if generation_process_state["termination_confirmed"]:
                        return SenderCancellationResult("terminated", confirmed=True)
                    if generation_process_state["completed"]:
                        return SenderCancellationResult("completed")
                    return SenderCancellationResult("not_started")
                if phase == "communicating" and process is not None and process.poll() is not None:
                    return SenderCancellationResult("completed")
                generation_process_state["cancelled"] = True
                if phase == "idle":
                    generation_process_condition.notify_all()
                    return SenderCancellationResult("not_started")
                if phase == "spawning":
                    generation_process_state["cancel_handler_active"] = True
                    remaining = max(0.0, deadline - time.monotonic()) if deadline else 0.0
                    generation_process_condition.wait_for(
                        lambda: generation_process_state["phase"] != "spawning",
                        timeout=remaining,
                    )
                    phase = generation_process_state["phase"]
                    if phase == "spawning":
                        generation_process_state["cancel_handler_active"] = False
                        generation_process_condition.notify_all()
                        return SenderCancellationResult("pending_spawn")
                if phase == "terminating":
                    remaining = max(0.0, deadline - time.monotonic()) if deadline else 0.0
                    generation_process_condition.wait_for(
                        lambda: generation_process_state["phase"] == "done",
                        timeout=remaining,
                    )
                    if generation_process_state["termination_confirmed"]:
                        return SenderCancellationResult("terminated", confirmed=True)
                    return SenderCancellationResult("pending_termination")
                process = generation_process_state["process"]
                if process is None:
                    generation_process_state["cancel_handler_active"] = False
                    generation_process_condition.notify_all()
                    return SenderCancellationResult("pending_spawn")
                generation_process_state["phase"] = "terminating"
                generation_process_state["cancel_handler_active"] = True

            reaped, kill_requested = kill_and_reap_generation_process(
                process,
                deadline_monotonic=deadline,
            )
            finish_generation_process(
                process,
                reaped=reaped,
                kill_requested=kill_requested,
                completed=reaped and not kill_requested,
            )
            if sender_termination_confirmed(kill_requested=kill_requested, reaped=reaped):
                return SenderCancellationResult("terminated", confirmed=True)
            if reaped:
                return SenderCancellationResult("completed")
            return SenderCancellationResult("pending_termination")

        send_generation_request.cancel = cancel_generation_request
        send_generation_request.begin = begin_generation_request

        execute_pair(
            requests,
            output_dir=O,
            send_request=send_generation_request,
            parse_response=make_response_parser(results),
            guard=guard,
        )
    except Exception as exc:
        write_execution_error(exc)
        raise
finally:
    try:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        stop_monitor.set()
        if monitor_thread is not None and monitor_started:
            monitor_thread.join()
        if server_log is not None:
            server_log.close()
        (O / "server-stopped.json").write_text(
            json.dumps(
                {
                    "returncode": server.returncode if server is not None else None,
                    "epoch": time.time(),
                    "send_intents": count_send_intents(O / "attempts.jsonl"),
                    "count_semantics": "durable send intents, not independent server receipt confirmation",
                }
            ),
            encoding="utf-8",
        )
        with zipfile.ZipFile(R / "task6-results.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in O.iterdir():
                if path.is_file():
                    archive.write(path, path.name)
        print(
            "SERVER_STOPPED_SEND_INTENTS",
            count_send_intents(O / "attempts.jsonl"),
            "STOP_KAGGLE_SESSION_IN_UI",
            flush=True,
        )
    finally:
        guard.release()
