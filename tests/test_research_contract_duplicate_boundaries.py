from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from display_project.research_contract import (
    ContractValidationError,
    validate_research_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST_PATH = (
    REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
)
LEDGER_PATH = REPO_ROOT / "research/dataset-ledger.csv"


def test_duplicate_ledger_header_is_rejected(tmp_path: Path) -> None:
    lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    lines[0] += ",asset_id"
    ledger_path = tmp_path / "dataset-ledger.csv"
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="duplicate ledger columns: .*asset_id"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


def test_extra_trailing_ledger_cell_is_rejected(tmp_path: Path) -> None:
    lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    lines[1] += ',"UNDECLARED"'
    ledger_path = tmp_path / "dataset-ledger.csv"
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="ledger row 2 has extra trailing cells"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


def test_missing_ledger_cell_is_rejected(tmp_path: Path) -> None:
    with LEDGER_PATH.open("r", encoding="utf-8", newline="") as handle:
        matrix = list(csv.reader(handle))
    matrix[1] = matrix[1][:-1]
    ledger_path = tmp_path / "dataset-ledger.csv"
    with ledger_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(matrix)

    with pytest.raises(
        ContractValidationError, match="ledger row 2 has missing cell values"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


def test_duplicate_top_level_checklist_key_is_rejected(tmp_path: Path) -> None:
    source = CHECKLIST_PATH.read_text(encoding="utf-8")
    source = source.replace(
        '  "schema": "computers-in-industry-submission-checklist-v1",',
        '  "schema": "computers-in-industry-submission-checklist-v1",\n'
        '  "schema": "duplicate-must-fail",',
        1,
    )
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="checklist JSON contains duplicate key: schema"
    ):
        validate_research_contract(
            REPO_ROOT,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_duplicate_nested_checklist_key_is_rejected(tmp_path: Path) -> None:
    source = CHECKLIST_PATH.read_text(encoding="utf-8")
    source = source.replace(
        '    "frame_level_independence_allowed": false,',
        '    "frame_level_independence_allowed": false,\n'
        '    "frame_level_independence_allowed": true,',
        1,
    )
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(source, encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="checklist JSON contains duplicate key: frame_level_independence_allowed",
    ):
        validate_research_contract(
            REPO_ROOT,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_duplicate_checklist_gate_id_is_rejected(tmp_path: Path) -> None:
    checklist = json.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))
    checklist["gates"][1]["id"] = "G0"
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="duplicate checklist gate ID: G0"
    ):
        validate_research_contract(
            REPO_ROOT,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_duplicate_checklist_phase_id_is_rejected(tmp_path: Path) -> None:
    checklist = json.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))
    checklist["phases"][1]["id"] = "P0"
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="duplicate checklist phase ID: P0"
    ):
        validate_research_contract(
            REPO_ROOT,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )
