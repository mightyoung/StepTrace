from __future__ import annotations

import json
from pathlib import Path

from scripts import audit_g3_run_admission as cli
from tests.test_g3_run_admission import _snapshot


def test_cli_persists_non_authorizing_blocked_audit(tmp_path: Path):
    predecessor, current = _snapshot()
    predecessor_path = tmp_path / "previous.json"
    current_path = tmp_path / "current.json"
    output = tmp_path / "admission.json"
    predecessor_path.write_text(json.dumps(predecessor), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")
    result = cli.main(
        [
            "--current-snapshot",
            str(current_path),
            "--snapshot-predecessor",
            str(predecessor_path),
            "--output",
            str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result == 2
    assert payload["status"] == "BLOCKED"
    assert payload["ready"] is False
    assert payload["batch_receipt"] is None
    assert "STATISTICAL_ENGINE_MISSING" in payload["reason_codes"]
