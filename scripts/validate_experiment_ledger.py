"""Fail-closed validation for paired attempt JSONL ledgers."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from collections import defaultdict
from scripts.ledger_anchor import validate_anchor
from display_project.experiments.paired_runner import (
    METHOD_NAMES,
    STATUSES,
    _VLM_AUTHORITY_KEYS,
    _contains_non_authoritative_evidence,
    _event_authority_error,
    verify_ledger_integrity,
    validate_registration_records,
    _decode_video,
    _hash,
    _valid_frame_trace,
    make_unit_identity,
)


_VLM_EVENT_AUTHORITY_KEYS = frozenset(
    {
        *_VLM_AUTHORITY_KEYS,
        "authorization",
        "state_transition",
        "rollback",
        "override",
        "transition",
        "state_write",
        "certificate",
        "advance",
    }
)


def _nonempty(value: object) -> bool:
    """Match the runner's fail-closed non-empty semantics for JSON values."""
    return value not in (None, [], {}, 0, False, "")


def _validate_vlm_boundary(row: dict, line_number: int, errors: list[str]) -> None:
    """Reject authority-bearing VLM ledger payloads for every terminal status."""
    if row.get("method") != "vlm_only":
        return

    # Runner output is retained as a mapping containing status/trace metadata;
    # inspect only authority-bearing members rather than rejecting valid traces.
    containers = [(row, "row")]
    output = row.get("output")
    if isinstance(output, dict):
        containers.append((output, "output"))
    elif _nonempty(output):
        errors.append(f"line {line_number}: VLM-only output must be empty or an object")

    for container, label in containers:
        for key in _VLM_AUTHORITY_KEYS:
            if key in container and _nonempty(container.get(key)):
                errors.append(
                    f"line {line_number}: VLM-only {label}.{key} contains authority output"
                )

    for event in [*row.get("events", []), *row.get("partial_events", [])]:
        if (
            isinstance(event, dict)
            and event.get("evidence_source") in {"vlm", "vlm_audit"}
            and (
                event.get("decision") != "unknown"
                or event.get("observed_step") is not None
            )
        ):
            errors.append(
                f"line {line_number}: VLM evidence cannot provide a state decision"
            )
        if not isinstance(event, dict):
            errors.append(f"line {line_number}: VLM event schema/authority violation")
            continue
        if (
            event.get("decision") != "unknown"
            or "observed_step" not in event
            or event.get("observed_step") is not None
            or event.get("evidence_source") not in {"vlm", "vlm_audit"}
        ):
            errors.append(f"line {line_number}: VLM event schema/authority violation")
        for key in _VLM_EVENT_AUTHORITY_KEYS:
            if key in event and _nonempty(event.get(key)):
                errors.append(
                    f"line {line_number}: VLM event contains authority field {key}"
                )


REQUIRED = {
    "video_id",
    "source_dataset",
    "canonical_root_id",
    "canonical_group_id",
    "unit_identity",
    "method",
    "status",
    "attempt",
    "first_attempt",
    "primary",
    "retry_of",
    "lineage_id",
    "task_id",
    "operator_id",
    "complete_job_id",
    "video_hash",
    "expected_frames",
    "frames_processed",
    "complete_video",
    "code_hash",
    "config_hash",
    "model_hash",
    "environment_hash",
    "annotation_used",
    "annotation_hash",
    "partition_hash",
    "manifest_hash",
    "task_config_hash",
    "task_source_hash",
    "experiment_profile",
    "seed",
    "randomization",
    "started_at",
    "elapsed_s",
    "resources",
    "events",
    "partial_events",
    "error",
    "error_code",
    "schedule_id",
    "schedule",
}

ALLOWED_ROW_FIELDS = REQUIRED | {
    "output",
    "record_kind",
    "registration_id",
    "attempt_identity",
    "source_video_path",
    "artifact_paths",
    "effective_policy",
    "scorer_metadata",
    "vlm_authority_verified",
    "scoring",
    "authority_boundary",
    "authorization_events",
    "state_writes",
    "certificates",
    "ledger_integrity",
}


def _validate_non_authority_containers(
    row: dict, line_number: int, errors: list[str]
) -> None:
    """Scan enriched payload containers for audit-only evidence."""
    containers = {
        "output": row.get("output"),
        "artifact_paths": row.get("artifact_paths"),
        "scorer_metadata": row.get("scorer_metadata"),
        "scoring": row.get("scoring"),
        "authorization_events": row.get("authorization_events"),
        "certificates": row.get("certificates"),
    }
    output = containers["output"]
    if isinstance(output, dict):
        output = dict(output)
        for field in ("events", "partial_events", "frame_trace"):
            output.pop(field, None)
        containers["output"] = output
    nested_scoring = None
    metadata = containers["scorer_metadata"]
    if isinstance(metadata, dict):
        metadata = dict(metadata)
        nested_scoring = metadata.pop("scoring", None)
        containers["scorer_metadata"] = metadata
    scoring = containers["scoring"]
    for label, value in (
        ("scorer_metadata.scoring", nested_scoring),
        ("scoring", scoring),
    ):
        if isinstance(value, dict):
            value = dict(value)
            truth_hash = value.pop("ground_truth_hash", None)
            if truth_hash is not None and (
                not isinstance(truth_hash, str)
                or not __import__("re").fullmatch(r"[0-9a-f]{64}", truth_hash)
                or not set(truth_hash) - {"0"}
            ):
                errors.append(
                    f"line {line_number}: {label}.ground_truth_hash is invalid"
                )
            containers[label] = value
        elif value is not None:
            containers[label] = value
    for label, value in containers.items():
        if value is not None and _contains_non_authoritative_evidence(
            value, _reject_root_source=label != "artifact_paths"
        ):
            errors.append(
                f"line {line_number}: {label} contains annotation/truth/dataset or unknown evidence"
            )


