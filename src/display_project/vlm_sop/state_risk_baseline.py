"""Deterministic B0 fixed-streak state/risk baseline.

This module is deliberately independent from the execution state machine.  It
consumes the same validated, time-bounded evidence window as the VLM path and
returns the joint state/risk contract.  It never reads labels, calls a model,
or turns a textual hypothesis into an observation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from .evidence_window import EvidenceWindowError, validate_window
from .state_risk_contract import validate_inference
from .state_risk_knowledge import validate_knowledge


class StateRiskBaselineError(ValueError):
    """Raised when an evidence window cannot be safely consumed."""


_FORBIDDEN = {
    "answer", "annotation", "correct_step", "dataset_answer", "ground_truth",
    "model_hypothesis", "oracle", "target_step", "truth", "truth_store",
}
_TOKEN_RE = re.compile(r"[^\w.-]+", re.UNICODE)


def _reject_forbidden(value: object, path: str = "window") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN:
                raise StateRiskBaselineError(f"forbidden non-observation field: {path}.{key}")
            _reject_forbidden(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _reject_forbidden(child, f"{path}[{index}]")


def _norm(value: object) -> str:
    return str(value).strip().casefold()


def _tokens(value: object) -> set[str]:
    text = _norm(value)
    return {part for part in _TOKEN_RE.split(text) if part}


def _truthy(value: object) -> bool:
    return value is True or _norm(value) in {"true", "yes", "observed", "visible", "confirmed", "present", "triggered"}


def _event_facts(event: Mapping[str, object]) -> set[str]:
    """Return only facts explicitly present in one observed relation/object."""

    facts: set[str] = set()
    for key in ("predicate", "relation", "type", "kind", "cue_id", "observation", "condition", "rule_id", "step_id", "value"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            facts.add(_norm(value))
            facts.update(_tokens(value))
    # A false/negative relation is still useful evidence, but cannot satisfy a
    # positive condition.  Prefixing the polarity keeps the rule language
    # explicit and avoids treating a negative cue as an affirmative cue.
    polarity = _norm(event.get("polarity", event.get("status", "")))
    if polarity in {"negative", "absent", "missing", "false", "not_observed", "not-visible"}:
        facts.update({f"negative:{token}" for token in tuple(facts)})
    elif polarity in {"positive", "observed", "present", "true", "confirmed"}:
        facts.update({f"positive:{token}" for token in tuple(facts)})
    return facts


def _positive_event(event: Mapping[str, object]) -> bool:
    polarity = _norm(event.get("polarity", event.get("status", "")))
    return polarity not in {"negative", "absent", "missing", "false", "not_observed", "not-visible"}


def _event_frame_id(event: Mapping[str, object], fallback: str) -> str:
    for key in ("frame_id", "frame_ref", "frame"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _matches(condition: str, event: Mapping[str, object]) -> bool:
    """Match a frozen textual rule atom against explicit event fields.

    Knowledge is intentionally string-only and truth-free.  A condition is
    executable when it names a relation/cue/rule atom that occurs explicitly
    in the observed event.  Free prose therefore remains unsupported and is
    conservatively treated as absent.
    """

    wanted = _norm(condition)
    if not wanted or not _positive_event(event):
        return False
    facts = _event_facts(event)
    if wanted in facts:
        return True
    # Structured aliases such as ``cue:foo`` and ``relation=foo`` are a small,
    # frozen rule language; only the value after the delimiter is executable.
    if ":" in wanted or "=" in wanted:
        atom = re.split(r"[:=]", wanted, maxsplit=1)[1].strip()
        return bool(atom and (atom in facts or atom in {_norm(x) for x in event.values() if isinstance(x, str)}))
    return False


def _event_refs(events: Sequence[Mapping[str, object]], conditions: Sequence[str]) -> list[str]:
    refs: list[str] = []
    for event in events:
        if any(_matches(condition, event) for condition in conditions):
            ref = event.get("id")
            if isinstance(ref, str) and ref.strip() and ref not in refs:
                refs.append(ref)
    return refs


class StateRiskBaseline:
    """B0 fixed-streak baseline with no VLM or execution-state authority."""

    def __init__(self, knowledge: Mapping[str, object], confirm_frames: int = 4, cooldown_frames: int = 12) -> None:
        if isinstance(confirm_frames, bool) or not isinstance(confirm_frames, int) or confirm_frames < 1:
            raise ValueError("confirm_frames must be a positive integer")
        if isinstance(cooldown_frames, bool) or not isinstance(cooldown_frames, int) or cooldown_frames < 0:
            raise ValueError("cooldown_frames must be a non-negative integer")
        self.knowledge = validate_knowledge(knowledge)
        self.confirm_frames = confirm_frames
        self.cooldown_frames = cooldown_frames
        self._seen_frames: set[str] = set()
        self._seen_events: set[str] = set()
        self._candidate: str | None = None
        self._candidate_frames: list[str] = []
        self._cooldown_remaining = 0
        self._completed: set[str] = set()
        self._current_stage: str | None = None

    def _ordered_frames(self, window: Mapping[str, object]) -> list[tuple[str, Mapping[str, object]]]:
        raw_frames = window.get("frame_refs")
        raw_events = window.get("relation_events")
        if isinstance(raw_frames, (str, bytes)) or not isinstance(raw_frames, Sequence):
            raise StateRiskBaselineError("window.frame_refs must be a list")
        if isinstance(raw_events, (str, bytes)) or not isinstance(raw_events, Sequence):
            raise StateRiskBaselineError("window.relation_events must be a list")
        frames: list[tuple[str, Mapping[str, object]]] = []
        for item in raw_frames:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                raise StateRiskBaselineError("each frame_ref must have an id")
            frame_id = str(item["id"])
            frames.append((frame_id, item))
        if not frames and raw_events:
            raise StateRiskBaselineError("relation events require frame_refs")
        grouped: dict[str, list[Mapping[str, object]]] = {frame_id: [] for frame_id, _ in frames}
        for event in raw_events:
            if not isinstance(event, Mapping) or not isinstance(event.get("id"), str):
                raise StateRiskBaselineError("each relation_event must have an id")
            frame_id = _event_frame_id(event, "")
            if not frame_id:
                event_time = event.get("time")
                if isinstance(event_time, (int, float)) and not isinstance(event_time, bool):
                    prior = [item for item in frames if isinstance(item[1].get("time"), (int, float)) and item[1]["time"] <= event_time]
                    frame_id = (prior[-1][0] if prior else frames[0][0])
                else:
                    frame_id = frames[0][0]
            if frame_id in grouped:
                grouped[frame_id].append(event)
        return [(frame_id, {"frame": frame, "events": grouped[frame_id]}) for frame_id, frame in frames]

    def _candidate_for_frame(self, events: Sequence[Mapping[str, object]]) -> tuple[str | None, list[str]]:
        for step in self.knowledge["steps"]:  # type: ignore[index]
            step_id = str(step["id"])
            prerequisites = set(step["prerequisites"])  # type: ignore[arg-type]
            if not prerequisites.issubset(self._completed):
                continue
            required = set(step["required_observations"])  # type: ignore[arg-type]
            if not required.issubset(self._available_observations):
                continue
            refs = _event_refs(events, step["completion_cues"])  # type: ignore[arg-type]
            if refs:
                return step_id, refs
        return None, []

    def _observe_frames(self, frames: Sequence[tuple[str, Mapping[str, object]]]) -> None:
        for frame_id, payload in frames:
            if frame_id in self._seen_frames:
                continue
            self._seen_frames.add(frame_id)
            events = [event for event in payload["events"] if event.get("id") not in self._seen_events]  # type: ignore[index]
            self._seen_events.update(str(event["id"]) for event in events)
            candidate, refs = self._candidate_for_frame(events)  # type: ignore[arg-type]
            if self._cooldown_remaining:
                self._cooldown_remaining -= 1
                continue
            if candidate is None:
                self._candidate = None
                self._candidate_frames.clear()
                continue
            if candidate != self._candidate:
                self._candidate = candidate
                self._candidate_frames = []
            self._candidate_frames.append(frame_id)
            if len(self._candidate_frames) >= self.confirm_frames:
                self._current_stage = candidate
                self._completed.add(candidate)
                self._candidate_frames.clear()
                self._cooldown_remaining = self.cooldown_frames

    def infer(self, window: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(window, Mapping):
            raise StateRiskBaselineError("window must be a mapping")
        _reject_forbidden(window)
        try:
            validate_window(window)
        except EvidenceWindowError as exc:
            raise StateRiskBaselineError(f"invalid evidence window: {exc}") from exc
        observability = window.get("observability")
        if not isinstance(observability, Mapping):
            raise StateRiskBaselineError("window.observability must be a mapping")
        missing = observability.get("missing", [])
        if isinstance(missing, (str, bytes)) or not isinstance(missing, Sequence):
            raise StateRiskBaselineError("window.observability.missing must be a list")
        self._available_observations = {
            str(item) for item in missing
        }
        all_required = {
            str(item)
            for step in self.knowledge["steps"]  # type: ignore[index]
            for item in step["required_observations"]  # type: ignore[index]
        } | {
            str(item)
            for rule in self.knowledge["risk_rules"]  # type: ignore[index]
            for item in rule["required_observations"]  # type: ignore[index]
        }
        if observability.get("view") == "complete":
            self._available_observations = all_required - self._available_observations
        else:
            self._available_observations = set()
        self._observe_frames(self._ordered_frames(window))
        events = [item for item in window["relation_events"] if isinstance(item, Mapping)]  # type: ignore[index]
        stage = self._current_stage
        normalized = self.knowledge
        step = next((item for item in normalized["steps"] if item["id"] == stage), None)  # type: ignore[index]
        completion = "unknown"
        if stage is not None:
            completion = "completed" if stage in self._completed else "in_progress"
            stage_required = set(step["required_observations"]) if step is not None else set()  # type: ignore[arg-type]
            if not stage_required.issubset(self._available_observations):
                completion = "unknown"
            if step is not None and any(_matches(cue, event) for cue in step["negative_cues"] for event in events):  # type: ignore[index]
                completion = "unknown"
        evidence_refs = [str(item["id"]) for item in window["frame_refs"] if isinstance(item, Mapping) and isinstance(item.get("id"), str)]  # type: ignore[index]
        evidence_refs.extend(str(item["id"]) for item in events if isinstance(item.get("id"), str) and str(item["id"]) not in evidence_refs)
        assessments: list[dict[str, object]] = []
        view_complete = observability.get("view") == "complete"
        missing_set = set(missing)
        for rule in normalized["risk_rules"]:  # type: ignore[index]
            rule_id = str(rule["id"])
            applicable = (stage is None and "*" in rule["applicable_stages"]) or (stage is not None and ("*" in rule["applicable_stages"] or stage in rule["applicable_stages"]))  # type: ignore[operator]
            required = list(rule["required_observations"])  # type: ignore[arg-type]
            absent = [item for item in required if item in missing_set or observability.get("view") != "complete"]
            refs = _event_refs(events, rule["trigger_conditions"])  # type: ignore[arg-type]
            exception_refs = _event_refs(events, rule["exceptions"])  # type: ignore[arg-type]
            if stage is None and not applicable:
                status, rule_refs, missing_evidence = "unknown", [], ["estimated_stage", *absent]
            elif not applicable:
                status, rule_refs, missing_evidence = "not_applicable", [], []
            elif absent:
                status, rule_refs, missing_evidence = "unknown", [], absent
            elif refs and not exception_refs:
                status, rule_refs, missing_evidence = "triggered", refs, []
            elif view_complete:
                frame_ids = [str(item["id"]) for item in window["frame_refs"] if isinstance(item, Mapping)]  # type: ignore[index]
                status, rule_refs, missing_evidence = "not_triggered", refs or frame_ids[:1], []
            else:
                status, rule_refs, missing_evidence = "unknown", [], [str(item) for item in required]
            assessments.append({"rule_id": rule_id, "status": status, "evidence_refs": rule_refs, "missing_evidence": missing_evidence})
        result = {
            "schema_version": "1.0", "task_id": normalized["task_id"], "window_id": window.get("window_id"),
            "estimated_stage": stage, "completion_state": completion, "deviations": [],
            "risk_assessments": assessments, "evidence_refs": evidence_refs,
            "conflicting_evidence_refs": [], "brief_rationale": "B0 fixed-streak evidence-only baseline; no model or truth input.",
            "authority": "state_estimation_only",
        }
        return validate_inference(result, knowledge=normalized, window=window)
