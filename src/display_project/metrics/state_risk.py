"""Strict, offline-only metrics for VLM state-and-risk development runs.

The scorer intentionally receives truth as a separate file from runtime output.
It is a development utility, not a G3 scorer and never produces formal statistics.

Truth schema (version ``1.0``)::

    {"schema_version":"1.0", "evaluation_id":"dev-001",
     "no_event_category_policy":"exclude_empty_categories_from_macro_f1",
     "matching_tolerance_s":2.0, "enabled_risk_ids":["missing-part"],
     "jobs":[{"job_id":"job-1", "task_id":"task-a", "video_id":"v1",
               "duration_s":60.0}],
     "opportunities":[{"opportunity_id":"o1", "job_id":"job-1",
                         "timestamp_s":10.0, "stage_id":"fit",
                         "completion_state":"completed"}],
     "risk_events":[{"event_id":"r1", "job_id":"job-1",
                       "risk_id":"missing-part", "start_s":12.0}],
     "transitions":[{"transition_id":"t1", "job_id":"job-1",
                       "timestamp_s":10.0, "stage_id":"fit",
                       "completion_state":"completed"}]}

Predictions use the same ``evaluation_id``.  Their ``opportunities`` may be a
subset of the fixed truth opportunities: omitted rows are scored as unavailable,
never silently excluded.  Each row has ``opportunity_id``, ``job_id``,
``timestamp_s``, ``estimated_stage``, ``completion_state``, and a status of
``ok``, ``unknown``, ``timeout``, or ``failed``.  Risk rows have ``event_id``,
``job_id``, ``risk_id``, and ``timestamp_s``.  Terminal rows have ``job_id`` and
one of ``completed``, ``timeout``, ``failed``, or ``unavailable``.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0"
DEVELOPMENT_ONLY = "DEVELOPMENT_ONLY"
NO_EVENT_POLICY = "exclude_empty_categories_from_macro_f1"
_PREDICTION_STATUSES = {"ok", "unknown", "timeout", "failed"}
_TERMINAL_STATUSES = {"completed", "timeout", "failed", "unavailable"}


def _error(message: str) -> None:
    raise ValueError(message)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _error(f"{field} must be a non-empty string")
    return value.strip()


def _number(value: Any, field: str, *, nonnegative: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _error(f"{field} must be finite")
    result = float(value)
    if nonnegative and result < 0:
        _error(f"{field} must be non-negative")
    return result


def _sha256(value: Any, field: str) -> str:
    result = _string(value, field)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        _error(f"{field} must be a lowercase SHA-256")
    return result


def _rows(document: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list):
        _error(f"{key} must be a list")
    if not all(isinstance(row, Mapping) for row in value):
        _error(f"{key} must contain objects")
    return list(value)


def _unique(rows: Sequence[Mapping[str, Any]], id_key: str, field: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identifier = _string(row.get(id_key), f"{field}.{id_key}")
        if identifier in indexed:
            _error(f"duplicate {field} id: {identifier}")
        indexed[identifier] = row
    return indexed


def _header(document: Mapping[str, Any], label: str) -> str:
    if not isinstance(document, Mapping):
        _error(f"{label} must be an object")
    if document.get("schema_version") != SCHEMA_VERSION:
        _error(f"{label}.schema_version must be {SCHEMA_VERSION}")
    return _string(document.get("evaluation_id"), f"{label}.evaluation_id")


def _validate_truth(truth: Mapping[str, Any]) -> dict[str, Any]:
    evaluation_id = _header(truth, "truth")
    if truth.get("no_event_category_policy") != NO_EVENT_POLICY:
        _error(f"truth.no_event_category_policy must be {NO_EVENT_POLICY}")
    tolerance = _number(truth.get("matching_tolerance_s"), "truth.matching_tolerance_s")
    enabled = truth.get("enabled_risk_ids")
    if not isinstance(enabled, list) or not enabled:
        _error("truth.enabled_risk_ids must be a non-empty list")
    risk_ids = [_string(value, "truth.enabled_risk_ids") for value in enabled]
    if len(set(risk_ids)) != len(risk_ids):
        _error("truth.enabled_risk_ids contains duplicates")
    jobs = _unique(_rows(truth, "jobs"), "job_id", "jobs")
    for job_id, row in jobs.items():
        _string(row.get("task_id"), f"jobs[{job_id}].task_id")
        _string(row.get("video_id"), f"jobs[{job_id}].video_id")
        _number(row.get("duration_s"), f"jobs[{job_id}].duration_s")
        if "video_sha256" in row:
            _sha256(row["video_sha256"], f"jobs[{job_id}].video_sha256")
    opportunities = _unique(_rows(truth, "opportunities"), "opportunity_id", "opportunities")
    for opportunity_id, row in opportunities.items():
        job_id = _string(row.get("job_id"), f"opportunities[{opportunity_id}].job_id")
        if job_id not in jobs:
            _error(f"opportunity {opportunity_id} references unknown job {job_id}")
        timestamp = _number(row.get("timestamp_s"), f"opportunities[{opportunity_id}].timestamp_s")
        if timestamp > float(jobs[job_id]["duration_s"]):
            _error(f"opportunity {opportunity_id} is outside its job duration")
        _string(row.get("stage_id"), f"opportunities[{opportunity_id}].stage_id")
        _string(row.get("completion_state"), f"opportunities[{opportunity_id}].completion_state")
    risk_events = _unique(_rows(truth, "risk_events"), "event_id", "risk_events")
    for event_id, row in risk_events.items():
        job_id = _string(row.get("job_id"), f"risk_events[{event_id}].job_id")
        risk_id = _string(row.get("risk_id"), f"risk_events[{event_id}].risk_id")
        if job_id not in jobs or risk_id not in risk_ids:
            _error(f"risk event {event_id} references unknown job or disabled risk")
        timestamp = _number(row.get("start_s"), f"risk_events[{event_id}].start_s")
        if timestamp > float(jobs[job_id]["duration_s"]):
            _error(f"risk event {event_id} is outside its job duration")
    transitions = _unique(_rows(truth, "transitions"), "transition_id", "transitions")
    for transition_id, row in transitions.items():
        job_id = _string(row.get("job_id"), f"transitions[{transition_id}].job_id")
        if job_id not in jobs:
            _error(f"transition {transition_id} references unknown job")
        timestamp = _number(row.get("timestamp_s"), f"transitions[{transition_id}].timestamp_s")
        if timestamp > float(jobs[job_id]["duration_s"]):
            _error(f"transition {transition_id} is outside its job duration")
        _string(row.get("stage_id"), f"transitions[{transition_id}].stage_id")
        _string(row.get("completion_state"), f"transitions[{transition_id}].completion_state")
    return {"evaluation_id": evaluation_id, "jobs": jobs, "opportunities": opportunities,
            "risk_events": risk_events, "transitions": transitions, "risk_ids": risk_ids,
            "tolerance": tolerance}


def _validate_predictions(predictions: Mapping[str, Any], truth: Mapping[str, Any], index: Mapping[str, Any]) -> dict[str, Any]:
    if _header(predictions, "predictions") != index["evaluation_id"]:
        _error("predictions.evaluation_id must equal truth.evaluation_id")
    _string(predictions.get("method_id"), "predictions.method_id")
    opportunity_rows = _unique(_rows(predictions, "opportunities"), "opportunity_id", "prediction opportunities")
    for opportunity_id, row in opportunity_rows.items():
        expected = index["opportunities"].get(opportunity_id)
        if expected is None:
            _error(f"prediction opportunity {opportunity_id} is outside fixed truth universe")
        if _string(row.get("job_id"), f"prediction opportunities[{opportunity_id}].job_id") != expected["job_id"]:
            _error(f"prediction opportunity {opportunity_id} has inconsistent job_id")
        timestamp = _number(row.get("timestamp_s"), f"prediction opportunities[{opportunity_id}].timestamp_s")
        if timestamp != float(expected["timestamp_s"]):
            _error(f"prediction opportunity {opportunity_id} has inconsistent timestamp_s")
        status = _string(row.get("status"), f"prediction opportunities[{opportunity_id}].status")
        if status not in _PREDICTION_STATUSES:
            _error(f"invalid prediction status for {opportunity_id}")
        if status == "ok":
            _string(row.get("estimated_stage"), f"prediction opportunities[{opportunity_id}].estimated_stage")
            _string(row.get("completion_state"), f"prediction opportunities[{opportunity_id}].completion_state")
    risk_rows = _unique(_rows(predictions, "risk_events"), "event_id", "prediction risk_events")
    for event_id, row in risk_rows.items():
        job_id = _string(row.get("job_id"), f"prediction risk_events[{event_id}].job_id")
        risk_id = _string(row.get("risk_id"), f"prediction risk_events[{event_id}].risk_id")
        if job_id not in index["jobs"] or risk_id not in index["risk_ids"]:
            _error(f"prediction risk event {event_id} references unknown job or disabled risk")
        timestamp = _number(row.get("timestamp_s"), f"prediction risk_events[{event_id}].timestamp_s")
        if timestamp > float(index["jobs"][job_id]["duration_s"]):
            _error(f"prediction risk event {event_id} is outside its job duration")
    terminal_rows = _unique(_rows(predictions, "terminal_jobs"), "job_id", "terminal_jobs")
    if set(terminal_rows) != set(index["jobs"]):
        _error("terminal_jobs must contain exactly the fixed truth job ids")
    for job_id, row in terminal_rows.items():
        status = _string(row.get("status"), f"terminal_jobs[{job_id}].status")
        if status not in _TERMINAL_STATUSES:
            _error(f"invalid terminal status for {job_id}")
    return {"opportunities": opportunity_rows, "risk_events": risk_rows, "terminal_jobs": terminal_rows}


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return 0.0 if denominator == 0 else float(numerator) / float(denominator)


def _f1(tp: int, fp: int, fn: int) -> float:
    return _safe_div(2 * tp, 2 * tp + fp + fn)


def _joint(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row["stage_id"]), str(row["completion_state"]))


def _prediction_joint(row: Mapping[str, Any] | None) -> tuple[str, str] | None:
    if row is None or row.get("status") != "ok":
        return None
    return (str(row["estimated_stage"]), str(row["completion_state"]))


def _state_metrics(opportunities: Sequence[Mapping[str, Any]], predictions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    labels = sorted({_joint(row) for row in opportunities})
    counts = {label: {"tp": 0, "fp": 0, "fn": 0} for label in labels}
    certain = wrong_certain = unknown = missing = 0
    for truth_row in opportunities:
        truth_label = _joint(truth_row)
        predicted = _prediction_joint(predictions.get(str(truth_row["opportunity_id"])))
        if predicted is None:
            counts[truth_label]["fn"] += 1
            if str(truth_row["opportunity_id"]) in predictions:
                unknown += 1
            else:
                missing += 1
            continue
        certain += 1
        if predicted == truth_label:
            counts[truth_label]["tp"] += 1
        else:
            wrong_certain += 1
            counts[truth_label]["fn"] += 1
            if predicted in counts:
                counts[predicted]["fp"] += 1
    macro = sum(_f1(**count) for count in counts.values()) / len(counts) if counts else 0.0
    total = len(opportunities)
    return {"joint_macro_f1": macro, "labels_scored": len(labels), "opportunities": total,
            "certain": certain, "unknown": unknown, "missing": missing,
            "coverage": _safe_div(certain, total), "unknown_rate": _safe_div(unknown + missing, total),
            "wrong_certain": wrong_certain, "wrong_certain_rate": _safe_div(wrong_certain, certain)}


def _match_events(truth_rows: Sequence[Mapping[str, Any]], prediction_rows: Sequence[Mapping[str, Any]], *, truth_time: str, prediction_time: str, tolerance: float) -> tuple[list[tuple[Mapping[str, Any], Mapping[str, Any]]], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Return a maximum-cardinality valid matching, then minimize clock error.

    A nearest-first greedy loop can consume the only usable prediction for a
    later event.  This successive-shortest-augmenting-path implementation uses
    reverse residual edges, so it can revise an earlier pairing to retain every
    valid one-to-one match.  It is deliberately local to a single job/type
    group; event identifiers are never used as a proxy for temporal order.
    """
    truth_rows = list(truth_rows)
    prediction_rows = list(prediction_rows)
    truth_count, prediction_count = len(truth_rows), len(prediction_rows)
    source, truth_start = 0, 1
    prediction_start = truth_start + truth_count
    sink = prediction_start + prediction_count
    graph: list[list[dict[str, Any]]] = [[] for _ in range(sink + 1)]

    def add_edge(start: int, end: int, cost: float) -> None:
        forward = {"to": end, "reverse": len(graph[end]), "capacity": 1, "cost": cost}
        backward = {"to": start, "reverse": len(graph[start]), "capacity": 0, "cost": -cost}
        graph[start].append(forward)
        graph[end].append(backward)

    for truth_index in range(truth_count):
        add_edge(source, truth_start + truth_index, 0.0)
    for prediction_index in range(prediction_count):
        add_edge(prediction_start + prediction_index, sink, 0.0)
    for truth_index, truth_row in enumerate(truth_rows):
        for prediction_index, prediction_row in enumerate(prediction_rows):
            delta = abs(float(truth_row[truth_time]) - float(prediction_row[prediction_time]))
            if delta <= tolerance:
                add_edge(truth_start + truth_index, prediction_start + prediction_index, delta)

    # Bellman-Ford is appropriate here: reverse residual edges have negative
    # cost.  Exhausting augmenting paths maximizes cardinality; shortest paths
    # select the least-clock-error solution among those maximum matchings.
    while True:
        distance = [math.inf] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = 0.0
        for _ in range(len(graph) - 1):
            changed = False
            for start, edges in enumerate(graph):
                if not math.isfinite(distance[start]):
                    continue
                for edge_index, edge in enumerate(edges):
                    if edge["capacity"] and distance[start] + edge["cost"] < distance[edge["to"]]:
                        distance[edge["to"]] = distance[start] + edge["cost"]
                        previous[edge["to"]] = (start, edge_index)
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node != source:
            predecessor = previous[node]
            if predecessor is None:
                raise RuntimeError("incomplete matching path")
            start, edge_index = predecessor
            edge = graph[start][edge_index]
            edge["capacity"] -= 1
            graph[node][edge["reverse"]]["capacity"] += 1
            node = start

    matched: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    used_truth: set[int] = set()
    used_predictions: set[int] = set()
    for truth_index in range(truth_count):
        for edge in graph[truth_start + truth_index]:
            prediction_index = edge["to"] - prediction_start
            if 0 <= prediction_index < prediction_count and edge["capacity"] == 0:
                matched.append((truth_rows[truth_index], prediction_rows[prediction_index]))
                used_truth.add(truth_index)
                used_predictions.add(prediction_index)
                break
    return (
        matched,
        [row for index, row in enumerate(truth_rows) if index not in used_truth],
        [row for index, row in enumerate(prediction_rows) if index not in used_predictions],
    )


def _risk_metrics(jobs: Mapping[str, Mapping[str, Any]], truth_events: Mapping[str, Mapping[str, Any]], prediction_events: Mapping[str, Mapping[str, Any]], risk_ids: Sequence[str], tolerance: float) -> dict[str, Any]:
    grouped_truth: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    grouped_predictions: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in truth_events.values():
        grouped_truth[(str(row["job_id"]), str(row["risk_id"]))].append(row)
    for row in prediction_events.values():
        grouped_predictions[(str(row["job_id"]), str(row["risk_id"]))].append(row)
    totals = {risk: {"tp": 0, "fp": 0, "fn": 0} for risk in risk_ids}
    fp_total = 0
    fn_total = 0
    latencies = []
    for job_id in jobs:
        for risk_id in risk_ids:
            matched, missed, false = _match_events(grouped_truth[(job_id, risk_id)], grouped_predictions[(job_id, risk_id)], truth_time="start_s", prediction_time="timestamp_s", tolerance=tolerance)
            totals[risk_id]["tp"] += len(matched)
            totals[risk_id]["fp"] += len(false)
            totals[risk_id]["fn"] += len(missed)
            fp_total += len(false)
            fn_total += len(missed)
            latencies.extend(float(pred["timestamp_s"]) - float(actual["start_s"]) for actual, pred in matched)
    included = [risk for risk in risk_ids if sum(totals[risk].values()) > 0]
    macro = sum(_f1(**totals[risk]) for risk in included) / len(included) if included else 0.0
    hours = sum(float(row["duration_s"]) for row in jobs.values()) / 3600.0
    return {"event_macro_f1": macro, "macro_categories": len(included), "no_event_category_policy": NO_EVENT_POLICY,
            "tp": sum(value["tp"] for value in totals.values()), "fp": fp_total, "fn": fn_total,
            "false_alarms_per_video_hour": _safe_div(fp_total, hours), "matched_latency_count": len(latencies),
            "mean_detection_latency_s": _safe_div(sum(latencies), len(latencies)), "missed_risk_events": fn_total}


