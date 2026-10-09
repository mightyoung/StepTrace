from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_development_real_data_batch.py"
SPEC = importlib.util.spec_from_file_location("development_batch_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_four_video_design_preserves_development_only_unit_boundary() -> None:
    design = validator.load_design(
        ROOT
        / "configs"
        / "experiments"
        / "development-real-data-four-video-v1.json"
    )

    assert design["output_artifact_count"] == 4
    assert len(design["runs"]) == 4
    assert design["distinct_source_recording_count"] == 1
    assert design["independent_unit_count"] == 1
    assert design["four_outputs_are_independent_replicates"] is False
    assert design["inferential_comparison_allowed"] is False
    assert design["formal_evidence"] is False
    assert design["g3_evidence"] is False


def test_four_video_design_rejects_pseudoreplication_claim(tmp_path: Path) -> None:
    source = (
        ROOT
        / "configs"
        / "experiments"
        / "development-real-data-four-video-v1.json"
    )
    design = json.loads(source.read_text(encoding="utf-8"))
    design["four_outputs_are_independent_replicates"] = True
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(design), encoding="utf-8")

    with pytest.raises(ValueError, match="development-only boundaries"):
        validator.load_design(invalid)


def test_selected_lifecycle_accepts_cancelled_later_runs() -> None:
    selected = {"run-a", "run-b"}
    ledger = [
        {"experiment_id": "run-a", "event": "START", "scope": "DEVELOPMENT_ONLY_REAL_DATA"},
        {"experiment_id": "run-a", "event": "EXIT", "exit_code": 0, "scope": "DEVELOPMENT_ONLY_REAL_DATA"},
        {"experiment_id": "run-b", "event": "START", "scope": "DEVELOPMENT_ONLY_REAL_DATA"},
        {"experiment_id": "run-b", "event": "EXIT", "exit_code": 0, "scope": "DEVELOPMENT_ONLY_REAL_DATA"},
        {"experiment_id": "run-c", "event": "START", "scope": "DEVELOPMENT_ONLY_REAL_DATA"},
        {"event": "CANCELLED", "scope": "DEVELOPMENT_ONLY_REAL_DATA"},
    ]

    validator.validate_selected_lifecycles(ledger, selected)


def test_selected_lifecycle_rejects_incomplete_selected_run() -> None:
    ledger = [
        {"experiment_id": "run-a", "event": "START", "scope": "DEVELOPMENT_ONLY_REAL_DATA"}
    ]

    with pytest.raises(RuntimeError, match="invalid selected lifecycle"):
        validator.validate_selected_lifecycles(ledger, {"run-a"})
