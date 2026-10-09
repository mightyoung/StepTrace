"""Fail-closed audit of the official IndustReal RGB video archive."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from .industreal_capability import audit_industreal_capability

import cv2

OFFICIAL_ARCHIVE_SIZE = 4_960_644_152
OFFICIAL_ARCHIVE_MD5 = "c5b8901dba179d2eb10e9348f8163c79"
OFFICIAL_COUNT = 86
OFFICIAL_LABELED_COUNT = 84
OFFICIAL_PARTICIPANT_GROUP_COUNT = 27
OFFICIAL_ARCHIVE_ONLY = {"15_main_0_1", "21_assy_1_1"}
ID_RE = re.compile(r"^\d{2}_(?:assy|main)_\d_\d$")
MEMBER_RE = re.compile(r"^\d{2}_(?:assy|main)_\d_\d\.mp4$")


def recording_prefix(recording_id: str) -> str:
    """Return the first-token participant-group proxy from an official ID."""
    return str(recording_id).split("_", 1)[0]


def action_recording_prefix_groups(
    recordings: dict[str, str], *, expected_count: int | None = None
) -> dict[str, str]:
    groups: dict[str, str] = {}
    errors: list[str] = []
    for recording_id, split in recordings.items():
        prefix = recording_prefix(recording_id)
        old = groups.get(prefix)
        if old is not None and old != split:
            errors.append(
                f"recording prefix appears in multiple action splits: {prefix}"
            )
        groups[prefix] = split
    if expected_count is not None and len(groups) != expected_count:
        errors.append(
            f"expected {expected_count} action-label recording prefixes, observed {len(groups)}"
        )
    if errors:
        raise ValueError("; ".join(errors))
    return dict(sorted(groups.items()))


def canonical_sha256(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body["canonical_sha256"] = ""
    canonical_json = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)
    raw = (canonical_json + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def official_recording_ids_sha256(ids: Iterable[str]) -> str:
    raw = ("\n".join(sorted(str(item) for item in ids)) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def action_recordings(
    labels_dir: str | Path,
    *,
    expected_count: int = OFFICIAL_LABELED_COUNT,
    expected_recording_ids: Iterable[str] | None = None,
    expected_prefix_group_count: int | None = None,
) -> dict[str, str]:
    root = Path(labels_dir)
    found: dict[str, str] = {}
    errors: list[str] = []
    for split in ("train", "val", "test"):
        path = root / f"{split}.csv"
        if not path.is_file():
            errors.append(f"missing action-label split: {path}")
            continue
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.reader(stream):
                    if not row or all(not str(v).strip() for v in row):
                        continue
                    rid = str(row[0]).strip() if row else ""
                    if not ID_RE.fullmatch(rid):
                        errors.append(f"malformed action-label recording ID: {rid}")
                        continue
                    old = found.get(rid)
                    if old is not None and old != split:
                        errors.append(
                            f"recording appears in multiple action splits: {rid}"
                        )
                    found[rid] = split
        except (OSError, UnicodeError, csv.Error) as exc:
            errors.append(f"malformed action-label file: {path}: {exc}")
    if expected_recording_ids is not None:
        expected = {str(x) for x in expected_recording_ids}
        if set(found) != expected:
            errors.append(
                "action-label recording IDs do not match expected official IDs"
            )
    if len(found) != expected_count:
        errors.append(
            f"expected {expected_count} action-labeled recordings, observed {len(found)}"
        )
    try:
        action_recording_prefix_groups(
            found, expected_count=expected_prefix_group_count
        )
    except ValueError as exc:
        errors.extend(str(exc).split("; "))
    if errors:
        raise ValueError("; ".join(errors))
    return found


def _digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _video_info(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    info: dict[str, Any] = {"path": str(path.resolve()), "bytes": path.stat().st_size}
    info["sha256"] = _digest(path, "sha256")
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            errors.append(f"video could not be opened: {path.name}")
            return None, errors
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        count = int(round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        width = int(round(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
        height = int(round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        fourcc_num = int(round(cap.get(cv2.CAP_PROP_FOURCC)))
        codec = (
            "".join(chr((fourcc_num >> (8 * i)) & 0xFF) for i in range(4)).strip(
                "\x00 "
            )
            or "UNKNOWN"
        )
        info.update(
            {
                "codec": codec,
                "fps": fps,
                "width": width,
                "height": height,
                "resolution": {"width": width, "height": height},
                "frame_count": count,
                "duration_seconds": count / fps if fps > 0 else float("nan"),
                "decode": {},
            }
        )
        for label, pos in (
            ("first", 0),
            ("mid", max(0, count // 2)),
            ("last", max(0, count - 1)),
        ):
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            info["decode"][label] = bool(ok and frame is not None and frame.size > 0)
        if not all(info["decode"].values()):
            errors.append(f"video decode failed: {path.name}")
        if not (
            info["bytes"] > 0
            and codec != "UNKNOWN"
            and width > 0
            and height > 0
            and count > 0
            and math.isfinite(fps)
            and fps > 0
            and math.isfinite(info["duration_seconds"])
        ):
            errors.append(f"incomplete or UNKNOWN video metadata: {path.name}")
        return info, errors
    finally:
        cap.release()


def _archive_members(zpath: Path) -> tuple[list[zipfile.ZipInfo], list[str]]:
    errors: list[str] = []
    try:
        archive = zipfile.ZipFile(zpath)
        members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        return [], [f"invalid ZIP archive: {exc}"]
    raw_seen: set[str] = set()
    norm_seen: set[str] = set()
    valid: list[zipfile.ZipInfo] = []
    for member in members:
        name = member.filename
        norm = name.replace("\\", "/")
        if name in raw_seen:
            errors.append(f"duplicate raw archive member: {name}")
        raw_seen.add(name)
        key = norm.casefold()
        if key in norm_seen:
            errors.append(f"duplicate normalized archive member: {name}")
        norm_seen.add(key)
        mode = (member.external_attr >> 16) & 0o170000
        if (
            name.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", name)
            or "\\" in name
            or any(p in ("", ".", "..") for p in norm.split("/"))
        ):
            errors.append(f"unsafe archive member: {name}")
            continue
        if mode == 0o120000:
            errors.append(f"symlink archive member: {name}")
            continue
        if member.flag_bits & 1:
            errors.append(f"encrypted archive member: {name}")
            continue
        if member.is_dir():
            errors.append(f"unexpected regular archive member: {name}")
            continue
        if member.file_size == 0:
            errors.append(f"zero-size archive member: {name}")
            continue
        if "/" in norm:
            errors.append(f"archive member not at root: {name}")
            continue
        if not MEMBER_RE.fullmatch(norm):
            errors.append(f"unexpected regular archive member: {name}")
            continue
        valid.append(member)
    try:
        bad = archive.testzip()
        if bad:
            errors.append(f"CRC failure: {bad}")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        errors.append(f"CRC failure: {exc}")
    archive.close()
    return valid, errors


def audit_industreal_videos(
    zip_path: str | Path,
    labels_dir: str | Path,
    *,
    expected_size: int = OFFICIAL_ARCHIVE_SIZE,
    expected_md5: str = OFFICIAL_ARCHIVE_MD5,
    expected_count: int = OFFICIAL_COUNT,
    expected_recording_ids: Iterable[str] | None = None,
    extracted_dir: str | Path | None = None,
    verified_on: str | None = None,
) -> dict[str, Any]:
    zpath = Path(zip_path)
    errors: list[str] = []
    warnings: list[str] = []
    verified = verified_on or date.today().isoformat()
    try:
        parsed_verified = datetime.strptime(verified, "%Y-%m-%d")
        if parsed_verified.strftime("%Y-%m-%d") != verified:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"verified_on invalid: {verified}")
    archive_md5 = ""
    if zpath.is_file():
        if expected_size is not None and zpath.stat().st_size != expected_size:
            errors.append("archive size mismatch")
        archive_md5 = _digest(zpath, "md5")
        if expected_md5 and archive_md5 != expected_md5:
            errors.append("archive MD5 mismatch")
    else:
        errors.append(f"missing archive: {zpath}")
    labeled_expected = (
        expected_count - len(OFFICIAL_ARCHIVE_ONLY)
        if expected_count == OFFICIAL_COUNT
        else expected_count
    )
    try:
        labels = action_recordings(
            labels_dir,
            expected_count=labeled_expected,
            expected_recording_ids=None,
            expected_prefix_group_count=(
                OFFICIAL_PARTICIPANT_GROUP_COUNT
                if expected_count == OFFICIAL_COUNT
                else None
            ),
        )
    except ValueError as exc:
        labels = {}
        errors.extend(str(exc).split("; "))
    members, member_errors = _archive_members(zpath) if zpath.is_file() else ([], [])
    errors.extend(member_errors)
    ids = sorted(m.filename[:-4] for m in members if MEMBER_RE.fullmatch(m.filename))
    expected_ids = (
        {str(x) for x in expected_recording_ids}
        if expected_recording_ids is not None
        else None
    )
    if len(ids) != expected_count:
        errors.append(f"expected {expected_count} archive videos, observed {len(ids)}")
    if expected_ids is not None and set(ids) != expected_ids:
        errors.append("archive recording IDs do not match expected official IDs")
    if expected_count == OFFICIAL_COUNT and (
        set(ids) & OFFICIAL_ARCHIVE_ONLY
    ) != OFFICIAL_ARCHIVE_ONLY:
        errors.append("archive-only recording IDs do not match official two-ID set")
    if (
        expected_ids is None
        and expected_count == OFFICIAL_COUNT
        and set(ids) != set(labels) | OFFICIAL_ARCHIVE_ONLY
    ):
        errors.append(
            "archive recording IDs do not match official labels plus archive-only IDs"
        )
    if len(labels) != labeled_expected:
        warnings.append(
            f"declared {labeled_expected} labeled recordings, observed {len(labels)}"
        )
    if expected_count == OFFICIAL_COUNT and len(ids) == OFFICIAL_COUNT:
        warnings.append(
            "official metadata/action labels cover 84 recordings while the verified "
            "official RGB archive contains 86; the two archive-only recordings retain "
            "UNKNOWN_OFFICIAL_UNLABELED source split"
        )
    extracted_root = (
        Path(extracted_dir).resolve() if extracted_dir is not None else None
    )
    if extracted_root is None:
        errors.append("extracted_dir is required for complete video audit")
    elif not errors and not extracted_root.exists():
        tmp = Path(
            tempfile.mkdtemp(
                prefix=f".{extracted_root.name}.", dir=str(extracted_root.parent)
            )
        )
        try:
            with zipfile.ZipFile(zpath) as archive:
                archive.extractall(tmp)
            os.replace(tmp, extracted_root)
        except Exception as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            errors.append(f"safe extraction failed: {exc}")
    records: list[dict[str, Any]] = []
    prefix_groups = (
        action_recording_prefix_groups(labels)
        if labels
        else {}
    )
    for rid in ids:
        split = labels.get(rid, "UNKNOWN_OFFICIAL_UNLABELED")
        record: dict[str, Any] = {
            "source_recording_id": rid,
            "recording_prefix": recording_prefix(rid),
            "participant_group_proxy": recording_prefix(rid),
            "official_split": split,
            "operator_id": "UNKNOWN",
            "complete_job_id": f"INDUSTREAL-RECORDING-{rid}",
            "project_analysis_unit": "source_recording_id",
            "unit_status": "AUTHORITATIVE_COMPLETE_RECORDING_UNIT",
            "project_split": "UNASSIGNED_BLOCKED_IDENTITY",
            "formal_eligible": False,
            "video": None,
        }
        if extracted_root is not None and extracted_root.exists():
            path = extracted_root / f"{rid}.mp4"
            info, verr = (
                _video_info(path)
                if path.is_file()
                else (None, [f"missing extracted video: {rid}"])
            )
            record["video"] = info
            errors.extend(verr)
        records.append(record)
    if extracted_root is not None and extracted_root.exists():
        actual = {p.name for p in extracted_root.glob("*.mp4")}
        expected_names = {f"{rid}.mp4" for rid in ids}
        for extra in sorted(actual - expected_names):
            errors.append(f"unexpected extracted MP4: {extra}")
        all_entries = {p.name for p in extracted_root.iterdir()}
        for extra in sorted(all_entries - expected_names):
            if extra not in actual:
                errors.append(f"unexpected extracted member: {extra}")
        if actual != expected_names:
            errors.append("extracted directory contents mismatch")
    report = {
        "status": "PASS" if not errors else "BLOCKED",
        "errors": errors,
        "warnings": warnings,
        "verified_on": verified,
        "archive_path": str(zpath.resolve()),
        "archive": {
            "path": str(zpath.resolve()),
            "bytes": zpath.stat().st_size if zpath.is_file() else 0,
            "md5": archive_md5,
            "crc_test": not any("CRC failure" in e for e in errors),
            "observed_count": len(ids),
        },
        "extracted_root": str(extracted_root) if extracted_root else None,
        "official_recording_ids": ids,
        "official_recording_ids_sha256": official_recording_ids_sha256(ids),
        "action_labeled_recording_ids": sorted(labels),
        "archive_only_recording_ids": sorted(set(ids) & OFFICIAL_ARCHIVE_ONLY),
        "participant_group_proxy": {
            "kind": "recording_prefix",
            "source": "official_action_label_recording_id",
            "group_count": len(prefix_groups),
            "recording_prefixes": prefix_groups,
            "operator_id": "UNKNOWN",
        },
        "records": records,
        "canonical_sha256": "",
    }
    report.update(audit_industreal_capability(report))
    report["canonical_sha256"] = canonical_sha256(report)
    return report


def write_inventory(path: str | Path, report: dict[str, Any]) -> None:
    if report.get("canonical_sha256") != canonical_sha256(report):
        raise ValueError("report canonical_sha256 does not verify")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False)
    data = (serialized + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


register_industreal_videos = audit_industreal_videos
