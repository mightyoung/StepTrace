"""Public paired-runner API with explicit provenance-safe compatibility boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from display_project.experiments import _paired_runner_core as _core


AUTHORIZING_EVIDENCE_SOURCES = _core.AUTHORIZING_EVIDENCE_SOURCES
LEDGER_INTEGRITY_VERSION = _core.LEDGER_INTEGRITY_VERSION
METHOD_NAMES = _core.METHOD_NAMES
REGISTRATION_FIELDS = _core.REGISTRATION_FIELDS
STATUSES = _core.STATUSES
FixedStreakAdapter = _core.FixedStreakAdapter
FixedStreakConfig = _core.FixedStreakConfig
MethodAdapter = _core.MethodAdapter
MethodSpec = _core.MethodSpec
PairedRun = _core.PairedRun
UnavailableMethodAdapter = _core.UnavailableMethodAdapter
_EMPTY_AUTHORITY_VALUES = _core._EMPTY_AUTHORITY_VALUES
_EVENT_EVIDENCE_SOURCES = _core._EVENT_EVIDENCE_SOURCES
_FORBIDDEN_AUTHORITY_KEY_TOKENS = _core._FORBIDDEN_AUTHORITY_KEY_TOKENS
_NON_AUTHORITY_KEYS = _core._NON_AUTHORITY_KEYS
_VLM_AUTHORITY_KEYS = _core._VLM_AUTHORITY_KEYS
_VLM_EVIDENCE_SOURCES = _core._VLM_EVIDENCE_SOURCES
_artifact_paths = _core._artifact_paths
_attempt_artifacts = _core._attempt_artifacts
_canonical_json = _core._canonical_json
_classify_exit = _core._classify_exit
_clean_identity = _core._clean_identity
_contains_non_authoritative_evidence = _core._contains_non_authoritative_evidence
_decode_video = _core._decode_video
_event_authority_error = _core._event_authority_error
_hash = _core._hash
_invoke = _core._invoke
_normalise_child_result = _core._normalise_child_result
_read_partial_jsonl = _core._read_partial_jsonl
_recover_partial_artifacts = _core._recover_partial_artifacts
_resource_schema = _core._resource_schema
_valid_frame_trace = _core._valid_frame_trace
_valid_hash = _core._valid_hash
_validate_timeout = _core._validate_timeout
append_jsonl_atomic = _core.append_jsonl_atomic
default_method_adapters = _core.default_method_adapters
fixed_streak_adapter = _core.fixed_streak_adapter
make_unit_identity = _core.make_unit_identity
validate_registration_candidate = _core.validate_registration_candidate
validate_registration_records = _core.validate_registration_records
validate_g3_token_claim_records = _core.validate_g3_token_claim_records
verify_ledger_integrity = _core.verify_ledger_integrity


@dataclass
class AttemptRecord(_core.AttemptRecord):
    """Fixture-friendly record that preserves the core dataclass type contract."""

    experiment_profile: str = "SOFTWARE_FIXTURE"


class PairedExperimentRunner(_core.PairedExperimentRunner):
    """Bind the experiment profile into every registration before execution."""

    def __init__(
        self,
        methods: Mapping[str, Callable[..., Any]] | Sequence[MethodSpec],
        *,
        timeout_s: float = 3600,
        ledger_path: str | Path | None = None,
        retries: int = 0,
        frozen_schedule: Sequence[str] | None = None,
        schedule_id: str = "",
        metadata: Mapping[str, Any] | None = None,
        capability: str | Path | None = None,
        admission_receipt: str | Path | None = None,
    ) -> None:
        normalized_metadata = dict(metadata or {})
        raw_profile = normalized_metadata.get("experiment_profile", "SOFTWARE_FIXTURE")
        if not isinstance(raw_profile, str) or not raw_profile.strip():
            raise ValueError("experiment_profile must be a non-empty string")
        profile = raw_profile.strip()
        scorer_metadata = dict(normalized_metadata.get("scorer_metadata", {}))
        registered_profile = scorer_metadata.get("experiment_profile")
        if "experiment_profile" in scorer_metadata:
            if (
                not isinstance(registered_profile, str)
                or not registered_profile.strip()
            ):
                raise ValueError(
                    "scorer_metadata.experiment_profile must be a non-empty string"
                )
            if registered_profile.strip() != profile:
                raise ValueError(
                    "scorer_metadata.experiment_profile conflicts with experiment_profile"
                )
        scorer_metadata["experiment_profile"] = profile
        for key in (
            "code_hash",
            "environment_hash",
            "annotation_hash",
            "partition_hash",
            "manifest_hash",
            "task_config_hash",
            "task_source_hash",
        ):
            value = normalized_metadata.get(key)
            if isinstance(value, str) and value:
                scorer_metadata[key] = value
        normalized_metadata["experiment_profile"] = profile
        normalized_metadata["scorer_metadata"] = scorer_metadata
        super().__init__(
            methods,
            timeout_s=timeout_s,
            ledger_path=ledger_path,
            retries=retries,
            frozen_schedule=frozen_schedule,
            schedule_id=schedule_id,
            metadata=normalized_metadata,
            capability=capability,
            admission_receipt=admission_receipt,
        )


def _recovery_profile(
    registrations: Sequence[Mapping[str, Any]],
) -> tuple[str, set[str]]:
    profiles: set[str] = set()
    legacy_fixture_identities: set[str] = set()
    for registration in registrations:
        scorer = registration.get("scorer_metadata")
        scorer_mapping = scorer if isinstance(scorer, Mapping) else {}
        raw_profile = scorer_mapping.get("experiment_profile")
        if isinstance(raw_profile, str) and raw_profile.strip():
            profiles.add(raw_profile.strip())
            continue
        if (
            registration.get("model_hash") == ""
            and registration.get("config_hash") == ""
            and not scorer_mapping
        ):
            profiles.add("SOFTWARE_FIXTURE")
            legacy_fixture_identities.add(str(registration["attempt_identity"]))
            continue
        raise ValueError(
            "non-fixture recovery registration requires "
            "scorer_metadata.experiment_profile"
        )
    if len(profiles) != 1:
        raise ValueError(
            "recovery registrations require one consistent experiment profile"
        )
    return next(iter(profiles)), legacy_fixture_identities


def reconcile_host_interrupted(ledger_path: str | Path) -> int:
    """Recover registered attempts without mutating process-global core symbols."""

    ledger = Path(ledger_path)
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    registrations, _tail_hash = validate_registration_records(sidecar)
    outcomes = (
        [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if ledger.is_file()
        else []
    )
    existing = {
        row.get("attempt_identity") for row in outcomes if isinstance(row, Mapping)
    }
    missing = [
        row
        for row in registrations
        if row.get("record_kind") == "schedule_registration"
        and row.get("attempt_identity") not in existing
    ]
    if not missing:
        return 0
    profile, legacy_fixture_identities = _recovery_profile(missing)

    added = 0
    for registration in missing:
        scorer_metadata = dict(registration.get("scorer_metadata", {}))
        source_path = Path(str(registration.get("source_video_path", "")))
        source_valid = source_path.is_file() and _hash(source_path) == registration.get(
            "video_hash"
        )
        decoded_valid, decoded_count, source_hashes = (
            _decode_video(source_path) if source_valid else (False, 0, [])
        )
        source_valid = (
            source_valid
            and decoded_valid
            and decoded_count == registration.get("expected_frames")
        )
        artifacts = (
            registration.get("artifact_paths")
            if isinstance(registration.get("artifact_paths"), dict)
            else None
        )
        recovered_events: list[dict[str, Any]] = []
        recovered_refs: list[str] = []
        recovered_frames = 0
        if artifacts is not None and source_valid:
            (
                recovered_events,
                _partial,
                recovered_frames,
                _recovery_error,
                _recovery_code,
                recovered_refs,
            ) = _recover_partial_artifacts(
                artifacts,
                decoded_hashes=source_hashes,
                expected_input_hash=str(registration.get("video_hash", "")),
                expected_model_hash=str(registration.get("model_hash", "")),
                expected_config_hash=str(registration.get("config_hash", "")),
            )

        attempt_identity = str(registration["attempt_identity"])
        output: dict[str, Any] = {
            "artifact_refs": recovered_refs,
            "frame_trace": [
                {
                    "frame": index,
                    "source_frame_sha256": event["source_frame_sha256"],
                }
                for index, event in enumerate(recovered_events)
            ],
            "annotation_used": False,
            "vlm_authority_verified": True,
            "authority_boundary": "runner_verified_no_annotation_or_truth_authority",
        }
        if attempt_identity in legacy_fixture_identities:
            output["profile_provenance"] = (
                "LEGACY_SOFTWARE_FIXTURE_INFERRED_FROM_EMPTY_HASHES"
            )
        row = AttemptRecord(
            video_id=str(registration.get("video_id", "UNKNOWN")),
            method=str(registration.get("method", "")),
            status="host_interrupted",
            source_dataset=str(registration.get("source_dataset", "UNKNOWN")),
            canonical_root_id=str(registration.get("canonical_root_id", "UNKNOWN")),
            canonical_group_id=str(registration.get("canonical_group_id", "UNKNOWN")),
            unit_identity=str(registration.get("unit_identity", "")),
            attempt=int(registration.get("attempt", 1)),
            first_attempt=bool(registration.get("primary", False)),
            primary=bool(registration.get("primary", False)),
            retry_of=registration.get("retry_of"),
            lineage_id=(
                f"{registration.get('unit_identity')}:{registration.get('method')}"
            ),
            task_id=str(registration.get("task_id", "UNKNOWN")),
            operator_id=str(registration.get("operator_id", "UNKNOWN")),
            complete_job_id=str(registration.get("complete_job_id", "UNKNOWN")),
            video_hash=str(registration.get("video_hash", "")),
            expected_frames=registration.get("expected_frames", 0),
            frames_processed=recovered_frames,
            complete_video=False,
            code_hash=str(scorer_metadata.get("code_hash", "")),
            model_hash=str(registration.get("model_hash", "")),
            config_hash=str(registration.get("config_hash", "")),
            environment_hash=str(scorer_metadata.get("environment_hash", "")),
            annotation_hash=str(scorer_metadata.get("annotation_hash", "")),
            partition_hash=str(scorer_metadata.get("partition_hash", "")),
            manifest_hash=str(scorer_metadata.get("manifest_hash", "")),
            task_config_hash=str(scorer_metadata.get("task_config_hash", "")),
            task_source_hash=str(scorer_metadata.get("task_source_hash", "")),
            experiment_profile=profile,
            events=recovered_events,
            partial_events=recovered_events,
            resources=_resource_schema(
                0.0,
                recovered_frames,
                termination_kind="host_interrupted",
                reason="host process interrupted before terminal outcome",
            ),
            error="host process interrupted before terminal outcome",
            error_code="HOST_INTERRUPTED",
            output=output,
            schedule_id=str(registration.get("schedule_id", "")),
            schedule=tuple(registration.get("schedule", [])),
        )
        payload = asdict(row)
        payload.update(
            record_kind="attempt_outcome",
            attempt_identity=attempt_identity,
            registration_id=registration["registration_id"],
            source_video_path=registration["source_video_path"],
            artifact_paths=registration["artifact_paths"],
            effective_policy=registration["effective_policy"],
            scorer_metadata=registration["scorer_metadata"],
            annotation_used=False,
            vlm_authority_verified=True,
        )
        if isinstance(registration["scorer_metadata"].get("scoring"), Mapping):
            payload["scoring"] = dict(registration["scorer_metadata"]["scoring"])
        append_jsonl_atomic(ledger, [payload])
        existing.add(attempt_identity)
        added += 1
    return added


__all__ = [
    "AttemptRecord",
    "FixedStreakAdapter",
    "FixedStreakConfig",
    "METHOD_NAMES",
    "MethodAdapter",
    "MethodSpec",
    "PairedExperimentRunner",
    "PairedRun",
    "STATUSES",
    "UnavailableMethodAdapter",
    "append_jsonl_atomic",
    "default_method_adapters",
    "fixed_streak_adapter",
    "make_unit_identity",
    "reconcile_host_interrupted",
    "validate_registration_candidate",
    "validate_registration_records",
    "verify_ledger_integrity",
]
