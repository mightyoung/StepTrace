"""Independent frozen commitments for paired experiment ledgers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from display_project.experiments.paired_runner import verify_ledger_integrity

ANCHOR_SCHEMA_VERSION = 1
ANCHOR_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "ledger_path",
        "ledger_schema",
        "row_count",
        "tail_hash",
        "ledger_hash",
        "artifact_hash",
    }
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ledger_facts(ledger_path: Path) -> tuple[int, str, str]:
    raw = ledger_path.read_bytes()
    errors, tail_hash = verify_ledger_integrity(raw)
    if errors:
        raise ValueError("ledger integrity invalid: " + "; ".join(errors[:3]))
    row_count = sum(bool(line.strip()) for line in raw.decode("utf-8").splitlines())
    return row_count, tail_hash, _sha256(raw)


def _anchor_body(
    ledger_path: Path, row_count: int, tail_hash: str, ledger_hash: str
) -> dict[str, Any]:
    return {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "artifact_type": "ledger_tail_anchor",
        "ledger_path": str(ledger_path.resolve()),
        "ledger_schema": "paired-attempt-ledger-v1",
        "row_count": row_count,
        "tail_hash": tail_hash,
        "ledger_hash": ledger_hash,
    }


def make_anchor(ledger_path: str | Path) -> dict[str, Any]:
    ledger = Path(ledger_path).resolve()
    rows, tail, ledger_hash = _ledger_facts(ledger)
    body = _anchor_body(ledger, rows, tail, ledger_hash)
    body["artifact_hash"] = _sha256(_canonical(body))
    return body


def write_anchor(ledger_path: str | Path, anchor_path: str | Path) -> dict[str, Any]:
    anchor = make_anchor(ledger_path)
    path = Path(anchor_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(anchor) + b"\n")
    return anchor


def validate_anchor(
    ledger_path: str | Path,
    anchor_path: str | Path,
    *,
    expected_anchor_hash: str | None = None,
) -> list[str]:
    ledger = Path(ledger_path).resolve()
    anchor_file = Path(anchor_path)
    errors: list[str] = []
    try:
        anchor = json.loads(anchor_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"ledger anchor unreadable: {exc}"]
    if not isinstance(anchor, dict):
        return ["ledger anchor must be an object"]
    if set(anchor) != ANCHOR_FIELDS:
        errors.append("ledger anchor schema fields are invalid")
    if (
        anchor.get("schema_version") != ANCHOR_SCHEMA_VERSION
        or anchor.get("artifact_type") != "ledger_tail_anchor"
    ):
        errors.append("ledger anchor schema version/type is invalid")
    if anchor.get("ledger_path") != str(ledger):
        errors.append("ledger anchor path mismatch")
    for key in ("tail_hash", "ledger_hash", "artifact_hash"):
        value = anchor.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            errors.append(f"ledger anchor {key} is invalid")
    row_count = anchor.get("row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        errors.append("ledger anchor row_count is invalid")
    body = dict(anchor)
    supplied = body.pop("artifact_hash", None)
    if isinstance(supplied, str) and _sha256(_canonical(body)) != supplied:
        errors.append("ledger anchor artifact hash mismatch")
    if expected_anchor_hash is not None:
        actual_file_hash = _sha256(anchor_file.read_bytes())
        if actual_file_hash != expected_anchor_hash:
            errors.append("ledger anchor external hash mismatch")
    try:
        rows, tail, ledger_hash = _ledger_facts(ledger)
        if anchor.get("row_count") != rows:
            errors.append("ledger anchor row_count mismatch")
        if anchor.get("tail_hash") != tail:
            errors.append("ledger anchor tail hash mismatch")
        if anchor.get("ledger_hash") != ledger_hash:
            errors.append("ledger anchor ledger hash mismatch")
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(f"ledger anchor ledger verification failed: {exc}")
    return errors
