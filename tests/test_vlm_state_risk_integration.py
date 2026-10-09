from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
from urllib.error import URLError

import pytest

from display_project.vlm_sop.state_memory import StateMemory
from display_project.vlm_sop.state_risk_engine import QwenCallError, QwenHTTPAdapter, StateRiskEngine
from display_project.vlm_sop.state_risk_prompt import (
    METHOD_B1,
    METHOD_B2,
    METHOD_P,
    METHOD_P_NO_FACT_SPLIT,
    METHOD_P_NO_PERCEPTION,
    build_state_risk_request,
)
from tests.test_vlm_state_risk_contract import _inference, _knowledge


IMAGE_BYTES = b"synthetic-image-bytes"
IMAGE_URL = "data:image/jpeg;base64,c3ludGhldGljLWltYWdlLWJ5dGVz"
H = "a" * 64
IMAGE_HASH = hashlib.sha256(IMAGE_BYTES).hexdigest()


def _window(window_id: str = "window-1", decision_time: float = 2.0):
    return {
        "schema_version": "1.0",
        "task_id": "task-a",
        "window_id": window_id,
        "decision_time": decision_time,
        "frame_refs": [
            {
                "id": "frame-1",
                "time": decision_time,
                "image_sha256": IMAGE_HASH,
                "image_verified": True,
                "cache_ref": "image-1",
            }
        ],
        "object_track_refs": [{"id": "track-1", "time": decision_time, "class": "component"}],
        "relation_events": [{"id": "relation-1", "time": decision_time, "relation": "misaligned"}],
        "observability": {"view": "complete", "missing": [], "absence_confirmed": []},
        "hashes": {"source_sha256": H, "model_sha256": "b" * 64, "config_sha256": "c" * 64},
    }


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _http_body(inference=None):
    content = json.dumps(inference or _inference())
    return json.dumps({
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 101, "completion_tokens": 37, "total_tokens": 138},
    }).encode()


def test_real_http_payload_roundtrip_updates_only_estimated_memory(tmp_path: Path):
    captured = {}

    def urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data)
        return _Response(_http_body())

    adapter = QwenHTTPAdapter("http://qwen.test/v1/chat/completions", urlopen=urlopen)
    memory = StateMemory("task-a")
    result = StateRiskEngine(_knowledge(), adapter, tmp_path).infer(
        _window(), state_memory=memory, image_urls=[IMAGE_URL]
    )

    assert result["authority"] == "state_estimation_only"
    assert captured["url"] == "http://qwen.test/v1/chat/completions"
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert memory.current_estimate["model_sha256"] == "b" * 64
    assert memory.current_estimate["inference"]["decision_time"] == 2.0
    assert "execution_state" not in memory.current_estimate["inference"]
    assert list((tmp_path / "raw-responses").glob("*.json"))
    call = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert call["request_bytes"] == len(json.dumps(captured["payload"], ensure_ascii=False).encode("utf-8"))
    assert call["prompt_bytes"] > 0
    assert call["image_count"] == 1
    assert call["method_id"] == METHOD_P
    assert (call["prompt_tokens"], call["completion_tokens"], call["total_tokens"]) == (101, 37, 138)


def test_validate_first_prevents_model_call_and_all_writes(tmp_path: Path):
    calls = []

    class Adapter:
        def infer(self, *_args, **_kwargs):
            calls.append(True)

    invalid = _window()
    invalid["truth"] = "install"
    with pytest.raises(ValueError):
        StateRiskEngine(_knowledge(), Adapter(), tmp_path).infer(invalid)
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_raw_malformed_response_is_preserved_and_each_retry_is_ledged(tmp_path: Path):
    replies = iter([_Response(b"not-json"), URLError("offline")])

    def urlopen(_request, *, timeout):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    adapter = QwenHTTPAdapter("http://qwen.test", max_attempts=2, urlopen=urlopen)
    with pytest.raises(QwenCallError):
        adapter.infer({"messages": [], "response_format": {}}, ledger_dir=tmp_path, request_id="request-1")
    assert (tmp_path / "raw-responses/request-1-attempt-1.json").read_bytes() == b"not-json"
    failures = [json.loads(line) for line in (tmp_path / "failures.jsonl").read_text().splitlines()]
    assert [row["attempt"] for row in failures] == [1, 2]


def test_failed_new_window_does_not_leave_stale_current_estimate(tmp_path: Path):
    class Adapter:
        def infer(self, *_args, **_kwargs):
            raise TimeoutError("late")

    memory = StateMemory("task-a")
    prior = _inference()
    prior["decision_time"] = 1.0
    memory.record_window(_window("window-1", 1.0))
    memory.append_estimate(prior, model_sha256="b" * 64)
    with pytest.raises(TimeoutError):
        StateRiskEngine(_knowledge(), Adapter(), tmp_path).infer(
            _window("window-2", 3.0), state_memory=memory, image_urls=[IMAGE_URL]
        )
    assert memory.estimate_history[-1]["kind"] == "failure"
    assert memory.current_estimate is None
    terminal = json.loads((tmp_path / "terminal.jsonl").read_text().splitlines()[-1])
    assert terminal["status"] == "failed"