def _transition_metrics(transitions: Mapping[str, Mapping[str, Any]], opportunities: Mapping[str, Mapping[str, Any]], predictions: Mapping[str, Mapping[str, Any]], tolerance: float) -> dict[str, Any]:
    candidates: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for opportunity_id, truth_row in opportunities.items():
        prediction = predictions.get(opportunity_id)
        if _prediction_joint(prediction) is not None:
            candidate = {"opportunity_id": opportunity_id, "timestamp_s": truth_row["timestamp_s"], "stage_id": prediction["estimated_stage"], "completion_state": prediction["completion_state"]}
            key = (str(truth_row["job_id"]), str(candidate["stage_id"]), str(candidate["completion_state"]))
            candidates[key].append(candidate)
    grouped_truth: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in transitions.values():
        key = (str(row["job_id"]), str(row["stage_id"]), str(row["completion_state"]))
        grouped_truth[key].append(row)
    matched = 0
    missed = 0
    errors = []
    for key, rows in grouped_truth.items():
        pairs, absent, _ = _match_events(rows, candidates[key], truth_time="timestamp_s", prediction_time="timestamp_s", tolerance=tolerance)
        matched += len(pairs)
        missed += len(absent)
        errors.extend(float(pred["timestamp_s"]) - float(actual["timestamp_s"]) for actual, pred in pairs)
    return {"transition_truth_count": len(transitions), "matched_transitions": matched, "missed_transitions": missed,
            "mean_transition_error_s": _safe_div(sum(errors), len(errors)), "mean_absolute_transition_error_s": _safe_div(sum(abs(value) for value in errors), len(errors))}


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        _error(f"run export is missing {path.name}")
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            _error(f"{path.name} line {line_number} is invalid JSON: {error.msg}")
        if not isinstance(value, Mapping):
            _error(f"{path.name} line {line_number} must be an object")
        rows.append(value)
    return rows


