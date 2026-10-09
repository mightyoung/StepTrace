"""Render monitoring estimates in a separate Chinese panel beside source video."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import textwrap
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def render(video: Path, estimates: Path, output: Path, font_path: Path,
           *, max_age_seconds: float = 10) -> dict:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from display_project.provenance import sha256_file
    from display_project.vlm_sop.state_risk_contract import validate_inference
    from display_project.localization.zh_cn import STEP_NAMES

    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("max age must be finite and positive")
    if output.exists() or output.resolve() == video.resolve():
        raise ValueError("use a new output video")
    source_hash = sha256_file(video)
    run_dir = estimates.parent
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["terminal"].get("source_sha256") != source_hash or not manifest["terminal"].get("input_integrity_verified"):
        raise ValueError("estimate source integrity does not match the selected video")
    for name in (estimates.name, "knowledge.json", "evidence-windows.jsonl"):
        if manifest["artifacts"].get(name) != sha256_file(run_dir / name):
            raise ValueError("estimate artifact hash mismatch")
    knowledge = json.loads((run_dir / "knowledge.json").read_text(encoding="utf-8"))
    stage_names = {step["id"]: STEP_NAMES.get(step["id"], step["title"]) for step in knowledge["steps"]}
    risk_categories = {rule["id"]: {"quality": "质量风险", "safety": "安全风险", "procedure": "程序风险"}[rule["category"]]
                       for rule in knowledge["risk_rules"]}
    windows = {row["window_id"]: row for row in (
        json.loads(line) for line in (run_dir / "evidence-windows.jsonl").read_text(encoding="utf-8").splitlines())}
    rows = [json.loads(line) for line in estimates.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    for row in rows:
        if row.get("source_sha256") != source_hash:
            raise ValueError("estimate source mismatch")
        if row.get("status") == "ok":
            validate_inference(row["inference"], knowledge=knowledge, window=windows[row["window_id"]])
        if any(isinstance(row.get(key), bool) or not isinstance(row.get(key), (int, float))
               or not math.isfinite(row[key]) or row[key] < 0
               for key in ("decision_time", "available_time")):
            raise ValueError("invalid estimate clock")
        if row["available_time"] < row["decision_time"]:
            raise ValueError("estimate cannot arrive before its evidence")
    rows.sort(key=lambda row: row["available_time"])
    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS)
    width, height = (int(capture.get(key)) for key in
                     (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
    expected = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or min(width, height) < 1:
        capture.release()
        raise ValueError("cannot decode video")
    output.parent.mkdir(parents=True, exist_ok=True)
    panel_width = 480
    canvas_height = max(height, 480)
    # Codec dimensions must be even, including small synthetic QA sources.
    out_width = (width + panel_width + 1) // 2 * 2
    canvas_height = (canvas_height + 1) // 2 * 2
    font = ImageFont.truetype(str(font_path), 22)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                             (out_width, canvas_height))
    if not writer.isOpened():
        capture.release()
        raise OSError("video writer unavailable")
    completion = {"not_started": "未开始", "in_progress": "进行中",
                  "completed": "已完成（估计）", "unknown": "证据不足"}
    risk_status = {"triggered": "触发", "not_triggered": "未触发该规则",
                   "unknown": "证据不足", "not_applicable": "不适用"}
    current = None
    cursor = count = 0
    preview = output.with_suffix(".preview.png")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            timestamp = count / fps
            while cursor < len(rows) and rows[cursor]["available_time"] <= timestamp:
                incoming = rows[cursor]
                if current is None or incoming["decision_time"] >= current["decision_time"]:
                    current = incoming
                cursor += 1
            canvas = Image.new("RGB", (out_width, canvas_height), (19, 25, 34))
            canvas.paste(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), (0, 0))
            draw = ImageDraw.Draw(canvas)
            lines = ["装配监测估计", "用途：监测状态与预设风险"]
            stale = current is None or timestamp - current["decision_time"] > max_age_seconds
            if stale or current.get("status") != "ok":
                lines += ["估计阶段：证据不足", "完成状态：未知", "风险：未知",
                          "状态：等待新证据 / 推理不可用"]
            else:
                inference = current["inference"]
                lines += ["估计阶段：" + stage_names.get(inference.get("estimated_stage"), "未知"),
                          "完成状态：" + completion.get(inference.get("completion_state"), "未知")]
                for ordinal, rule in enumerate(inference.get("risk_assessments", []), start=1):
                    lines.append(f"规则{ordinal}（{risk_categories[rule['rule_id']]}）：" + risk_status.get(rule["status"], "未知"))
                if not inference.get("risk_assessments"):
                    lines.append("风险：未配置可评估规则")
                lines += ["依据：" + str(inference.get("brief_rationale", "证据不足"))]
            if current:
                lines.append(f"更新时间：{current['available_time']:.2f} 秒")
            y = 22
            for line in lines:
                for segment in textwrap.wrap(line, 20, break_long_words=True):
                    if y + 30 >= canvas_height:
                        break
                    draw.text((width + 18, y), segment, font=font, fill=(232, 239, 247))
                    y += 32
            writer.write(cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR))
            if count == expected // 2:
                canvas.save(preview)
            count += 1
    finally:
        writer.release()
        capture.release()
    if count != expected or count == 0:
        raise RuntimeError("incomplete render")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    result = {"status": "DEVELOPMENT_ONLY", "frames": count, "fps": fps,
              "width": out_width, "height": canvas_height, "sha256": digest,
              "font": str(font_path.resolve()), "source_unchanged": sha256_file(video) == source_hash,
              "source_sha256": source_hash,
              "preview": str(preview.resolve())}
    output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2)
                                            + "\n", encoding="utf-8")
    if not result["source_unchanged"]:
        result["status"] = "FAILED_INPUT_CHANGED"
        output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2)
                                                + "\n", encoding="utf-8")
        raise RuntimeError("source changed during render; output retained as failed evidence")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--estimates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=float, default=10)
    args = parser.parse_args()
    print(json.dumps(render(args.video, args.estimates, args.output, args.font,
                            max_age_seconds=args.max_age_seconds), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
