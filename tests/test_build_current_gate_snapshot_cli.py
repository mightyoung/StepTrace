from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts.build_current_gate_snapshot import build_lineage


NOW = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)


def _gate(path: Path, status: str, *, execute: bool = False) -> Path:
    value = {
        "schema_version": "g1-g2-joint-gate-1.1.0",
        "status": status,
        "g1": {"status": "PASS" if status == "PASS" else "BLOCKED"},
        "g2": {"status": "PASS" if status == "PASS" else "BLOCKED"},
        "execute_g3_method_panel_allowed": execute,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_cli_builder_records_pass_without_run_authority(tmp_path: Path):
    historical = _gate(tmp_path / "historical.json", "BLOCKED")
    current = _gate(tmp_path / "current.json", "PASS")
    timestamp = datetime(2026, 8, 20, 12, tzinfo=timezone.utc).timestamp()
    import os

    os.utime(historical, (timestamp, timestamp))
    os.utime(current, (timestamp + 60, timestamp + 60))
    previous, snapshot = build_lineage(historical, current, now=NOW)
    assert snapshot["predecessor"]["artifact_sha256"] == previous["artifact_sha256"]
    assert snapshot["predicates"] == {
        "g1_prerequisite": "PASS",
        "g2_prerequisite": "PASS",
        "implementation_readiness": "BLOCKED",
        "g3_run_admission": "BLOCKED",
        "g3_decision": "NOT_RUN",
        "downstream_admission": "BLOCKED",
    }


def test_cli_builder_rejects_joint_gate_execution_authority(tmp_path: Path):
    historical = _gate(tmp_path / "historical.json", "BLOCKED")
    current = _gate(tmp_path / "current.json", "PASS", execute=True)
    with pytest.raises(ValueError, match="may not authorize"):
        build_lineage(historical, current, now=NOW)
