"""Build a two-snapshot lineage from historical and current G1/G2 gates."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from display_project.current_gate_snapshot import build_current_gate_snapshot


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"gate is not an object: {path}")
    return value, raw


def _timestamp(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source(path: Path, gate: Mapping[str, Any], raw: bytes, logical_id: str):
    return {
        "logical_id": logical_id,
        "schema_version": str(gate.get("schema_version", "UNKNOWN")),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_timestamp": _iso(_timestamp(path)),
    }


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_lineage(
    historical_gate_path: Path,
    current_gate_path: Path,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    historical_gate, historical_raw = _load(historical_gate_path)
    current_gate, current_raw = _load(current_gate_path)
    if historical_gate.get("status") != "BLOCKED":
        raise ValueError("historical gate must be BLOCKED")
    if current_gate.get("status") != "PASS" or not (
        current_gate.get("g1", {}).get("status") == "PASS"
        and current_gate.get("g2", {}).get("status") == "PASS"
    ):
        raise ValueError("current gate must contain current G1/G2 PASS")
    if current_gate.get("execute_g3_method_panel_allowed") is not False:
        raise ValueError("joint gate may not authorize G3 execution")
    historical_time = _timestamp(historical_gate_path)
    current_time = _timestamp(current_gate_path)
    observed_now = now or datetime.now(timezone.utc)
    if current_time < historical_time:
        raise ValueError("gate source timestamps regress")
    initial_predicates = {
        "g1_prerequisite": "BLOCKED",
        "g2_prerequisite": "BLOCKED",
        "implementation_readiness": "BLOCKED",
        "g3_run_admission": "BLOCKED",
        "g3_decision": "NOT_RUN",
        "downstream_admission": "BLOCKED",
    }
    historical = build_current_gate_snapshot(
        snapshot_id="snapshot:pre-g3-history-0001",
        observed_at=_iso(historical_time),
        effective_at=_iso(historical_time),
        expires_at=_iso(historical_time + timedelta(days=30)),
        transition_event="INITIAL_BLOCKED",
        reason_codes=["HISTORICAL_G1_G2_BLOCKED"],
        predicates=initial_predicates,
        sources=[
            _source(
                historical_gate_path,
                historical_gate,
                historical_raw,
                "gate:g1-g2-historical",
            )
        ],
        now=observed_now,
    )
    current = build_current_gate_snapshot(
        snapshot_id="snapshot:pre-g3-current-0002",
        observed_at=_iso(current_time),
        effective_at=_iso(current_time),
        expires_at=_iso(current_time + timedelta(days=30)),
        transition_event="PREREQUISITE_REFRESH",
        reason_codes=[
            "REMOTE_REAL_ASSET_G1_PASS",
            "REMOTE_REAL_ASSET_G2_PASS",
            "IMPLEMENTATION_AUTHORITY_NOT_ATTESTED",
            "G3_RUN_ADMISSION_MISSING",
        ],
        predicates={
            **initial_predicates,
            "g1_prerequisite": "PASS",
            "g2_prerequisite": "PASS",
        },
        sources=[
            _source(
                current_gate_path,
                current_gate,
                current_raw,
                "gate:g1-g2-current",
            )
        ],
        predecessor=historical,
        now=observed_now,
    )
    return historical, current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-gate", type=Path, required=True)
    parser.add_argument("--current-gate", type=Path, required=True)
    parser.add_argument("--historical-output", type=Path, required=True)
    parser.add_argument("--current-output", type=Path, required=True)
    args = parser.parse_args(argv)
    historical, current = build_lineage(
        args.historical_gate.resolve(), args.current_gate.resolve()
    )
    _write(args.historical_output, historical)
    _write(args.current_output, current)
    print(
        json.dumps(
            {
                "historical": historical["artifact_sha256"],
                "current": current["artifact_sha256"],
                "predicates": current["predicates"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
