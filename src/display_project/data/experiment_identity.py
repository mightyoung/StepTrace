"""Fail-closed resolution of registered video identity for paired experiments."""

from __future__ import annotations
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ExperimentIdentityError(ValueError):
    """The input video cannot be bound unambiguously to registered evidence."""


NON_FIXTURE_IDENTITY_PLACEHOLDERS = frozenset(
    {
        "",
        "-",
        "?",
        "UNKNOWN",
        "UNK",
        "NONE",
        "NULL",
        "N/A",
        "NA",
        "NOT_APPLICABLE",
        "UNASSIGNED",
        "UNASSIGNED_BLOCKED_IDENTITY",
        "UNKNOWN_EXTERNAL_IDENTITY",
        "UNKNOWN_IDENTITY",
        "UNKNOWN_ASSET",
        "UNREGISTERED",
        "UNREGISTERED_ASSET",
        "PLACEHOLDER",
        "TBD",
        "TBC",
        "SOFTWARE_FIXTURE",
        "FIXTURE",
        "SYNTHETIC",
        "SYNTHETIC_FIXTURE",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_TASK_ID_ALIASES = (
    frozenset({"meccano", "meccano_assembly"}),
    frozenset({"industreal", "industreal_assembly"}),
)
_IDENTITY_PROFILES = {
    "DEVELOPMENT_ONLY": {"splits": {"DEVELOPMENT", "DEVELOPMENT_ONLY_CONTAMINATED"}, "formal": "NO", "eligible": "NO"},
    "DEVELOPMENT_TUNING": {"splits": {"DEVELOPMENT", "DEVELOPMENT_TUNING", "DEVELOPMENT_ONLY_CONTAMINATED"}, "formal": "NO", "eligible": "NO"},
    "DEVELOPMENT_G3_EVALUATION": {
        "splits": {"DEVELOPMENT_G3_EVALUATION", "DEVELOPMENT"},
        "formal": "NO",
        "eligible": "YES",
    },
    "CALIBRATION": {"splits": {"CALIBRATION"}, "formal": "NO", "eligible": "YES"},
    "PILOT": {"splits": {"PILOT"}, "formal": "NO", "eligible": "YES"},
    "FORMAL": {"splits": {"FORMAL_TEST_SEALED"}, "formal": "YES", "eligible": "YES"},
    "FORMAL_TEST": {"splits": {"FORMAL_TEST_SEALED"}, "formal": "YES", "eligible": "YES"},
    "FORMAL_TEST_SEALED": {"splits": {"FORMAL_TEST_SEALED"}, "formal": "YES", "eligible": "YES"},
}
_FIELDS = (
    "source_dataset",
    "canonical_root_id",
    "canonical_group_id",
    "operator_id",
    "complete_job_id",
    "project_split",
    "formal_use_allowed",
)


def _is_industreal_complete_job_fallback(
    ledger: Mapping[str, Any], manifest: Mapping[str, Any]
) -> bool:
    """Recognize only the ratified, disclosed IndustReal fallback boundary."""

    dataset = _text(ledger.get("source_dataset") or manifest.get("source_dataset"))
    return (
        dataset.replace("_", "").replace("-", "").casefold() == "industreal"
        and _text(ledger.get("operator_id") or manifest.get("operator_id")).upper()
        == "UNKNOWN"
        and _text(ledger.get("cluster_level") or manifest.get("cluster_level"))
        == "complete_job"
        and _text(
            ledger.get("operator_identity_status")
            or manifest.get("operator_identity_status")
        )
        == "UNKNOWN_DISCLOSED"
        and _text(ledger.get("truth_capability") or manifest.get("truth_capability"))
        == "ACTION_STEP_STATE_ONLY"
    )


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _task_id_matches(actual: str, expected: str) -> bool:
    """Accept only the two registered dataset/config task-id spellings."""
    actual_key = re.sub(r"[^a-z0-9]+", "_", actual.lower()).strip("_")
    expected_key = re.sub(r"[^a-z0-9]+", "_", expected.lower()).strip("_")
    if actual_key == expected_key:
        return True
    return any(
        actual_key in aliases and expected_key in aliases
        for aliases in _TASK_ID_ALIASES
    )


def is_reserved_non_fixture_identity(value: Any) -> bool:
    """Return True for missing, non-string, or reserved non-fixture identity values."""
    return (
        not isinstance(value, str)
        or value.strip().upper() in NON_FIXTURE_IDENTITY_PLACEHOLDERS
    )


def _is_non_fixture_placeholder(value: Any) -> bool:
    return is_reserved_non_fixture_identity(value)


def _is_meccano_0009_alias(value: Any) -> bool:
    token = re.sub(r"[^a-z0-9]", "", _text(value).lower())
    return token in {"meccano0009", "meccano09", "meccano9", "video0009", "video09", "video9", "tombstonemeccano0009", "tombstonemeccano09", "tombstonemeccano9"}


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ExperimentIdentityError(f"cannot read file: {path}") from exc
    return digest.hexdigest()


def _load_ledger(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise ExperimentIdentityError(f"cannot read dataset ledger: {path}") from exc
    if not rows:
        raise ExperimentIdentityError("dataset ledger has no records")
    return [{str(k): _text(v) for k, v in row.items()} for row in rows]


def _load_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentIdentityError(f"invalid split manifest: {path}") from exc
    if (
        not isinstance(payload, Mapping)
        or not isinstance(payload.get("records"), list)
        or not payload["records"]
    ):
        raise ExperimentIdentityError("split manifest records are missing")
    if payload.get("status") not in {"BLOCKED_MISSING_EXTERNAL_IDENTITY", "READY"}:
        raise ExperimentIdentityError("split manifest status is not recognized")
    rules = payload.get("rules")
    if (
        not isinstance(rules, Mapping)
        or rules.get("no_cross_split_identity") is not True
        or rules.get("adjacent_frames_inherit_root_split") is not True
        or rules.get("unknown_identity_blocks_calibration_pilot_formal_test")
        is not True
    ):
        raise ExperimentIdentityError("split manifest rules are not frozen")
    rows = [dict(row) for row in payload["records"] if isinstance(row, Mapping)]
    if len(rows) != len(payload["records"]):
        raise ExperimentIdentityError("split manifest contains non-object record")
    return dict(payload), rows


def _decode_frames(path: Path) -> int:
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise ExperimentIdentityError("input video cannot be decoded")
        count = 0
        while True:
            ok, _ = cap.read()
            if not ok:
                break
            count += 1
        cap.release()
        return count
    except ImportError as exc:
        raise ExperimentIdentityError(
            "cv2 is required to prove complete video"
        ) from exc


def _positive_int(row: Mapping[str, Any], field: str) -> int:
    value = _text(row.get(field))
    if not value or not value.isdigit() or (value.startswith("0") and value != "0"):
        raise ExperimentIdentityError(f"invalid integer {field}")
    number = int(value)
    if number <= 0:
        raise ExperimentIdentityError(f"{field} must be positive")
    return number


@dataclass(frozen=True)
class ExperimentIdentity:
    asset_id: str
    video_id: str
    source_dataset: str
    canonical_root_id: str
    canonical_group_id: str
    operator_id: str
    complete_job_id: str
    task_id: str
    project_split: str
    source_split: str
    formal_use_allowed: str
    independent_unit_eligible: str
    ledger_hash: str
    ledger_file_hash: str
    manifest_file_hash: str
    cluster_level: str = "operator"
    operator_identity_status: str = "VERIFIED"
    truth_capability: str = "FULL"


def resolve_experiment_identity(
    video: str | Path,
    *,
    asset_id: str,
    ledger_path: str | Path,
    manifest_path: str | Path,
    profile: str,
    video_id: str | None = None,
    expected_task_id: str | None = None,
    expected_ledger_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> ExperimentIdentity:
    if _text(profile).upper() == "SOFTWARE_FIXTURE":
        raise ExperimentIdentityError(
            "SOFTWARE_FIXTURE must use its fixture identity path"
        )
    ledger_file, manifest_file = Path(ledger_path), Path(manifest_path)
    ledger_file_hash, manifest_file_hash = (
        _file_hash(ledger_file),
        _file_hash(manifest_file),
    )
    for name, value, expected in (
        ("dataset ledger", ledger_file_hash, expected_ledger_sha256),
        ("split manifest", manifest_file_hash, expected_manifest_sha256),
    ):
        if (
            expected is None
            or not _SHA256.fullmatch(_text(expected))
            or value != _text(expected).lower()
        ):
            raise ExperimentIdentityError(f"{name} file hash is not explicitly frozen")
    requested = _text(asset_id)
    if _is_non_fixture_placeholder(requested):
        raise ExperimentIdentityError("--asset-id is required for non-fixture profiles")
    ledger_rows = _load_ledger(ledger_file)
    _, manifest_rows = _load_manifest(manifest_file)
    lm = [
        r for r in ledger_rows if _text(r.get("asset_id")).upper() == requested.upper()
    ]
    mm = [
        r
        for r in manifest_rows
        if _text(r.get("asset_id")).upper() == requested.upper()
    ]
    if len(lm) != 1 or len(mm) != 1:
        raise ExperimentIdentityError(
            f"asset_id is not unique in registration: {requested}"
        )
    ledger, manifest = lm[0], mm[0]
    actual_hash = _file_hash(Path(video))
    registered_hash = _text(ledger.get("sha256")).lower()
    if not _SHA256.fullmatch(registered_hash) or actual_hash != registered_hash:
        raise ExperimentIdentityError(
            f"input hash does not match registered asset: {requested}"
        )
    manifest_hash = _text(manifest.get("sha256") or manifest.get("video_sha256"))
    if manifest_hash and (
        not _SHA256.fullmatch(manifest_hash) or manifest_hash.lower() != actual_hash
    ):
        raise ExperimentIdentityError(
            f"split manifest hash does not match input: {requested}"
        )
    for field in (
        "source_dataset",
        "canonical_root_id",
        "canonical_group_id",
        "operator_id",
        "complete_job_id",
        "project_split",
        "source_split",
        "formal_use_allowed",
        "independent_unit_eligible",
        "task_id",
    ):
        manifest_value = _text(manifest.get(field))
        if manifest_value and _text(ledger.get(field)) != manifest_value:
            raise ExperimentIdentityError(
                f"ledger/manifest mismatch for {field}: {requested}"
            )
    root, op, job = (
        _text(ledger.get(x))
        for x in ("canonical_root_id", "operator_id", "complete_job_id")
    )
    vid = (
        _text(ledger.get("video_id"))
        or _text(manifest.get("video_id"))
        or Path(_text(ledger.get("source_path"))).stem
    )
    unit = manifest.get("split_unit")
    if not isinstance(unit, list) or [str(x) for x in unit] != [root, op, job, vid]:
        raise ExperimentIdentityError("split_unit does not match registered identity")
    task_id = _text(ledger.get("task_id"))
    if expected_task_id is not None and not _task_id_matches(
        task_id, _text(expected_task_id)
    ):
        raise ExperimentIdentityError("ledger task_id does not match compiled task")
    values = {field: _text(ledger.get(field)) for field in _FIELDS}
    complete_job_fallback = _is_industreal_complete_job_fallback(ledger, manifest)
    normalized_profile = _text(profile).upper()
    profile_rule = _IDENTITY_PROFILES.get(normalized_profile)
    if profile_rule is None:
        raise ExperimentIdentityError(f"unrecognized experiment profile: {profile}")
    if values["project_split"] not in profile_rule["splits"]:
        raise ExperimentIdentityError(
            f"profile {normalized_profile} cannot use project split {values['project_split']}"
        )
    if values["formal_use_allowed"].upper() != profile_rule["formal"]:
        raise ExperimentIdentityError("formal_use_allowed is incompatible with experiment profile")
    if _text(ledger.get("independent_unit_eligible")).upper() != profile_rule["eligible"]:
        raise ExperimentIdentityError("independent_unit_eligible is incompatible with experiment profile")
    if complete_job_fallback and normalized_profile not in {
        "CALIBRATION",
        "DEVELOPMENT_G3_EVALUATION",
    }:
        raise ExperimentIdentityError(
            "IndustReal complete-job fallback is prohibited for pilot/formal profiles"
        )
    identity_tokens = [requested, *(_text(ledger.get(k)) for k in ("video_id", "canonical_root_id", "canonical_group_id", "parent_asset_id")), *(_text(manifest.get(k)) for k in ("video_id", "canonical_root_id", "canonical_group_id", "tombstone_id"))]
    if any(re.sub(r"[^a-z0-9]", "", _text(token).lower()).startswith("tombstonemeccano0009") for token in identity_tokens):
        raise ExperimentIdentityError("TOMBSTONE-MECCANO-0009 excludes this identity and all aliases/derivatives")
    if _text(values["source_dataset"]).upper() == "MECCANO" and any(_is_meccano_0009_alias(token) for token in identity_tokens):
        raise ExperimentIdentityError("TOMBSTONE-MECCANO-0009 excludes this identity and all aliases/derivatives")
    if values["project_split"] == "DEVELOPMENT_ONLY_CONTAMINATED" and normalized_profile not in {"DEVELOPMENT_ONLY", "DEVELOPMENT_TUNING"}:
        raise ExperimentIdentityError("contaminated development lineage is not eligible for restricted runs")
    identity_fields = {
        "source_dataset": values["source_dataset"],
        "canonical_root_id": values["canonical_root_id"],
        "canonical_group_id": values["canonical_group_id"],
        "operator_id": values["operator_id"],
        "complete_job_id": values["complete_job_id"],
        "task_id": task_id,
    }
    for field, value in identity_fields.items():
        if field == "operator_id" and complete_job_fallback:
            continue
        if _is_non_fixture_placeholder(value):
            raise ExperimentIdentityError(
                f"placeholder {field} blocks non-fixture run: {requested}"
            )
    if _is_non_fixture_placeholder(
        values["project_split"]
    ) or _is_non_fixture_placeholder(_text(ledger.get("source_split"))):
        raise ExperimentIdentityError("unassigned source/project split blocks run")
    if (
        _text(ledger.get("record_type")).upper() != "CANONICAL_ROOT_VIDEO"
        or _text(ledger.get("derivation")).upper() != "NONE"
        or _text(ledger.get("frame_start")) != "0"
    ):
        raise ExperimentIdentityError(
            "paired experiments require a canonical complete video"
        )
    frame_count, frame_end = (
        _positive_int(ledger, "frame_count"),
        _text(ledger.get("frame_end")),
    )
    if not frame_end.isdigit() or frame_count != int(frame_end) + 1:
        raise ExperimentIdentityError("registered frame range is not complete")
    decoded = _decode_frames(Path(video))
    if decoded != frame_count:
        raise ExperimentIdentityError(
            f"decoded frame count {decoded} != registered {frame_count}"
        )
    ledger_vid, manifest_vid = (
        _text(ledger.get("video_id")),
        _text(manifest.get("video_id")),
    )
    resolved_vid = (
        ledger_vid
        or manifest_vid
        or Path(_text(ledger.get("source_path"))).stem
        or requested
    )
    if _is_non_fixture_placeholder(resolved_vid):
        raise ExperimentIdentityError(
            f"placeholder video_id blocks non-fixture run: {requested}"
        )
    if ledger_vid and manifest_vid and ledger_vid != manifest_vid:
        raise ExperimentIdentityError("ledger/manifest video_id mismatch")
    if video_id is not None and _text(video_id) != resolved_vid:
        raise ExperimentIdentityError("--video-id does not match registered asset")
    return ExperimentIdentity(
        asset_id=_text(ledger.get("asset_id")),
        video_id=resolved_vid,
        source_dataset=values["source_dataset"],
        canonical_root_id=values["canonical_root_id"],
        canonical_group_id=values["canonical_group_id"],
        operator_id=values["operator_id"],
        complete_job_id=values["complete_job_id"],
        task_id=task_id,
        project_split=values["project_split"],
        source_split=_text(ledger.get("source_split")),
        formal_use_allowed=values["formal_use_allowed"],
        independent_unit_eligible=_text(ledger.get("independent_unit_eligible")),
        ledger_hash=registered_hash,
        ledger_file_hash=ledger_file_hash,
        manifest_file_hash=manifest_file_hash,
        cluster_level=_text(ledger.get("cluster_level")) or "operator",
        operator_identity_status=_text(ledger.get("operator_identity_status"))
        or "VERIFIED",
        truth_capability=_text(ledger.get("truth_capability")) or "FULL",
    )
