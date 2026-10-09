"""Non-mutating authority commitments for a dirty pre-G3 worktree."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Iterable, Mapping


DIRTY_TREE_SCHEMA = "display_project.pre_g3_dirty_tree_receipt-1.0.0"
AUTHORITY_ENVELOPE_SCHEMA = "display_project.pre_g3_authority_envelope-1.0.0"
AUTHORITY_ATTESTATION_SCHEMA = "display_project.pre_g3_authority_attestation-1.0.0"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40,64}$")
LOGICAL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_./-]*$")


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _logical_id_valid(value: object) -> bool:
    if not isinstance(value, str) or LOGICAL_ID_RE.fullmatch(value) is None:
        return False
    _, path = value.split(":", 1)
    return all(part not in {"", ".", ".."} for part in path.split("/"))


def _normalize_declared_paths(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        if not isinstance(raw, str) or not raw.strip() or "\\" in raw:
            raise ValueError("DIRTY_PATH_INVALID")
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("DIRTY_PATH_INVALID")
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        raise ValueError("DIRTY_PATH_DUPLICATE")
    if len({value.casefold() for value in normalized}) != len(normalized):
        raise ValueError("DIRTY_PATH_CASE_COLLISION")
    return tuple(sorted(normalized))


def _matches(path: str, declared: str) -> bool:
    return path == declared or path.startswith(declared + "/")


def _validate_disjoint(included: tuple[str, ...], excluded: tuple[str, ...]) -> None:
    for left in included:
        for right in excluded:
            if _matches(left, right) or _matches(right, left):
                raise ValueError("DIRTY_PATH_OWNERSHIP_OVERLAP")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nul_paths(raw: bytes) -> list[str]:
    return sorted(
        item.decode("utf-8", errors="strict").replace("\\", "/")
        for item in raw.split(b"\0")
        if item
    )


def _entry(root: Path, path: str, kind: str) -> dict[str, Any]:
    target = root / PurePosixPath(path)
    if target.is_file():
        return {
            "path": path,
            "kind": kind,
            "status": "PRESENT",
            "size": target.stat().st_size,
            "sha256": _sha256_file(target),
        }
    if target.exists():
        raise ValueError(f"DIRTY_NON_FILE_UNSUPPORTED:{path}")
    return {
        "path": path,
        "kind": kind,
        "status": "DELETED",
        "size": 0,
        "sha256": None,
    }


def build_dirty_tree_receipt(
    root: str | Path,
    *,
    included_paths: Iterable[str],
    excluded_paths: Iterable[str],
) -> dict[str, Any]:
    """Snapshot dirty content without staging, cleaning or rewriting the worktree."""
    repo = Path(root).resolve()
    included = _normalize_declared_paths(included_paths)
    excluded = _normalize_declared_paths(excluded_paths)
    _validate_disjoint(included, excluded)
    base_head = _git(repo, "rev-parse", "HEAD").decode("ascii").strip().lower()
    base_tree = _git(repo, "rev-parse", "HEAD^{tree}").decode("ascii").strip().lower()
    tracked_patch = _git(repo, "diff", "--binary", "--no-ext-diff", "HEAD", "--")
    tracked = _nul_paths(
        _git(repo, "diff", "--name-only", "-z", "--no-ext-diff", "HEAD", "--")
    )
    untracked = _nul_paths(
        _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    )
    entries = [_entry(repo, path, "TRACKED") for path in tracked]
    entries.extend(_entry(repo, path, "UNTRACKED") for path in untracked)
    entries.sort(key=lambda item: (item["path"], item["kind"]))
    included_entries: list[dict[str, Any]] = []
    excluded_entries: list[dict[str, Any]] = []
    unclassified: list[str] = []
    for entry in entries:
        path = entry["path"]
        in_scope = any(_matches(path, declared) for declared in included)
        out_scope = any(_matches(path, declared) for declared in excluded)
        if in_scope == out_scope:
            unclassified.append(path)
        elif in_scope:
            included_entries.append(entry)
        else:
            excluded_entries.append(entry)
    if unclassified:
        raise ValueError("DIRTY_PATH_UNCLASSIFIED:" + ",".join(unclassified))
    untracked_entries = [entry for entry in entries if entry["kind"] == "UNTRACKED"]
    body: dict[str, Any] = {
        "schema_version": DIRTY_TREE_SCHEMA,
        "base_head": base_head,
        "base_tree": base_tree,
        "tracked_binary_patch_sha256": hashlib.sha256(tracked_patch).hexdigest(),
        "untracked_inventory_sha256": _canonical_sha256(untracked_entries),
        "included_paths": list(included),
        "excluded_paths": list(excluded),
        "included_entries": included_entries,
        "excluded_entries": excluded_entries,
        "included_commitment_sha256": _canonical_sha256(
            {"paths": included, "entries": included_entries}
        ),
        "excluded_commitment_sha256": _canonical_sha256(
            {"paths": excluded, "entries": excluded_entries}
        ),
    }
    body["artifact_sha256"] = _canonical_sha256(body)
    return body


def validate_dirty_tree_receipt(
    root: str | Path, receipt: Mapping[str, Any] | object
) -> tuple[bool, list[str]]:
    """Compare a persisted receipt with the current worktree fail closed."""
    if not isinstance(receipt, Mapping):
        return False, ["RECEIPT_NOT_MAPPING"]
    expected_keys = {
        "schema_version",
        "base_head",
        "base_tree",
        "tracked_binary_patch_sha256",
        "untracked_inventory_sha256",
        "included_paths",
        "excluded_paths",
        "included_entries",
        "excluded_entries",
        "included_commitment_sha256",
        "excluded_commitment_sha256",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(receipt) != expected_keys:
        reasons.append("RECEIPT_SCHEMA_KEYS_INVALID")
    if receipt.get("schema_version") != DIRTY_TREE_SCHEMA:
        reasons.append("RECEIPT_SCHEMA_VERSION_INVALID")
    supplied_hash = receipt.get("artifact_sha256")
    body = {key: value for key, value in receipt.items() if key != "artifact_sha256"}
    try:
        if supplied_hash != _canonical_sha256(body):
            reasons.append("RECEIPT_HASH_INVALID")
        current = build_dirty_tree_receipt(
            root,
            included_paths=receipt.get("included_paths", []),
            excluded_paths=receipt.get("excluded_paths", []),
        )
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        reasons.append("WORKTREE_RECOMPUTE_FAILED")
        return False, reasons
    for key in (
        "base_head",
        "base_tree",
        "tracked_binary_patch_sha256",
        "untracked_inventory_sha256",
        "included_commitment_sha256",
        "excluded_commitment_sha256",
    ):
        if receipt.get(key) != current.get(key):
            reasons.append(f"{key.upper()}_MISMATCH")
    if receipt.get("included_entries") != current["included_entries"]:
        reasons.append("INCLUDED_ENTRIES_MISMATCH")
    if receipt.get("excluded_entries") != current["excluded_entries"]:
        reasons.append("EXCLUDED_ENTRIES_MISMATCH")
    return not reasons, reasons


def _dirty_receipt_structure_reasons(receipt: object) -> list[str]:
    if not isinstance(receipt, Mapping):
        return ["DIRTY_TREE_RECEIPT_NOT_MAPPING"]
    expected = {
        "schema_version",
        "base_head",
        "base_tree",
        "tracked_binary_patch_sha256",
        "untracked_inventory_sha256",
        "included_paths",
        "excluded_paths",
        "included_entries",
        "excluded_entries",
        "included_commitment_sha256",
        "excluded_commitment_sha256",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(receipt) != expected:
        reasons.append("DIRTY_TREE_RECEIPT_SCHEMA_KEYS_INVALID")
    if receipt.get("schema_version") != DIRTY_TREE_SCHEMA:
        reasons.append("DIRTY_TREE_RECEIPT_SCHEMA_VERSION_INVALID")
    try:
        body = {key: value for key, value in receipt.items() if key != "artifact_sha256"}
        if receipt.get("artifact_sha256") != _canonical_sha256(body):
            reasons.append("DIRTY_TREE_RECEIPT_HASH_INVALID")
    except (TypeError, ValueError):
        reasons.append("DIRTY_TREE_RECEIPT_HASH_INVALID")
    if GIT_OBJECT_RE.fullmatch(str(receipt.get("base_head", ""))) is None:
        reasons.append("DIRTY_TREE_BASE_HEAD_INVALID")
    if GIT_OBJECT_RE.fullmatch(str(receipt.get("base_tree", ""))) is None:
        reasons.append("DIRTY_TREE_BASE_TREE_INVALID")
    return reasons


def _commitment_valid(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"logical_id", "schema_version", "sha256"}
        and _logical_id_valid(value.get("logical_id"))
        and isinstance(value.get("schema_version"), str)
        and bool(str(value.get("schema_version")).strip())
        and isinstance(value.get("sha256"), str)
        and HASH_RE.fullmatch(value["sha256"]) is not None
    )


def _evidence_bundle_valid(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    required = {
        "logical_id",
        "schema_version",
        "sha256",
        "source_timestamp",
        "lineage",
    }
    if any(not isinstance(item, Mapping) or set(item) != required for item in value):
        return False
    logical_ids = [item.get("logical_id") for item in value]
    return (
        all(_logical_id_valid(item) for item in logical_ids)
        and len(set(logical_ids)) == len(logical_ids)
        and len({str(item).casefold() for item in logical_ids}) == len(logical_ids)
        and all(
            isinstance(item.get("schema_version"), str)
            and bool(str(item.get("schema_version")).strip())
            and isinstance(item.get("sha256"), str)
            and HASH_RE.fullmatch(item["sha256"]) is not None
            and isinstance(item.get("source_timestamp"), str)
            and item["source_timestamp"].endswith("Z")
            and (
                item.get("lineage") is None
                or _logical_id_valid(item.get("lineage"))
            )
            for item in value
        )
    )


def build_authority_envelope(
    *,
    logical_id: str,
    implementation_commit: Mapping[str, str],
    environment_commitment: Mapping[str, str],
    evidence_bundle_commitment: list[Mapping[str, Any]],
    dirty_tree_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal separated authority commitments without embedding an attestation."""
    if not _logical_id_valid(logical_id):
        raise ValueError("AUTHORITY_LOGICAL_ID_INVALID")
    if (
        set(implementation_commit)
        != {"commit_sha", "tree_sha", "implementation_branch_id"}
        or GIT_OBJECT_RE.fullmatch(implementation_commit.get("commit_sha", "")) is None
        or GIT_OBJECT_RE.fullmatch(implementation_commit.get("tree_sha", "")) is None
        or not str(implementation_commit.get("implementation_branch_id", "")).strip()
    ):
        raise ValueError("IMPLEMENTATION_COMMIT_INVALID")
    if not _commitment_valid(environment_commitment):
        raise ValueError("ENVIRONMENT_COMMITMENT_INVALID")
    if not _evidence_bundle_valid(evidence_bundle_commitment):
        raise ValueError("EVIDENCE_BUNDLE_COMMITMENT_INVALID")
    dirty_reasons = _dirty_receipt_structure_reasons(dirty_tree_receipt)
    if dirty_reasons:
        raise ValueError(dirty_reasons[0])
    body: dict[str, Any] = {
        "schema_version": AUTHORITY_ENVELOPE_SCHEMA,
        "logical_id": logical_id,
        "lifecycle_state": "SEALED",
        "implementation_commit": dict(implementation_commit),
        "environment_commitment": dict(environment_commitment),
        "evidence_bundle_commitment": [dict(item) for item in evidence_bundle_commitment],
        "dirty_tree_receipt": dict(dirty_tree_receipt),
        "prohibited_actions": [
            "embedded_self_attestation",
            "G3_method_panel_execution",
            "pilot",
            "power_analysis",
            "formal_test",
            "cross_task_generalization",
        ],
    }
    body["artifact_sha256"] = _canonical_sha256(body)
    return body


