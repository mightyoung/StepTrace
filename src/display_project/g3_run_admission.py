"""Fail-closed pre-run admission for the paired G3 method panel."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import random
import re
from typing import Any, Mapping

from display_project.current_gate_snapshot import validate_current_gate_snapshot
from display_project.g3_contract import (
    CANONICAL_TASK_DATASET,
    validate_task_dataset_bindings,
)
from display_project.pre_g3_authority import validate_authority_envelope


ADMISSION_DECISION_SCHEMA = "display_project.g3_run_admission_decision-1.0.0"
BATCH_RECEIPT_SCHEMA = "display_project.g3_evaluation_batch_admission-1.0.0"
UNIT_TOKEN_SCHEMA = "display_project.g3_unit_execution_token-1.0.0"
METHOD_MANIFEST_SCHEMA = "g3-method-manifest-1.0.0"
STATISTICAL_PRODUCER_SCHEMA = "g3-statistical-producer-1.1.0"
RESULT_DESTINATION_SCHEMA = "g3-result-destination-1.0.0"
PROFILE = "DEVELOPMENT_G3_EVALUATION"
METHOD_SCHEDULE = ("fixed_streak", "hmm_or_crf", "proposed")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LOGICAL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_./-]*$")
RESERVED = {
    "",
    "UNKNOWN",
    "UNK",
    "NONE",
    "NULL",
    "N/A",
    "NA",
    "NOT_APPLICABLE",
    "UNASSIGNED",
    "UNASSIGNED_BLOCKED_IDENTITY",
    "FIXTURE",
    "SOFTWARE_FIXTURE",
    "SYNTHETIC",
}


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _logical_id_valid(value: object) -> bool:
    if not isinstance(value, str) or LOGICAL_ID_RE.fullmatch(value) is None:
        return False
    _, path = value.split(":", 1)
    return all(part not in {"", ".", ".."} for part in path.split("/"))


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _artifact_hash_valid(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    supplied = value.get("artifact_sha256")
    try:
        return (
            isinstance(supplied, str)
            and HASH_RE.fullmatch(supplied) is not None
            and supplied
            == _canonical_sha256(
                {key: item for key, item in value.items() if key != "artifact_sha256"}
            )
        )
    except (TypeError, ValueError):
        return False


def _method_manifest_reasons(value: object) -> list[str]:
    if not isinstance(value, Mapping) or not value:
        return ["METHOD_MANIFEST_MISSING"]
    expected = {
        "schema_version",
        "profile",
        "schedule",
        "randomization_seed",
        "method_hashes",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(value) != expected or value.get("schema_version") != METHOD_MANIFEST_SCHEMA:
        reasons.append("METHOD_MANIFEST_SCHEMA_INVALID")
    if value.get("profile") != PROFILE:
        reasons.append("EVALUATION_PROFILE_INVALID")
    if value.get("schedule") != list(METHOD_SCHEDULE):
        reasons.append("METHOD_SCHEDULE_INVALID")
    seed = value.get("randomization_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        reasons.append("RANDOMIZATION_SEED_INVALID")
    hashes = value.get("method_hashes")
    if (
        not isinstance(hashes, Mapping)
        or set(hashes) != set(METHOD_SCHEDULE)
        or any(not isinstance(item, str) or HASH_RE.fullmatch(item) is None for item in hashes.values())
    ):
        reasons.append("METHOD_HASHES_INVALID")
    if not _artifact_hash_valid(value):
        reasons.append("METHOD_MANIFEST_HASH_INVALID")
    return reasons


def _producer_reasons(value: object) -> list[str]:
    if value is None:
        return ["STATISTICAL_ENGINE_MISSING"]
    if not isinstance(value, Mapping):
        return ["STATISTICAL_PRODUCER_INVALID"]
    expected = {
        "schema_version",
        "producer_id",
        "producer_version",
        "runtime",
        "fixture",
        "real_input_enabled",
        "source_sha256",
        "runtime_asset_sha256",
        "code_sha256",
        "environment_sha256",
        "bootstrap_count",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(value) != expected or value.get("schema_version") != STATISTICAL_PRODUCER_SCHEMA:
        reasons.append("STATISTICAL_PRODUCER_SCHEMA_INVALID")
    if not _logical_id_valid(value.get("producer_id")):
        reasons.append("STATISTICAL_PRODUCER_ID_INVALID")
    if value.get("runtime") not in {"R", "statsmodels"}:
        reasons.append("STATISTICAL_RUNTIME_INVALID")
    if value.get("fixture") is not False:
        reasons.append("STATISTICAL_PRODUCER_FIXTURE_FORBIDDEN")
    if value.get("real_input_enabled") is not True:
        reasons.append("STATISTICAL_PRODUCER_REAL_INPUT_DISABLED")
    if value.get("bootstrap_count") != 10_000:
        reasons.append("BOOTSTRAP_COUNT_INVALID")
    source_hashes = value.get("source_sha256")
    if (
        not isinstance(source_hashes, list)
        or not source_hashes
        or source_hashes != sorted(set(source_hashes))
        or any(not isinstance(item, str) or HASH_RE.fullmatch(item) is None for item in source_hashes)
    ):
        reasons.append("STATISTICAL_PRODUCER_SOURCES_INVALID")
    elif value.get("code_sha256") != _canonical_sha256(source_hashes):
        reasons.append("STATISTICAL_PRODUCER_CODE_AGGREGATE_INVALID")
    runtime_assets = value.get("runtime_asset_sha256")
    if (
        not isinstance(runtime_assets, Mapping)
        or not runtime_assets
        or any(
            not isinstance(key, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", key) is None
            or not isinstance(item, str)
            or HASH_RE.fullmatch(item) is None
            for key, item in runtime_assets.items()
        )
    ):
        reasons.append("STATISTICAL_RUNTIME_ASSETS_INVALID")
    for key in ("code_sha256", "environment_sha256"):
        item = value.get(key)
        if not isinstance(item, str) or HASH_RE.fullmatch(item) is None:
            reasons.append(f"{key.upper()}_INVALID")
    if not isinstance(value.get("producer_version"), str) or not value.get(
        "producer_version"
    ):
        reasons.append("STATISTICAL_PRODUCER_VERSION_INVALID")
    if not _artifact_hash_valid(value):
        reasons.append("STATISTICAL_PRODUCER_HASH_INVALID")
    return reasons


def _result_destination_reasons(value: object) -> list[str]:
    if not isinstance(value, Mapping) or not value:
        return ["RESULT_DESTINATION_MISSING"]
    expected = {"schema_version", "ledger_id", "ledger_path_sha256"}
    reasons: list[str] = []
    if set(value) != expected or value.get("schema_version") != RESULT_DESTINATION_SCHEMA:
        reasons.append("RESULT_DESTINATION_SCHEMA_INVALID")
    if not _logical_id_valid(value.get("ledger_id")):
        reasons.append("RESULT_LEDGER_ID_INVALID")
    path_hash = value.get("ledger_path_sha256")
    if not isinstance(path_hash, str) or HASH_RE.fullmatch(path_hash) is None:
        reasons.append("RESULT_LEDGER_PATH_HASH_INVALID")
    return reasons


def validate_admission_input_bundle(
    *,
    task_bindings: list[Mapping[str, Any]] | object,
    method_manifest: Mapping[str, Any] | object,
    unit_inventory: list[Mapping[str, Any]] | object,
    statistical_producer: Mapping[str, Any] | object | None,
    result_destination: Mapping[str, Any] | object,
) -> tuple[bool, list[str]]:
    """Validate the five non-authority inputs required by G3 admission."""
    reasons: list[str] = []
    binding_valid, binding_reasons = validate_task_dataset_bindings(task_bindings)
    if not binding_valid:
        reasons.extend(binding_reasons)
    reasons.extend(_method_manifest_reasons(method_manifest))
    reasons.extend(_unit_inventory_reasons(unit_inventory, task_bindings))
    reasons.extend(_producer_reasons(statistical_producer))
    reasons.extend(_result_destination_reasons(result_destination))
    reasons = list(dict.fromkeys(reasons))
    return not reasons, reasons


def _unit_inventory_reasons(
    value: object, bindings: list[Mapping[str, Any]] | object
) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["UNIT_INVENTORY_MISSING"]
    required = {
        "unit_id",
        "task_id",
        "dataset_id",
        "operator_id",
        "complete_job_id",
        "canonical_root_id",
        "canonical_group_id",
        "video_id",
        "video_sha256",
        "task_config_hash",
        "split_sha256",
        "ledger_sha256",
        "cluster_level",
        "operator_identity_status",
    }
    reasons: list[str] = []
    if any(not isinstance(item, Mapping) or set(item) != required for item in value):
        return ["UNIT_INVENTORY_SCHEMA_INVALID"]
    ids = [item.get("unit_id") for item in value]
    if (
        any(not isinstance(item, str) or HASH_RE.fullmatch(item) is None for item in ids)
        or len(set(ids)) != len(ids)
        or len({str(item).casefold() for item in ids}) != len(ids)
    ):
        reasons.append("UNIT_IDENTIFIERS_INVALID")
    binding_hashes = {
        item.get("task_id"): item.get("task_config_hash")
        for item in bindings
        if isinstance(item, Mapping)
    } if isinstance(bindings, list) else {}
    seen_pairs: set[tuple[object, object]] = set()
    fallback_jobs: list[str] = []
    for item in value:
        task = item.get("task_id")
        dataset = item.get("dataset_id")
        seen_pairs.add((task, dataset))
        if CANONICAL_TASK_DATASET.get(task) != dataset:
            reasons.append("UNIT_TASK_DATASET_BIJECTION_INVALID")
        if item.get("task_config_hash") != binding_hashes.get(task):
            reasons.append("UNIT_TASK_CONFIG_HASH_MISMATCH")
        operator = item.get("operator_id")
        cluster_level = item.get("cluster_level")
        identity_status = item.get("operator_identity_status")
        complete_job_fallback = (
            task == "industreal"
            and isinstance(operator, str)
            and operator.strip().upper() == "UNKNOWN"
            and cluster_level == "complete_job"
            and identity_status == "UNKNOWN_DISCLOSED"
        )
        if cluster_level not in {"operator", "complete_job"}:
            reasons.append("UNIT_CLUSTER_LEVEL_INVALID")
        if complete_job_fallback:
            fallback_jobs.append(str(item.get("complete_job_id")))
        elif cluster_level != "operator" or identity_status != "VERIFIED":
            reasons.append("UNIT_OPERATOR_CLUSTER_CONTRACT_INVALID")
        for key in (
            "complete_job_id",
            "canonical_root_id",
            "canonical_group_id",
            "video_id",
        ):
            identity = item.get(key)
            if not isinstance(identity, str) or identity.strip().upper() in RESERVED:
                if task == "industreal" and key == "operator_id":
                    reasons.append("BLOCKED_UNKNOWN_INDUSTREAL_OPERATOR_ID")
                else:
                    reasons.append(f"UNIT_IDENTITY_INVALID:{key}")
        if not complete_job_fallback and (
            not isinstance(operator, str) or operator.strip().upper() in RESERVED
        ):
            if task == "industreal":
                reasons.append("BLOCKED_UNKNOWN_INDUSTREAL_OPERATOR_ID")
            else:
                reasons.append("UNIT_IDENTITY_INVALID:operator_id")
        for key in ("video_sha256", "task_config_hash", "split_sha256", "ledger_sha256"):
            digest = item.get(key)
            if not isinstance(digest, str) or HASH_RE.fullmatch(digest) is None:
                reasons.append(f"UNIT_HASH_INVALID:{key}")
        tokens = " ".join(str(item.get(key, "")) for key in item).lower()
        compact = re.sub(r"[^a-z0-9]", "", tokens)
        if task == "meccano" and any(
            alias in compact
            for alias in ("meccano0009", "meccano09", "tombstonemeccano0009")
        ):
            reasons.append("TOMBSTONE_MECCANO_0009_FORBIDDEN")
    expected_pairs = set(CANONICAL_TASK_DATASET.items())
    if seen_pairs != expected_pairs:
        reasons.append("TWO_TASK_UNIT_COVERAGE_INVALID")
    if fallback_jobs and (
        len(fallback_jobs) < 2 or len(set(fallback_jobs)) != len(fallback_jobs)
    ):
        reasons.append("COMPLETE_JOB_FALLBACK_SUPPORT_INVALID")
    return list(dict.fromkeys(reasons))


def _receipt(
    *,
    consumer_id: str,
    nonce: str,
    issued_at: str,
    expires_at: str,
    authority_envelope: Mapping[str, Any],
    authority_attestation: Mapping[str, Any],
    current_snapshot: Mapping[str, Any],
    task_bindings: list[Mapping[str, Any]],
    method_manifest: Mapping[str, Any],
    unit_inventory: list[Mapping[str, Any]],
    statistical_producer: Mapping[str, Any],
    result_destination: Mapping[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": BATCH_RECEIPT_SCHEMA,
        "batch_id": "batch:" + hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24],
        "consumer_id": consumer_id,
        "profile": PROFILE,
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "authority_envelope_sha256": authority_envelope["artifact_sha256"],
        "authority_attestation_sha256": authority_attestation["artifact_sha256"],
        "current_snapshot_sha256": current_snapshot["artifact_sha256"],
        "task_bindings": [dict(item) for item in task_bindings],
        "method_manifest_sha256": method_manifest["artifact_sha256"],
        "method_schedule": list(method_manifest["schedule"]),
        "randomization_seed": method_manifest["randomization_seed"],
        "statistical_producer_sha256": statistical_producer["artifact_sha256"],
        "result_destination": dict(result_destination),
        "unit_inventory": [dict(item) for item in unit_inventory],
        "retry_policy": {
            "primary_attempt_immutable": True,
            "retries_never_replace_primary": True,
        },
        "lifecycle_state": "ISSUED",
        "consumed": False,
        "revoked": False,
        "downstream_authorized": False,
    }
    body["artifact_sha256"] = _canonical_sha256(body)
    return body


def evaluate_g3_run_admission(
    *,
    authority_envelope: Mapping[str, Any] | object,
    authority_attestation: Mapping[str, Any] | object,
    current_snapshot: Mapping[str, Any] | object,
    snapshot_predecessor: Mapping[str, Any] | None,
    task_bindings: list[Mapping[str, Any]] | object,
    method_manifest: Mapping[str, Any] | object,
    unit_inventory: list[Mapping[str, Any]] | object,
    statistical_producer: Mapping[str, Any] | object | None,
    result_destination: Mapping[str, Any] | object,
    profile: str,
    consumer_id: str,
    nonce: str,
    issued_at: str,
    expires_at: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return READY plus one batch receipt, or a non-authorizing BLOCKED decision."""
    reasons: list[str] = []
    if not isinstance(authority_envelope, Mapping) or not authority_envelope:
        reasons.append("AUTHORITY_ENVELOPE_MISSING")
    elif not isinstance(authority_attestation, Mapping) or not authority_attestation:
        reasons.append("AUTHORITY_ATTESTATION_MISSING")
    else:
        authority_valid, authority_reasons = validate_authority_envelope(
            authority_envelope, authority_attestation
        )
        if not authority_valid:
            reasons.extend(f"AUTHORITY:{reason}" for reason in authority_reasons)
    snapshot_valid, snapshot_reasons = validate_current_gate_snapshot(
        current_snapshot, predecessor=snapshot_predecessor, now=now
    )
    if not snapshot_valid:
        reasons.extend(f"SNAPSHOT:{reason}" for reason in snapshot_reasons)
    predicates = (
        current_snapshot.get("predicates", {})
        if isinstance(current_snapshot, Mapping)
        else {}
    )
    if not (
        predicates.get("g1_prerequisite") == "PASS"
        and predicates.get("g2_prerequisite") == "PASS"
    ):
        reasons.append("G1_G2_PREREQUISITES_NOT_PASS")
    if predicates.get("implementation_readiness") != "READY":
        reasons.append("IMPLEMENTATION_NOT_READY")
    if predicates.get("g3_decision") not in {"NOT_RUN", "BLOCKED"}:
        reasons.append("G3_ALREADY_DECIDED")
    if profile != PROFILE:
        reasons.append("EVALUATION_PROFILE_INVALID")
    if not isinstance(task_bindings, list) or not task_bindings:
        reasons.append("TASK_BINDINGS_MISSING")
    else:
        binding_valid, binding_reasons = validate_task_dataset_bindings(task_bindings)
        if not binding_valid:
            reasons.extend(binding_reasons)
    reasons.extend(_method_manifest_reasons(method_manifest))
    reasons.extend(_unit_inventory_reasons(unit_inventory, task_bindings))
    reasons.extend(_producer_reasons(statistical_producer))
    reasons.extend(_result_destination_reasons(result_destination))
    if not _logical_id_valid(consumer_id):
        reasons.append("CONSUMER_ID_INVALID")
    issued = _timestamp(issued_at)
    expiry = _timestamp(expires_at)
    current_time = now or datetime.now(timezone.utc)
    if (
        issued is None
        or expiry is None
        or issued > current_time
        or issued >= expiry
        or not isinstance(nonce, str)
        or len(nonce.strip()) < 16
    ):
        reasons.append("ADMISSION_VALIDITY_INVALID")
    reasons = list(dict.fromkeys(reasons))
    receipt = None
    if not reasons:
        receipt = _receipt(
            consumer_id=consumer_id,
            nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
            authority_envelope=authority_envelope,
            authority_attestation=authority_attestation,
            current_snapshot=current_snapshot,
            task_bindings=task_bindings,
            method_manifest=method_manifest,
            unit_inventory=unit_inventory,
            statistical_producer=statistical_producer,
            result_destination=result_destination,
        )
    body: dict[str, Any] = {
        "schema_version": ADMISSION_DECISION_SCHEMA,
        "status": "G3_RUN_ADMISSION_READY" if receipt else "BLOCKED",
        "ready": receipt is not None,
        "reason_codes": reasons,
        "profile": profile,
        "consumer_id": consumer_id,
        "batch_receipt": receipt,
        "downstream_authorized": False,
    }
    body["artifact_sha256"] = _canonical_sha256(body)
    return body


