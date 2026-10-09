from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

import cv2
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def knowledge(task_id):
    return {"schema_version": "1.0", "task_id": task_id,
            "steps": [{"id": "assemble", "title": "组装", "prerequisites": [],
                       "completion_cues": ["parts connected"], "negative_cues": [],
                       "required_observations": ["connection"], "source_refs": ["fixture"]}],
            "risk_rules": [{"id": "fixture-risk", "category": "procedure", "applicable_stages": ["*"],
                            "trigger_conditions": ["fixture relation missing"], "exceptions": [],
                            "required_observations": ["connection"], "severity": "fixture-only",
                            "severity_source": "fixture", "source_refs": ["fixture"]}],
            "deviation_types": [], "source_refs": ["fixture"],
            "status": "SYNTHETIC_SOFTWARE_TEST_ONLY"}


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "input.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12, (128, 96))
    assert writer.isOpened()
    for index in range(12):
        writer.write(np.full((96, 128, 3), index * 15, np.uint8))
    writer.release()
    return path


@pytest.fixture
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = payload["messages"][1]["content"][0]["text"]
            context = json.loads(text[text.index("{"):])
            inference = {
                "schema_version": "1.0", "task_id": context["task_id"],
                "window_id": context["window_id"], "estimated_stage": None,
                "completion_state": "unknown", "deviations": [],
                "risk_assessments": [{"rule_id": rule["id"], "status": "unknown", "evidence_refs": [],
                                      "missing_evidence": rule["required_observations"]} for rule in context["risk_rules"]],
                "evidence_refs": [], "conflicting_evidence_refs": [],
                "brief_rationale": "合成接口测试，证据不足", "authority": "state_estimation_only"}
            body = json.dumps({"choices": [{"message": {"content": json.dumps(inference)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_cli(tmp_path, video, task_id, endpoint):
    pack = tmp_path / f"{task_id}.json"
    pack.write_text(json.dumps(knowledge(task_id)), encoding="utf-8")
    output = tmp_path / task_id
    command = [sys.executable, str(ROOT / "scripts/run_vlm_state_risk.py"),
               "--knowledge", str(pack), "--video", str(video), "--model-hash", "a" * 64,
               "--config-hash", "b" * 64, "--output-dir", str(output),
               "--profile", "development", "--sample-every", "3", "--endpoint", endpoint]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, cwd=ROOT)
    return result, output, command


def test_cli_two_software_tasks_complete_http_and_keep_source_hash(tmp_path, video, endpoint):
    import hashlib
    original_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    for task in ("task-a", "task-b"):
        result, output, command = run_cli(tmp_path, video, task, endpoint)
        assert result.returncode == 0, result.stdout + result.stderr
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["terminal"]["frames_decoded"] == 12
        assert manifest["terminal"]["decisions"] == 4
        assert manifest["terminal"]["source_sha256"] == original_hash
        rows = [json.loads(line) for line in (output / "estimates.jsonl").read_text(encoding="utf-8").splitlines()]
        assert all(row["status"] == "ok" for row in rows)
        assert all(row["available_time"] >= row["decision_time"] for row in rows)
        before = (output / "manifest.json").read_bytes()
        rejected = subprocess.run(command, capture_output=True, text=True, timeout=30, cwd=ROOT)
        assert rejected.returncode == 2
        assert (output / "manifest.json").read_bytes() == before
    assert hashlib.sha256(video.read_bytes()).hexdigest() == original_hash


def test_cli_connection_failure_keeps_every_opportunity_and_terminal(tmp_path, video):
    result, output, _ = run_cli(tmp_path, video, "task-a", "http://127.0.0.1:1")
    assert result.returncode == 1
    terminal = json.loads((output / "manifest.json").read_text(encoding="utf-8"))["terminal"]
    assert terminal["decisions"] == terminal["failures"] == 4
    assert terminal["status"] == "completed_with_failures"
    assert len((output / "estimates.jsonl").read_text(encoding="utf-8").splitlines()) == 4


def test_bad_perception_keeps_registered_future_opportunities(tmp_path, video, endpoint):
    import hashlib
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps(knowledge("task-a")), encoding="utf-8")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"source_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                                "frames": {"3": {"unsupported": True}}}), encoding="utf-8")
    output = tmp_path / "bad-cache-run"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_vlm_state_risk.py"),
        "--knowledge", str(pack), "--video", str(video), "--model-hash", "a" * 64,
        "--config-hash", "b" * 64, "--output-dir", str(output), "--profile", "development",
        "--sample-every", "3", "--endpoint", endpoint, "--perception-cache", str(cache)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    rows = [json.loads(line) for line in (output / "estimates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4
    assert [row["status"] for row in rows] == ["ok", "failed", "failed", "failed"]


def test_invalid_hash_rejects_without_poisoning_output(tmp_path, video, endpoint):
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps(knowledge("task-a")), encoding="utf-8")
    output = tmp_path / "rejected"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_vlm_state_risk.py"),
        "--knowledge", str(pack), "--video", str(video), "--model-hash", "invalid",
        "--config-hash", "b" * 64, "--output-dir", str(output), "--profile", "development"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert not output.exists()


def test_renderer_rejects_another_source_before_rendering(tmp_path, video, endpoint):
    import importlib.util
    result, output, _ = run_cli(tmp_path, video, "task-a", endpoint)
    assert result.returncode == 0, result.stdout + result.stderr
    spec = importlib.util.spec_from_file_location("state_renderer", ROOT / "scripts/render_vlm_state_risk_zh.py")
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    other = tmp_path / "other.mp4"
    other.write_bytes(video.read_bytes() + b"different-source")
    with pytest.raises(ValueError, match="source integrity"):
        renderer.render(other, output / "estimates.jsonl", tmp_path / "render.mp4", tmp_path / "font.ttf")


def test_panel_rejects_changed_perception_for_later_paired_cell(tmp_path, video, monkeypatch):
    import importlib.util
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location("panel", ROOT / "scripts/run_vlm_state_risk_panel.py")
    panel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(panel)
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps(knowledge("task-a")), encoding="utf-8")
    cache = tmp_path / "cache.json"
    cache.write_text("{}", encoding="utf-8")
    config = {"profile": "development", "formal_admission": False,
              "methods": ["B0-fixed-streak-v1", "P-structured-temporal-v1"],
              "budget": {"sample_every": 3, "window_frames": 3, "max_attempts": 1,
                         "call_timeout_seconds": 5, "job_timeout_seconds": 20,
                         "max_backlog_seconds": 60},
              "jobs": [{"job_id": "job-a", "knowledge": str(pack), "video": str(video),
                        "model_hash": "a" * 64, "perception_cache": str(cache)}]}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        cache.write_text('{"changed":true}', encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(panel.subprocess, "run", fake_run)
    report = panel.run_panel(path, tmp_path / "panel")
    assert len(calls) == 1
    assert report["registered"] == report["terminal"] == 2
    assert report["results"][1]["status"] == "failed"
    assert "perception cache changed" in report["results"][1]["error"]
