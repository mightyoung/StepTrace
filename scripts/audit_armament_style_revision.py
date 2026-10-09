from __future__ import annotations

import json
import re
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "paper-armament"
BEFORE = OUT / "manuscript_armament.md"
AFTER = OUT / "manuscript_armament_humanized.md"


def citation_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for group in re.findall(r"\[([0-9,\-]+)\]", text):
        for item in group.split(","):
            if "-" in item:
                start, end = [int(value) for value in item.split("-", 1)]
                numbers.update(range(start, end + 1))
            else:
                numbers.add(int(item))
    return numbers


def sentence_stats(text: str) -> dict[str, float | int]:
    chinese_body = text.split("## Abstract", 1)[0] + text.split("## 0  引言", 1)[-1].split("## 参考文献", 1)[0]
    sentences = [part.strip() for part in re.split(r"[。！？]", chinese_body) if 8 <= len(part.strip()) <= 250]
    lengths = [len(re.sub(r"\s+", "", part)) for part in sentences]
    return {
        "sentence_count": len(lengths),
        "minimum": min(lengths),
        "median": statistics.median(lengths),
        "maximum": max(lengths),
        "standard_deviation": round(statistics.pstdev(lengths), 2),
    }


def main() -> None:
    before = BEFORE.read_text(encoding="utf-8")
    after = AFTER.read_text(encoding="utf-8")
    failures: list[str] = []

    required_anchors = [
        "6203070安全系统工程", "6203020爆炸安全工程", "351帧", "1920×1080",
        "12 FPS", "2帧", "349帧", "94项", "766项", "14项失败",
        "annotation_used", "MECCANO-0009", "VLM", "不确定", "证据证书",
    ]
    missing_anchors = [anchor for anchor in required_anchors if anchor not in after]
    if missing_anchors:
        failures.append(f"missing semantic anchors: {missing_anchors}")

    before_citations = citation_numbers(before.split("## 参考文献", 1)[0])
    after_citations = citation_numbers(after.split("## 参考文献", 1)[0])
    if before_citations != after_citations:
        failures.append("citation-number set changed")

    template_phrases = [
        "本文主要工作为", "结果表明", "整体研究主线如图", "综上所述",
        "值得注意的是", "一是", "二是", "三是", "四是", "五是",
    ]
    remaining_template_phrases = {phrase: after.count(phrase) for phrase in template_phrases if after.count(phrase)}
    if remaining_template_phrases:
        failures.append(f"template phrases remain: {remaining_template_phrases}")

    result = {
        "status": "PASS" if not failures else "FAIL",
        "scope": "stylistic revision only; no claim of AI-detector evasion",
        "semantic_anchors_checked": required_anchors,
        "missing_semantic_anchors": missing_anchors,
        "citation_numbers_before": sorted(before_citations),
        "citation_numbers_after": sorted(after_citations),
        "sentence_stats_before": sentence_stats(before),
        "sentence_stats_after": sentence_stats(after),
        "remaining_template_phrases": remaining_template_phrases,
        "failures": failures,
    }
    (OUT / "style_revision_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
