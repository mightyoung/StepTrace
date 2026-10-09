"""Prompt construction for development-only VLM state/risk estimation."""

from __future__ import annotations

import json
from typing import Mapping, Sequence

from .state_risk_contract import inference_json_schema
from .state_risk_knowledge import validate_knowledge


METHOD_B1 = "B1-raw-imagery-v1"
METHOD_B2 = "B2-simple-concat-v1"
METHOD_P = "P-structured-temporal-v1"
METHOD_P_NO_PERCEPTION = "P-ablation-no-perception-v1"
METHOD_P_NO_HISTORY = "P-ablation-no-history-v1"
METHOD_P_NO_PROCESS = "P-ablation-no-process-constraints-v1"
METHOD_P_NO_FACT_SPLIT = "P-ablation-no-fact-split-v1"
METHOD_IDS = frozenset(
    {
        METHOD_B1,
        METHOD_B2,
        METHOD_P,
        METHOD_P_NO_PERCEPTION,
        METHOD_P_NO_HISTORY,
        METHOD_P_NO_PROCESS,
        METHOD_P_NO_FACT_SPLIT,
    }
)
MAX_HISTORY_SNAPSHOTS = 4

_SYSTEM = """你是装配过程的在线状态与预定义风险估计器。
只使用当前请求中可见的图像、观察证据、在线历史和工艺知识。动作出现不等于步骤正确完成。
不得根据文件名、原始样本编号、数据集身份或视频绝对进度猜测答案。
缺少规则要求的观察时必须输出 unknown；不得把未检出当成已确认不存在。
每条已配置风险规则必须且只能输出一次。只引用本窗口列出的证据 id。
输出仅能更新 estimated_state，authority 必须为 state_estimation_only；不得生成生产控制或 execution_state 指令。
只输出符合 JSON Schema 的 JSON 对象，不输出 Markdown、隐藏思维过程或额外说明。"""


def _step_summary(step: Mapping[str, object]) -> dict[str, object]:
    return {"id": step["id"], "title": step["title"]}


def _tokens(value: object) -> set[str]:
    text = json.dumps(value, ensure_ascii=False).casefold()
    return {part for part in text.replace("_", " ").replace("-", " ").split() if len(part) > 1}


def _candidate_steps(
    knowledge: Mapping[str, object],
    window: Mapping[str, object],
    history: Mapping[str, object],
    limit: int,
) -> list[dict[str, object]]:
    steps = list(knowledge["steps"])  # type: ignore[arg-type]
    evidence_tokens = _tokens(
        {
            "objects": window.get("object_track_refs", []),
            "relations": window.get("relation_events", []),
            "observability": window.get("observability", {}),
        }
    )
    prior_ids = {
        item.get("estimated_stage")
        for item in history.get("estimates", [])  # type: ignore[union-attr]
        if isinstance(item, Mapping)
    }
    scored: list[tuple[int, int, dict[str, object]]] = []
    for index, raw in enumerate(steps):
        step = dict(raw)
        searchable = _tokens(
            {
                "title": step["title"],
                "completion_cues": step["completion_cues"],
                "negative_cues": step["negative_cues"],
                "required_observations": step["required_observations"],
            }
        )
        score = len(evidence_tokens & searchable) * 3
        if step["id"] in prior_ids:
            score += 2
        if any(prior in prior_ids for prior in step["prerequisites"]):  # type: ignore[union-attr]
            score += 1
        scored.append((score, -index, step))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def _safe_image_urls(image_urls: Sequence[str]) -> list[str]:
    urls = list(image_urls)
    for url in urls:
        if not isinstance(url, str) or not url.startswith("data:image/"):
            raise ValueError("image_urls must contain only staged data:image URLs")
    return urls


def _relative_refs(entries: object, decision_time: float) -> list[dict[str, object]]:
    output = []
    for entry in entries:  # type: ignore[union-attr]
        copied = {key: value for key, value in entry.items() if key not in {"time", "cache_ref", "image_sha256", "image_verified"}}
        copied["seconds_before_decision"] = decision_time - float(entry["time"])
        output.append(copied)
    return output


