import json
from pathlib import Path

import pytest

from display_project.research_contract import (
    ContractValidationError,
    validate_research_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_missing_metric_section_fails_explicitly(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis-plan.md"
    analysis.write_text(
        "# Analysis plan without the frozen metric section\n", encoding="utf-8"
    )

    with pytest.raises(
        ContractValidationError, match="metric dictionary section markers"
    ):
        validate_research_contract(
            REPO_ROOT,
            analysis_path=analysis,
            verify_asset_hashes=False,
        )


def test_checklist_cannot_leave_g0_pending(tmp_path: Path) -> None:
    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    next(gate for gate in checklist["gates"] if gate["id"] == "G0")["status"] = (
        "PENDING"
    )
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="checklist G0 status must be PASS"
    ):
        validate_research_contract(
            REPO_ROOT,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )
