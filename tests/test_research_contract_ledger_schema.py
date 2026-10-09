import csv
import subprocess
import sys
from pathlib import Path

import pytest

from display_project.research_contract import (
    ContractValidationError,
    validate_research_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _ledger_rows() -> tuple[list[dict[str, str]], list[str]]:
    with (REPO_ROOT / "research/dataset-ledger.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _write_ledger(
    path: Path, rows: list[dict[str, str]], fieldnames: list[str]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_cli_rejects_asset_hash_bypass() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/validate_research_contract.py"),
            "--skip-asset-hashes",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert '"status": "PASS"' not in result.stdout


def test_ledger_rejects_extra_columns(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    fieldnames.append("undeclared_extension")
    for row in rows:
        row["undeclared_extension"] = "hidden"
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="dataset ledger columns must exactly match schema 1.0.0",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("frame_start", "-1", "invalid non-negative integer frame_start"),
        ("frame_end", "not-an-integer", "invalid non-negative integer frame_end"),
        ("frame_count", "0", "frame_count must be positive"),
        ("width", "0", "width must be positive"),
        ("height", "-1", "invalid non-negative integer height"),
        ("frame_count", "2", "frame interval/count mismatch"),
    ],
)
def test_ledger_rejects_invalid_video_shape(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    rows, fieldnames = _ledger_rows()
    rows[0][field] = replacement
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(ContractValidationError, match=message):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


def test_ledger_rejects_invalid_hash_verification_date(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    rows[0]["hash_verified_on"] = "2026-13-40"
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(ContractValidationError, match="invalid hash_verified_on date"):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("record_type", "VIDEO", "invalid record_type"),
        ("source_split", "test split", "invalid source_split token"),
        ("annotation_status", "lowercase status", "invalid annotation_status token"),
        (
            "contamination_status",
            "lowercase status",
            "invalid contamination_status token",
        ),
    ],
)
def test_ledger_rejects_invalid_machine_vocabulary(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    rows, fieldnames = _ledger_rows()
    rows[0][field] = replacement
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(ContractValidationError, match=message):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )
