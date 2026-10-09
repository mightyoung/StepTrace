"""Versioned declarative task configuration compiler."""

from __future__ import annotations
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
import yaml  # type: ignore[import-untyped]
from .state_machine import SOPStateMachine


class TaskConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TaskStep:
    id: str
    depends_on: tuple[str, ...] = ()
    metadata: Mapping[str, Any] | None = None
    evidence: tuple[Mapping[str, Any], ...] = ()
    prompts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledTask:
    task_id: str
    schema_version: int
    step_specs: tuple[TaskStep, ...]
    allowed_classes: tuple[str, ...] = ()
    detector_prompts: tuple[str, ...] = ()
    class_aliases: Mapping[str, tuple[str, ...]] | None = None

    @property
    def steps(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.step_specs)

    def state_machine(self, **kwargs: Any) -> SOPStateMachine:
        return SOPStateMachine(self.steps, **kwargs)

    def aliases_for(self, label: str) -> tuple[str, ...]:
        return (
            tuple(self.class_aliases.get(label, (label,)))
            if self.class_aliases
            else (label,)
        )


class TaskConfigCompiler:
    _MAX_EVIDENCE_DEPTH = 32
    _MAX_EVIDENCE_NODES = 512

    @classmethod
    def _validate_predicate(
        cls, raw: object, step_id: str, *, depth: int, budget: list[int]
    ) -> Mapping[str, Any]:
        if depth > cls._MAX_EVIDENCE_DEPTH:
            raise TaskConfigError(
                f"evidence predicate for {step_id} exceeds maximum nesting depth"
            )
        if not isinstance(raw, Mapping):
            raise TaskConfigError(f"evidence predicate for {step_id} must be a mapping")
        budget[0] += 1
        if budget[0] > cls._MAX_EVIDENCE_NODES:
            raise TaskConfigError(
                f"evidence predicates for {step_id} exceed maximum node count"
            )
        item = dict(raw)
        selector_keys = [key for key in ("predicate", "type") if key in item]
        if len(selector_keys) != 1:
            raise TaskConfigError(
                f"evidence predicate for {step_id} requires exactly one predicate/type"
            )
        kind = item[selector_keys[0]]
        if not isinstance(kind, str) or kind not in {"count", "near", "all", "any"}:
            raise TaskConfigError(f"unknown evidence predicate for {step_id}: {kind!r}")
        common = {"predicate", "type", "reason"}
        allowed = {
            "count": common
            | {
                "label",
                "min",
                "max",
                "min_confidence",
                "max_area_ratio",
                "min_aspect_ratio",
            },
            "near": common
            | {
                "left",
                "right",
                "max_gap",
                "left_min_confidence",
                "right_min_confidence",
            },
            "all": common | {"predicates"},
            "any": common | {"predicates"},
        }[kind]
        unknown = set(item) - allowed
        if unknown:
            raise TaskConfigError(
                f"unknown fields for {kind} evidence predicate for {step_id}: {sorted(unknown)}"
            )
        if "reason" in item and (
            not isinstance(item["reason"], str) or not item["reason"].strip()
        ):
            raise TaskConfigError(
                f"evidence predicate for {step_id} has invalid reason"
            )

        def finite_number(name: str, *, high: float | None = None) -> None:
            if name not in item:
                return
            value = item[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise TaskConfigError(
                    f"{kind} evidence predicate for {step_id} has invalid {name}"
                )
            if float(value) < 0 or (high is not None and float(value) > high):
                raise TaskConfigError(
                    f"{kind} evidence predicate for {step_id} has invalid {name}"
                )

        if kind == "count":
            if not isinstance(item.get("label"), str) or not item["label"].strip():
                raise TaskConfigError(
                    f"count evidence predicate for {step_id} requires label"
                )
            if "min" not in item and "max" not in item:
                raise TaskConfigError(
                    f"count evidence predicate for {step_id} requires min or max"
                )
            for name in ("min", "max"):
                if name in item and (
                    isinstance(item[name], bool)
                    or not isinstance(item[name], int)
                    or item[name] < 0
                ):
                    raise TaskConfigError(
                        f"count evidence predicate for {step_id} has invalid {name}"
                    )
            if "min" in item and "max" in item and item["min"] > item["max"]:
                raise TaskConfigError(
                    f"count evidence predicate for {step_id} has min greater than max"
                )
            finite_number("min_confidence", high=1.0)
            finite_number("max_area_ratio", high=1.0)
            finite_number("min_aspect_ratio")
        elif kind == "near":
            for name in ("left", "right"):
                if not isinstance(item.get(name), str) or not item[name].strip():
                    raise TaskConfigError(
                        f"near evidence predicate for {step_id} requires left/right"
                    )
            finite_number("max_gap", high=1.0)
            finite_number("left_min_confidence", high=1.0)
            finite_number("right_min_confidence", high=1.0)
        else:
            children = item.get("predicates")
            if (
                isinstance(children, (str, bytes))
                or not isinstance(children, Sequence)
                or not children
            ):
                raise TaskConfigError(
                    f"{kind} evidence predicate for {step_id} requires predicates"
                )
            for child in children:
                cls._validate_predicate(child, step_id, depth=depth + 1, budget=budget)
        return item

    def compile(self, config: Mapping[str, Any]) -> CompiledTask:
        if not isinstance(config, Mapping):
            raise TaskConfigError("task config must be a mapping")
        version = config.get("schema_version", config.get("version"))
        if version != 1:
            raise TaskConfigError("unsupported schema_version; expected 1")
        task_id = config.get("task_id", config.get("id"))
        if not isinstance(task_id, str) or not task_id.strip():
            raise TaskConfigError("task_id must be non-empty")
        detector = config.get("detector", {}) or {}
        raw_classes = config.get("allowed_classes", config.get("classes", ()))
        if not raw_classes and isinstance(detector, Mapping):
            raw_classes = detector.get("allowed_classes", detector.get("classes", ()))
        if isinstance(raw_classes, (str, bytes)) or not isinstance(
            raw_classes, Sequence
        ):
            raise TaskConfigError("allowed_classes must be a list")
        if any(not isinstance(item, str) or not item.strip() for item in raw_classes):
            raise TaskConfigError("allowed_classes entries must be non-empty strings")
        allowed_classes = tuple(item.strip() for item in raw_classes)
        raw_aliases = config.get("class_aliases", config.get("aliases", {})) or {}
        if not raw_aliases and isinstance(detector, Mapping):
            raw_aliases = (
                detector.get("class_aliases", detector.get("aliases", {})) or {}
            )
        if not isinstance(raw_aliases, Mapping):
            raise TaskConfigError("class_aliases must be a mapping")
        aliases = {}
        for key, values in raw_aliases.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or isinstance(values, (str, bytes))
                or not isinstance(values, Sequence)
            ):
                raise TaskConfigError("class_aliases values must be lists")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise TaskConfigError("class_aliases entries must be non-empty strings")
            clean_key = key.strip()
            aliases[clean_key] = tuple(
                dict.fromkeys((clean_key, *(value.strip() for value in values)))
            )
        raw_steps = config.get("steps")
        if (
            not isinstance(raw_steps, Sequence)
            or isinstance(raw_steps, (str, bytes))
            or not raw_steps
        ):
            raise TaskConfigError("steps must be non-empty list")
        specs, ids = [], set()
        for raw in raw_steps:
            if isinstance(raw, str):
                raw = {"id": raw}
            if not isinstance(raw, Mapping):
                raise TaskConfigError("each step must be a mapping")
            sid = raw.get("id", raw.get("name"))
            if not isinstance(sid, str) or not sid.strip():
                raise TaskConfigError("step id must be non-empty")
            sid = sid.strip()
            if sid in ids:
                raise TaskConfigError(f"duplicate step id: {sid}")
            deps = raw.get("depends_on", raw.get("dependencies", ())) or ()
            if isinstance(deps, str) or not isinstance(deps, Sequence):
                raise TaskConfigError(f"invalid dependencies for {sid}")
            deps = tuple(str(d).strip() if isinstance(d, str) else d for d in deps)
            if sid in deps:
                raise TaskConfigError(f"step cannot depend on itself: {sid}")
            if any(not isinstance(d, str) or not d.strip() for d in deps):
                raise TaskConfigError(f"invalid dependency for {sid}")
            metadata = raw.get("metadata", {})
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, Mapping):
                raise TaskConfigError(f"metadata for {sid} must be a mapping")
            evidence = raw.get("evidence", ())
            if evidence is None:
                evidence = ()
            if isinstance(evidence, Mapping):
                unknown = set(evidence) - {"all"}
                if unknown:
                    raise TaskConfigError(
                        f"unknown evidence schema for {sid}: {sorted(unknown)}"
                    )
                evidence = evidence.get("all", ())
            if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
                raise TaskConfigError(f"evidence for {sid} must be a list")
            compiled_evidence = []
            budget = [0]
            for predicate in evidence:
                compiled_evidence.append(
                    self._validate_predicate(predicate, sid, depth=0, budget=budget)
                )
            raw_prompts = raw.get("prompts", raw.get("target_prompts", ())) or ()
            if isinstance(raw_prompts, str):
                raw_prompts = (raw_prompts,)
            if not isinstance(raw_prompts, Sequence):
                raise TaskConfigError(f"prompts for {sid} must be a list")
            if any(
                not isinstance(prompt, str) or not prompt.strip()
                for prompt in raw_prompts
            ):
                raise TaskConfigError(
                    f"prompts for {sid} must contain non-empty strings"
                )
            specs.append(
                TaskStep(
                    sid,
                    deps,
                    dict(metadata),
                    tuple(compiled_evidence),
                    tuple(prompt.strip() for prompt in raw_prompts),
                )
            )
            ids.add(sid)
        for s in specs:
            missing = set(s.depends_on) - ids
            if missing:
                raise TaskConfigError(
                    f"dangling dependency for {s.id}: {sorted(missing)}"
                )
        by_id = {s.id: s for s in specs}
        remaining = {s.id: set(s.depends_on) for s in specs}
        ordered = []
        while remaining:
            ready = [s.id for s in specs if s.id in remaining and not remaining[s.id]]
            if not ready:
                raise TaskConfigError("task dependencies contain a cycle")
            for sid in ready:
                ordered.append(by_id[sid])
                remaining.pop(sid)
                for deps in remaining.values():
                    deps.discard(sid)
        prompts = config.get("detector_prompts", config.get("prompts", ()))
        if not prompts and isinstance(detector, Mapping):
            prompts = detector.get("prompts", ())
        if isinstance(prompts, str):
            prompts = (prompts,)
        if not prompts:
            prompts = allowed_classes
        if isinstance(prompts, (str, bytes)) or not isinstance(prompts, Sequence):
            raise TaskConfigError("detector_prompts must be a list")
        if any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts):
            raise TaskConfigError("detector_prompts entries must be non-empty strings")
        return CompiledTask(
            task_id,
            int(version),
            tuple(ordered),
            allowed_classes,
            tuple(prompt.strip() for prompt in prompts),
            aliases,
        )

    def compile_file(self, path: str | Path) -> CompiledTask:
        with Path(path).open(encoding="utf-8") as f:
            return self.compile(yaml.safe_load(f))


def compile_task_config(config: Mapping[str, Any]) -> CompiledTask:
    return TaskConfigCompiler().compile(config)


def load_task_config(path: str | Path) -> CompiledTask:
    return TaskConfigCompiler().compile_file(path)
