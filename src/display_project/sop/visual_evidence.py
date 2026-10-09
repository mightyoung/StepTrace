from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from pathlib import Path
from typing import Mapping, Sequence, TypeAlias, cast

from .task_config import CompiledTask

Predicate: TypeAlias = Mapping[str, object]
Specs: TypeAlias = dict[str, tuple[Predicate, ...]]
_TASK_CONFIG_UNSET = object()


@dataclass(frozen=True, slots=True)
class ObjectObservation:
    label: str
    confidence: float
    xyxy: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("label must be non-empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        x1, y1, x2, y2 = self.xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError("xyxy must have positive area")


@dataclass(frozen=True, slots=True)
class VisualEvidenceResult:
    candidate_step: str | None
    confirmed_step: str | None
    streak: int
    reasons: tuple[str, ...]
    metrics: dict[str, float | int]


def _area_ratio(item: ObjectObservation, width: int, height: int) -> float:
    x1, y1, x2, y2 = item.xyxy
    return ((x2 - x1) * (y2 - y1)) / float(width * height)


def _aspect_ratio(item: ObjectObservation) -> float:
    x1, y1, x2, y2 = item.xyxy
    width, height = x2 - x1, y2 - y1
    return max(width / height, height / width)


def _normalized_gap(
    left: ObjectObservation,
    right: ObjectObservation,
    width: int,
    height: int,
) -> float:
    lx1, ly1, lx2, ly2 = left.xyxy
    rx1, ry1, rx2, ry2 = right.xyxy
    dx = max(lx1 - rx2, rx1 - lx2, 0.0)
    dy = max(ly1 - ry2, ry1 - ly2, 0.0)
    return hypot(dx, dy) / hypot(width, height)


def _any_near(
    left: Sequence[ObjectObservation],
    right: Sequence[ObjectObservation],
    width: int,
    height: int,
    *,
    threshold: float = 0.08,
) -> bool:
    return any(
        _normalized_gap(a, b, width, height) <= threshold
        for a in left
        for b in right
        if a is not b
    )


class VisualEvidenceEngine:
    """Confirm ordered SOP steps from model geometry only.

    The engine has no action-annotation or absolute-frame input. It evaluates
    only the state machine's currently expected step and requires a stable
    evidence streak before returning deterministic evidence.
    """

    def __init__(
        self,
        *,
        confirm_frames: int = 4,
        cooldown_frames: int = 12,
        task_config: CompiledTask
        | Mapping[str, object]
        | str
        | Path
        | None
        | object = _TASK_CONFIG_UNSET,
        legacy_baseline: bool = False,
    ) -> None:
        omitted_task_config = task_config is _TASK_CONFIG_UNSET
        requested_legacy = legacy_baseline
        if omitted_task_config:
            task_config = None
            legacy_baseline = requested_legacy or (
                confirm_frames == 4 and cooldown_frames == 12
            )
        if confirm_frames < 1:
            raise ValueError("confirm_frames must be positive")
        if cooldown_frames < 0:
            raise ValueError("cooldown_frames must be non-negative")
        # ``None`` is reserved for the frozen historical fixed-streak trace.
        if task_config is None and not legacy_baseline and not omitted_task_config:
            raise ValueError(
                "task_config=None is reserved for the legacy fixed_streak baseline"
            )
        if (
            task_config is None
            and (not legacy_baseline)
            and (confirm_frames != 4 or cooldown_frames != 12)
        ):
            raise ValueError(
                "task_config is required outside the legacy fixed_streak baseline; "
                "use VisualEvidenceEngine.legacy_fixed_streak() for the frozen trace"
            )
        if (
            task_config is None
            and legacy_baseline
            and (confirm_frames != 4 or cooldown_frames != 12)
        ):
            raise ValueError(
                "legacy fixed_streak baseline is frozen at confirm_frames=4, cooldown_frames=12"
            )
        task_config = cast(
            CompiledTask | Mapping[str, object] | str | Path | None, task_config
        )
        self.confirm_frames, self.cooldown_frames = confirm_frames, cooldown_frames
        self._frame_index = -1
        self._streak_step = None
        self._streak = 0
        self._last_confirmation = -(10**9)
        self.legacy_baseline = task_config is None
        self._aliases = getattr(task_config, "class_aliases", {}) or {}
        self._specs = self._load_specs(task_config)

    @classmethod
    def legacy_fixed_streak(cls) -> "VisualEvidenceEngine":
        """Return the explicitly named, frozen MECCANO baseline engine."""
        return cls(legacy_baseline=True)

    @staticmethod
    def _load_specs(
        task_config: CompiledTask | Mapping[str, object] | str | Path | None,
    ) -> Specs:
        if task_config is None:
            return {
                "pick_components": (
                    {
                        "predicate": "near",
                        "left": "hand",
                        "right": "component",
                        "max_gap": 0.08,
                        "reason": "hand_component_contact",
                    },
                ),
                "align_components": (
                    {
                        "predicate": "all",
                        "predicates": [
                            {
                                "predicate": "count",
                                "label": "hand",
                                "min": 2,
                                "min_confidence": 0.35,
                            },
                            {
                                "predicate": "count",
                                "label": "component",
                                "min": 2,
                                "min_confidence": 0.22,
                            },
                            {
                                "predicate": "near",
                                "left": "component",
                                "right": "component",
                                "max_gap": 0.05,
                            },
                            {
                                "predicate": "near",
                                "left": "hand",
                                "right": "component",
                                "max_gap": 0.08,
                            },
                        ],
                    },
                ),
                "insert_screw": (
                    {
                        "predicate": "all",
                        "predicates": [
                            {
                                "predicate": "count",
                                "label": "screw",
                                "min": 1,
                                "min_confidence": 0.30,
                                "max_area_ratio": 0.025,
                                "reason": "small_fastener_missing",
                            },
                            {
                                "predicate": "near",
                                "left": "screw",
                                "right": "hand",
                                "max_gap": 0.08,
                            },
                            {
                                "predicate": "near",
                                "left": "screw",
                                "right": "component",
                                "max_gap": 0.08,
                            },
                        ],
                    },
                ),
                "insert_bolt": (
                    {
                        "predicate": "all",
                        "predicates": [
                            {
                                "predicate": "count",
                                "label": "bolt",
                                "min": 1,
                                "min_confidence": 0.30,
                                "max_area_ratio": 0.025,
                                "reason": "small_fastener_missing",
                            },
                            {
                                "predicate": "near",
                                "left": "bolt",
                                "right": "hand",
                                "max_gap": 0.08,
                            },
                            {
                                "predicate": "near",
                                "left": "bolt",
                                "right": "component",
                                "max_gap": 0.08,
                            },
                        ],
                    },
                ),
                "tighten_fastener": (
                    {
                        "predicate": "all",
                        "predicates": [
                            {
                                "predicate": "count",
                                "label": "screwdriver",
                                "min": 1,
                                "min_confidence": 0.24,
                                "max_area_ratio": 0.04,
                                "min_aspect_ratio": 1.8,
                            },
                            {
                                "predicate": "near",
                                "left": "screwdriver",
                                "right": "hand",
                                "max_gap": 0.08,
                            },
                            {
                                "predicate": "near",
                                "left": "screwdriver",
                                "right": "component",
                                "max_gap": 0.08,
                            },
                        ],
                    },
                ),
                "tighten_screw": (
                    {
                        "predicate": "all",
                        "predicates": [
                            {
                                "predicate": "count",
                                "label": "screwdriver",
                                "min": 1,
                                "min_confidence": 0.24,
                                "max_area_ratio": 0.04,
                                "min_aspect_ratio": 1.8,
                            },
                            {
                                "predicate": "near",
                                "left": "screwdriver",
                                "right": "hand",
                                "max_gap": 0.08,
                            },
                            {
                                "predicate": "near",
                                "left": "screwdriver",
                                "right": "component",
                                "max_gap": 0.08,
                            },
                        ],
                    },
                ),
            }
        if hasattr(task_config, "step_specs"):
            return {s.id: s.evidence for s in task_config.step_specs}
        from .task_config import compile_task_config, load_task_config

        if isinstance(task_config, Mapping):
            task_config = compile_task_config(task_config)
        elif isinstance(task_config, (str, Path)):
            task_config = load_task_config(task_config)
        else:
            raise TypeError("task_config must be CompiledTask, mapping, or path")
        return {s.id: s.evidence for s in task_config.step_specs}

    def _label_is(self, item, *names):
        expanded = []
        for name in names:
            expanded.extend(self._aliases.get(name, (name,)))
            if name.casefold() == "component":
                expanded.extend(
                    self._aliases.get("component", ("metal bar", "partial assembly"))
                )
        value = item.label.casefold()
        return any(str(name).casefold() in value for name in expanded)

    def observe(self, expected_step, observations, *, frame_size):
        self._frame_index += 1
        width, height = frame_size
        if width <= 0 or height <= 0:
            raise ValueError("frame_size must be positive")
        candidate, reasons, metrics = self._evaluate(
            expected_step, observations, width, height
        )
        cooldown_ready = (
            self._frame_index - self._last_confirmation >= self.cooldown_frames
        )
        if candidate is not None and cooldown_ready:
            if candidate == self._streak_step:
                self._streak += 1
            else:
                self._streak_step, self._streak = candidate, 1
        else:
            self._streak_step, self._streak = None, 0
        confirmed = None
        if candidate is not None and self._streak >= self.confirm_frames:
            confirmed = candidate
            self._last_confirmation = self._frame_index
            self._streak_step, self._streak = None, 0
        metrics["cooldown_ready"] = int(cooldown_ready)
        return VisualEvidenceResult(
            candidate, confirmed, self._streak, tuple(reasons), metrics
        )

    def _evaluate(self, expected_step, observations, width, height):
        metrics = {"observations": len(observations)}
        if expected_step is None:
            return None, ["sop_complete"], metrics
        predicates = self._specs.get(expected_step)
        if not predicates:
            return None, ["unsupported_expected_step"], metrics
        reasons: list[str] = []
        passed = self._eval_predicates(
            predicates, observations, width, height, metrics, reasons
        )
        return (expected_step if passed else None), reasons, metrics

    def _eval_predicates(
        self, predicates, observations, width, height, metrics, reasons
    ):
        ok = True
        for p in predicates:
            result = self._eval_predicate(p, observations, width, height, metrics)
            ok = result and ok
            raw_reason = p.get("reason")
            reason = raw_reason if isinstance(raw_reason, str) else None
            if reason is None and p.get("predicate", p.get("type")) in {"all", "any"}:
                failed = next(
                    (
                        c.get("reason")
                        for c in p.get("predicates", ())
                        if c.get("reason")
                        and not self._eval_predicate(
                            c, observations, width, height, metrics
                        )
                    ),
                    None,
                )
                reason = (
                    failed
                    if isinstance(failed, str)
                    else ("evidence_ok" if result else "evidence_missing")
                )
            reasons.append(
                reason
                or f"{p.get('predicate', p.get('type'))}_{'ok' if result else 'missing'}"
            )
        return ok

    def _eval_predicate(self, p, observations, width, height, metrics):
        kind = p.get("predicate", p.get("type"))
        if kind in {"all", "any"}:
            vals = [
                self._eval_predicate(x, observations, width, height, metrics)
                for x in p["predicates"]
            ]
            return all(vals) if kind == "all" else any(vals)
        if kind == "count":
            items = [
                x
                for x in observations
                if self._label_is(x, p["label"])
                and x.confidence >= p.get("min_confidence", 0.0)
            ]
            if "max_area_ratio" in p:
                items = [
                    x
                    for x in items
                    if _area_ratio(x, width, height) <= p["max_area_ratio"]
                ]
            if "min_aspect_ratio" in p:
                items = [x for x in items if _aspect_ratio(x) >= p["min_aspect_ratio"]]
            metrics[f"{p['label']}_count"] = len(items)
            return ("min" not in p or len(items) >= p["min"]) and (
                "max" not in p or len(items) <= p["max"]
            )
        if kind == "near":
            left = [
                x
                for x in observations
                if self._label_is(x, p["left"])
                and x.confidence >= p.get("left_min_confidence", 0.0)
            ]
            right = [
                x
                for x in observations
                if self._label_is(x, p["right"])
                and x.confidence >= p.get("right_min_confidence", 0.0)
            ]
            return _any_near(
                left, right, width, height, threshold=p.get("max_gap", 0.08)
            )
        return False
