from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "research" / "next-execution-plan-20260814.md"
DESIGN = ROOT / "configs" / "experiments" / "development-real-data-four-video-v1.json"
G3_AUDIT = ROOT / "research" / "audits" / "g3-mechanism-capability.current.json"
RESULT = (
    ROOT
    / ".omx"
    / "specs"
    / "autoresearch-paper-next-execution-plan"
    / "result.json"
)


def main() -> int:
    failures: list[str] = []
    plan = PLAN.read_text(encoding="utf-8")
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    g3 = json.loads(G3_AUDIT.read_text(encoding="utf-8"))

    expected_design = {
        "output_artifact_count": 4,
        "distinct_source_recording_count": 1,
        "independent_unit_count": 1,
        "inferential_comparison_allowed": False,
        "formal_evidence": False,
        "g3_evidence": False,
    }
    for key, expected in expected_design.items():
        if design.get(key) != expected:
            failures.append(f"design mismatch: {key}={design.get(key)!r}")

    if g3.get("decision_type") != "PRECHECK_ONLY":
        failures.append("G3 audit is not PRECHECK_ONLY")
    if g3.get("status") != "BLOCKED":
        failures.append("G3 audit is not BLOCKED")
    if g3.get("downstream_authorized") is not False:
        failures.append("G3 downstream authorization is not false")

    required_phrases = [
        "不得写成\n  `n=4`",
        "formal_evidence=false",
        "g3_evidence=false",
        "inferential_comparison_allowed=false",
        "当前不安排新的远程算法运行",
        "fixed_streak",
        "hmm_or_crf",
        "proposed",
        "Branch A/B 各 alpha=0.025",
        "10,000 次 paired cluster bootstrap",
        "BLOCKED_UNKNOWN_INDUSTREAL_OPERATOR_ID",
        "READY_FOR_CORRESPONDING_AUTHOR_UPLOAD",
        "2026-10-11：G3 admission 决策",
        "不启动七视频验收",
    ]
    for phrase in required_phrases:
        if phrase not in plan:
            failures.append(f"missing required phrase: {phrase!r}")

    required_sections = [
        "## 1. 当前结论与规划入口",
        "## 2. 论文主线与证据分工",
        "## 3. 总体执行顺序",
        "## 4. 分阶段执行包",
        "## 5. 可立即并行的论文工作（不依赖新实验）",
        "## 6. 每周控制面板与升级规则",
        "## 7. 下一轮只执行的五项任务",
    ]
    for section in required_sections:
        if section not in plan:
            failures.append(f"missing section: {section}")

    forbidden_phrases = [
        "四个独立实验",
        "四个独立录制",
        "G3 已通过",
        "正式证据=true",
    ]
    for phrase in forbidden_phrases:
        if phrase in plan:
            failures.append(f"forbidden claim: {phrase!r}")

    result = {
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "passed": not failures,
        "summary": (
            "next execution plan preserves development/G3/formal boundaries"
            if not failures
            else "next execution plan validation failed"
        ),
        "output_artifact_path": PLAN.relative_to(ROOT).as_posix(),
        "output_artifact_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "checks": {
            "four_video_design_boundary": not any(
                item.startswith("design mismatch") for item in failures
            ),
            "g3_authority_boundary": not any(
                item.startswith("G3 ") for item in failures
            ),
            "required_plan_contract": not any(
                item.startswith("missing") or item.startswith("forbidden")
                for item in failures
            ),
        },
        "failures": failures,
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
