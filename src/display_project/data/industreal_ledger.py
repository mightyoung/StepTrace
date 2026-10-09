"""Fail-closed, transactional registration of an audited IndustReal RGB archive."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

LEDGER_FIELDS = [
    "schema_version",
    "asset_id",
    "record_type",
    "source_dataset",
    "source_path",
    "canonical_root_id",
    "canonical_group_id",
    "parent_asset_id",
    "derivation",
    "frame_start",
    "frame_end",
    "frame_count",
    "width",
    "height",
    "operator_id",
    "complete_job_id",
    "task_id",
    "error_types",
    "source_split",
    "project_split",
    "sha256",
    "hash_verified_on",
    "manually_viewed",
    "view_purpose",
    "annotation_status",
    "contamination_status",
    "formal_use_allowed",
    "split_locked",
    "independent_unit_eligible",
    "tombstone_id",
    "tombstone_irreversible",
    "notes",
]
_ID_RE = re.compile(r"^\d{2}_(?:assy|main)_\d_\d$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OFFICIAL_RECORDING_COUNT = 86
OFFICIAL_LABELED_COUNT = 84
OFFICIAL_UNLABELED_IDS = frozenset({"15_main_0_1", "21_assy_1_1"})


class IndustRealLedgerError(ValueError):
    """Raised when the audit or destination state is not safe to merge."""


def _canonical_hash(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body["canonical_sha256"] = ""
    raw = (
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise IndustRealLedgerError(f"missing destination: {path}")
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            return list(reader), list(reader.fieldnames or [])
    except (OSError, UnicodeError, csv.Error) as exc:
        raise IndustRealLedgerError(f"malformed ledger: {path}") from exc


def _video_fields(record: dict[str, Any]) -> dict[str, Any]:
    video = record.get("video")
    return video if isinstance(video, dict) else record


def _anchor_ids(audit: dict[str, Any]) -> list[str] | None:
    primary: list[str] | None = None
    for key in ("official_recording_ids", "recording_ids", "official_ids"):
        value = audit.get(key)
        if isinstance(value, list):
            primary = [str(item) for item in value]
            break
    anchor = audit.get("official_id_anchor")
    if (
        primary is None
        and isinstance(anchor, dict)
        and isinstance(anchor.get("recording_ids"), list)
    ):
        primary = [str(item) for item in anchor["recording_ids"]]
    if primary is None:
        return None
    extras: list[str] = []
    for key in (
        "archive_only_recording_ids",
        "official_unlabeled_recording_ids",
        "archive_only_ids",
    ):
        value = audit.get(key)
        if isinstance(value, list):
            extras.extend(str(item) for item in value)
    if isinstance(anchor, dict):
        for key in ("archive_only_recording_ids", "official_unlabeled_recording_ids"):
            value = anchor.get(key)
            if isinstance(value, list):
                extras.extend(str(item) for item in value)
    return sorted(set(primary) | set(extras))


def _finite_positive(value: Any, name: str, rid: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise IndustRealLedgerError(f"invalid {name}: {rid}")
    if not math.isfinite(number) or number <= 0:
        raise IndustRealLedgerError(f"invalid {name}: {rid}")
    return number


def _source_root(
    audit: dict[str, Any], records: list[dict[str, Any]], audit_path: Path
) -> Path:
    root_value = (
        audit.get("extracted_root")
        or audit.get("extracted_dir")
        or audit.get("audit_extracted_root")
    )
    if root_value:
        root = Path(str(root_value))
        if not root.is_absolute():
            root = (audit_path.parent / root).resolve()
        return root
    paths = []
    for record in records:
        video = _video_fields(record)
        value = video.get("path", record.get("source_path"))
        if value:
            paths.append(Path(str(value)).resolve())
    if not paths:
        raise IndustRealLedgerError("audit extracted root missing")
    try:
        return Path(os.path.commonpath([str(path.parent) for path in paths]))
    except ValueError:
        raise IndustRealLedgerError("audit paths have no common extracted root")


def _build_row(
    record: dict[str, Any], verified_on: str, root: Path, workspace_root: Path
) -> dict[str, str]:
    rid = str(record.get("source_recording_id", "")).strip()
    video = _video_fields(record)
    path = Path(str(video.get("path", record.get("source_path", ""))))
    if not path.is_absolute():
        path = (root / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise IndustRealLedgerError(f"source path outside extracted root: {rid}")
    try:
        relative_source_path = path.relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        raise IndustRealLedgerError(f"source path outside workspace root: {rid}")
    if not path.is_file():
        raise IndustRealLedgerError(f"source video missing: {rid}")
    frame_count = int(video["frame_count"])
    width = int(video["width"])
    height = int(video["height"])
    root_id = f"INDUSTREAL-{rid}"
    complete_job_id = str(
        record.get("complete_job_id", f"INDUSTREAL-RECORDING-{rid}")
    )
    if complete_job_id != f"INDUSTREAL-RECORDING-{rid}":
        raise IndustRealLedgerError(f"complete recording identity mismatch: {rid}")
    return {
        "schema_version": "1.0.0",
        "asset_id": root_id,
        "record_type": "CANONICAL_ROOT_VIDEO",
        "source_dataset": "IndustReal",
        "source_path": relative_source_path,
        "canonical_root_id": root_id,
        "canonical_group_id": f"INDUSTREAL::{rid}",
        "parent_asset_id": "NONE",
        "derivation": "NONE",
        "frame_start": "0",
        "frame_end": str(frame_count - 1),
        "frame_count": str(frame_count),
        "width": str(width),
        "height": str(height),
        "operator_id": "UNKNOWN",
        "complete_job_id": complete_job_id,
        "task_id": "INDUSTREAL_ASSEMBLY",
        "error_types": "UNKNOWN",
        # Keep the audit marker out of the train/val/test namespace. Archive-only recordings remain registered but are not split data.
        "source_split": (
            str(record["official_split"])
            if str(record["official_split"]) != "UNKNOWN_OFFICIAL_UNLABELED"
            else "NOT_APPLICABLE"
        ),
        "project_split": "UNASSIGNED_BLOCKED_IDENTITY",
        "sha256": str(video["sha256"]).lower(),
        "hash_verified_on": verified_on,
        "manually_viewed": "UNKNOWN",
        "view_purpose": "UNKNOWN",
        "annotation_status": (
            "OFFICIAL_ACTION_ANNOTATIONS_AVAILABLE"
            if str(record["official_split"]) != "UNKNOWN_OFFICIAL_UNLABELED"
            else "OFFICIAL_ACTION_ANNOTATIONS_UNAVAILABLE"
        ),
        "contamination_status": (
            "OFFICIAL_TEST_SOURCE"
            if str(record["official_split"]) == "test"
            else "OFFICIAL_SOURCE"
        ),
        "formal_use_allowed": "NO",
        "split_locked": "YES",
        "independent_unit_eligible": "NO",
        "tombstone_id": "NONE",
        "tombstone_irreversible": "NO",
        "notes": "IndustReal official RGB root; the complete recording is the authoritative complete-job unit. Operator identity remains UNKNOWN, so formal use remains blocked.",
    }


def _manifest_record(row: dict[str, str], rid: str) -> dict[str, Any]:
    return {
        "manifest_version": "1.1.0",
        "asset_id": row["asset_id"],
        "video_id": rid,
        "source_dataset": "IndustReal",
        "source_split": row["source_split"],
        "project_split": row["project_split"],
        "canonical_root_id": row["canonical_root_id"],
        "operator_id": "UNKNOWN",
        "complete_job_id": row["complete_job_id"],
        "split_unit": [
            row["canonical_root_id"],
            "UNKNOWN",
            row["complete_job_id"],
            rid,
        ],
        "formal_use_allowed": "NO",
        "independent_unit_eligible": "NO",
        "tombstone_id": "NONE",
    }


def _validate_existing(rows: list[dict[str, str]], fields: list[str]) -> None:
    if fields != LEDGER_FIELDS:
        raise IndustRealLedgerError("ledger schema/header mismatch")
    for row in rows:
        # DictReader stores short rows as None values and extra columns under a
        # None key. Reject either shape before merging malformed rows.
        if set(row) != set(LEDGER_FIELDS) or any(
            not isinstance(value, str) for value in row.values()
        ):
            raise IndustRealLedgerError("malformed ledger row")
        if row["schema_version"] != "1.0.0":
            raise IndustRealLedgerError("invalid ledger schema version")
        if not row["source_dataset"]:
            raise IndustRealLedgerError("missing ledger source dataset")
        if not _SHA_RE.fullmatch(row["sha256"].lower()):
            raise IndustRealLedgerError("invalid ledger sha256")

    for field in ("asset_id", "sha256"):
        values = [row[field] for row in rows]
        if any(not value for value in values) or len(values) != len(set(values)):
            raise IndustRealLedgerError(f"duplicate or missing ledger {field}")

    by_asset = {row["asset_id"]: row for row in rows}
    roots: dict[str, list[dict[str, str]]] = {}
    group_roots: dict[str, str] = {}
    for row in rows:
        root_id = row["canonical_root_id"]
        group_id = row["canonical_group_id"]
        if not root_id or not group_id:
            raise IndustRealLedgerError("missing ledger root or group identity")
        roots.setdefault(root_id, []).append(row)
        prior_root = group_roots.setdefault(group_id, root_id)
        if prior_root != root_id:
            raise IndustRealLedgerError("canonical group spans multiple roots")

    for root_id, members in roots.items():
        root_rows = [
            row
            for row in members
            if row["asset_id"] == root_id and row["parent_asset_id"] == "NONE"
        ]
        if len(root_rows) != 1:
            raise IndustRealLedgerError(
                f"canonical root must have exactly one root record: {root_id}"
            )
        root = root_rows[0]
        for row in members:
            if row is root:
                continue
            parent = by_asset.get(row["parent_asset_id"])
            if parent is None:
                raise IndustRealLedgerError(
                    f"derived asset parent missing: {row['asset_id']}"
                )
            if parent["canonical_root_id"] != root_id:
                raise IndustRealLedgerError(
                    f"derived asset crosses canonical root: {row['asset_id']}"
                )
            if row["canonical_group_id"] != root["canonical_group_id"]:
                raise IndustRealLedgerError(
                    f"derived asset crosses canonical group: {row['asset_id']}"
                )
            if row["source_dataset"] != root["source_dataset"]:
                raise IndustRealLedgerError(
                    f"derived asset crosses source dataset: {row['asset_id']}"
                )
            if row["project_split"] != root["project_split"]:
                raise IndustRealLedgerError(
                    f"derived asset escapes root project split: {row['asset_id']}"
                )
            if root["formal_use_allowed"] == "NO" and row["formal_use_allowed"] != "NO":
                raise IndustRealLedgerError(
                    f"derived asset weakens formal-use exclusion: {row['asset_id']}"
                )
            if root["split_locked"] == "YES" and row["split_locked"] != "YES":
                raise IndustRealLedgerError(
                    f"derived asset weakens split lock: {row['asset_id']}"
                )
            if (
                root["tombstone_id"] != "NONE"
                or root["tombstone_irreversible"] == "YES"
            ):
                if (
                    row["tombstone_id"] != root["tombstone_id"]
                    or row["tombstone_irreversible"] != "YES"
                ):
                    raise IndustRealLedgerError(
                        f"derived asset escapes root tombstone: {row['asset_id']}"
                    )


def _validate_manifest(records: Any) -> None:
    if not isinstance(records, list):
        raise IndustRealLedgerError("manifest records missing")
    seen: set[tuple[str, str, str, str]] = set()
    seen_asset: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise IndustRealLedgerError("malformed manifest record")
        asset, root, video = (
            str(record.get("asset_id", "")),
            str(record.get("canonical_root_id", "")),
            str(record.get("video_id", "")),
        )
        if not asset or asset in seen_asset:
            raise IndustRealLedgerError("duplicate manifest asset")
        seen_asset.add(asset)
        key = (
            root,
            video,
            str(record.get("source_split", "")),
            str(record.get("project_split", "")),
        )
        if key in seen:
            raise IndustRealLedgerError("duplicate manifest mapping")
        for old_root, old_video, old_split, old_project in seen:
            if root == old_root and video == old_video and old_split != key[2]:
                raise IndustRealLedgerError("root/video crosses source split")
        seen.add(key)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _transactional_write(
    ledger: Path, manifest: Path, ledger_bytes: bytes, manifest_bytes: bytes
) -> None:
    parent = ledger.parent
    temps: list[Path] = []
    backups: list[tuple[Path, Path]] = []
    journal = parent / f".{ledger.name}.{manifest.name}.journal"
    try:
        for target, data in ((ledger, ledger_bytes), (manifest, manifest_bytes)):
            fd, name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=parent
            )
            os.close(fd)
            temp = Path(name)
            temps.append(temp)
            with temp.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        journal_bytes = json.dumps(
            {"targets": [str(ledger), str(manifest)]}, ensure_ascii=False
        ).encode("utf-8")
        with journal.open("wb") as stream:
            stream.write(journal_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(parent)
        for target in (ledger, manifest):
            backup = target.with_name(f".{target.name}.bak")
            shutil.copyfile(target, backup)
            # Track the backup before durability sync so cleanup also removes it if fsync itself fails.
            backups.append((target, backup))
            with backup.open("r+b") as stream:
                os.fsync(stream.fileno())
        os.replace(temps[0], ledger)
        os.replace(temps[1], manifest)
        _fsync_dir(parent)
    except Exception:
        for target, backup in reversed(backups):
            try:
                os.replace(backup, target)
            except OSError:
                pass
        raise
    finally:
        for path in temps + [backup for _, backup in backups] + [journal]:
            try:
                path.unlink()
            except OSError:
                pass
        _fsync_dir(parent)


def integrate(
    audit_path: str | Path,
    ledger_path: str | Path,
    manifest_path: str | Path,
    *,
    expected_audit_sha256: str | None = None,
    apply: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    if overwrite:
        raise IndustRealLedgerError("overwrite is disabled")
    audit_p, ledger_p, manifest_p = map(Path, (audit_path, ledger_path, manifest_path))
    if not audit_p.is_file():
        raise IndustRealLedgerError("audit missing")
    audit = json.loads(audit_p.read_text(encoding="utf-8"))
    supplied = str(audit.get("canonical_sha256", ""))
    if (
        not supplied
        or not _SHA_RE.fullmatch(supplied)
        or supplied != _canonical_hash(audit)
    ):
        raise IndustRealLedgerError("audit canonical hash not verified")
    if expected_audit_sha256 and supplied != expected_audit_sha256:
        raise IndustRealLedgerError("audit hash mismatch")
    if audit.get("status") != "PASS":
        raise IndustRealLedgerError("audit status is not PASS")
    verified_on = str(audit.get("verified_on", ""))
    if not _DATE_RE.fullmatch(verified_on):
        raise IndustRealLedgerError("audit verified_on must be fixed ISO date")
    try:
        datetime.strptime(verified_on, "%Y-%m-%d")
    except ValueError:
        raise IndustRealLedgerError("audit verified_on invalid")
    records = audit.get("records")
    if not isinstance(records, list) or len(records) != OFFICIAL_RECORDING_COUNT:
        raise IndustRealLedgerError(
            f"audit must contain exactly {OFFICIAL_RECORDING_COUNT} records"
        )
    ids = [str(r.get("source_recording_id", "")).strip() for r in records]
    anchor = _anchor_ids(audit)
    if (
        anchor is None
        or len(anchor) != OFFICIAL_RECORDING_COUNT
        or set(anchor) != set(ids)
    ):
        raise IndustRealLedgerError("official ID anchor not verified")
    if (
        any(not _ID_RE.fullmatch(x) for x in ids)
        or len(set(ids)) != OFFICIAL_RECORDING_COUNT
    ):
        raise IndustRealLedgerError("invalid or duplicate recording IDs")
    root = _source_root(audit, records, audit_p).resolve()
    workspace_root = ledger_p.parent.parent.resolve()
    try:
        root.relative_to(workspace_root)
    except ValueError as exc:
        raise IndustRealLedgerError("extracted root outside workspace root") from exc
    split_by_id = {
        rid: str(record.get("official_split", "")).strip()
        for record, rid in zip(records, ids)
    }
    labeled_ids = {
        rid
        for rid, split in split_by_id.items()
        if split.lower() in {"train", "val", "test"}
    }
    unlabeled_ids = {
        rid
        for rid, split in split_by_id.items()
        if split == "UNKNOWN_OFFICIAL_UNLABELED"
    }
    if (
        len(labeled_ids) != OFFICIAL_LABELED_COUNT
        or unlabeled_ids != OFFICIAL_UNLABELED_IDS
    ):
        raise IndustRealLedgerError(
            "official split coverage does not match labeled/archive-only IDs"
        )
    if set(split_by_id) != labeled_ids | unlabeled_ids:
        raise IndustRealLedgerError("invalid official split")
    new_rows: list[dict[str, str]] = []
    for record, rid in zip(records, ids):
        video = _video_fields(record)
        sha = str(video.get("sha256", ""))
        if not _SHA_RE.fullmatch(sha):
            raise IndustRealLedgerError(f"invalid SHA256: {rid}")
        for key in ("bytes", "frame_count", "width", "height"):
            _finite_positive(video.get(key), key, rid)
        _finite_positive(video.get("fps"), "fps", rid)
        _finite_positive(video.get("duration_seconds"), "duration_seconds", rid)
        if (
            not str(video.get("codec", "")).strip()
            or str(video.get("codec")).upper() == "UNKNOWN"
        ):
            raise IndustRealLedgerError(f"invalid codec: {rid}")
        dec = video.get("decode")
        if not isinstance(dec, dict) or any(
            dec.get(k) is not True for k in ("first", "mid", "last")
        ):
            raise IndustRealLedgerError(f"decode audit failed: {rid}")
        split = str(record.get("official_split", "")).strip()
        if (
            split.lower() not in {"train", "val", "test"}
            and split != "UNKNOWN_OFFICIAL_UNLABELED"
        ):
            raise IndustRealLedgerError(f"missing official split: {rid}")
        new_rows.append(_build_row(record, verified_on, root, workspace_root))
    existing, fields = _read_rows(ledger_p)
    _validate_existing(existing, fields)
    by_asset = {r["asset_id"]: r for r in existing}
    immutable_fields = (
        "asset_id",
        "record_type",
        "source_dataset",
        "source_path",
        "canonical_root_id",
        "canonical_group_id",
        "parent_asset_id",
        "frame_start",
        "frame_end",
        "frame_count",
        "width",
        "height",
        "sha256",
        "hash_verified_on",
    )
    migrated_by_asset: dict[str, dict[str, str]] = {}
    for row in new_rows:
        old = by_asset.get(row["asset_id"])
        if old is None:
            continue
        if not row["asset_id"].startswith("INDUSTREAL-"):
            if old != row:
                raise IndustRealLedgerError(
                    f"asset collision or hash change: {row['asset_id']}"
                )
            continue
        if any(old[field] != row[field] for field in immutable_fields):
            raise IndustRealLedgerError(
                f"immutable provenance mismatch: {row['asset_id']}"
            )
        migrated_by_asset[row["asset_id"]] = row
    merged = [migrated_by_asset.get(old["asset_id"], old) for old in existing]
    merged.extend(row for row in new_rows if row["asset_id"] not in by_asset)
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    _validate_manifest(manifest.get("records"))
    old_records = manifest["records"]
    by_manifest = {str(r["asset_id"]): r for r in old_records}
    additions = []
    migrated_manifest: dict[str, dict[str, Any]] = {}
    for row, rid in zip(new_rows, ids):
        item = _manifest_record(row, rid)
        old = by_manifest.get(row["asset_id"])
        if old is not None and old != item:
            if not row["asset_id"].startswith("INDUSTREAL-"):
                raise IndustRealLedgerError(f"manifest collision: {row['asset_id']}")
            for field in (
                "asset_id",
                "video_id",
                "source_dataset",
                "canonical_root_id",
            ):
                if str(old.get(field, "")) != str(item[field]):
                    raise IndustRealLedgerError(
                        f"manifest immutable mismatch: {row['asset_id']}"
                    )
            migrated_manifest[row["asset_id"]] = item
        if old is None:
            additions.append(item)
    out_manifest = dict(manifest)
    out_manifest["status"] = "BLOCKED_MISSING_EXTERNAL_IDENTITY"
    out_manifest["status_scope"] = "GLOBAL_OPERATOR_LEVEL_IDENTITY"
    out_manifest["g2_admission_scope"] = (
        "MECCANO_FORMAL_CANDIDATE_PLUS_INDUSTREAL_ACTION_STEP_STATE_ONLY"
    )
    out_manifest["status_interpretation"] = (
        "The top-level BLOCKED status applies to unavailable IndustReal "
        "operator-level identity and detection/generalization claims; it "
        "does not override dataset-scoped MECCANO READY admission or the "
        "IndustReal complete-job action/step/state-only boundary."
    )
    out_manifest["records"] = [
        migrated_manifest.get(str(old["asset_id"]), old) for old in old_records
    ] + additions
    _validate_manifest(out_manifest["records"])
    result = {
        "status": "PASS",
        "audit_sha256": supplied,
        "added": len(additions),
        "ledger_rows": len(merged),
    }
    if not apply:
        return result | {"dry_run": True}
    ledger_text = _csv_bytes(LEDGER_FIELDS, merged)
    manifest_text = (
        json.dumps(out_manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    _transactional_write(ledger_p, manifest_p, ledger_text, manifest_text)
    return result | {"dry_run": False}


def _csv_bytes(fields: list[str], rows: list[dict[str, str]]) -> bytes:
    import io

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=fields, quoting=csv.QUOTE_ALL, lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")
