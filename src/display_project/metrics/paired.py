"""Frozen paired estimands and fail-closed operating-point rules."""

from __future__ import annotations
from dataclasses import dataclass, field
import math
from typing import Any, Hashable, Iterable, Mapping, Sequence, cast

PRIMARY_METHODS = ("proposed", "fixed_streak", "hmm_or_crf")
BRANCH_A = "A_FSAR_AT_MATCHED_COVERAGE"
BRANCH_B = "B_COVERAGE_AT_MATCHED_FSAR"


def compute_fsar(
    predicted_advances: Iterable[bool] | None = None,
    true_advances: Iterable[bool] | None = None,
    *,
    false_advances: int | None = None,
    emitted_advances: int | None = None,
) -> float:
    """False emitted advances / all emitted advances; vectors are emitted and validity masks."""
    if false_advances is not None or emitted_advances is not None:
        if (
            false_advances is None
            or emitted_advances is None
            or isinstance(false_advances, bool)
            or isinstance(emitted_advances, bool)
            or not isinstance(false_advances, int)
            or not isinstance(emitted_advances, int)
            or not math.isfinite(float(false_advances))
            or not math.isfinite(float(emitted_advances))
            or false_advances < 0
            or emitted_advances < 0
            or false_advances > emitted_advances
        ):
            raise ValueError("invalid advancement counts")
        return (
            float("nan") if emitted_advances == 0 else false_advances / emitted_advances
        )
    if predicted_advances is None or true_advances is None:
        raise ValueError("aligned emitted and validity masks are required")
    emitted = list(predicted_advances)
    valid = list(true_advances)
    if len(emitted) != len(valid):
        raise ValueError("emitted and validity masks must align")
    denominator = sum(bool(x) for x in emitted)
    false_count = sum(bool(e) and not bool(v) for e, v in zip(emitted, valid))
    return float("nan") if denominator == 0 else false_count / denominator


def coverage(
    decisions: Iterable[object],
    *,
    abstain_values=(
        None,
        "abstain",
        "ABSTAIN",
        "unknown",
        "UNKNOWN",
        "insufficient evidence",
    ),
) -> float:
    values = list(decisions)
    return (
        0.0
        if not values
        else sum(v not in abstain_values for v in values) / len(values)
    )


def confirmation_delay(
    events: Sequence[Mapping[str, object]],
    *,
    start_key: str = "candidate_frame",
    confirm_key: str = "confirmed_frame",
    source_fps: float | None = None,
    video_end_frame: float | None = None,
    return_censoring: bool = False,
) -> float | None | dict[str, Any]:
    if source_fps is not None and source_fps <= 0:
        raise ValueError("source_fps must be positive")
    observed = []
    starts = []
    divisor = source_fps or 1.0
    for event in events:
        start, confirmed = event.get(start_key), event.get(confirm_key)
        if not isinstance(start, (int, float)):
            continue
        if isinstance(confirmed, (int, float)):
            observed.append(max(0.0, float(confirmed) - float(start)) / divisor)
        else:
            starts.append(float(start))
    if starts and video_end_frame is None:
        raise ValueError("video_end_frame is required for right-censored transitions")
    censor_end = float(video_end_frame) if video_end_frame is not None else 0.0
    censoring = [max(0.0, censor_end - s) / divisor for s in starts]
    mean = (
        None
        if not observed and not starts
        else (sum(observed) / len(observed) if observed else float("nan"))
    )
    if return_censoring:
        result: dict[str, Any] = {
            "mean_delay": mean,
            "observed": len(observed),
            "right_censored": len(censoring),
            "censoring_times": censoring,
        }
        if source_fps is not None:
            result["unit"] = "seconds"
        return result
    return mean


def _method(value: object) -> str | None:
    return {
        "proposed": "proposed",
        "fixed_streak": "fixed_streak",
        "hmm_or_crf": "hmm_or_crf",
        "hmm": "hmm_or_crf",
        "crf": "hmm_or_crf",
    }.get(str(value).strip().lower())


def _cell(key: Hashable) -> tuple[str, str] | None:
    if isinstance(key, tuple) and len(key) == 2:
        method = _method(key[0])
        task = str(key[1]).strip()
        return (method, task) if method and task else None
    if isinstance(key, str):
        for separator in ("::", "|", "/"):
            if separator in key:
                raw, task = key.split(separator, 1)
                method = _method(raw)
                task = task.strip()
                return (method, task) if method and task else None
    return None


