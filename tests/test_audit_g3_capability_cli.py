from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from display_project.g3_capability import G3_CAPABILITY_SCHEMA, canonical_sha256


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_direct_cli_writes_only_a_blocked_precheck(tmp_path: Path) -> None:
    output = tmp_path / "g3-precheck.json"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "audit_g3_capability.py"),
            "--task-id",
            "meccano-task",
            "--task-id",
            "industreal-task",
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == G3_CAPABILITY_SCHEMA
    assert payload["decision_type"] == "PRECHECK_ONLY"
    assert payload["status"] == "BLOCKED"
    assert payload["downstream_authorized"] is False
    expected_hash = canonical_sha256(
        {key: value for key, value in payload.items() if key != "artifact_sha256"}
    )
    assert payload["artifact_sha256"] == expected_hash
    printed = json.loads(result.stdout)
    assert printed["status"] == "BLOCKED"
    assert printed["artifact_sha256"] == expected_hash
