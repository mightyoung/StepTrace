"""Register verified official MECCANO RGB roots and anonymous study identities.

The registry is deliberately idempotent: existing rows are preserved by asset
id, while missing official canonical roots are derived from the frozen archive
inventory and the official action-annotation split files.
"""

from __future__ import annotations

import argparse
import csv
import json
import io
import os
import tempfile
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "datasets/MECCANO/official/inventory.json"
ACTION_ROOT = ROOT / "datasets/MECCANO/MECCANO_action_annotations"
LEDGER = ROOT / "research/dataset-ledger.csv"
MANIFEST = ROOT / "research/split-manifest.json"
CURRENT_AUDIT = ROOT / "research/audits/split-manifest-current.json"
IDENTITY_PROVENANCE = ROOT / "research/audits/meccano-identity-provenance.json"

PROJECT_SPLITS = {
    "train": "DEVELOPMENT",
    "val": "DEVELOPMENT",
    "test": "DEVELOPMENT",
}


def _sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _provenance_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _anonymous_identity(video_id: str) -> tuple[str, str]:
    sequence = int(video_id)
    if not 1 <= sequence <= 20:
        raise ValueError(f"MECCANO sequence is outside 0001..0020: {video_id}")
    return f"MECCANO-P-{sequence:02d}", f"MECCANO-ASSEMBLY-{sequence:02d}"