def _prompt_window(window: Mapping[str, object]) -> dict[str, object]:
    decision_time = float(window["decision_time"])
    return {
        "frame_refs": _relative_refs(window["frame_refs"], decision_time),
        "object_track_refs": _relative_refs(window["object_track_refs"], decision_time),
        "relation_events": _relative_refs(window["relation_events"], decision_time),
        "observability": window["observability"],
    }


def _prompt_history(history: Mapping[str, object], decision_time: float) -> dict[str, object]:
    observations = [
        {
            "frame_refs": _relative_refs(item["frame_refs"], decision_time),
            "object_track_refs": _relative_refs(item["object_track_refs"], decision_time),
            "relation_events": _relative_refs(item["relation_events"], decision_time),
            "observability": item["observability"],
        }
        for item in list(history.get("observations", []))[-MAX_HISTORY_SNAPSHOTS:]  # type: ignore[arg-type]
    ]
    estimates = []
    for item in list(history.get("estimates", []))[-MAX_HISTORY_SNAPSHOTS:]:  # type: ignore[arg-type]
        copied = {key: value for key, value in item.items() if key not in {"window_id", "decision_time"}}
        prior_time = item.get("decision_time")
        if isinstance(prior_time, (int, float)) and not isinstance(prior_time, bool):
            copied["seconds_before_decision"] = decision_time - float(prior_time)
        estimates.append(copied)
    failures = []
    for item in list(history.get("failures", []))[-MAX_HISTORY_SNAPSHOTS:]:  # type: ignore[arg-type]
        copied = {"status": "missing"}
        prior_time = item.get("decision_time")
        if isinstance(prior_time, (int, float)) and not isinstance(prior_time, bool):
            copied["seconds_before_decision"] = decision_time - float(prior_time)
        failures.append(copied)
    return {"observations": observations, "estimates": estimates, "failures": failures}


def _imagery_only_history(history: Mapping[str, object]) -> dict[str, object]:
    observations = [
        {"frame_refs": item["frame_refs"], "observability": item["observability"]}
        for item in history.get("observations", [])  # type: ignore[union-attr]
    ]
    estimates = []
    for item in history.get("estimates", []):  # type: ignore[union-attr]
        copied = dict(item)
        copied["evidence_refs"] = []
        copied["conflicting_evidence_refs"] = []
        copied["deviations"] = [
            {**deviation, "evidence_refs": []}
            for deviation in copied.get("deviations", [])
        ]
        copied["risk_assessments"] = [
            {**assessment, "evidence_refs": []}
            for assessment in copied.get("risk_assessments", [])
        ]
        estimates.append(copied)
    return {"observations": observations, "estimates": estimates, "failures": history.get("failures", [])}


