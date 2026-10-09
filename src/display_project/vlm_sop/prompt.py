"""Qwen prompt construction for non-authoritative SOP review."""

from __future__ import annotations

import json
from typing import Mapping, Sequence

from .knowledge import SOPKnowledgePack, reject_truth_material


_SYSTEM_PROMPT = """你是工业装配视频的 SOP 审计员，不是状态机控制器。

你只能依据输入图像中可见的物体、几何关系、装配结果，以及提供的 SOP 步骤卡进行复核。
必须区分“正在尝试某动作”和“该工序已正确完成”。动作出现不等于工序完成。
看不清紧固件、连接关系、完成状态或关键前置条件时，必须输出 uncertain。
禁止利用帧号、视频位置、文件名或数据集先验猜测答案。
禁止把说明书之外的常识扩写为确定事实。
你的结论仅用于审计：不得推进、回退或覆盖 SOP 状态。
只输出符合给定 JSON Schema 的一个 JSON 对象，不要输出 Markdown 或额外解释。"""


def qwen_review_json_schema(step_ids: Sequence[str]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "task_id",
            "clip_frame",
            "observed_action",
            "verdict",
            "confidence",
            "visible_evidence",
            "missing_evidence",
            "negative_evidence",
            "rationale",
            "authority",
            "state_write_allowed",
            "annotation_used",
        ],
        "properties": {
            "schema_version": {"const": "1.0"},
            "task_id": {"type": "string"},
            "clip_frame": {"type": "integer", "minimum": 0},
            "observed_action": {
                "anyOf": [{"enum": list(step_ids)}, {"type": "null"}]
            },
            "verdict": {
                "enum": ["consistent", "conflict", "uncertain", "human_review"]
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "visible_evidence": {"type": "array", "items": {"type": "string"}},
            "missing_evidence": {"type": "array", "items": {"type": "string"}},
            "negative_evidence": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
            "authority": {"const": "audit_only"},
            "state_write_allowed": {"const": False},
            "annotation_used": {"const": False},
        },
    }


def _relevant_cards(
    pack: SOPKnowledgePack, current_step: str | None, expected_step: str
) -> list[dict[str, object]]:
    selected = {expected_step}
    if current_step is not None:
        selected.add(current_step)
    expected = pack.step(expected_step)
    selected.update(expected.prerequisites)
    selected.update(expected.allowed_next)
    return [card.prompt_payload() for card in pack.steps if card.id in selected]


def build_qwen_sop_review_request(
    pack: SOPKnowledgePack,
    *,
    clip_frame: int,
    expected_step: str,
    current_step: str | None,
    perception_summary: Mapping[str, object] | None = None,
    image_roles: Sequence[str] = (
        "before: 前一关键帧",
        "current: 当前待复核帧",
        "after: 后一关键帧（若可用）",
    ),
) -> dict[str, object]:
    if isinstance(clip_frame, bool) or not isinstance(clip_frame, int) or clip_frame < 0:
        raise ValueError("clip_frame must be a non-negative integer")
    pack.step(expected_step)
    if current_step is not None:
        pack.step(current_step)
    perception_summary = dict(perception_summary or {})
    reject_truth_material(perception_summary)
    context = {
        "task_id": pack.task_id,
        "knowledge_pack_id": pack.pack_id,
        "knowledge_status": pack.status,
        "authority": pack.authority,
        "clip_frame": clip_frame,
        "current_authorized_step": current_step,
        "expected_step": expected_step,
        "image_roles": list(image_roles),
        "global_rules": list(pack.global_rules),
        "relevant_step_cards": _relevant_cards(pack, current_step, expected_step),
        "deterministic_perception_summary": perception_summary,
        "decision_rules": [
            "只有完成线索清晰且无冲突时，才能输出 consistent。",
            "若出现错误零件、缺失前置条件、错误装配或顺序冲突，输出 conflict。",
            "关键区域不可见、被遮挡或证据不足时，输出 uncertain。",
            "风险较高且仅靠当前图像无法排除错误时，输出 human_review。",
            "observed_action 只能填写步骤卡 id；无法判断时填写 null。",
        ],
    }
    user_prompt = (
        "复核随本请求附带的装配关键帧。严格依据以下运行时上下文判断。\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )
    return {
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "qwen_sop_audit_review",
                "strict": True,
                "schema": qwen_review_json_schema(pack.step_ids),
            },
        },
    }
