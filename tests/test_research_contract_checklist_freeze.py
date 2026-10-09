import json
from pathlib import Path

import pytest

from display_project.research_contract import (
    ContractValidationError,
    validate_research_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("field_path", "replacement", "message"),
    [
        (
            ("frozen_claims",),
            ["C1_RISK_CONTROLLED_SOP_ADVANCEMENT"],
            "checklist frozen claims differ from the P0 contract",
        ),
        (
            ("primary_endpoint",),
            "frame_accuracy",
            "checklist primary endpoint differs from the P0 contract",
        ),
        (
            ("independent_unit",),
            "frame",
            "checklist independent unit differs from the P0 contract",
        ),
        (
            ("formal_start_rules", "frame_level_independence_allowed"),
            True,
            "checklist formal start rules differ from the P0 contract",
        ),
        (
            ("formal_start_rules", "adjacent_frame_split_allowed"),
            True,
            "checklist formal start rules differ from the P0 contract",
        ),
        (
            ("formal_start_rules", "development_data_in_test_allowed"),
            True,
            "checklist formal start rules differ from the P0 contract",
        ),
        (
            ("formal_start_rules", "effect_peeking_allowed"),
            True,
            "checklist formal start rules differ from the P0 contract",
        ),
        (
            ("formal_start_rules", "silent_failed_run_deletion_allowed"),
            True,
            "checklist formal start rules differ from the P0 contract",
        ),
        (
            ("formal_start_rules", "VLM_can_advance_state"),
            True,
            "checklist formal start rules differ from the P0 contract",
        ),
    ],
)
def test_checklist_p0_safety_contract_is_frozen(
    tmp_path: Path,
    field_path: tuple[str, ...],
    replacement: object,
    message: str,
) -> None:
    checklist_path = (
        REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
    )
    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    target = checklist
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = replacement
    mutated_path = tmp_path / "submission-checklist.json"
    mutated_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(ContractValidationError, match=message):
        validate_research_contract(
            REPO_ROOT,
            checklist_path=mutated_path,
            verify_asset_hashes=False,
        )
