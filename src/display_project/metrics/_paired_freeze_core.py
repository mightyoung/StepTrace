"""Development-only freeze rules for paired video metrics.

This module deliberately treats a failed or incomplete primary attempt as an
ineligible unit while retaining every attempt in the emitted audit payload.
It is not a statistical results runner and refuses pilot/formal profiles.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

from display_project.data.experiment_identity import (
    is_reserved_non_fixture_identity,
)

from .paired import BRANCH_A, BRANCH_B, common_support, freeze_operating_point

METHODS = (
    "fixed_streak",
    "hmm_or_crf",
    "vlm_only",
    "yolo",
    "grounding_dino",
    "no_calibration_certificate",
    "proposed",
)
PRIMARY_METHODS = ("proposed", "fixed_streak", "hmm_or_crf")
FAILURE_STATUSES = {
    "crash",
    "timeout",
    "oom",
    "unreadable",
    "missing_frames",
    "manual_intervention",
    "partial",
    "unavailable",
    "host_interrupted",
}
ABSTAIN = {
    None,
    "",
    "abstain",
    "unknown",
    "insufficient evidence",
    "\u4e0d\u786e\u5b9a",
    "\u8bc1\u636e\u4e0d\u8db3",
}

_HEX = set("0123456789abcdef")


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _event_metrics(
    events: Sequence[Mapping[str, Any]], *, fps: float | None, end_frame: int
) -> dict[str, Any]:
    scorable = definitive = emitted = false = 0
    observed: list[float] = []
    censored: list[float] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("event must be an object")
        scorable_value = event.get("scorable", True)
        if not isinstance(scorable_value, bool):
            raise ValueError("scorable must be boolean")
        if scorable_value is False:
            continue
        scorable += 1
        decision = event.get("decision", event.get("event"))
        if decision is not None and not isinstance(decision, str):
            raise ValueError("decision must be string or null")
        definitive_value = event.get("definitive", True)
        if not isinstance(definitive_value, bool):
            raise ValueError("definitive must be boolean")
        if decision not in ABSTAIN and definitive_value is not False:
            definitive += 1
        if "emitted_advance" not in event:
            raise ValueError("each scorable event requires explicit emitted_advance")
        emitted_flag = event["emitted_advance"]
        if not isinstance(emitted_flag, bool):
            raise ValueError("emitted_advance must be boolean")
        if emitted_flag:
            emitted += 1
            validity = event.get(
                "valid_advance", event.get("valid", event.get("true_advance"))
            )
            if not isinstance(validity, bool):
                raise ValueError("emitted advance requires explicit boolean validity")
            if validity is False or event.get("false_advance") is True:
                false += 1
        start = event.get("candidate_frame")
        confirmed = event.get("confirmed_frame")
        for name, value in (("candidate_frame", start), ("confirmed_frame", confirmed)):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number or null")
        if confirmed is not None and start is None:
            raise ValueError("confirmed_frame requires candidate_frame")
        if start is not None:
            if confirmed is not None:
                if confirmed < start:
                    raise ValueError("confirmed_frame cannot precede candidate_frame")
                observed.append((float(confirmed) - float(start)) / (fps or 1.0))
            else:
                censored.append(
                    max(0.0, float(end_frame) - float(start)) / (fps or 1.0)
                )
    return {
        "fsar": None if emitted == 0 else false / emitted,
        "coverage": 0.0 if scorable == 0 else definitive / scorable,
        "scorable_opportunities": scorable,
        "definitive_decisions": definitive,
        "emitted_advances": emitted,
        "false_advances": false,
        "confirmation_delay": {
            "mean": (sum(observed) / len(observed)) if observed else None,
            "observed": len(observed),
            "right_censored": len(censored),
            "unit": "seconds" if fps else "frames",
            "censoring_times": censored,
        },
    }


_PLACEHOLDERS = {"", "unknown", "unk", "n/a", "na", "none", "null", "-", "?"}


def _identity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return None if text.lower() in _PLACEHOLDERS else (text or None)


def _unit_id(row: Mapping[str, Any]) -> str:
    from display_project.experiments.paired_runner import make_unit_identity

    fields = {
        key: _identity(row.get(key))
        for key in (
            "source_dataset",
            "canonical_root_id",
            "operator_id",
            "complete_job_id",
            "video_id",
        )
    }
    missing = [key for key, value in fields.items() if value is None]
    fixture_profile = row.get("profile", row.get("experiment_profile"))
    source_dataset = row.get("source_dataset")
    is_fixture = (
        isinstance(fixture_profile, str)
        and fixture_profile.strip().upper() == "SOFTWARE_FIXTURE"
    ) or (
        isinstance(source_dataset, str)
        and source_dataset.strip().upper().startswith("SOFTWARE_FIXTURE")
    )
    if not is_fixture:
        missing.extend(
            key
            for key, value in fields.items()
            if value is not None and is_reserved_non_fixture_identity(value)
        )
    if missing:
        raise ValueError(f"independent unit identity field {missing[0]} is required")
    return make_unit_identity(
        source_dataset=str(fields["source_dataset"]),
        canonical_root_id=str(fields["canonical_root_id"]),
        operator_id=str(fields["operator_id"]),
        complete_job_id=str(fields["complete_job_id"]),
        video_id=str(fields["video_id"]),
    )


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_provenance(
    rows: Sequence[Mapping[str, Any]], unit: str, task: str
) -> None:
    successful = [row for row in rows if row.get("status") == "success"]
    if successful:
        for row in successful:
            if not _is_hash(row.get("video_hash")):
                raise ValueError(
                    f"successful primary requires lowercase SHA-256 video_hash for {unit}/{task}"
                )
            expected = row.get("expected_frames")
            if (
                isinstance(expected, bool)
                or not isinstance(expected, int)
                or expected <= 0
            ):
                raise ValueError(
                    f"successful primary requires positive expected_frames for {unit}/{task}"
                )
        profiles = {_identity(row.get("profile")) for row in successful}
        if len(profiles) != 1 or None in profiles:
            raise ValueError(
                f"successful primary requires one consistent profile for {unit}/{task}"
            )
        profile = next(iter(profiles))
        if profile != "SOFTWARE_FIXTURE":
            for field in ("task_config_hash", "manifest_hash", "task_source_hash"):
                if not all(_is_hash(row.get(field)) for row in successful):
                    raise ValueError(
                        f"successful primary requires {field} for {unit}/{task}"
                    )
    fields = (
        "video_hash",
        "task_config_hash",
        "manifest_hash",
        "task_source_hash",
        "profile",
        "task_source_profile",
    )
    for field in fields:
        present = [row.get(field) for row in rows if row.get(field) is not None]
        if not present:
            continue
        if len(present) != len(rows) or any(not _identity(v) for v in present):
            raise ValueError(
                f"mixed or placeholder provenance for {unit}/{task}: {field}"
            )
        if field.endswith("_hash") and any(not _is_hash(v) for v in present):
            raise ValueError(f"invalid provenance hash for {unit}/{task}: {field}")
        if len(set(present)) != 1:
            raise ValueError(f"mixed provenance for {unit}/{task}: {field}")
    frame_values = [row.get("expected_frames") for row in rows]
    if any(
        isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in frame_values
    ):
        raise ValueError(
            f"expected_frames must be a non-negative integer for {unit}/{task}"
        )


def _task_id(row: Mapping[str, Any]) -> str:
    value = row.get("task_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("task_id is required")
    return value.strip()


def _validate_scoring(
    row: Mapping[str, Any], *, require_total: bool = False
) -> tuple[str, str, int | None]:
    scoring = row.get("scoring")
    if not isinstance(scoring, Mapping):
        raise ValueError("scorer-enriched scoring metadata is required")
    schema = scoring.get("schema_version")
    if not isinstance(schema, str) or not schema.strip():
        raise ValueError("scoring schema_version is required")
    if scoring.get("scorer_enriched") is not True:
        raise ValueError("scoring metadata must declare scorer_enriched=true")
    truth_hash = scoring.get("ground_truth_hash")
    if not _is_hash(truth_hash):
        raise ValueError("ground_truth_hash must be lowercase SHA-256")
    if scoring.get("runtime_annotation_used") is not False:
        raise ValueError("runtime_annotation_used must be false")
    if row.get("annotation_used", False) is not False:
        raise ValueError("runtime annotation use is forbidden")
    totals = []
    for key in ("scorable_opportunities", "total_scorable_opportunities"):
        if key in scoring:
            value = scoring[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    "scorable opportunities must be a non-negative integer"
                )
            totals.append(value)
    if len(totals) > 1 and len(set(totals)) != 1:
        raise ValueError("conflicting frozen scorable opportunities")
    total = totals[0] if totals else None
    if require_total and total is None:
        raise ValueError("frozen scorable opportunities are required")
    return schema.strip(), str(truth_hash), total


def compute_paired_metrics(
    records: Iterable[Mapping[str, Any]], *, allow_methods: Sequence[str] | None = None
) -> dict[str, Any]:
    """Compute task-level metrics while retaining every scheduled attempt."""
    rows = [dict(row) for row in records]
    required_methods = tuple(allow_methods) if allow_methods is not None else METHODS
    if set(required_methods) != set(METHODS) and allow_methods is None:
        raise ValueError("all seven runner methods are required by default")
    if not required_methods or not set(required_methods) <= set(METHODS):
        raise ValueError("invalid method allowlist")
    if not rows:
        raise ValueError("paired records are required")
    all_attempts: list[dict[str, Any]] = []
    primary: dict[tuple[str, str], Mapping[str, Any]] = {}
    unit_tasks: dict[str, str] = {}
    for raw_row in rows:
        row: Mapping[str, Any] = raw_row
        method = row.get("method")
        if method not in required_methods:
            raise ValueError(f"unsupported or missing method: {method!r}")
        unit, task = _unit_id(row), _task_id(row)
        old_task = unit_tasks.setdefault(unit, task)
        if old_task != task:
            raise ValueError(f"independent unit appears in multiple tasks: {unit}")
        status = row.get("status")
        if not isinstance(status, str):
            raise ValueError("attempt status is required")
        all_attempts.append(
            {
                "unit_id": unit,
                "task_id": task,
                "method": method,
                "status": status,
                "attempt": row.get("attempt"),
                "primary": row.get("primary", False),
                "complete_video": row.get("complete_video", False),
                "error_code": row.get("error_code"),
                "retry_of": row.get("retry_of"),
            }
        )
        if row.get("primary", False) is True:
            key = (unit, method)
            if key in primary:
                raise ValueError(f"duplicate primary attempt: {unit}/{method}")
            primary[key] = row
    units = sorted(unit_tasks)
    tasks = tuple(sorted(set(unit_tasks.values())))
    if len(tasks) != 2:
        raise ValueError("exactly two frozen assembly tasks are required")
    missing = [(u, m) for u in units for m in required_methods if (u, m) not in primary]
    if missing:
        raise ValueError(f"missing paired methods: {missing}")
    for unit in units:
        task = unit_tasks[unit]
        _validate_provenance(
            [primary[(unit, method)] for method in required_methods], unit, task
        )
    unit_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    ground_truth_hashes: set[str] = set()
    scoring_schemas: set[str] = set()
    for unit in units:
        for method in required_methods:
            row = primary[(unit, method)]
            expected, processed = (
                row.get("expected_frames"),
                row.get("frames_processed"),
            )
            complete = row.get("complete_video") is True
            failed_or_incomplete = (
                row.get("status") != "success"
                or not complete
                or not isinstance(expected, int)
                or expected <= 0
                or processed != expected
            )
            events = row.get("events")
            has_events = isinstance(events, list)
            scoring_schema = truth_hash = None
            frozen_total = None
            scoring_error = None
            if has_events or not failed_or_incomplete:
                try:
                    scoring_schema, truth_hash, frozen_total = _validate_scoring(
                        row, require_total=True
                    )
                except ValueError as exc:
                    scoring_error = str(exc)
            if scoring_error is not None:
                if not failed_or_incomplete:
                    raise ValueError(scoring_error)
                unit_metrics[(unit, method)] = {
                    "unit_id": unit,
                    "task_id": unit_tasks[unit],
                    "method": method,
                    "status": row.get("status"),
                    "complete_video": complete,
                    "endpoint_eligible": False,
                    "metric_ineligibility_reason": scoring_error,
                    "fsar": None,
                    "coverage": 0.0,
                    "scorable_opportunities": 0,
                    "definitive_decisions": 0,
                    "emitted_advances": 0,
                    "false_advances": 0,
                }
                continue
            if not has_events or scoring_schema is None or truth_hash is None:
                unit_metrics[(unit, method)] = {
                    "unit_id": unit,
                    "task_id": unit_tasks[unit],
                    "method": method,
                    "status": row.get("status"),
                    "complete_video": complete,
                    "endpoint_eligible": False,
                    "metric_ineligibility_reason": "scorer truth/events unavailable",
                    "fsar": None,
                    "coverage": 0.0,
                    "scorable_opportunities": 0,
                    "definitive_decisions": 0,
                    "emitted_advances": 0,
                    "false_advances": 0,
                }
                continue
            event_list = cast(list[Mapping[str, Any]], events)
            if frozen_total is None:
                raise ValueError("frozen scorable opportunities are required")
            observed_scorable = sum(
                1 for event in event_list if event.get("scorable", True) is not False
            )
            if frozen_total < observed_scorable:
                raise ValueError(
                    f"scorable opportunities are below observed events for {unit}/{method}"
                )
            scoring_schemas.add(scoring_schema)
            ground_truth_hashes.add(truth_hash)
            fps = row.get("source_fps", row.get("fps"))
            if fps is not None and (
                isinstance(fps, bool)
                or not isinstance(fps, (int, float))
                or not math.isfinite(float(fps))
                or fps <= 0
            ):
                raise ValueError("source_fps must be positive")
            metric = _event_metrics(
                event_list,
                fps=float(fps) if fps is not None else None,
                end_frame=max(expected - 1, 0) if isinstance(expected, int) else 0,
            )

            metric["coverage"] = (
                float(metric["definitive_decisions"]) / frozen_total
                if frozen_total
                else 0.0
            )
            metric.update(
                {
                    "unit_id": unit,
                    "task_id": unit_tasks[unit],
                    "method": method,
                    "video_id": row.get("video_id"),
                    "video_hash": row.get("video_hash", ""),
                    "status": row.get("status"),
                    "complete_video": complete,
                    "endpoint_eligible": True,
                    "scoring_schema_version": scoring_schema,
                    "ground_truth_hash": truth_hash,
                    "scorable_opportunities": frozen_total,
                }
            )
            unit_metrics[(unit, method)] = metric
    method_metrics: dict[str, list[dict[str, Any]]] = {m: [] for m in required_methods}
    for task in tasks:
        task_units = [u for u in units if unit_tasks[u] == task]
        for method in required_methods:
            cells = [unit_metrics[(u, method)] for u in task_units]
            scorable = sum(int(c.get("scorable_opportunities", 0)) for c in cells)
            definitive = sum(int(c.get("definitive_decisions", 0)) for c in cells)
            emitted = sum(int(c.get("emitted_advances", 0)) for c in cells)
            false = sum(int(c.get("false_advances", 0)) for c in cells)
            delays = [c.get("confirmation_delay", {}) for c in cells]
            observed = sum(int(d.get("observed", 0)) for d in delays)
            censored = sum(int(d.get("right_censored", 0)) for d in delays)
            delay_total = sum(
                float(d.get("mean")) * int(d.get("observed", 0))
                for d in delays
                if isinstance(d.get("mean"), (int, float))
            )
            scheduled = [
                a
                for a in all_attempts
                if a["method"] == method
                and a["task_id"] == task
                and a["primary"] is True
            ]
            failed_runs = sum(
                1
                for a in scheduled
                if str(a["status"]).strip().lower() in FAILURE_STATUSES
                or a["status"] != "success"
            )
            method_metrics[method].append(
                {
                    "task_id": task,
                    "run_failure_rate": failed_runs / len(scheduled)
                    if scheduled
                    else 0.0,
                    "scheduled_runs": len(scheduled),
                    "failed_runs": failed_runs,
                    "independent_units": [c["unit_id"] for c in cells],
                    "unit_count": len(cells),
                    "fsar": None if emitted == 0 else false / emitted,
                    "coverage": 0.0 if scorable == 0 else definitive / scorable,
                    "scorable_opportunities": scorable,
                    "definitive_decisions": definitive,
                    "emitted_advances": emitted,
                    "false_advances": false,
                    "confirmation_delay": {
                        "mean": (delay_total / observed) if observed else None,
                        "observed": observed,
                        "right_censored": censored,
                        "unit": "seconds"
                        if any(d.get("unit") == "seconds" for d in delays)
                        else "frames",
                    },
                }
            )
    primary_attempts = [a for a in all_attempts if a["primary"] is True]
    total_scheduled = len(primary_attempts)
    total_failed = sum(
        1
        for a in primary_attempts
        if str(a["status"]).strip().lower() in FAILURE_STATUSES
        or a["status"] != "success"
    )
    primary_failures = [
        a
        for a in all_attempts
        if a["primary"] and (a["status"] != "success" or not a["complete_video"])
    ]
    return {
        "units": units,
        "tasks": tasks,
        "methods": method_metrics,
        "all_attempts": all_attempts,
        "primary_attempt_count": len(primary),
        "all_attempt_count": len(all_attempts),
        "primary_failures": primary_failures,
        "endpoint_ineligible": [
            {
                "unit_id": metric["unit_id"],
                "task_id": metric["task_id"],
                "method": metric["method"],
                "reason": metric.get("metric_ineligibility_reason", "unknown"),
            }
            for metric in unit_metrics.values()
            if metric.get("endpoint_eligible") is False
        ],
        "run_failure_rate": total_failed / total_scheduled if total_scheduled else 0.0,
        "scheduled_runs": total_scheduled,
        "failed_runs": total_failed,
        "ground_truth_hashes": sorted(ground_truth_hashes),
        "scoring_schema_versions": sorted(scoring_schemas),
    }


@dataclass(frozen=True)
class FreezeArtifact:
    payload: dict[str, Any]
    sha256: str


def freeze_paired_metrics(
    records: Iterable[Mapping[str, Any]],
    *,
    input_ledger_hash: str,
    operating_points: Mapping[str, Iterable[Mapping[str, Any]]],
    allow_methods: Sequence[str] | None = None,
    profile: str = "development",
    fixed_confirm_frames: int = 4,
    fixed_cooldown_frames: int = 12,
    expected_artifact_hash: str | None = None,
    branch: str = BRANCH_A,
    target: float = 0.5,
    delta_c: float = 0.0,
    delta_r: float = 0.0,
    min_support_width: float = 0.0,
    min_coverage: float = 0.5,
    min_units_per_task: int = 1,
) -> FreezeArtifact:
    if profile != "development":
        raise ValueError("paired metric freezing is development-only")
    if not _is_hash(input_ledger_hash):
        raise ValueError("input ledger hash must be lowercase SHA-256")
    if (
        not isinstance(fixed_confirm_frames, int)
        or isinstance(fixed_confirm_frames, bool)
        or not isinstance(fixed_cooldown_frames, int)
        or isinstance(fixed_cooldown_frames, bool)
        or (fixed_confirm_frames, fixed_cooldown_frames) != (4, 12)
    ):
        raise ValueError("fixed streak baseline is frozen at 4/12")
    metrics = compute_paired_metrics(records, allow_methods=allow_methods)
    if metrics["endpoint_ineligible"]:
        reasons = "; ".join(
            "{}/{}: {}".format(
                item.get("unit_id"), item.get("method"), item.get("reason")
            )
            for item in metrics["endpoint_ineligible"]
        )
        raise ValueError(f"cannot freeze metrics: {reasons}")
    tasks = metrics["tasks"]
    if set(operating_points) != {f"{m}::{t}" for m in PRIMARY_METHODS for t in tasks}:
        raise ValueError("operating points must cover every method/task cell")
    if branch not in {BRANCH_A, BRANCH_B}:
        raise ValueError("unsupported branch")
    for name, value in (
        ("target", target),
        ("delta_c", delta_c),
        ("delta_r", delta_r),
        ("min_support_width", min_support_width),
        ("min_coverage", min_coverage),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"{name} must be finite")
    if not 0.0 <= float(target) <= 1.0:
        raise ValueError("target must be in [0, 1]")
    if delta_c < 0 or delta_r < 0 or min_support_width < 0:
        raise ValueError("matching tolerances and support width must be non-negative")
    if not 0.0 <= float(min_coverage) <= 1.0:
        raise ValueError("min_coverage must be in [0, 1]")
    if (
        not isinstance(min_units_per_task, int)
        or isinstance(min_units_per_task, bool)
        or min_units_per_task < 1
    ):
        raise ValueError("min_units_per_task must be positive")
    if any(
        sum(
            1
            for u in metrics["units"]
            if any(
                x["task_id"] == t and u in x["independent_units"]
                for x in metrics["methods"][METHODS[0]]
            )
        )
        < min_units_per_task
        for t in tasks
    ):
        raise ValueError("minimum independent units per task is not met")
    normalized = {k: list(v) for k, v in operating_points.items()}
    support = common_support(
        cast(Mapping[Any, Iterable[Any]], normalized),
        frozen_tasks=tasks,
        required_cells=6,
        min_width=min_support_width,
        min_coverage=min_coverage,
    )
    if support is None:
        raise ValueError("common support is unavailable")
    op = freeze_operating_point(
        cast(Mapping[Any, Iterable[Mapping[str, object]]], normalized),
        frozen_tasks=tasks,
        branch=branch,
        target=target,
        delta_c=delta_c,
        delta_r=delta_r,
        min_support_width=min_support_width,
        min_coverage=min_coverage,
    )
    if not op.eligible or op.support is None:
        raise ValueError(f"operating point is ineligible: {op.reason}")
    payload: dict[str, Any] = {
        "schema_version": "paired-metrics-freeze-1.0.0",
        "profile": profile,
        "input_ledger_hash": input_ledger_hash,
        "scorer_truth_boundary": "ground truth is admitted only by scorer-enriched derived ledger; runtime annotation use remains forbidden",
        "ground_truth_hashes": metrics["ground_truth_hashes"],
        "scoring_schema_versions": metrics["scoring_schema_versions"],
        "units": metrics["units"],
        "tasks": tasks,
        "methods": tuple(metrics["methods"]),
        "primary_comparison_methods": PRIMARY_METHODS,
        "metrics": metrics["methods"],
        "all_attempts": metrics["all_attempts"],
        "attempt_accounting": {
            "primary": metrics["primary_attempt_count"],
            "all": metrics["all_attempt_count"],
            "failure_estimand": "primary scheduled attempt failure rate; retries remain in all_attempts and recovery accounting",
            "primary_failures": len(metrics["primary_failures"]),
            "run_failure_rate": metrics["run_failure_rate"],
            "scheduled_runs": metrics["scheduled_runs"],
            "failed_runs": metrics["failed_runs"],
            "failure_statuses_retained": sorted(FAILURE_STATUSES),
        },
        "common_support": {
            "lower": support[0],
            "upper": support[1],
            "cells": sorted(normalized),
        },
        "operating_point_freeze": {
            "branch": branch,
            "target": target,
            "delta_c": delta_c,
            "delta_r": delta_r,
            "min_support_width": min_support_width,
            "min_coverage": min_coverage,
            "min_units_per_task": min_units_per_task,
            "directional_rule": "A: lower Proposed FSAR at matched coverage; B: higher Proposed coverage at matched FSAR; both require every comparator pair",
            "abstention_guard": "each cell coverage >= min_coverage; zero-emission FSAR is undefined and ineligible",
            "thresholds_by_cell": op.thresholds_by_cell,
            "threshold_semantics_by_cell": op.threshold_semantics_by_cell,
            "achieved_by_cell": op.achieved_by_cell,
            "tie_break": op.tie_break,
        },
        "metric_definitions": {
            "fsar": "false emitted advances / all emitted advances; zero-emission is undefined",
            "coverage": "definitive non-abstained decisions / scorable opportunities",
            "confirmation_delay": "candidate to first confirmation; unconfirmed right-censored at video end",
        },
        "confirmation_delay_unit": "seconds when source_fps is present, otherwise frames",
        "fixed_streak_baseline": {"confirm_frames": 4, "cooldown_frames": 12},
        "operating_points": normalized,
        "operating_points_canonical_hash": _hash(normalized),
    }
    digest = _hash(payload)
    if expected_artifact_hash is not None:
        if not _is_hash(expected_artifact_hash):
            raise ValueError("expected artifact hash must be lowercase SHA-256")
        if digest != expected_artifact_hash:
            raise ValueError("artifact hash does not match external expected hash")
    return FreezeArtifact(payload, digest)


def write_freeze_artifact(artifact: FreezeArtifact, path: str | Path) -> None:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite frozen artifact: {target}")
    envelope = dict(artifact.payload)
    envelope["sha256"] = artifact.sha256
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        handle.flush()
        import os

        os.fsync(handle.fileno())


def verify_freeze_artifact(
    data: bytes | str | Mapping[str, Any], *, expected_hash: str | None = None
) -> bool:
    if isinstance(data, Mapping):
        obj = dict(data)
    else:
        raw = data.decode("utf-8") if isinstance(data, bytes) else data
        if raw.startswith("\ufeff") or "\r" in raw:
            return False
        try:
            obj = json.loads(raw)
        except (TypeError, ValueError, UnicodeError):
            return False
    digest = obj.pop("sha256", None)
    if not _is_hash(digest) or _hash(obj) != digest:
        return False
    if expected_hash is not None and not _is_hash(expected_hash):
        return False
    return expected_hash is None or digest == expected_hash