def _cells(
    values: Mapping[Hashable, Iterable[Any]], frozen_tasks: Sequence[str] | None
) -> tuple[dict[tuple[str, str], Iterable[Any]], tuple[str, str]] | None:
    parsed = {}
    for raw, points in values.items():
        key = _cell(raw)
        if key is None or key in parsed:
            return None
        parsed[key] = list(points)
    tasks = (
        tuple(sorted({task for _, task in parsed}))
        if frozen_tasks is None
        else tuple(str(t).strip() for t in frozen_tasks)
    )
    if len(tasks) != 2 or len(set(tasks)) != 2 or any(not task for task in tasks):
        return None
    expected = {(m, t) for m in PRIMARY_METHODS for t in tasks}
    return (parsed, (tasks[0], tasks[1])) if set(parsed) == expected else None


def _valid(values: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            return []
        threshold = value.get("threshold")
        semantic = value.get("threshold_semantic")
        direction = value.get("conservative_direction")
        c = value.get("coverage")
        r = value.get("fsar")
        emitted = value.get("emitted_advances")
        if not (
            isinstance(c, (int, float))
            and not isinstance(c, bool)
            and math.isfinite(float(c))
            and 0.0 <= float(c) <= 1.0
            and isinstance(r, (int, float))
            and not isinstance(r, bool)
            and math.isfinite(float(r))
            and 0.0 <= float(r) <= 1.0
            and isinstance(emitted, int)
            and not isinstance(emitted, bool)
            and emitted > 0
            and isinstance(threshold, (int, float))
            and not isinstance(threshold, bool)
            and math.isfinite(float(threshold))
            and 0.0 <= float(threshold) <= 1.0
            and isinstance(semantic, str)
            and isinstance(direction, str)
            and (semantic, direction)
            in {
                ("risk_max_allowed", "lower"),
                ("confidence", "higher"),
                ("non_tunable", "non_tunable"),
            }
        ):
            continue
        result.append(
            {
                "coverage": float(c),
                "fsar": float(r),
                "emitted_advances": emitted,
                "threshold": float(threshold),
                "threshold_semantic": semantic,
                "conservative_direction": direction,
            }
        )
    return result


def common_support(
    values_by_cell: Mapping[Hashable, Iterable[Any]],
    *,
    frozen_tasks: Sequence[str] | None = None,
    min_width: float = 0.0,
    required_cells: int = 6,
    min_coverage: float | None = None,
) -> tuple[float, float] | None:
    if (
        required_cells != 6
        or not isinstance(min_width, (int, float))
        or isinstance(min_width, bool)
        or not math.isfinite(float(min_width))
        or min_width < 0
        or (
            min_coverage is not None
            and (
                not isinstance(min_coverage, (int, float))
                or isinstance(min_coverage, bool)
                or not math.isfinite(float(min_coverage))
                or not 0.0 <= float(min_coverage) <= 1.0
            )
        )
    ):
        return None
    normalized = _cells(values_by_cell, frozen_tasks)
    if normalized is None:
        return None
    ranges = []
    for values in normalized[0].values():
        raw_values = list(values)
        valid = _valid(raw_values)
        if len(valid) != len(raw_values):
            return None
        semantics = {
            (p["threshold_semantic"], p["conservative_direction"]) for p in valid
        }
        if len(semantics) != 1:
            return None
        if (
            next(iter(semantics))[1] == "non_tunable"
            and len({p["threshold"] for p in valid}) != 1
        ):
            return None
        if min_coverage is not None:
            valid = [point for point in valid if point["coverage"] >= min_coverage]
        if not valid:
            return None
        lower = min(p["coverage"] for p in valid)
        upper = max(p["coverage"] for p in valid)
        ranges.append((lower, upper))
    lower = max(v[0] for v in ranges)
    upper = min(v[1] for v in ranges)
    return (lower, upper) if upper >= lower and upper - lower >= min_width else None


@dataclass(frozen=True)
class OperatingPoint:
    threshold: float | None
    branch: str
    tie_break: tuple[str, str] = ("LOWER_ESTIMATED_FSAR", "MORE_CONSERVATIVE_THRESHOLD")
    eligible: bool = True
    reason: str = ""
    support: tuple[float, float] | None = None
    thresholds_by_cell: dict[str, float] = field(default_factory=dict)
    achieved_by_cell: dict[str, dict[str, float]] = field(default_factory=dict)
    threshold_semantics_by_cell: dict[str, dict[str, str]] = field(default_factory=dict)


def _conservative_key(point: Mapping[str, Any]) -> float:
    direction = point["conservative_direction"]
    threshold = float(point["threshold"])
    if direction == "lower":
        return threshold
    if direction == "higher":
        return -threshold
    return 0.0


def _ineligible(branch: str, reason: str) -> OperatingPoint:
    return OperatingPoint(None, branch, eligible=False, reason=reason)


def freeze_operating_point(
    points: Mapping[Hashable, Iterable[Mapping[str, object]]]
    | Iterable[Mapping[str, object]],
    *,
    frozen_tasks: Sequence[str] | None = None,
    branch: str = BRANCH_A,
    target: float | None = None,
    delta_c: float = 0.0,
    delta_r: float = 0.0,
    common_support_interval: tuple[float, float] | None = None,
    min_support_width: float = 0.0,
    min_coverage: float | None = None,
    manifest_hash: str | None = None,
    frozen_manifest_hash: str | None = None,
) -> OperatingPoint:
    if branch not in {BRANCH_A, BRANCH_B}:
        raise ValueError("unsupported branch")
    if not isinstance(points, Mapping):
        return _ineligible(branch, "all six method-task cells are required")
    normalized = _cells(points, frozen_tasks)
    if normalized is None:
        return _ineligible(branch, "exact primary 3-method by 2-task cells required")
    cells, tasks = normalized
    if target is None:
        return _ineligible(branch, "frozen target is required")
    if delta_c < 0 or delta_r < 0:
        return _ineligible(branch, "matching tolerances must be non-negative")
    support = common_support(
        cast(Mapping[Hashable, Iterable[Any]], cells),
        frozen_tasks=tasks,
        min_width=min_support_width,
        min_coverage=min_coverage,
    )
    field_name = "coverage" if branch == BRANCH_A else "fsar"
    tolerance = delta_c if branch == BRANCH_A else delta_r
    # Keep pairwise tolerance violations observable even when cell ranges do
    # not intersect. This remains fail-closed while making the frozen rule
    # deterministic for diagnostics.
    preliminary = {}

    def preliminary_key(point: Mapping[str, Any]) -> tuple[float, float, float]:
        if branch == BRANCH_A:
            return (
                abs(point["coverage"] - target),
                point["fsar"],
                _conservative_key(point),
            )
        return (
            abs(point["fsar"] - target),
            -point["coverage"],
            _conservative_key(point),
        )

    for cell, values in cells.items():
        candidates = [
            p
            for p in _valid(values)
            if abs(p[field_name] - target) <= tolerance
            and (min_coverage is None or p["coverage"] >= min_coverage)
        ]
        if candidates:
            preliminary[cell] = min(candidates, key=preliminary_key)
    if len(preliminary) == 6:
        for task in tasks:
            proposed = preliminary[("proposed", task)][field_name]
            for comparator in ("fixed_streak", "hmm_or_crf"):
                if (
                    abs(proposed - preliminary[(comparator, task)][field_name])
                    > tolerance
                ):
                    return _ineligible(
                        branch,
                        f"pairwise {field_name} mismatch for {comparator}/{task}",
                    )
    if support is None:
        return _ineligible(branch, "six-cell common support is unavailable")
    if common_support_interval is not None and support != common_support_interval:
        return _ineligible(branch, "common-support manifest mismatch")
    if branch == BRANCH_A and not support[0] <= target <= support[1]:
        return _ineligible(branch, "coverage target lies outside common support")
    if frozen_manifest_hash is not None and manifest_hash != frozen_manifest_hash:
        return _ineligible(branch, "operating-point manifest hash mismatch")
    selected = {}
    for cell, values in cells.items():
        candidates = [
            p
            for p in _valid(values)
            if abs(p[field_name] - target) <= tolerance
            and (min_coverage is None or p["coverage"] >= min_coverage)
        ]
        if not candidates:
            return _ineligible(branch, f"cell {cell!r} has no point within tolerance")
        selected[cell] = min(candidates, key=preliminary_key)
    for task in tasks:
        proposed = selected[("proposed", task)][field_name]
        for comparator in ("fixed_streak", "hmm_or_crf"):
            if abs(proposed - selected[(comparator, task)][field_name]) > tolerance:
                return _ineligible(
                    branch, f"pairwise {field_name} mismatch for {comparator}/{task}"
                )
    thresholds = {f"{m}::{t}": p["threshold"] for (m, t), p in selected.items()}
    achieved = {
        f"{m}::{t}": {"coverage": p["coverage"], "fsar": p["fsar"]}
        for (m, t), p in selected.items()
    }
    semantics = {
        f"{m}::{t}": {
            "threshold_semantic": p["threshold_semantic"],
            "conservative_direction": p["conservative_direction"],
        }
        for (m, t), p in selected.items()
    }
    unique = set(thresholds.values())
    scalar = next(iter(unique)) if len(unique) == 1 else None
    return OperatingPoint(
        scalar,
        branch,
        eligible=True,
        support=support,
        thresholds_by_cell=thresholds,
        achieved_by_cell=achieved,
        threshold_semantics_by_cell=semantics,
        tie_break=("LOWER_ESTIMATED_FSAR", "MORE_CONSERVATIVE_THRESHOLD")
        if branch == BRANCH_A
        else ("HIGHER_ESTIMATED_COVERAGE", "MORE_CONSERVATIVE_THRESHOLD"),
    )