def predictions_from_run_dir(
    truth: Mapping[str, Any], run_dir: str | Path, *, job_id: str, method_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    index = _validate_truth(truth)
    selected_job_id = _string(job_id, "job_id")
    selected_method_id = _string(method_id, "method_id")
    if selected_job_id not in index["jobs"]:
        _error("job_id must identify a truth job")
    directory = Path(run_dir)
    manifest_path = directory / "manifest.json"
    estimates_path = directory / "estimates.jsonl"
    if not manifest_path.is_file():
        _error("run export is missing manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _error(f"manifest.json is invalid JSON: {error.msg}")
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("terminal"), Mapping):
        _error("manifest.json must contain terminal")
    terminal = manifest["terminal"]
    if _string(terminal.get("method_id"), "manifest.terminal.method_id") != selected_method_id:
        _error("manifest method_id must equal --method-id")
    source_hash = _sha256(terminal.get("source_sha256"), "manifest.terminal.source_sha256")
    expected_source_hash = index["jobs"][selected_job_id].get("video_sha256")
    source_binding: dict[str, Any]
    if expected_source_hash is None:
        source_binding = {"status": "NOT_DECLARED_IN_TRUTH"}
    else:
        expected_source_hash = _sha256(expected_source_hash, f"jobs[{selected_job_id}].video_sha256")
        if source_hash != expected_source_hash:
            _error("manifest source_sha256 does not match truth job video_sha256")
        source_binding = {"status": "MATCHED", "video_sha256": source_hash}

    opportunity_by_time: dict[float, Mapping[str, Any]] = {}
    for opportunity in index["opportunities"].values():
        if opportunity["job_id"] == selected_job_id:
            timestamp = float(opportunity["timestamp_s"])
            if timestamp in opportunity_by_time:
                _error("truth opportunities for selected job must have unique timestamp_s for run export")
            opportunity_by_time[timestamp] = opportunity

    adapted_opportunities: list[dict[str, Any]] = []
    risk_events: list[dict[str, Any]] = []
    matched_opportunities: set[str] = set()
    previous_triggered = {risk_id: False for risk_id in index["risk_ids"]}
    unmatched_estimates = 0
    for ordinal, row in enumerate(_read_jsonl(estimates_path)):
        decision_time = _number(row.get("decision_time"), f"estimates[{ordinal}].decision_time")
        available_time = _number(row.get("available_time"), f"estimates[{ordinal}].available_time")
        if available_time < decision_time:
            _error("estimate available_time cannot precede decision_time")
        status = _string(row.get("status"), f"estimates[{ordinal}].status")
        if status not in {"ok", "failed"}:
            _error("estimate status must be ok or failed")
        inference = row.get("inference") if status == "ok" else None
        if status == "ok" and not isinstance(inference, Mapping):
            _error("ok estimate must contain inference object")
        opportunity = opportunity_by_time.get(decision_time)
        if opportunity is None:
            unmatched_estimates += 1
        else:
            opportunity_id = str(opportunity["opportunity_id"])
            if opportunity_id in matched_opportunities:
                _error("multiple estimates map to one fixed truth opportunity")
            matched_opportunities.add(opportunity_id)
            adapted = {"opportunity_id": opportunity_id, "job_id": selected_job_id,
                       "timestamp_s": decision_time, "status": "failed"}
            if status == "ok":
                estimated_stage = inference.get("estimated_stage")
                completion_state = inference.get("completion_state")
                if isinstance(estimated_stage, str) and estimated_stage.strip() and isinstance(completion_state, str) and completion_state.strip() and completion_state != "unknown":
                    adapted.update(status="ok", estimated_stage=estimated_stage,
                                   completion_state=completion_state)
                else:
                    adapted["status"] = "unknown"
            adapted_opportunities.append(adapted)

        assessments = inference.get("risk_assessments") if isinstance(inference, Mapping) else None
        assessment_by_id: dict[str, Mapping[str, Any]] = {}
        if assessments is not None:
            if not isinstance(assessments, list) or not all(isinstance(item, Mapping) for item in assessments):
                _error("inference.risk_assessments must be a list of objects")
            for assessment in assessments:
                risk_id = _string(assessment.get("rule_id"), "risk_assessment.rule_id")
                if risk_id not in previous_triggered:
                    _error("risk assessment references disabled risk")
                if risk_id in assessment_by_id:
                    _error("duplicate risk assessment rule_id in estimate")
                assessment_status = _string(assessment.get("status"), "risk_assessment.status")
                if assessment_status not in {"triggered", "not_triggered", "unknown", "not_applicable"}:
                    _error("invalid risk assessment status")
                assessment_by_id[risk_id] = assessment
        for risk_id in index["risk_ids"]:
            assessment = assessment_by_id.get(risk_id)
            triggered = assessment is not None and assessment.get("status") == "triggered"
            if triggered and not previous_triggered[risk_id]:
                risk_events.append({"event_id": f"run-{ordinal:06d}-{risk_id}", "job_id": selected_job_id,
                                    "risk_id": risk_id, "timestamp_s": available_time})
            previous_triggered[risk_id] = triggered

    runner_status = _string(terminal.get("status"), "manifest.terminal.status")
    selected_terminal_status = "completed" if runner_status in {"completed", "completed_with_failures"} else "failed"
    terminal_jobs = [
        {"job_id": truth_job_id, "status": selected_terminal_status if truth_job_id == selected_job_id else "unavailable"}
        for truth_job_id in index["jobs"]
    ]
    predictions = {"schema_version": SCHEMA_VERSION, "evaluation_id": index["evaluation_id"],
                   "method_id": selected_method_id, "opportunities": adapted_opportunities,
                   "risk_events": risk_events, "terminal_jobs": terminal_jobs}
    metadata = {"run_dir": str(directory.resolve()), "estimates_sha256": sha256_file(estimates_path),
                "manifest_sha256": sha256_file(manifest_path), "source_binding": source_binding,
                "matched_opportunities": len(matched_opportunities), "unmatched_estimates": unmatched_estimates}
    return predictions, metadata


def score_state_risk(truth: Mapping[str, Any], predictions: Mapping[str, Any]) -> dict[str, Any]:
    index = _validate_truth(truth)
    predicted = _validate_predictions(predictions, truth, index)
    per_job = {}
    for job_id in index["jobs"]:
        job_opportunities = [row for row in index["opportunities"].values() if row["job_id"] == job_id]
        per_job[job_id] = _state_metrics(job_opportunities, predicted["opportunities"])
        per_job[job_id]["terminal_status"] = predicted["terminal_jobs"][job_id]["status"]
    aggregate_state = _state_metrics(list(index["opportunities"].values()), predicted["opportunities"])
    aggregate_state["joint_macro_f1"] = _safe_div(
        sum(row["joint_macro_f1"] for row in per_job.values()), len(per_job)
    )
    aggregate_state["aggregation_unit"] = "complete_job"
    risk = _risk_metrics(index["jobs"], index["risk_events"], predicted["risk_events"], index["risk_ids"], index["tolerance"])
    transitions = _transition_metrics(index["transitions"], index["opportunities"], predicted["opportunities"], index["tolerance"])
    terminal = predicted["terminal_jobs"]
    statuses = {status: sum(row["status"] == status for row in terminal.values()) for status in sorted(_TERMINAL_STATUSES)}
    return {"status": DEVELOPMENT_ONLY, "formal_statistics": "NOT_RUN", "schema_version": SCHEMA_VERSION,
            "evaluation_id": index["evaluation_id"], "method_id": predictions["method_id"],
            "fixed_opportunity_universe": len(index["opportunities"]), "complete_job_units": len(index["jobs"]),
            "per_job": per_job, "state": aggregate_state, "risk": risk, "transitions": transitions,
            "terminal_availability": {"completed_jobs": statuses["completed"], "total_jobs": len(terminal),
                                      "availability": _safe_div(statuses["completed"], len(terminal)), "statuses": statuses}}


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_file(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()
