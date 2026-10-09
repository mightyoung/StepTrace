"""Fail-closed validation and registration for the IndustReal error extension."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any

ERROR_CATEGORY_MAP = {
    1: "missing_component",
    2: "incorrect_orientation",
    3: "incorrect_placement",
    4: "part_level_error",
}
SPLITS = {"train", "val", "test"}
IMAGE_RE = re.compile(
    r"^(?P<recording>\d{2}_(?:assy|main)_\d+_\d+)_(?P<frame>\d{6})\.jpg$"
)
EXPECTED_MD5 = "fb2e8551df9b5f97dfba39562e6a4e77"
EXPECTED_LABELS_SHA256 = (
    "bbb0bb08e469247f6e38b4fbc8a27e2e860863c7605b426c1ba82fde05831093"
)
EXPECTED_SIZE = 412456960


def _md5(path: Path) -> str:
    # The publisher supplies MD5 as an archive identity checksum. It is not
    # used for authentication or any security decision.
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body["canonical_sha256"] = ""
    text = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_registry_canonical(payload: dict[str, Any]) -> bool:
    """Verify the hash against the complete payload, including CLI fields."""
    supplied = payload.get("canonical_sha256")
    return isinstance(supplied, str) and supplied == _canonical_hash(payload)


def validate_error_extension(
    tar_path: str | Path,
    labels_path: str | Path,
    *,
    expected_md5: str | None = None,
    expected_size: int = EXPECTED_SIZE,
    expected_labels_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate source archive and extracted labels without trusting metadata."""
    archive = Path(tar_path)
    labels = Path(labels_path)
    errors: list[str] = []
    if not archive.is_file():
        errors.append("error extension tar missing")
    else:
        if archive.stat().st_size != expected_size:
            errors.append(f"tar size mismatch: {archive.stat().st_size}")
        if expected_md5 and _md5(archive) != expected_md5.lower():
            errors.append("tar MD5 mismatch")
        try:
            with tarfile.open(archive, "r:*") as tf:
                for member in tf.getmembers():
                    name = member.name.replace("\\", "/")
                    if name.startswith("/") or ".." in Path(name).parts:
                        errors.append(f"unsafe archive member: {member.name}")
                    if not (
                        name
                        in {
                            "assembly_states_errors",
                            "assembly_states_errors/",
                            "assembly_states_errors/images",
                            "assembly_states_errors/images/",
                            "assembly_states_errors/labels.json",
                        }
                        or name.startswith("assembly_states_errors/images/")
                    ):
                        errors.append(f"unexpected archive member: {member.name}")
        except (OSError, tarfile.TarError) as exc:
            errors.append(f"tar unreadable: {exc}")
    if not labels.is_file():
        errors.append("labels.json missing")
        payload: object = {}
    else:
        if expected_labels_sha256 and _sha256(labels) != expected_labels_sha256.lower():
            errors.append("labels SHA256 mismatch")
        try:
            payload = json.loads(labels.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"labels JSON unreadable: {exc}")
            payload = {}
    if not isinstance(payload, dict):
        errors.append("labels root must be object")
        payload = {}
    required = ["images", "labels", "bbox", "clean", "split", "intended", "error_cat"]
    if any(key not in payload for key in required):
        errors.append("labels missing required arrays")
        rows: list[dict[str, Any]] = []
    else:
        arrays = {key: payload[key] for key in required}
        lengths = {
            key: len(value) for key, value in arrays.items() if isinstance(value, list)
        }
        if len(lengths) != len(required) or len(set(lengths.values())) != 1:
            errors.append(f"array length mismatch: {lengths}")
        n = min(lengths.values(), default=0)
        rows = []
        recordings: dict[str, str] = {}
        for i in range(n):
            image = arrays["images"][i]
            match = IMAGE_RE.fullmatch(image) if isinstance(image, str) else None
            if not match:
                errors.append(f"invalid image name at {i}: {image!r}")
                continue
            recording, frame = match.group("recording"), int(match.group("frame"))
            split = arrays["split"][i]
            if split not in SPLITS:
                errors.append(f"unknown split at {i}: {split!r}")
            elif recording in recordings and recordings[recording] != split:
                errors.append(f"recording crosses split: {recording}")
            recordings[recording] = split
            bbox = arrays["bbox"][i]
            if (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or not all(isinstance(v, (int, float)) and v >= 0 for v in bbox)
                or bbox[2] <= 0
                or bbox[3] <= 0
            ):
                errors.append(f"invalid bbox at {i}")
            if not isinstance(arrays["labels"][i], int) or not isinstance(
                arrays["intended"][i], int
            ):
                errors.append(f"invalid label/intended type at {i}")
            cat = arrays["error_cat"][i]
            if cat not in ERROR_CATEGORY_MAP:
                errors.append(f"unknown error category at {i}: {cat!r}")
            if arrays["clean"][i] not in {0, 1}:
                errors.append(f"invalid clean flag at {i}")
            rows.append(
                {
                    "source_recording_id": recording,
                    "source_frame": frame,
                    "extension_split": split,
                    "label": arrays["labels"][i],
                    "clean": arrays["clean"][i],
                    "intended": arrays["intended"][i],
                    "error_cat": cat,
                    "error_category": ERROR_CATEGORY_MAP.get(cat, "UNKNOWN"),
                    "image": image,
                }
            )
    return {
        "status": "PASS" if not errors else "BLOCKED",
        "errors": sorted(set(errors)),
        "tar_path": str(archive),
        "labels_path": str(labels),
        "tar_size": archive.stat().st_size if archive.is_file() else None,
        "tar_md5": _md5(archive) if archive.is_file() else None,
        "labels_sha256": _sha256(labels) if labels.is_file() else None,
        "row_count": len(rows),
        "recording_count": len({r["source_recording_id"] for r in rows}),
        "split_counts": {
            split: sum(r["extension_split"] == split for r in rows)
            for split in sorted(SPLITS)
        },
        "error_category_counts": {
            str(cat): sum(r["error_cat"] == cat for r in rows)
            for cat in sorted(ERROR_CATEGORY_MAP)
        },
        "records": rows,
    }


def build_error_extension_registry(
    report: dict[str, Any], *, asd_zip: str | Path | None = None
) -> dict[str, Any]:
    """Add provenance/identity fields; never infer operator or complete-job IDs."""
    rows = []
    for item in report.get("records", []):
        rows.append(
            {
                **item,
                "operator_id": "UNKNOWN",
                "complete_job_id": "UNKNOWN",
                "project_analysis_unit": "recording_id",
                "status": "PROJECT_DERIVED_COMPLETE_VIDEO_UNIT",
            }
        )
    baseline: dict[str, Any] = {
        "status": "AVAILABLE_VERIFIED" if report.get("status") == "PASS" else "BLOCKED",
        "asset": "ASD_results_IndustRealplusSynthetic_test.zip",
        "role": "baseline_result_gt_bbox_state",
        "error_labels": False,
    }
    if asd_zip:
        path = Path(asd_zip)
        baseline.update(
            {
                "path": str(path),
                "size": path.stat().st_size if path.is_file() else None,
                "md5": _md5(path) if path.is_file() else None,
            }
        )
    out = {
        "schema_version": "industreal-error-extension-1.0.0",
        "source": "Extension to IndustReal Dataset: Assembly State Recognition with Error States",
        "status": report.get("status"),
        "asset_status": "AVAILABLE_VERIFIED"
        if report.get("status") == "PASS"
        else "BLOCKED",
        "psr_labels_with_errors": "MISSING_FROM_DOWNLOADED_LABEL_ASSET",
        "tar": {k: report.get(k) for k in ("tar_path", "tar_size", "tar_md5")},
        "labels": {
            k: report.get(k)
            for k in (
                "labels_path",
                "labels_sha256",
                "row_count",
                "recording_count",
                "split_counts",
                "error_category_counts",
            )
        },
        "error_category_map": {str(k): v for k, v in ERROR_CATEGORY_MAP.items()},
        "records": rows,
        "asd_baseline": baseline,
        "errors": report.get("errors", []),
        "canonical_sha256": "",
    }
    out["canonical_sha256"] = _canonical_hash(out)
    return out