def build_external_attestation(
    *,
    logical_id: str,
    envelope: Mapping[str, Any],
    reviewer_role: str,
    attested_at: str,
    nonce: str,
    storage_logical_id: str,
) -> dict[str, Any]:
    """Create an out-of-band attestation that points to a sealed envelope."""
    valid, reasons = validate_authority_envelope(envelope)
    if not valid:
        raise ValueError(reasons[0])
    if not _logical_id_valid(logical_id) or not _logical_id_valid(storage_logical_id):
        raise ValueError("ATTESTATION_LOGICAL_ID_INVALID")
    if storage_logical_id == envelope.get("logical_id"):
        raise ValueError("ATTESTATION_STORAGE_NOT_EXTERNAL")
    if reviewer_role not in {
        "independent_verifier",
        "architect_reviewer",
        "corresponding_author",
    }:
        raise ValueError("ATTESTATION_REVIEWER_ROLE_INVALID")
    if not isinstance(attested_at, str) or not attested_at.endswith("Z"):
        raise ValueError("ATTESTATION_TIMESTAMP_INVALID")
    if not isinstance(nonce, str) or len(nonce.strip()) < 16:
        raise ValueError("ATTESTATION_NONCE_INVALID")
    body: dict[str, Any] = {
        "schema_version": AUTHORITY_ATTESTATION_SCHEMA,
        "logical_id": logical_id,
        "envelope_logical_id": envelope["logical_id"],
        "envelope_sha256": envelope["artifact_sha256"],
        "reviewer_role": reviewer_role,
        "attested_at": attested_at,
        "nonce": nonce,
        "storage_logical_id": storage_logical_id,
    }
    body["artifact_sha256"] = _canonical_sha256(body)
    return body