def build_state_risk_request(
    knowledge: Mapping[str, object],
    window: Mapping[str, object],
    *,
    method_id: str = METHOD_P,
    state_memory: object | None = None,
    image_urls: Sequence[str] = (),
    max_candidate_details: int = 3,
) -> dict[str, object]:
    """Build a comparable B1/B2/P or ablation request without truth hints."""

    pack = validate_knowledge(knowledge)
    if method_id not in METHOD_IDS:
        raise ValueError(f"unsupported state/risk method_id: {method_id}")
    if isinstance(max_candidate_details, bool) or max_candidate_details < 1:
        raise ValueError("max_candidate_details must be positive")
    max_candidate_details = min(max_candidate_details, len(pack["steps"]))  # type: ignore[arg-type]
    if window.get("task_id") != pack["task_id"]:
        raise ValueError("window task_id must match knowledge")
    urls = _safe_image_urls(image_urls)
    history = {"observations": [], "estimates": [], "failures": []}
    if state_memory is not None and method_id != METHOD_P_NO_HISTORY:
        prompt_history = getattr(state_memory, "prompt_history", None)
        if not callable(prompt_history):
            raise TypeError("state_memory must provide prompt_history(before_time)")
        history = dict(prompt_history(window["decision_time"]))
    history = _prompt_history(history, float(window["decision_time"]))
    imagery_history = _imagery_only_history(history)

    full_summary = [_step_summary(step) for step in pack["steps"]]  # type: ignore[arg-type]
    base: dict[str, object] = {
        "method_id": method_id,
        "task_id": pack["task_id"],
        "window_id": window["window_id"],
        "complete_sop_summary": full_summary,
        "complete_process_knowledge": pack["steps"],
        "risk_rules": pack["risk_rules"],
        "deviation_types": pack["deviation_types"],
        "image_count": len(urls),
        "history_limit": MAX_HISTORY_SNAPSHOTS,
        "history_observation_count": len(history["observations"]),  # type: ignore[arg-type]
        "history_estimate_count": len(history["estimates"]),  # type: ignore[arg-type]
        "history_failure_count": len(history["failures"]),  # type: ignore[arg-type]
        "evidence_reference_ids": [
            item["id"]
            for field in ("frame_refs", "object_track_refs", "relation_events")
            for item in window[field]  # type: ignore[union-attr]
        ],
        "rules": [
            "图像按给定顺序均为决策时刻及之前的在线观察。",
            "brief_rationale 仅简述可核查事实、冲突、缺证和适用规则。",
        ],
    }
    if method_id == METHOD_B1:
        base["input_design"] = "raw imagery plus complete SOP/risk knowledge"
        base["evidence_reference_ids"] = [item["id"] for item in window["frame_refs"]]  # type: ignore[union-attr]
        base["simple_online_history"] = list(imagery_history["observations"]) + list(imagery_history["estimates"]) + list(imagery_history["failures"])  # type: ignore[arg-type]
    elif method_id == METHOD_B2:
        base["input_design"] = "simple concatenation without candidate retrieval or fact/hypothesis structure"
        base["evidence_window"] = _prompt_window(window)
        base["simple_online_history"] = list(history.get("observations", [])) + list(history.get("estimates", [])) + list(history.get("failures", []))  # type: ignore[arg-type]
    else:
        base["input_design"] = "structured temporal evidence and knowledge retrieval"
        if method_id == METHOD_P_NO_PERCEPTION:
            base["evidence_reference_ids"] = [item["id"] for item in window["frame_refs"]]  # type: ignore[union-attr]
        if method_id != METHOD_P_NO_PERCEPTION:
            base["observed_facts"] = {
                "frame_refs": _relative_refs(window.get("frame_refs", []), float(window["decision_time"])),
                "object_track_refs": _relative_refs(window.get("object_track_refs", []), float(window["decision_time"])),
                "relation_events": _relative_refs(window.get("relation_events", []), float(window["decision_time"])),
                "observability": window.get("observability", {}),
            }
        if method_id == METHOD_P_NO_FACT_SPLIT:
            base["unseparated_history"] = list(history.get("observations", [])) + list(history.get("estimates", [])) + list(history.get("failures", []))  # type: ignore[arg-type]
        elif method_id != METHOD_P_NO_HISTORY:
            base["historical_observation_facts"] = history.get("observations", [])
            base["historical_model_hypotheses"] = history.get("estimates", [])
            base["historical_missing_estimates"] = history.get("failures", [])
        if method_id == METHOD_P_NO_PROCESS:
            base.pop("complete_sop_summary", None)
            base.pop("complete_process_knowledge", None)
            base["candidate_step_details"] = [
                {k: v for k, v in step.items() if k not in {"prerequisites", "source_refs"}}
                for step in list(pack["steps"])[:max_candidate_details]  # type: ignore[arg-type]
            ]
        else:
            retrieval_window = window
            retrieval_history = history
            if method_id == METHOD_P_NO_PERCEPTION:
                retrieval_window = {**window, "object_track_refs": [], "relation_events": []}
                retrieval_history = imagery_history
                base["historical_observation_facts"] = imagery_history["observations"]
            candidates = _candidate_steps(pack, retrieval_window, retrieval_history, max_candidate_details)
            base["candidate_step_details"] = candidates
            base["candidate_reselection"] = {
                "available": True,
                "instruction": "若详卡均不符合，依据 complete_sop_summary 重新选择其他步骤；不要被候选锁定。",
            }

    text = "依据以下开发期在线上下文估计当前状态和风险：\n" + json.dumps(base, ensure_ascii=False, separators=(",", ":"))
    user_content: list[dict[str, object]] = [{"type": "text", "text": text}]
    user_content.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)
    return {
        "method_id": method_id,
        "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user_content}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "qwen_joint_state_risk", "strict": True, "schema": inference_json_schema(pack)},
        },
    }
