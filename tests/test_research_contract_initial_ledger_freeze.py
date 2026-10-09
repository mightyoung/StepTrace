import csv
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


@pytest.mark.parametrize(
    ("row_index", "field", "replacement", "asset_id"),
    [
        (0, "manually_viewed", "NO", "MECCANO-0009"),
        (1, "manually_viewed", "NO", "MECCANO-0009-CLIP-150-500"),
        (0, "canonical_group_id", "MECCANO::ERASED", "MECCANO-0009"),
        (
            1,
            "canonical_group_id",
            "MECCANO::ERASED",
            "MECCANO-0009-CLIP-150-500",
        ),
    ],
)
def test_initial_meccano_identity_and_view_history_are_frozen(
    tmp_path: Path,
    row_index: int,
    field: str,
    replacement: str,
    asset_id: str,
) -> None:
    rows, fieldnames = _ledger_rows()
    rows[row_index][field] = replacement
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match=f"required initial ledger asset {asset_id} changed {field}",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )
