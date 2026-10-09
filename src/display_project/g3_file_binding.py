"""File-backed evidence binding helpers for G3 capability audits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_bound_json(path: str | Path) -> tuple[dict[str, Any], dict[str, str]]:
    source = Path(path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"evidence must be an object: {source}")
    return payload, {
        "path": source.as_posix(),
        "file_sha256": _file_sha256(source),
        "canonical_sha256": _canonical_sha256(payload),
    }


def verify_bound_json(evidence: Mapping[str, Any]) -> dict[str, Any]:
    path = evidence.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("evidence path missing")
    payload, actual = load_bound_json(path)
    if set(evidence) != set(actual):
        raise ValueError("evidence schema mismatch")
    for key, value in actual.items():
        if evidence.get(key) != value:
            raise ValueError(f"evidence {key} mismatch")
    return payload
