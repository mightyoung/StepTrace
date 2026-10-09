"""Run causal, development-only state estimation over a complete video.

Only explicitly selected video, knowledge and optional perception cache are read.
The model receives embedded images and validated payloads, never local file access.
"""
from __future__ import annotations

import argparse
import base64
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from display_project.vlm_sop.evidence_window import build_evidence_window
from display_project.vlm_sop.state_risk_knowledge import load_knowledge


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def append_record(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--knowledge", type=Path, required=True)
    result.add_argument("--video", type=Path, required=True)
    result.add_argument("--model-hash", required=True)
    result.add_argument("--config-hash", required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--profile", choices=["development"], required=True)
    result.add_argument("--endpoint")
    result.add_argument("--model-name", default="Qwen3.5-9B-Q4_K_M")
    result.add_argument("--method-id", default="P-structured-temporal-v1")
    result.add_argument("--sample-every", type=int, default=12)
    result.add_argument("--window-frames", type=int, default=3)
    result.add_argument("--timeout-seconds", type=float, default=60)
    result.add_argument("--max-attempts", type=int, default=1)
    result.add_argument("--max-backlog-seconds", type=float, default=60)
    result.add_argument("--perception-cache", type=Path,
                        help="Explicit development JSON cache bound to source hash")
    return result


def run(args: argparse.Namespace) -> dict:
    import cv2

    if args.sample_every < 1 or not 1 <= args.window_frames <= 8:
        raise ValueError("sample interval must be positive; window frames must be 1..8")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise ValueError("timeout must be finite and positive")
    if not 1 <= args.max_attempts <= 3:
        raise ValueError("max attempts must be 1..3")
    if not math.isfinite(args.max_backlog_seconds) or args.max_backlog_seconds <= 0:
        raise ValueError("backlog budget must be finite and positive")
    for value in (args.model_hash, args.config_hash):
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("model/config hashes must be lowercase SHA256")
    knowledge = load_knowledge(args.knowledge)
    video = args.video.resolve(strict=True)
    source_hash = sha256(video)
    knowledge_hash = sha256(args.knowledge)
    perception_hash = sha256(args.perception_cache) if args.perception_cache else None
    cache = {}
    if args.perception_cache:
        from display_project.vlm_sop.knowledge import reject_truth_material
        cache = json.loads(args.perception_cache.read_text(encoding="utf-8"))
        reject_truth_material(cache)
        if set(cache) != {"source_sha256", "frames"} or cache["source_sha256"] != source_hash:
            raise ValueError("perception cache must bind exact source video")
        if not isinstance(cache["frames"], dict):
            raise ValueError("perception cache frames must be an object")
        cache = cache["frames"]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output directory must be empty; previous runs are immutable")
    frame_dir = output / "frames"
    frame_dir.mkdir()
    configuration = {
        "method_id": args.method_id, "sample_every": args.sample_every,
        "window_frames": args.window_frames, "timeout_seconds": args.timeout_seconds,
        "max_attempts": args.max_attempts, "knowledge_sha256": knowledge_hash,
        "max_backlog_seconds": args.max_backlog_seconds,
        "declared_config_sha256": args.config_hash,
        "perception_sha256": perception_hash,
        "model_name": args.model_name,
        "model_sha256": args.model_hash,
        "model_identity_verification": "DECLARED_NOT_ENDPOINT_ATTESTED",
    }
    config_bytes = json.dumps(configuration, sort_keys=True).encode()
    (output / "configuration.json").write_bytes(config_bytes + b"\n")
    (output / "knowledge.json").write_text(json.dumps(knowledge, ensure_ascii=False, indent=2)
                                            + "\n", encoding="utf-8")
    hashes = {"source_sha256": source_hash, "model_sha256": args.model_hash,
              "config_sha256": hashlib.sha256(config_bytes).hexdigest()}
    terminal = {"schema_version": "1.0", "profile": "DEVELOPMENT_ONLY",
                "method_id": args.method_id, "source_sha256": source_hash,
                "model_sha256": args.model_hash,
                "status": "registered", "frames_decoded": 0,
                "decisions": 0, "failures": 0, "formal_admission": False}
    append_record(output / "run-ledger.jsonl", terminal)
    capture = None
    started = time.perf_counter()
    cpu_started = time.process_time()
    latencies = []
    opportunities = []
    written_windows = set()
    try:
        capture = cv2.VideoCapture(str(video))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        expected = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or expected <= 0:
            raise ValueError("video metadata is unavailable")
        terminal.update(fps=fps, expected_frames=expected)
        for frame_index in range(0, expected, args.sample_every):
            clock = frame_index / fps
            window_key = hashlib.sha256(f"{source_hash}:{clock}:{hashes['config_sha256']}".encode()).hexdigest()[:20]
            opportunity = {"window_id": f"window-{window_key}", "decision_time": clock,
                           "source_sha256": source_hash, "status": "registered"}
            opportunities.append(opportunity)
            append_record(output / "opportunities.jsonl", opportunity)
        if args.method_id == "B0-fixed-streak-v1":
            from display_project.vlm_sop.state_risk_baseline import StateRiskBaseline
            engine = StateRiskBaseline(knowledge)
            memory = None
        else:
            if not args.endpoint:
                raise ValueError("VLM methods require --endpoint")
            from display_project.vlm_sop.state_memory import StateMemory
            from display_project.vlm_sop.state_risk_engine import QwenHTTPAdapter, StateRiskEngine
            memory = StateMemory(knowledge["task_id"])
            adapter = QwenHTTPAdapter(args.endpoint, model=args.model_name,
                                      timeout_seconds=args.timeout_seconds,
                                      max_attempts=args.max_attempts)
            engine = StateRiskEngine(knowledge, adapter, output / "model")
        history = deque(maxlen=args.window_frames)
        perception_history = deque(maxlen=args.window_frames)
        index = 0
        previous_available_time = 0.0
        while True:
            decode_started = time.perf_counter()
            ok, frame = capture.read()
            if not ok:
                break
            terminal["frames_decoded"] += 1
            if index % args.sample_every:
                index += 1
                continue
            decision_time = index / fps
            ordinal = terminal["decisions"]
            image_path = frame_dir / f"image-{ordinal:06d}.jpg"
            if not cv2.imwrite(str(image_path), frame):
                raise OSError("cannot stage image")
            image_hash = sha256(image_path)
            opaque_id = hashlib.sha256(f"{image_hash}:{decision_time}:{hashes['config_sha256']}".encode()).hexdigest()[:20]
            frame_ref = {"id": f"frame-{opaque_id}", "time": decision_time,
                         "image_sha256": image_hash, "image_verified": True,
                         "cache_ref": f"image-{ordinal:06d}"}
            history.append((frame_ref, image_path))
            perception = cache.get(str(index), {})
            if not isinstance(perception, dict) or set(perception) - {
                "object_track_refs", "relation_events", "observability"
            }:
                raise ValueError("unsupported perception frame payload")
            perception_history.append(perception)
            window = build_evidence_window(
                task_id=knowledge["task_id"], window_id=opportunities[ordinal]["window_id"],
                decision_time=decision_time, frame_refs=[item[0] for item in history],
                object_track_refs=[ref for item in perception_history for ref in item.get("object_track_refs", [])],
                relation_events=[ref for item in perception_history for ref in item.get("relation_events", [])],
                observability=perception.get("observability", {
                    "view": "partial", "missing": ["detector_summary_unavailable"],
                    "absence_confirmed": []}), hashes=hashes)
            append_record(output / "evidence-windows.jsonl", window)
            decode_s = time.perf_counter() - decode_started
            inference_started = time.perf_counter()
            row = {"window_id": window["window_id"], "decision_time": decision_time,
                   "status": "failed", "inference": None, "source_sha256": source_hash}
            queue_seconds = max(0.0, previous_available_time - decision_time)
            try:
                if queue_seconds > args.max_backlog_seconds:
                    raise TimeoutError("inference backlog exceeds development budget")
                if args.method_id == "B0-fixed-streak-v1":
                    inference = engine.infer(window)
                else:
                    urls = ["data:image/jpeg;base64," + base64.b64encode(
                        path.read_bytes()).decode("ascii") for _, path in history]
                    inference = engine.infer(window, method_id=args.method_id,
                                             state_memory=memory, image_urls=urls)
                row.update(status="ok", inference=inference)
            except Exception as error:
                row.update(error_type=type(error).__name__, error=str(error))
                terminal["failures"] += 1
            elapsed = time.perf_counter() - inference_started
            latencies.append(elapsed)
            row.update(inference_seconds=elapsed, decode_seconds=decode_s,
                       queue_seconds=queue_seconds,
                       available_time=decision_time + queue_seconds + decode_s + elapsed)
            previous_available_time = row["available_time"]
            append_record(output / "estimates.jsonl", row)
            written_windows.add(window["window_id"])
            if memory is not None:
                (output / "state-history.json").write_text(json.dumps({
                    "observations": [dict(item) for item in memory.observation_history],
                    "revisions": [dict(item) for item in memory.estimate_history],
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            terminal["decisions"] += 1
            index += 1
        if terminal["frames_decoded"] != expected:
            raise RuntimeError("incomplete video decode; declared full job was not completed")
        terminal["status"] = "completed_with_failures" if terminal["failures"] else "completed"
    except Exception as error:
        terminal.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        if capture is not None:
            capture.release()
        for opportunity in opportunities:
            if opportunity["window_id"] not in written_windows:
                append_record(output / "estimates.jsonl", dict(opportunity, status="failed",
                    available_time=opportunity["decision_time"], inference=None,
                    error_type="RunUnavailable", error=terminal.get("error", "run terminated")))
                terminal["decisions"] += 1
                terminal["failures"] += 1
        try:
            stable = sha256(video) == source_hash and sha256(args.knowledge) == knowledge_hash
            if args.perception_cache:
                stable = stable and sha256(args.perception_cache) == perception_hash
        except OSError:
            stable = False
        terminal["input_integrity_verified"] = stable
        if not stable:
            terminal.update(status="failed", error_type="InputChanged", error="input changed during run")
        terminal["elapsed_seconds"] = time.perf_counter() - started
        terminal["cpu_seconds"] = time.process_time() - cpu_started
        terminal["decisions_per_second"] = terminal["decisions"] / max(terminal["elapsed_seconds"], 1e-9)
        ordered = sorted(latencies)
        terminal["call_latency_p50_seconds"] = ordered[min(len(ordered) - 1, int(len(ordered) * .5))] if ordered else None
        terminal["call_latency_p95_seconds"] = ordered[min(len(ordered) - 1, int(len(ordered) * .95))] if ordered else None
        terminal["gpu_resource_measurement"] = "NOT_MEASURED"
        append_record(output / "run-ledger.jsonl", terminal)
        artifacts = {str(path.relative_to(output)): sha256(path)
                     for path in sorted(output.rglob("*")) if path.is_file()}
        (output / "manifest.json").write_text(json.dumps({"terminal": terminal,
            "artifacts": artifacts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return terminal


def main() -> int:
    args = parser().parse_args()
    try:
        result = run(args)
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "rejected", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