def _identity_provenance(inventory_path: Path, action_root: Path) -> dict:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    action_sources = []
    for filename in (
        "MECCANO_train_actions.csv",
        "MECCANO_val_actions.csv",
        "MECCANO_test_actions.csv",
    ):
        path = action_root / filename
        action_sources.append(
            {
                "path": _provenance_path(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256_bytes(path.read_bytes()),
            }
        )
    artifact = {
        "schema_version": "meccano-identity-provenance-1.0.0",
        "status": "VERIFIED_ANONYMOUS_SEQUENCE_IDENTITY",
        "source": {
            "publisher": "IPLab, University of Catania",
            "url": "https://iplab.dmi.unict.it/MECCANO/",
            "retrieved_on": "2026-07-21",
            "verbatim_evidence": [
                "20 different subjects in 2 countries (IT, U.K.)",
                "subjects built a toy model of a motorbike",
            ],
        },
        "local_inventory": {
            "path": _provenance_path(inventory_path),
            "bytes": inventory_path.stat().st_size,
            "sha256": _sha256_bytes(inventory_path.read_bytes()),
            "canonical_video_count": len(inventory.get("videos", [])),
        },
        "official_action_split_sources": action_sources,
        "mapping_policy": {
            "operator_id": "MECCANO-P-{sequence_number}",
            "complete_job_id": "MECCANO-ASSEMBLY-{sequence_number}",
            "basis": "The official release contains exactly 20 canonical sequences for 20 different subjects; sequence-scoped anonymous study identities preserve the documented one-subject-per-sequence independent unit without asserting names or demographics.",
            "personal_identity_claimed": False,
            "demographic_identity_claimed": False,
        },
        "tombstone_0009_preserved": True,
    }
    artifact["artifact_sha256"] = _sha256_bytes(
        json.dumps(
            artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    return artifact


def _write_transaction(entries: list[tuple[Path, bytes]]) -> None:
    """Commit all outputs together, restoring original bytes if a replace fails."""
    prepared: list[tuple[Path, str]] = []
    originals: dict[Path, bytes | None] = {}
    try:
        for path, payload in entries:
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = path.read_bytes() if path.exists() else None
            fd, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            prepared.append((path, temporary))
        replaced: list[Path] = []
        try:
            for path, temporary in prepared:
                os.replace(temporary, path)
                replaced.append(path)
        except Exception:
            for path in reversed(replaced):
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    fd, temporary = tempfile.mkstemp(
                        prefix=f".{path.name}.rollback.", suffix=".tmp", dir=path.parent
                    )
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(original)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
            raise
    finally:
        for _, temporary in prepared:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _official_splits(action_root: Path = ACTION_ROOT) -> dict[str, str]:
    result: dict[str, str] = {}
    for source_split, filename in (
        ("train", "MECCANO_train_actions.csv"),
        ("val", "MECCANO_val_actions.csv"),
        ("test", "MECCANO_test_actions.csv"),
    ):
        with (action_root / filename).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                result.setdefault(str(row["video_id"]).zfill(4), source_split)
    return result


def _load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _make_row(item: dict, source_split: str) -> dict[str, str]:
    video_id = Path(item["name"]).stem
    root_id = f"MECCANO-{video_id}"
    operator_id, complete_job_id = _anonymous_identity(video_id)
    return {
        "schema_version": "1.0.0",
        "asset_id": root_id,
        "record_type": "CANONICAL_ROOT_VIDEO",
        "source_dataset": "MECCANO",
        "source_path": f"datasets/MECCANO/official/{item['name']}",
        "canonical_root_id": root_id,
        "canonical_group_id": f"MECCANO::{operator_id}",
        "parent_asset_id": "NONE",
        "derivation": "NONE",
        "frame_start": "0",
        "frame_end": str(int(item["frame_count"]) - 1),
        "frame_count": str(item["frame_count"]),
        "width": str(item["width"]),
        "height": str(item["height"]),
        "operator_id": operator_id,
        "complete_job_id": complete_job_id,
        "task_id": "MECCANO_ASSEMBLY",
        "error_types": "UNKNOWN",
        "source_split": source_split,
        "project_split": PROJECT_SPLITS[source_split],
        "sha256": str(item["sha256"]).upper(),
        "hash_verified_on": date.today().isoformat(),
        "manually_viewed": "UNKNOWN",
        "view_purpose": "UNKNOWN",
        "annotation_status": "OFFICIAL_ACTION_ANNOTATIONS_AVAILABLE",
        "contamination_status": "OFFICIAL_TEST_SOURCE"
        if source_split == "test"
        else "MANUALLY_VIEWED",
        "formal_use_allowed": "NO",
        "split_locked": "YES",
        "independent_unit_eligible": "YES",
        "tombstone_id": "NONE",
        "tombstone_irreversible": "NO",
        "notes": "Official MECCANO RGB root with an anonymous sequence-scoped participant and complete-assembly identity bound by research/audits/meccano-identity-provenance.json. No personal or demographic identity is asserted.",
    }


def _migrate_identity(row: dict[str, str]) -> None:
    """Apply the frozen anonymous sequence identity to every MECCANO row."""
    if row.get("source_dataset", "").casefold() != "meccano":
        return
    if row.get("tombstone_id") == "TOMBSTONE-MECCANO-0009":
        row["operator_id"] = "UNKNOWN"
        row["complete_job_id"] = "UNKNOWN"
        row["canonical_group_id"] = "MECCANO::0009::IDENTITY_UNKNOWN"
        row["project_split"] = "DEVELOPMENT_ONLY_CONTAMINATED"
        row["formal_use_allowed"] = "NO"
        row["independent_unit_eligible"] = "NO"
        return
    candidates = (
        row.get("canonical_root_id", ""),
        row.get("asset_id", ""),
        Path(row.get("source_path", "")).stem,
    )
    video_id = ""
    for value in candidates:
        match = re.search(r"MECCANO-(\d{1,4})", str(value), re.IGNORECASE)
        if match:
            video_id = match.group(1).zfill(4)
            break
    if not video_id:
        raise ValueError(f"cannot recover MECCANO sequence identity: {row.get('asset_id')}")
    operator_id, complete_job_id = _anonymous_identity(video_id)
    row["operator_id"] = operator_id
    row["complete_job_id"] = complete_job_id
    row["canonical_group_id"] = f"MECCANO::{operator_id}"
    if row.get("record_type") == "CANONICAL_ROOT_VIDEO":
        row["project_split"] = PROJECT_SPLITS[row["source_split"]]
        row["formal_use_allowed"] = "NO"
        row["independent_unit_eligible"] = "YES"


def _manifest_record(row: dict[str, str]) -> dict[str, str | list[str]]:
    video_id = row["asset_id"].removeprefix("MECCANO-")
    return {
        "manifest_version": "1.1.0",
        "asset_id": row["asset_id"],
        "video_id": video_id,
        "source_dataset": row["source_dataset"],
        "source_split": row["source_split"],
        "project_split": row["project_split"],
        "canonical_root_id": row["canonical_root_id"],
        "canonical_group_id": row.get("canonical_group_id", "UNKNOWN"),
        "operator_id": row["operator_id"],
        "complete_job_id": row["complete_job_id"],
        "split_unit": [
            row["canonical_root_id"],
            row["operator_id"],
            row["complete_job_id"],
            video_id,
        ],
        "formal_use_allowed": row["formal_use_allowed"],
        "independent_unit_eligible": row["independent_unit_eligible"],
        "tombstone_id": row["tombstone_id"],
    }


def register(
    *,
    inventory_path: Path = INVENTORY,
    action_root: Path = ACTION_ROOT,
    ledger_path: Path = LEDGER,
    manifest_path: Path = MANIFEST,
    current_audit_path: Path = CURRENT_AUDIT,
    identity_provenance_path: Path = IDENTITY_PROVENANCE,
) -> dict:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    split_by_video = _official_splits(action_root)
    items = {Path(item["name"]).stem: item for item in inventory["videos"]}
    if set(items) != set(split_by_video):
        raise ValueError(
            "official inventory and action split CSV do not join to the same 20 videos"
        )
    fieldnames, rows = _load_rows(ledger_path)
    if not fieldnames:
        raise ValueError("dataset ledger has no header")
    by_asset = {row["asset_id"]: row for row in rows}
    for row in rows:
        _migrate_identity(row)
    for video_id in sorted(items):
        asset_id = f"MECCANO-{video_id}"
        if asset_id == "MECCANO-0009":
            if (
                asset_id not in by_asset
                or by_asset[asset_id]["tombstone_id"] != "TOMBSTONE-MECCANO-0009"
            ):
                raise ValueError(
                    "frozen MECCANO-0009 tombstone row is missing or altered"
                )
            continue
        by_asset.setdefault(
            asset_id, _make_row(items[video_id], split_by_video[video_id])
        )
    for row in by_asset.values():
        _migrate_identity(row)
    existing_assets = {r["asset_id"] for r in rows}
    ordered = rows + [
        by_asset[f"MECCANO-{video_id}"]
        for video_id in sorted(items)
        if f"MECCANO-{video_id}" not in existing_assets and video_id != "0009"
    ]
    ledger_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        ledger_buffer, fieldnames=fieldnames, quoting=csv.QUOTE_ALL, lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(ordered)
    ledger_bytes = ledger_buffer.getvalue().encode("utf-8")
    records = [
        _manifest_record(row)
        for row in ordered
        if row["source_dataset"] == "MECCANO"
        and row["record_type"] == "CANONICAL_ROOT_VIDEO"
    ]
    existing_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {"records": []}
    )
    preserved_records = [
        record
        for record in existing_manifest.get("records", [])
        if record.get("source_dataset") != "MECCANO"
    ]
    identity_provenance = _identity_provenance(inventory_path, action_root)
    identity_provenance_bytes = (
        json.dumps(identity_provenance, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    wrapper = {
        "schema_version": "1.1.0",
        "status": "BLOCKED_MISSING_EXTERNAL_IDENTITY",
        "status_scope": "GLOBAL_OPERATOR_LEVEL_IDENTITY",
        "g2_admission_scope": (
            "MECCANO_FORMAL_CANDIDATE_PLUS_"
            "INDUSTREAL_ACTION_STEP_STATE_ONLY"
        ),
        "status_interpretation": (
            "The top-level BLOCKED status applies to unavailable IndustReal "
            "operator-level identity and detection/generalization claims; it "
            "does not override dataset-scoped MECCANO READY admission or the "
            "IndustReal complete-job action/step/state-only boundary."
        ),
        "dataset_identity_status": {
            "MECCANO": "READY",
            "IndustReal": "BLOCKED_UNKNOWN_OPERATOR_ID",
        },
        "meccano_identity_provenance": {
            "path": _provenance_path(identity_provenance_path),
            "file_sha256": _sha256_bytes(identity_provenance_bytes),
            "artifact_sha256": identity_provenance["artifact_sha256"],
        },
        "split_unit": [
            "canonical_root_id",
            "operator_id",
            "complete_job_id",
            "video_id",
        ],
        "rules": {
            "no_cross_split_identity": True,
            "adjacent_frames_inherit_root_split": True,
            "unknown_identity_blocks_calibration_pilot_formal_test": True,
        },
        "records": preserved_records + records,
    }
    manifest_bytes = (json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    _write_transaction(
        [
            (ledger_path, ledger_bytes),
            (manifest_path, manifest_bytes),
            (current_audit_path, manifest_bytes),
            (identity_provenance_path, identity_provenance_bytes),
        ]
    )
    return {
        "ledger_rows": len(ordered),
        "meccano_roots": len(records),
        "status": wrapper["status"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    print(json.dumps(register(), ensure_ascii=False, sort_keys=True))