def validate_authority_envelope(
    envelope: Mapping[str, Any] | object,
    attestation: Mapping[str, Any] | object | None = None,
) -> tuple[bool, list[str]]:
    """Validate a sealed envelope and, optionally, its external attestation."""
    if not isinstance(envelope, Mapping):
        return False, ["AUTHORITY_ENVELOPE_NOT_MAPPING"]
    expected = {
        "schema_version",
        "logical_id",
        "lifecycle_state",
        "implementation_commit",
        "environment_commitment",
        "evidence_bundle_commitment",
        "dirty_tree_receipt",
        "prohibited_actions",
        "artifact_sha256",
    }
    reasons: list[str] = []
    if set(envelope) != expected:
        reasons.append("AUTHORITY_ENVELOPE_SCHEMA_KEYS_INVALID")
    if envelope.get("schema_version") != AUTHORITY_ENVELOPE_SCHEMA:
        reasons.append("AUTHORITY_ENVELOPE_SCHEMA_VERSION_INVALID")
    if envelope.get("lifecycle_state") != "SEALED":
        reasons.append("AUTHORITY_ENVELOPE_STATE_INVALID")
    if not _logical_id_valid(envelope.get("logical_id")):
        reasons.append("AUTHORITY_LOGICAL_ID_INVALID")
    try:
        body = {key: value for key, value in envelope.items() if key != "artifact_sha256"}
        if envelope.get("artifact_sha256") != _canonical_sha256(body):
            reasons.append("AUTHORITY_ENVELOPE_HASH_INVALID")
    except (TypeError, ValueError):
        reasons.append("AUTHORITY_ENVELOPE_HASH_INVALID")
    implementation = envelope.get("implementation_commit")
    if (
        not isinstance(implementation, Mapping)
        or set(implementation)
        != {"commit_sha", "tree_sha", "implementation_branch_id"}
        or GIT_OBJECT_RE.fullmatch(str(implementation.get("commit_sha", ""))) is None
        or GIT_OBJECT_RE.fullmatch(str(implementation.get("tree_sha", ""))) is None
        or not str(implementation.get("implementation_branch_id", "")).strip()
    ):
        reasons.append("IMPLEMENTATION_COMMIT_INVALID")
    if not _commitment_valid(envelope.get("environment_commitment")):
        reasons.append("ENVIRONMENT_COMMITMENT_INVALID")
    if not _evidence_bundle_valid(envelope.get("evidence_bundle_commitment")):
        reasons.append("EVIDENCE_BUNDLE_COMMITMENT_INVALID")
    reasons.extend(_dirty_receipt_structure_reasons(envelope.get("dirty_tree_receipt")))
    if "external_attestation" in envelope or "attestation" in envelope:
        reasons.append("EMBEDDED_SELF_ATTESTATION_FORBIDDEN")
    if attestation is not None:
        if not isinstance(attestation, Mapping):
            reasons.append("ATTESTATION_NOT_MAPPING")
        else:
            attestation_expected = {
                "schema_version",
                "logical_id",
                "envelope_logical_id",
                "envelope_sha256",
                "reviewer_role",
                "attested_at",
                "nonce",
                "storage_logical_id",
                "artifact_sha256",
            }
            if set(attestation) != attestation_expected:
                reasons.append("ATTESTATION_SCHEMA_KEYS_INVALID")
            if attestation.get("schema_version") != AUTHORITY_ATTESTATION_SCHEMA:
                reasons.append("ATTESTATION_SCHEMA_VERSION_INVALID")
            try:
                attestation_body = {
                    key: value
                    for key, value in attestation.items()
                    if key != "artifact_sha256"
                }
                if attestation.get("artifact_sha256") != _canonical_sha256(
                    attestation_body
                ):
                    reasons.append("ATTESTATION_HASH_INVALID")
            except (TypeError, ValueError):
                reasons.append("ATTESTATION_HASH_INVALID")
            if (
                attestation.get("envelope_logical_id") != envelope.get("logical_id")
                or attestation.get("envelope_sha256") != envelope.get("artifact_sha256")
            ):
                reasons.append("ATTESTATION_ENVELOPE_BINDING_INVALID")
            if attestation.get("storage_logical_id") == envelope.get("logical_id"):
                reasons.append("ATTESTATION_STORAGE_NOT_EXTERNAL")
    return not reasons, reasons