def validate_batch_receipt(
    receipt: Mapping[str, Any] | object,
    *,
    expected_consumer_id: str | None = None,
    expected_profile: str = PROFILE,
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    """Validate immutable receipt identity, time, lifecycle and consumer binding."""
    if not isinstance(receipt, Mapping):
        return False, ["BATCH_RECEIPT_NOT_MAPPING"]
    expected = {
        "schema_version",
        "batch_id",
        "consumer_id",
        "profile",
        "nonce",
        "issued_at",
        "expires_at",
        "authority_envelope_sha256",
        "authority_attestation_sha256",
        "current_snapshot_sha256",
        "task_bindings",
        "method_manifest_sha256",
        "method_schedule",
        "randomization_seed",
        "statistical_producer_sha256",
        "result_destination",
        "unit_inventory",
        "retry_policy",
        "lifecycle_state",
        "consumed",
        "revoked",
        "downstream_authorized",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(receipt) != expected:
        reasons.append("BATCH_RECEIPT_SCHEMA_KEYS_INVALID")
    if receipt.get("schema_version") != BATCH_RECEIPT_SCHEMA:
        reasons.append("BATCH_RECEIPT_SCHEMA_VERSION_INVALID")
    if not _artifact_hash_valid(receipt):
        reasons.append("BATCH_RECEIPT_HASH_INVALID")
    if not _logical_id_valid(receipt.get("batch_id")) or not _logical_id_valid(
        receipt.get("consumer_id")
    ):
        reasons.append("BATCH_RECEIPT_IDENTITY_INVALID")
    if expected_consumer_id is not None and receipt.get("consumer_id") != expected_consumer_id:
        reasons.append("BATCH_RECEIPT_CONSUMER_MISMATCH")
    if receipt.get("profile") != expected_profile:
        reasons.append("BATCH_RECEIPT_PROFILE_MISMATCH")
    if receipt.get("method_schedule") != list(METHOD_SCHEDULE):
        reasons.append("BATCH_RECEIPT_METHOD_SCHEDULE_INVALID")
    seed = receipt.get("randomization_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        reasons.append("BATCH_RECEIPT_RANDOMIZATION_INVALID")
    issued = _timestamp(receipt.get("issued_at"))
    expiry = _timestamp(receipt.get("expires_at"))
    current_time = now or datetime.now(timezone.utc)
    if issued is None or expiry is None or issued >= expiry or current_time >= expiry:
        reasons.append("BATCH_RECEIPT_EXPIRED_OR_INVALID")
    if (
        receipt.get("lifecycle_state") != "ISSUED"
        or receipt.get("consumed") is not False
        or receipt.get("revoked") is not False
    ):
        reasons.append("BATCH_RECEIPT_NOT_USABLE")
    if receipt.get("downstream_authorized") is not False:
        reasons.append("BATCH_RECEIPT_DOWNSTREAM_AUTHORITY_INVALID")
    binding_valid, binding_reasons = validate_task_dataset_bindings(
        receipt.get("task_bindings")
    )
    if not binding_valid:
        reasons.extend(binding_reasons)
    reasons.extend(
        _unit_inventory_reasons(
            receipt.get("unit_inventory"), receipt.get("task_bindings")
        )
    )
    reasons.extend(_result_destination_reasons(receipt.get("result_destination")))
    return not reasons, list(dict.fromkeys(reasons))


def issue_unit_execution_tokens(
    receipt: Mapping[str, Any] | object,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Issue exactly one immutable token for every unit in an admitted batch."""
    valid, reasons = validate_batch_receipt(receipt, now=now)
    if not valid:
        raise ValueError(reasons[0])
    tokens: list[dict[str, Any]] = []
    for unit in receipt["unit_inventory"]:
        token_identity = f"{receipt['batch_id']}|{unit['unit_id']}"
        unit_schedule = list(receipt["method_schedule"])
        random.Random(
            f"{receipt['randomization_seed']}:{unit['unit_id']}"
        ).shuffle(unit_schedule)
        body: dict[str, Any] = {
            "schema_version": UNIT_TOKEN_SCHEMA,
            "token_id": "token:" + hashlib.sha256(token_identity.encode("utf-8")).hexdigest()[:32],
            "batch_id": receipt["batch_id"],
            "batch_receipt_sha256": receipt["artifact_sha256"],
            "consumer_id": receipt["consumer_id"],
            "profile": receipt["profile"],
            "unit": dict(unit),
            "method_schedule": unit_schedule,
            "method_manifest_sha256": receipt["method_manifest_sha256"],
            "randomization_seed": receipt["randomization_seed"],
            "issued_at": receipt["issued_at"],
            "expires_at": receipt["expires_at"],
            "lifecycle_state": "ISSUED",
            "primary_attempt_registered": False,
            "terminal": False,
            "previous_token_sha256": None,
        }
        body["artifact_sha256"] = _canonical_sha256(body)
        tokens.append(body)
    return tokens


def validate_unit_execution_token(
    token: Mapping[str, Any] | object,
    receipt: Mapping[str, Any] | object,
    *,
    expected_consumer_id: str,
    expected_unit_id: str,
    consumed_token_ids: set[str] | frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    """Validate one unconsumed token against its immutable parent batch."""
    receipt_valid, receipt_reasons = validate_batch_receipt(
        receipt,
        expected_consumer_id=expected_consumer_id,
        now=now,
    )
    reasons = [f"PARENT:{reason}" for reason in receipt_reasons]
    if not isinstance(token, Mapping):
        return False, [*reasons, "UNIT_TOKEN_NOT_MAPPING"]
    expected = {
        "schema_version",
        "token_id",
        "batch_id",
        "batch_receipt_sha256",
        "consumer_id",
        "profile",
        "unit",
        "method_schedule",
        "method_manifest_sha256",
        "randomization_seed",
        "issued_at",
        "expires_at",
        "lifecycle_state",
        "primary_attempt_registered",
        "terminal",
        "previous_token_sha256",
        "artifact_sha256",
    }
    if set(token) != expected:
        reasons.append("UNIT_TOKEN_SCHEMA_KEYS_INVALID")
    if token.get("schema_version") != UNIT_TOKEN_SCHEMA:
        reasons.append("UNIT_TOKEN_SCHEMA_VERSION_INVALID")
    if not _artifact_hash_valid(token):
        reasons.append("UNIT_TOKEN_HASH_INVALID")
    if not _logical_id_valid(token.get("token_id")):
        reasons.append("UNIT_TOKEN_ID_INVALID")
    if token.get("token_id") in consumed_token_ids:
        reasons.append("BLOCKED_TOKEN_REPLAY")
    if receipt_valid and (
        token.get("batch_id") != receipt.get("batch_id")
        or token.get("batch_receipt_sha256") != receipt.get("artifact_sha256")
        or token.get("consumer_id") != expected_consumer_id
        or token.get("profile") != receipt.get("profile")
        or token.get("method_manifest_sha256")
        != receipt.get("method_manifest_sha256")
        or token.get("randomization_seed") != receipt.get("randomization_seed")
    ):
        reasons.append("UNIT_TOKEN_BATCH_BINDING_INVALID")
    expected_schedule = list(receipt.get("method_schedule", []))
    unit_id = (
        token.get("unit", {}).get("unit_id")
        if isinstance(token.get("unit"), Mapping)
        else ""
    )
    random.Random(f"{receipt.get('randomization_seed')}:{unit_id}").shuffle(
        expected_schedule
    )
    if token.get("method_schedule") != expected_schedule:
        reasons.append("UNIT_TOKEN_RANDOMIZED_SCHEDULE_INVALID")
    unit = token.get("unit")
    parent_units = receipt.get("unit_inventory", []) if isinstance(receipt, Mapping) else []
    expected_units = [
        item
        for item in parent_units
        if isinstance(item, Mapping) and item.get("unit_id") == expected_unit_id
    ]
    if (
        not isinstance(unit, Mapping)
        or unit.get("unit_id") != expected_unit_id
        or len(expected_units) != 1
        or dict(unit) != dict(expected_units[0])
    ):
        reasons.append("UNIT_TOKEN_UNIT_BINDING_INVALID")
    issued = _timestamp(token.get("issued_at"))
    expiry = _timestamp(token.get("expires_at"))
    current_time = now or datetime.now(timezone.utc)
    if issued is None or expiry is None or issued >= expiry or current_time >= expiry:
        reasons.append("UNIT_TOKEN_EXPIRED_OR_INVALID")
    if (
        token.get("lifecycle_state") != "ISSUED"
        or token.get("primary_attempt_registered") is not False
        or token.get("terminal") is not False
        or token.get("previous_token_sha256") is not None
    ):
        reasons.append("UNIT_TOKEN_NOT_STARTABLE")
    return not reasons, list(dict.fromkeys(reasons))
