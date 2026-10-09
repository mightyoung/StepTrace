"""Registration and integrity audit helpers for immutable official MECCANO assets."""

from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Any, TypedDict


class MeccanoAudit(TypedDict):
    path: str
    exists: bool
    sha256: str
    sha256_match: bool | None
    decode_failures: int
    width: int | None
    height: int | None
    fps: float | None
    frame_count: int | None
    status: str


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_meccano_video(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    expected_frames: int | None = None,
) -> MeccanoAudit:
    p = Path(path)
    out: MeccanoAudit = {
        "path": str(p),
        "exists": p.is_file(),
        "sha256": "UNKNOWN",
        "sha256_match": None,
        "decode_failures": 0,
        "width": None,
        "height": None,
        "fps": None,
        "frame_count": None,
        "status": "BLOCKED_MISSING_EXTERNAL_DATA",
    }
    if not p.is_file():
        return out
    out["sha256"] = sha256_file(p)
    out["sha256_match"] = (
        expected_sha256 is None or out["sha256"].lower() == expected_sha256.lower()
    )
    try:
        import cv2

        cap = cv2.VideoCapture(str(p))
        out["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        out["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out["fps"] = float(cap.get(cv2.CAP_PROP_FPS))
        out["frame_count"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, _ = cap.read()
        cap.release()
        if not ok:
            out["decode_failures"] = 1
    except Exception:
        out["decode_failures"] = 1
    checks = [
        (expected_width is None or out["width"] == expected_width),
        (expected_height is None or out["height"] == expected_height),
        (expected_fps is None or abs((out["fps"] or 0.0) - expected_fps) < 0.01),
        (expected_frames is None or out["frame_count"] == expected_frames),
    ]
    out["status"] = (
        "PASS"
        if out["sha256_match"] is True and out["decode_failures"] == 0 and all(checks)
        else "FAIL"
    )
    return out


def register_meccano_video(
    path: str | Path,
    *,
    video_id: str,
    source_split: str = "UNKNOWN",
    tombstone_id: str | None = None,
    official_source: str | None = None,
    expected_sha256: str | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    expected_frames: int | None = None,
) -> dict[str, Any]:
    p = Path(path)
    exists = p.is_file()
    tombstone = tombstone_id or (
        f"TOMBSTONE-MECCANO-{video_id}" if video_id == "0009" else "NONE"
    )
    if exists:
        integrity = audit_meccano_video(
            p,
            expected_sha256=expected_sha256,
            expected_width=expected_width,
            expected_height=expected_height,
            expected_fps=expected_fps,
            expected_frames=expected_frames,
        )
    else:
        integrity = MeccanoAudit(
            path=str(p),
            exists=False,
            sha256="UNKNOWN",
            sha256_match=None,
            decode_failures=0,
            width=None,
            height=None,
            fps=None,
            frame_count=None,
            status="BLOCKED_MISSING_EXTERNAL_DATA",
        )
    return {
        "asset_id": f"MECCANO-{video_id}",
        "record_type": "CANONICAL_ROOT_VIDEO",
        "source_dataset": "MECCANO",
        "source_path": str(p),
        "canonical_root_id": f"MECCANO-{video_id}",
        "source_split": source_split or "UNKNOWN",
        "sha256": integrity.get("sha256", "UNKNOWN"),
        "status": "REGISTERED" if exists else "BLOCKED_MISSING_EXTERNAL_DATA",
        "integrity_status": integrity.get("status"),
        "decode_failures": integrity.get("decode_failures", 0),
        "width": integrity.get("width"),
        "height": integrity.get("height"),
        "fps": integrity.get("fps"),
        "frame_count": integrity.get("frame_count"),
        "official_source": official_source or "UNKNOWN",
        "tombstone_id": tombstone,
        "tombstone_irreversible": video_id == "0009",
    }