def test_prompt_methods_keep_fact_hypothesis_separation_and_ablation_boundaries():
    memory = StateMemory("task-a")
    prior_window = _window("window-prior", 1.0)
    memory.record_window(prior_window)
    prior = _inference()
    prior["window_id"] = "window-prior"
    prior["decision_time"] = 1.0
    memory.append_estimate(prior, model_sha256="b" * 64)

    contexts = {}
    for method in (METHOD_B1, METHOD_B2, METHOD_P, METHOD_P_NO_FACT_SPLIT):
        request = build_state_risk_request(
            _knowledge(), _window(decision_time=2.0), method_id=method,
            state_memory=memory, image_urls=[IMAGE_URL],
        )
        text = request["messages"][1]["content"][0]["text"]
        contexts[method] = json.loads(text.split("\n", 1)[1])

    assert "evidence_window" not in contexts[METHOD_B1]
    assert "historical_model_hypotheses" not in contexts[METHOD_B2]
    assert contexts[METHOD_P]["historical_observation_facts"][0]["frame_refs"][0]["seconds_before_decision"] == 1.0
    assert contexts[METHOD_P]["historical_model_hypotheses"][0]["estimated_stage"] == "install"
    assert "window_id" not in contexts[METHOD_P]["historical_model_hypotheses"][0]
    assert "hashes" not in contexts[METHOD_B2]["evidence_window"]
    assert contexts[METHOD_B1]["simple_online_history"]
    assert "unseparated_history" in contexts[METHOD_P_NO_FACT_SPLIT]
    assert "historical_observation_facts" not in contexts[METHOD_P_NO_FACT_SPLIT]


def test_prompt_rejects_non_embedded_image_reference():
    with pytest.raises(ValueError, match="staged data:image"):
        build_state_risk_request(_knowledge(), _window(), image_urls=["file:///truth/labels.json"])


def test_b1_and_no_perception_ablation_exclude_structured_perception():
    memory = StateMemory("task-a")
    for index in range(6):
        memory.record_window(_window(f"prior-{index}", float(index + 1)))
        prior = _inference()
        prior.update(window_id=f"prior-{index}", decision_time=float(index + 1))
        memory.append_estimate(prior, model_sha256="b" * 64)
    current = _window("current", 10.0)

    contexts = {}
    for method in (METHOD_B1, METHOD_B2, METHOD_P, METHOD_P_NO_PERCEPTION):
        request = build_state_risk_request(
            _knowledge(), current, method_id=method, state_memory=memory,
            image_urls=[IMAGE_URL],
        )
        contexts[method] = json.loads(request["messages"][1]["content"][0]["text"].split("\n", 1)[1])

    assert contexts[METHOD_B1]["evidence_reference_ids"] == ["frame-1"]
    assert contexts[METHOD_P_NO_PERCEPTION]["evidence_reference_ids"] == ["frame-1"]
    for method in (METHOD_B1, METHOD_P_NO_PERCEPTION):
        encoded = json.dumps(contexts[method].get("simple_online_history", contexts[method].get("historical_observation_facts")))
        assert "object_track_refs" not in encoded
        assert "relation_events" not in encoded
        assert "track-1" not in encoded
        assert "relation-1" not in encoded
    assert contexts[METHOD_B2]["evidence_window"]["object_track_refs"]
    assert contexts[METHOD_P]["observed_facts"]["relation_events"]
    assert all(contexts[method]["history_limit"] == 4 for method in contexts)
    assert all(contexts[method]["history_observation_count"] == 4 for method in contexts)


def test_image_hash_mismatch_is_rejected_before_network_or_ledger_write(tmp_path: Path):
    calls = []

    class Adapter:
        def infer(self, *_args, **_kwargs):
            calls.append(True)

    with pytest.raises(ValueError, match="does not match frame_ref"):
        StateRiskEngine(_knowledge(), Adapter(), tmp_path).infer(
            _window(), image_urls=["data:image/jpeg;base64,dGFtcGVyZWQ="]
        )
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_adapter_rejects_request_id_replay_before_network(tmp_path: Path):
    calls = []

    def urlopen(_request, *, timeout):
        calls.append(timeout)
        return _Response(_http_body())

    adapter = QwenHTTPAdapter("http://qwen.test", urlopen=urlopen)
    request = {"method_id": METHOD_P, "messages": [], "response_format": {}}
    adapter.infer(request, ledger_dir=tmp_path, request_id="opaque-request")
    with pytest.raises(ValueError, match="already exist"):
        adapter.infer(request, ledger_dir=tmp_path, request_id="opaque-request")
    assert len(calls) == 1


def test_failure_history_is_bounded_relative_and_has_no_window_identifier():
    memory = StateMemory("task-a")
    for index in range(6):
        window = _window(f"failed-{index}", float(index + 1))
        memory.record_window(window)
        memory.record_failure(window["window_id"], decision_time=window["decision_time"], reason="timeout")
    request = build_state_risk_request(
        _knowledge(), _window("current", 10.0), method_id=METHOD_P,
        state_memory=memory, image_urls=[IMAGE_URL],
    )
    context = json.loads(request["messages"][1]["content"][0]["text"].split("\n", 1)[1])
    failures = context["historical_missing_estimates"]
    assert len(failures) == 4
    assert failures[0] == {"status": "missing", "seconds_before_decision": 7.0}
    assert all("window_id" not in failure and "decision_time" not in failure for failure in failures)