def validate(
    path: str | Path,
    *,
    expected_methods: list[str] | None = None,
    require_hashes: bool = False,
    anchor_path: str | Path | None = None,
    expected_anchor_hash: str | None = None,
) -> list[str]:
    errors = []
    if anchor_path is None and expected_anchor_hash is not None:
        errors.append("expected anchor hash requires --anchor")
    if anchor_path is not None:
        if expected_anchor_hash is None:
            errors.append("ledger anchor requires an externally frozen expected hash")
        errors.extend(
            validate_anchor(
                path, anchor_path, expected_anchor_hash=expected_anchor_hash
            )
        )
    rows = []
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return [f"ledger unreadable: {e}"]
    integrity_errors, _tail = verify_ledger_integrity(p.read_bytes())
    errors.extend(integrity_errors)
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception as e:
            errors.append(f"line {i}: invalid JSON ({e})")
            continue
        if not isinstance(row, dict):
            errors.append(f"line {i}: row must be object")
            continue
        rows.append((i, row))
        unknown = set(row) - ALLOWED_ROW_FIELDS
        if unknown:
            errors.append(f"line {i}: unknown top-level fields {sorted(unknown)}")
        missing = REQUIRED - set(row)
        if missing:
            errors.append(f"line {i}: missing fields {sorted(missing)}")
        # Non-fixture outcomes must be bound to an append-only registration record.
        # Keep software fixtures explicitly exempt, but never infer fixture status.
        is_fixture = row.get("experiment_profile") == "SOFTWARE_FIXTURE"
        if not is_fixture:
            if row.get("record_kind") != "attempt_outcome":
                errors.append(f"line {i}: non-fixture record_kind must be attempt_outcome")
            for field in ("registration_id", "attempt_identity", "source_video_path"):
                value = row.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"line {i}: non-fixture {field} must be a non-empty string")
            for field in ("artifact_paths", "scorer_metadata"):
                if not isinstance(row.get(field), dict) or not row[field]:
                    errors.append(f"line {i}: non-fixture {field} binding is required")
        string_fields = (
            "video_id",
            "source_dataset",
            "canonical_root_id",
            "canonical_group_id",
            "unit_identity",
            "method",
            "lineage_id",
            "task_id",
            "operator_id",
            "complete_job_id",
            "schedule_id",
        )
        for field in string_fields:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"line {i}: {field} must be a non-empty string")
        method = row.get("method")
        if not isinstance(method, str) or method not in METHOD_NAMES:
            errors.append(f"line {i}: unknown method")
        if row.get("status") not in STATUSES:
            errors.append(f"line {i}: invalid status")
        schedule = row.get("schedule")
        if (
            not isinstance(schedule, list)
            or not schedule
            or any(not isinstance(item, str) or not item.strip() for item in schedule)
            or len(schedule) != len(set(schedule))
        ):
            errors.append(f"line {i}: schedule must contain unique non-empty strings")
        attempt = row.get("attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int):
            attempt = 0
        if attempt < 1:
            errors.append(f"line {i}: attempt must be positive")
        if (
            not isinstance(row.get("first_attempt"), bool)
            or not isinstance(row.get("primary"), bool)
            or row.get("first_attempt") != (attempt == 1)
            or row.get("primary") != (attempt == 1)
        ):
            errors.append(f"line {i}: primary/first-attempt mismatch")
        if attempt == 1 and row.get("retry_of") not in (None, 0):
            errors.append(f"line {i}: first attempt cannot retry another attempt")
        if attempt > 1 and row.get("retry_of") != attempt - 1:
            errors.append(
                f"line {i}: retry chain must point to immediately previous attempt"
            )
        for key in (
            "video_hash",
            "code_hash",
            "config_hash",
            "model_hash",
            "environment_hash",
            "annotation_hash",
            "partition_hash",
            "manifest_hash",
            "task_config_hash",
            "task_source_hash",
        ):
            value = row.get(key)
            if value and (
                not isinstance(value, str)
                or not __import__("re").fullmatch(r"[0-9a-f]{64}", value)
            ):
                errors.append(
                    f"line {i}: {key} must be lowercase SHA-256 hex when non-empty"
                )
        if not isinstance(row.get("experiment_profile"), str):
            errors.append(f"line {i}: experiment_profile must be a string")
        if require_hashes:
            for key in (
                "video_hash",
                "code_hash",
                "config_hash",
                "model_hash",
                "environment_hash",
                "annotation_hash",
                "partition_hash",
            ):
                if not isinstance(row.get(key), str) or not row.get(key):
                    errors.append(f"line {i}: missing frozen {key}")
        resources = row.get("resources")
        if not isinstance(resources, dict) or resources.get("schema_version") != 1:
            errors.append(f"line {i}: resources schema_version=1 required")
        else:
            for key in (
                "wall_time_s",
                "frames_processed",
                "child_pid",
                "termination",
                "cpu",
                "rss",
                "gpu",
            ):
                if key not in resources:
                    errors.append(f"line {i}: resources missing {key}")
            term = resources.get("termination")
            if (
                not isinstance(term, dict)
                or not isinstance(term.get("kind"), str)
                or not isinstance(term.get("kill_requested"), bool)
                or "observed_at" not in term
                or "exit_code" not in term
                or "reason" not in term
            ):
                errors.append(f"line {i}: resources termination invalid")
            if (
                row.get("status") == "host_interrupted"
                and isinstance(term, dict)
                and term.get("kind") != "host_interrupted"
            ):
                errors.append(
                    f"line {i}: host_interrupted requires matching termination kind"
                )
            if (
                row.get("status") == "missing_frames"
                and isinstance(term, dict)
                and term.get("kind") != "missing_frames"
            ):
                errors.append(
                    f"line {i}: missing_frames requires matching termination kind"
                )
            for key in ("cpu", "rss", "gpu"):
                metric = resources.get(key)
                if (
                    not isinstance(metric, dict)
                    or metric.get("status")
                    not in {"measured", "unavailable", "not_applicable"}
                    or not isinstance(metric.get("reason"), str)
                ):
                    errors.append(f"line {i}: resources {key} invalid")
        if not isinstance(row.get("events"), list) or not isinstance(
            row.get("partial_events"), list
        ):
            errors.append(f"line {i}: events and partial_events must be lists")
        expected = row.get("expected_frames")
        frames = row.get("frames_processed")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            errors.append(f"line {i}: expected_frames must be a non-negative integer")
        if isinstance(frames, bool) or not isinstance(frames, int) or frames < 0:
            errors.append(f"line {i}: frames_processed must be a non-negative integer")
        elif isinstance(expected, int) and frames > expected:
            errors.append(f"line {i}: frames_processed exceeds expected_frames")
        if row.get("annotation_used") is not False:
            errors.append(f"line {i}: annotation_used must be explicitly false")
        output = row.get("output")
        output_authority: object
        if isinstance(output, dict):
            output_authority = dict(output)
            for field in ("events", "partial_events", "frame_trace"):
                output_authority.pop(field, None)
        else:
            output_authority = output
        if _contains_non_authoritative_evidence(
            output_authority, _reject_root_source=True
        ):
            errors.append(
                f"line {i}: annotation/truth/dataset/VLM or unknown evidence cannot provide authority"
            )
        event_rows = list(row["events"]) if isinstance(row.get("events"), list) else []
        partial_rows = (
            list(row["partial_events"])
            if isinstance(row.get("partial_events"), list)
            else []
        )
        for event in [*event_rows, *partial_rows]:
            if not isinstance(event, dict):
                errors.append(f"line {i}: event entries must be objects")
                continue
            authority_error = _event_authority_error(
                event, method=str(row.get("method", ""))
            )
            if authority_error is not None:
                errors.append(f"line {i}: {authority_error[0]}")
        if not isinstance(row.get("complete_video"), bool):
            errors.append(f"line {i}: complete_video must be boolean")
        video_hash = row.get("video_hash")
        if not isinstance(video_hash, str) or (video_hash and len(video_hash) != 64):
            errors.append(f"line {i}: video_hash must be a SHA-256 hex string or empty")
        if row.get("status") == "success":
            if (
                row.get("frames_processed") != row.get("expected_frames")
                or row.get("complete_video") is not True
            ):
                errors.append(
                    f"line {i}: success requires complete_video=true and all expected frames"
                )
        elif not isinstance(row.get("error_code"), str) or not row.get("error_code"):
            errors.append(f"line {i}: non-success rows require error_code")
        _validate_non_authority_containers(row, i, errors)
        _validate_vlm_boundary(row, i, errors)
    groups = defaultdict(list)
    for i, r in rows:
        try:
            expected_unit = make_unit_identity(
                source_dataset=r.get("source_dataset"),
                canonical_root_id=r.get("canonical_root_id"),
                operator_id=r.get("operator_id"),
                complete_job_id=r.get("complete_job_id"),
                video_id=r.get("video_id"),
            )
        except ValueError as exc:
            errors.append(f"line {i}: invalid unit identity ({exc})")
            expected_unit = f"INVALID:{i}"
        if r.get("unit_identity") != expected_unit:
            errors.append(f"line {i}: unit_identity mismatch")
        groups[expected_unit].append((i, r))
    declared = next(
        (
            r.get("schedule")
            for _, r in rows
            if isinstance(r.get("schedule"), list) and r.get("schedule")
        ),
        None,
    )
    methods = set(expected_methods or METHOD_NAMES)
    if declared is not None:
        declared_valid = all(
            isinstance(item, str) and item.strip() for item in declared
        ) and len(declared) == len(set(declared))
        if not declared_valid or set(declared) != methods:
            errors.append(
                "ledger schedule must contain exactly all seven frozen methods "
                f"(got {declared})"
            )
    for vid, items in groups.items():
        schedule_ids = [r.get("schedule_id") for _, r in items]
        if (
            any(
                not isinstance(value, str) or not value.strip()
                for value in schedule_ids
            )
            or len(set(value for value in schedule_ids if isinstance(value, str))) != 1
        ):
            errors.append(f"video {vid}: schedule_id mismatch")
        method_values = [r.get("method") for _, r in items]
        got = {value for value in method_values if isinstance(value, str)}
        if any(not isinstance(value, str) for value in method_values) or got != methods:
            errors.append(
                f"video {vid}: not all paired methods present (expected {sorted(methods)}, got {sorted(got)})"
            )
        for key in (
            "video_hash",
            "expected_frames",
            "task_id",
            "operator_id",
            "complete_job_id",
            "annotation_hash",
            "partition_hash",
            "manifest_hash",
            "task_config_hash",
            "task_source_hash",
            "experiment_profile",
        ):
            vals = {json.dumps(r.get(key), sort_keys=True) for _, r in items}
            if len(vals) > 1:
                errors.append(f"video {vid}: {key} mismatch across paired methods")
        bymethod = defaultdict(list)
        for line_number, row in items:
            raw_method = row.get("method")
            method_key = (
                raw_method
                if isinstance(raw_method, str)
                else f"INVALID_METHOD_LINE_{line_number}"
            )
            bymethod[method_key].append((line_number, row))
        for method, attempts in bymethod.items():
            raw_nums = [row.get("attempt") for _, row in attempts]
            valid_nums = all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 1
                for value in raw_nums
            )
            if not valid_nums:
                errors.append(f"video {vid}/{method}: attempt values must be integers")
                continue
            nums = [
                value
                for value in raw_nums
                if isinstance(value, int) and not isinstance(value, bool)
            ]
            if len(nums) != len(set(nums)):
                errors.append(f"video {vid}/{method}: duplicate attempt")
            if sorted(nums) != list(range(1, max(nums) + 1)):
                errors.append(f"video {vid}/{method}: attempt sequence has gaps")
            if sum(row.get("primary") is True for _, row in attempts) != 1 or not any(
                row.get("attempt") == 1 and row.get("primary") is True
                for _, row in attempts
            ):
                errors.append(
                    f"video {vid}/{method}: primary must be attempt 1 exactly once"
                )
            lineage_values = [row.get("lineage_id") for _, row in attempts]
            if (
                any(
                    not isinstance(value, str) or not value.strip()
                    for value in lineage_values
                )
                or len(set(lineage_values)) != 1
            ):
                errors.append(f"video {vid}/{method}: retry lineage mismatch")
    registration_path = p.with_suffix(p.suffix + ".registration.jsonl")
    if (
        any(
            isinstance(r, dict) and r.get("record_kind") == "attempt_outcome"
            for _, r in rows
        )
        and not registration_path.is_file()
    ):
        errors.append("paired ledger registration sidecar is missing")
    if registration_path.is_file():
        try:
            registrations, _registration_tail = validate_registration_records(
                registration_path
            )
            previous_record_hash = ""
            seen_registration_ids: set[str] = set()
            allowed_registration = {
                "schema_version",
                "record_type",
                "sequence",
                "prev_hash",
                "record_hash",
                "record_kind",
                "registration_id",
                "attempt_identity",
                "video_id",
                "method",
                "attempt",
                "primary",
                "retry_of",
                "video_hash",
                "source_video_path",
                "expected_frames",
                "schedule_id",
                "schedule",
                "source_dataset",
                "canonical_root_id",
                "canonical_group_id",
                "unit_identity",
                "operator_id",
                "complete_job_id",
                "task_id",
                "model_hash",
                "config_hash",
                "effective_policy",
                "artifact_paths",
                "scorer_metadata",
                "status",
                "ledger_integrity",
            }
            for sequence, registration in enumerate(registrations):
                if (
                    not isinstance(registration, dict)
                    or set(registration) - allowed_registration
                    or registration.get("schema_version") != 1
                    or registration.get("record_type") != "schedule_registration"
                ):
                    errors.append("registration schema is invalid")
                    continue
                registration_id = registration.get("registration_id")
                attempt_identity = registration.get("attempt_identity")
                if not (
                    isinstance(registration_id, str)
                    and registration_id.strip()
                    and isinstance(attempt_identity, str)
                    and attempt_identity.strip()
                ):
                    errors.append("registration identity is invalid")
                elif (
                    registration_id in seen_registration_ids
                    or attempt_identity in seen_registration_ids
                ):
                    errors.append("registration identity is duplicated")
                else:
                    seen_registration_ids.update({registration_id, attempt_identity})
                body = dict(registration)
                supplied = body.pop("record_hash", None)
                body.pop("ledger_integrity", None)
                expected_hash = (
                    __import__("hashlib")
                    .sha256(
                        json.dumps(
                            body,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
                    .hexdigest()
                )
                if (
                    registration.get("sequence") != sequence
                    or registration.get("prev_hash") != previous_record_hash
                    or supplied != expected_hash
                ):
                    errors.append("registration hash chain is invalid")
                previous_record_hash = supplied

                if (
                    not isinstance(registration, dict)
                    or registration.get("record_kind") != "schedule_registration"
                ):
                    continue
                vid = registration["video_id"]
                schedule = registration.get("schedule")
                if not isinstance(schedule, list) or any(
                    not isinstance(item, str) or not item.strip() for item in schedule
                ):
                    errors.append("registration schedule is invalid")
                    expected_schedule_methods: set[str] = set()
                else:
                    expected_schedule_methods = set(schedule)
                observed_outcome_methods: set[str] = set()
                for _, outcome in rows:
                    if (
                        outcome.get("video_id") == vid
                        and outcome.get("record_kind", "attempt_outcome")
                        == "attempt_outcome"
                    ):
                        method_name = outcome.get("method")
                        if isinstance(method_name, str):
                            observed_outcome_methods.add(method_name)
                missing = expected_schedule_methods - observed_outcome_methods
                if missing:
                    errors.append(
                        f"video {vid}: host-interrupted failure; missing terminal outcomes {sorted(missing)}"
                    )
                matching_outcomes = [
                    outcome
                    for _, outcome in rows
                    if outcome.get("attempt_identity")
                    == registration.get("attempt_identity")
                ]
                if not matching_outcomes:
                    errors.append(
                        f"attempt {registration.get('attempt_identity')}: host-interrupted failure; terminal outcome missing"
                    )
                for outcome in matching_outcomes:
                    for field in (
                        "video_id",
                        "method",
                        "unit_identity",
                        "video_hash",
                        "expected_frames",
                        "schedule_id",
                        "source_dataset",
                        "canonical_root_id",
                        "canonical_group_id",
                        "operator_id",
                        "complete_job_id",
                        "task_id",
                        "model_hash",
                        "config_hash",
                        "source_video_path",
                        "artifact_paths",
                        "effective_policy",
                        "scorer_metadata",
                    ):
                        if outcome.get(field) != registration.get(field):
                            errors.append(
                                f"registration/outcome mismatch {field} for {registration.get('attempt_identity')}"
                            )
                    if (
                        outcome.get("status") == "host_interrupted"
                        and int(outcome.get("frames_processed", 0) or 0) > 0
                    ):
                        source = Path(registration["source_video_path"])
                        valid_source = (
                            source.is_file()
                            and _hash(source) == registration["video_hash"]
                        )
                        decoded_ok, decoded_count, decoded_hashes = (
                            _decode_video(source) if valid_source else (False, 0, [])
                        )
                        output = outcome.get("output")
                        trace = (
                            output.get("frame_trace")
                            if isinstance(output, dict)
                            else None
                        )
                        if (
                            not decoded_ok
                            or decoded_count != registration["expected_frames"]
                            or not _valid_frame_trace(
                                trace, outcome["frames_processed"], decoded_hashes
                            )
                        ):
                            errors.append(
                                f"recovered frame_trace invalid for {registration.get('attempt_identity')}"
                            )
        except (OSError, ValueError) as exc:
            errors.append(f"schedule registration unreadable: {exc}")
    return errors


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ledger")
    p.add_argument("--require-hashes", action="store_true")
    p.add_argument("--reconcile-host-interrupted", action="store_true")
    p.add_argument("--anchor")
    p.add_argument("--expected-anchor-hash")
    args = p.parse_args()
    if args.reconcile_host_interrupted:
        from display_project.experiments.paired_runner import reconcile_host_interrupted

        reconcile_host_interrupted(args.ledger)
    errors = validate(
        args.ledger,
        require_hashes=args.require_hashes,
        anchor_path=args.anchor,
        expected_anchor_hash=args.expected_anchor_hash,
    )
    if errors:
        print("FAIL")
        print("\n".join(errors))
        raise SystemExit(1)
    print("PASS")


if __name__ == "__main__":
    main()
