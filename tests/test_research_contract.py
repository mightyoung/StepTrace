from __future__ import annotations

import hashlib
import csv
import json
from pathlib import Path

import pytest
import yaml

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


def test_current_research_contract_passes() -> None:
    evidence = validate_research_contract(REPO_ROOT, verify_asset_hashes=False)
    assert evidence["status"] == "PASS"
    assert evidence["claim_ids"] == ["C1", "C2", "C3"]
    assert evidence["ledger_rows"] == 108
    assert evidence["formal_test_rows"] == 0
    assert evidence["downstream_gates"]["G1"] == "BLOCKED_MISSING_FROZEN_ARTIFACTS"
    assert evidence["downstream_gates"]["G2"] == "PASS"
    assert evidence["downstream_gates"]["G3"] == "BLOCKED_BY_G1"


def test_meccano_tombstone_cannot_be_reassigned(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    rows[0]["project_split"] = "FORMAL_TEST_SEALED"
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError, match="irreversible exclusion MECCANO-0009"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_derivative_must_inherit_root_tombstone(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    rows[1]["tombstone_id"] = "TOMBSTONE-ESCAPE"
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(ContractValidationError, match="does not inherit tombstone_id"):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_unknown_operator_blocks_formal_assignment(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    rows[2]["project_split"] = "FORMAL_TEST_SEALED"
    rows[2]["formal_use_allowed"] = "YES"
    rows[2]["operator_id"] = "UNKNOWN"
    rows[2]["manually_viewed"] = "NO"
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="formal row SYNTHETIC-DEMO-001 has unknown operator_id",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_empty_deviation_whitelist_cannot_be_reportable(tmp_path: Path) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["endpoints"]["conditional_endpoints"][0]["reportable"] = True
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="must make deviation recall non-reportable"
    ):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            verify_asset_hashes=False,
        )


def test_exact_duplicate_bytes_cannot_escape_tombstone(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    duplicate = rows[0].copy()
    duplicate.update(
        {
            "asset_id": "MECCANO-0009-DUPLICATE",
            "canonical_root_id": "MECCANO-0009-DUPLICATE",
            "canonical_group_id": "MECCANO::DUPLICATE::OP_DUP::JOB_DUP",
            "parent_asset_id": "NONE",
            "operator_id": "OP_DUP",
            "complete_job_id": "JOB_DUP",
            "project_split": "PILOT",
            "tombstone_id": "",
            "tombstone_irreversible": "NO",
        }
    )
    rows.append(duplicate)
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError, match="duplicate SHA-256 .* crosses canonical roots"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_unknown_operator_blocks_calibration_assignment(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    rows[2]["project_split"] = "CALIBRATION"
    rows[2]["operator_id"] = " unknown "
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="restricted row SYNTHETIC-DEMO-001 has unknown operator_id",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_task_id_column_is_mandatory(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    fieldnames.remove("task_id")
    for row in rows:
        row.pop("task_id")
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError, match="dataset ledger is missing columns: .*task_id"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_meccano_tombstone_survives_claim_and_ledger_deletion(tmp_path: Path) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["data_boundary"]["known_permanent_exclusions"] = [
        exclusion
        for exclusion in claim["data_boundary"]["known_permanent_exclusions"]
        if exclusion["asset_id"] != "MECCANO-0009"
    ]
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    rows, fieldnames = _ledger_rows()
    rows = [row for row in rows if row["canonical_root_id"] != "MECCANO-0009"]
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="required irreversible tombstone MECCANO-0009 is absent from claim freeze",
    ):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_branch_alpha_must_be_positive_and_finite(tmp_path: Path) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["endpoints"]["primary_success_gate"]["branches"][0]["alpha"] = -5.0
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ContractValidationError, match="branch alpha values must be finite and positive"
    ):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            verify_asset_hashes=False,
        )


def test_every_claim_listed_supporting_metric_is_in_dictionary(tmp_path: Path) -> None:
    analysis_text = (REPO_ROOT / "research/analysis-plan.md").read_text(
        encoding="utf-8"
    )
    analysis_text = "\n".join(
        line
        for line in analysis_text.splitlines()
        if not line.startswith("| `throughput_fps` |")
    )
    analysis = tmp_path / "analysis-plan.md"
    analysis.write_text(analysis_text + "\n", encoding="utf-8")

    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    checklist["p0_validation"]["metric_rows"] -= 1
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="metric dictionary is missing claim-listed metric throughput_fps",
    ):
        validate_research_contract(
            REPO_ROOT,
            analysis_path=analysis,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def _write_rehashed_audit_bundle(
    tmp_path: Path, audit: dict[str, object]
) -> tuple[Path, Path, Path]:
    audit_path = tmp_path / "deviation-coverage-audit.yaml"
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    audit_hash = hashlib.sha256(audit_path.read_bytes()).hexdigest().upper()

    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["deviation_coverage_audit"]["sha256"] = audit_hash
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    checklist["p0_validation"]["audit_sha256"] = audit_hash
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")
    return audit_path, claim_path, checklist_path


def test_synthetic_tombstone_survives_claim_and_formal_reassignment(
    tmp_path: Path,
) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["data_boundary"]["known_permanent_exclusions"] = [
        exclusion
        for exclusion in claim["data_boundary"]["known_permanent_exclusions"]
        if exclusion["asset_id"] != "SYNTHETIC-DEMO-001"
    ]
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    rows, fieldnames = _ledger_rows()
    synthetic = rows[2]
    synthetic.update(
        {
            "operator_id": "OP-SYNTHETIC",
            "complete_job_id": "JOB-SYNTHETIC",
            "canonical_group_id": "SYNTHETIC::DEMO_001::OP-SYNTHETIC::JOB-SYNTHETIC",
            "project_split": "FORMAL_TEST_SEALED",
            "manually_viewed": "NO",
            "formal_use_allowed": "YES",
            "independent_unit_eligible": "YES",
            "tombstone_id": "",
            "tombstone_irreversible": "NO",
        }
    )
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    checklist["p0_validation"]["formal_test_rows"] = 1
    checklist["p0_validation"]["G2_status"] = "REQUIRES_FULL_ISOLATION_AUDIT"
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="required irreversible tombstone SYNTHETIC-DEMO-001 is absent from claim freeze",
    ):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            ledger_path=ledger,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_canonical_identity_normalization_blocks_split_alias(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    for index, (operator_id, project_split) in enumerate(
        (("OP-TRIM", "DEVELOPMENT"), (" op-trim ", "CALIBRATION")), start=1
    ):
        row = rows[2].copy()
        row.update(
            {
                "asset_id": f"REAL-ROOT-{index}",
                "record_type": "CANONICAL_ROOT_VIDEO",
                "source_dataset": "REAL-DATASET",
                "canonical_root_id": f"REAL-ROOT-{index}",
                "canonical_group_id": f"REAL::ROOT-{index}",
                "parent_asset_id": "NONE",
                "derivation": "NONE",
                "operator_id": operator_id,
                "complete_job_id": f"REAL-JOB-{index}",
                "project_split": project_split,
                "sha256": f"{index:064X}",
                "formal_use_allowed": "NO",
                "independent_unit_eligible": "NO",
                "tombstone_id": "",
                "tombstone_irreversible": "NO",
            }
        )
        rows.append(row)
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError, match="operator_id OP-TRIM crosses project splits"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_formal_derivative_cannot_be_an_independent_unit(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    root = rows[2].copy()
    root.update(
        {
            "asset_id": "FORMAL-ROOT-001",
            "record_type": "CANONICAL_ROOT_VIDEO",
            "source_dataset": "REAL-DATASET",
            "canonical_root_id": "FORMAL-ROOT-001",
            "canonical_group_id": "REAL::FORMAL-ROOT-001::OP-001::JOB-001",
            "parent_asset_id": "NONE",
            "derivation": "NONE",
            "operator_id": "OP-001",
            "complete_job_id": "JOB-001",
            "project_split": "FORMAL_TEST_SEALED",
            "sha256": "C" * 64,
            "manually_viewed": "NO",
            "formal_use_allowed": "YES",
            "independent_unit_eligible": "YES",
            "tombstone_id": "",
            "tombstone_irreversible": "NO",
        }
    )
    child = root.copy()
    child.update(
        {
            "asset_id": "FORMAL-ROOT-001-CLIP",
            "record_type": "DERIVED_CLIP",
            "parent_asset_id": "FORMAL-ROOT-001",
            "derivation": "frames 1-10",
            "sha256": "D" * 64,
            "independent_unit_eligible": "YES",
        }
    )
    rows.extend((root, child))
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="derived asset FORMAL-ROOT-001-CLIP cannot be an independent unit",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


def test_canonical_root_has_one_operator_job_group_and_split(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    for index in (1, 2):
        row = rows[2].copy()
        row.update(
            {
                "asset_id": f"SHARED-ROOT-ROW-{index}",
                "record_type": "CANONICAL_ROOT_VIDEO",
                "source_dataset": "REAL-DATASET",
                "canonical_root_id": "SHARED-ROOT",
                "canonical_group_id": f"REAL::SHARED-ROOT::GROUP-{index}",
                "parent_asset_id": "NONE",
                "derivation": "NONE",
                "operator_id": f"OP-{index}",
                "complete_job_id": f"JOB-{index}",
                "project_split": "DEVELOPMENT",
                "sha256": ("E" if index == 1 else "F") * 64,
                "formal_use_allowed": "NO",
                "independent_unit_eligible": "NO",
                "tombstone_id": "",
                "tombstone_irreversible": "NO",
            }
        )
        rows.append(row)
    ledger = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger, rows, fieldnames)

    with pytest.raises(
        ContractValidationError, match="canonical root SHARED-ROOT has inconsistent"
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger,
            verify_asset_hashes=False,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing_category", "deviation categories must be exactly"),
        ("missing_dataset", "deviation category wrong_tool must contain exactly"),
        (
            "invalid_classification",
            "deviation category wrong_tool has invalid Assembly101 classification",
        ),
        ("blank_decision", "deviation category wrong_tool must remain exclusionary"),
    ),
)
def test_deviation_matrix_semantics_survive_rehash(
    tmp_path: Path, mutation: str, message: str
) -> None:
    audit = yaml.safe_load(
        (REPO_ROOT / "research/deviation-coverage-audit.yaml").read_text(
            encoding="utf-8"
        )
    )
    if mutation == "missing_category":
        del audit["coverage_matrix"]["wrong_tool"]
    elif mutation == "missing_dataset":
        del audit["coverage_matrix"]["wrong_tool"]["Assembly101"]
    elif mutation == "invalid_classification":
        audit["coverage_matrix"]["wrong_tool"]["Assembly101"] = "TYPO_SUPPORTED"
    else:
        audit["coverage_matrix"]["wrong_tool"]["frozen_decision"] = " "

    audit_path, claim_path, checklist_path = _write_rehashed_audit_bundle(
        tmp_path, audit
    )
    with pytest.raises(ContractValidationError, match=message):
        validate_research_contract(
            REPO_ROOT,
            audit_path=audit_path,
            claim_path=claim_path,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_p0_deviation_whitelist_cannot_be_activated_by_joint_edit(
    tmp_path: Path,
) -> None:
    audit = yaml.safe_load(
        (REPO_ROOT / "research/deviation-coverage-audit.yaml").read_text(
            encoding="utf-8"
        )
    )
    audit["frozen_formal_deviation_claim_whitelist"] = ["wrong_tool"]
    audit_path = tmp_path / "deviation-coverage-audit.yaml"
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    audit_hash = hashlib.sha256(audit_path.read_bytes()).hexdigest().upper()

    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["deviation_coverage_audit"]["sha256"] = audit_hash
    claim["deviation_coverage_audit"]["frozen_formal_deviation_claim_whitelist"] = [
        "wrong_tool"
    ]
    endpoint = claim["endpoints"]["conditional_endpoints"][0]
    endpoint["status"] = "ACTIVE"
    endpoint["reportable"] = True
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    checklist["p0_validation"]["audit_sha256"] = audit_hash
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="P0 deviation whitelist must remain empty",
    ):
        validate_research_contract(
            REPO_ROOT,
            audit_path=audit_path,
            claim_path=claim_path,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("pilot_role", "operating-point schedule changed for p5_pilot"),
        ("branch_b_selector", "branch B selector must match achieved FSAR"),
        ("common_support", "common-support intersection rule changed"),
        ("task_direction", "task direction rule changed"),
    ),
)
def test_operating_point_contract_is_machine_frozen(
    tmp_path: Path, mutation: str, message: str
) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    contract = claim["endpoints"]["primary_success_gate"]["operating_point_contract"]
    if mutation == "pilot_role":
        contract["stage_schedule"]["p5_pilot"] = "PILOT_MAY_SELECT_THRESHOLDS"
    elif mutation == "branch_b_selector":
        contract["branch_b"]["selector"] = "MAXIMIZE_COVERAGE_BELOW_FSAR_CEILING"
    elif mutation == "common_support":
        contract["common_coverage_support"]["intersection"] = "PAIRWISE_ONLY"
    else:
        contract["task_direction_rule"] = "POOLED_DIRECTION_ONLY"
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    with pytest.raises(ContractValidationError, match=message):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            verify_asset_hashes=False,
        )


def test_analysis_plan_cannot_move_threshold_freeze_after_g3(tmp_path: Path) -> None:
    analysis_text = (REPO_ROOT / "research/analysis-plan.md").read_text(
        encoding="utf-8"
    )
    analysis_text = analysis_text.replace(
        "Final numeric thresholds are frozen before G3 and remain unchanged through P5 pilot and formal testing.",
        "Final numeric thresholds may be changed after G3.",
    )
    analysis_path = tmp_path / "analysis-plan.md"
    analysis_path.write_text(analysis_text, encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="analysis plan is missing frozen rule: Final numeric thresholds are frozen before G3",
    ):
        validate_research_contract(
            REPO_ROOT,
            analysis_path=analysis_path,
            verify_asset_hashes=False,
        )


def test_analysis_plan_cannot_drop_common_support_failure_rule(tmp_path: Path) -> None:
    analysis_text = (REPO_ROOT / "research/analysis-plan.md").read_text(
        encoding="utf-8"
    )
    analysis_text = analysis_text.replace(
        "If `U < L` or `U - L` is below the numeric minimum width frozen before G3, branch A, risk-coverage AUC, and G3 fail",
        "If common support is narrow, the result may be extrapolated",
    )
    analysis_path = tmp_path / "analysis-plan.md"
    analysis_path.write_text(analysis_text, encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="analysis plan is missing frozen rule: If `U < L` or `U - L`",
    ):
        validate_research_contract(
            REPO_ROOT,
            analysis_path=analysis_path,
            verify_asset_hashes=False,
        )


def test_claim_freeze_metadata_is_anchored(tmp_path: Path) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["status"] = "DRAFT"
    claim["freeze_version"] = "0.0.0"
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="claim freeze metadata differs from the frozen P0 contract",
    ):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            verify_asset_hashes=False,
        )


def _new_empirical_row(
    template: dict[str, str], *, asset_id: str, project_split: str
) -> dict[str, str]:
    row = template.copy()
    row.update(
        {
            "asset_id": asset_id,
            "record_type": "CANONICAL_ROOT_VIDEO",
            "source_dataset": "REAL-DATASET",
            "canonical_root_id": asset_id,
            "canonical_group_id": f"REAL::{asset_id}::OP::{asset_id}",
            "parent_asset_id": "NONE",
            "derivation": "NONE",
            "operator_id": f"OP-{asset_id}",
            "complete_job_id": f"JOB-{asset_id}",
            "task_id": "REAL-ASSEMBLY-TASK",
            "error_types": "NONE",
            "source_split": "train",
            "project_split": project_split,
            "sha256": ("A" if project_split == "CALIBRATION" else "B") * 64,
            "manually_viewed": "NO",
            "view_purpose": "REGISTERED_ONLY",
            "annotation_status": "REGISTERED",
            "contamination_status": "NONE",
            "formal_use_allowed": "NO",
            "split_locked": "YES",
            "independent_unit_eligible": "NO",
            "tombstone_id": "",
            "tombstone_irreversible": "NO",
            "notes": "Adversarial validator fixture.",
        }
    )
    return row


def test_unknown_task_blocks_restricted_split(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    row = _new_empirical_row(
        rows[2], asset_id="REAL-CALIBRATION-001", project_split="CALIBRATION"
    )
    row["task_id"] = " unknown "
    rows.append(row)
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="restricted row REAL-CALIBRATION-001 has unknown task_id",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


def test_nonformal_row_cannot_allow_formal_use(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    row = _new_empirical_row(
        rows[2], asset_id="REAL-DEVELOPMENT-001", project_split="DEVELOPMENT"
    )
    row["formal_use_allowed"] = "YES"
    rows.append(row)
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="non-formal row REAL-DEVELOPMENT-001 cannot allow formal use",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


def test_threshold_manifest_is_keyed_by_branch_method_and_task() -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    fields = set(
        claim["endpoints"]["primary_success_gate"]["operating_point_contract"][
            "required_manifest_fields"
        ]
    )
    assert "selected_thresholds_by_branch_method_task" in fields
    assert "selected_thresholds" not in fields
    analysis = (REPO_ROOT / "research/analysis-plan.md").read_text(encoding="utf-8")
    assert "branch x method x frozen primary task" in analysis


def test_analysis_revision_metadata_records_g002_remediation() -> None:
    analysis = (REPO_ROOT / "research/analysis-plan.md").read_text(encoding="utf-8")
    assert "- Version: `1.2.0`" in analysis
    assert "- Frozen: `2026-08-21`" in analysis
    assert "no pilot or formal-test data were accessed" in analysis
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    revision = claim["revision_history"][-1]
    assert revision["version"] == "1.4.0"
    assert revision["development_data_accessed"] is True
    assert revision["pilot_data_accessed"] is False
    assert revision["formal_data_accessed"] is False
    assert revision["approval_status"] == "CORRESPONDING_AUTHOR_RATIFIED"
    assert (
        revision["corresponding_author_ratification"] == "RATIFIED"
    )


def test_meccano_0009_is_mainline_admissible_but_not_formal_evidence() -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    exclusion = next(
        entry
        for entry in claim["data_boundary"]["known_permanent_exclusions"]
        if entry["asset_id"] == "MECCANO-0009"
    )

    assert exclusion["mainline_use_allowed"] is True
    assert exclusion["formal_use_allowed"] is False
    assert "qualitative system case study" in exclusion["permitted_mainline_roles"]
    assert set(exclusion["forbidden_mainline_roles"]) == {
        "calibration evidence",
        "pilot evidence",
        "formal test evidence",
        "unseen generalization evidence",
    }


def test_claim_freeze_canonical_text_is_immutable(tmp_path: Path) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    claim["claims"][0]["frozen_claim"] = "Frame-level accuracy is the only endpoint."
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="claim freeze canonical text does not match the frozen 1.4.0 contract",
    ):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            verify_asset_hashes=False,
        )


def test_analysis_plan_canonical_text_is_immutable(tmp_path: Path) -> None:
    analysis = (REPO_ROOT / "research/analysis-plan.md").read_text(encoding="utf-8")
    analysis += "\nFormal results may override thresholds.\n"
    analysis_path = tmp_path / "analysis-plan.md"
    analysis_path.write_text(analysis, encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="analysis plan canonical text does not match the frozen 1.2.0 contract",
    ):
        validate_research_contract(
            REPO_ROOT,
            analysis_path=analysis_path,
            verify_asset_hashes=False,
        )


def test_deviation_audit_canonical_text_is_immutable_after_joint_rehash(
    tmp_path: Path,
) -> None:
    audit = yaml.safe_load(
        (REPO_ROOT / "research/deviation-coverage-audit.yaml").read_text(
            encoding="utf-8"
        )
    )
    audit["sources"][0]["verified_facts"] = ["Fabricated replacement fact."]
    audit_path, claim_path, checklist_path = _write_rehashed_audit_bundle(
        tmp_path, audit
    )

    with pytest.raises(
        ContractValidationError,
        match="deviation audit canonical text does not match the frozen P0 evidence",
    ):
        validate_research_contract(
            REPO_ROOT,
            audit_path=audit_path,
            claim_path=claim_path,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_initial_derived_clip_cannot_be_deleted_by_joint_count_edit(
    tmp_path: Path,
) -> None:
    rows, fieldnames = _ledger_rows()
    rows = [row for row in rows if row["asset_id"] != "MECCANO-0009-CLIP-150-500"]
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    checklist["p0_validation"]["ledger_rows"] = 2
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="required initial ledger asset MECCANO-0009-CLIP-150-500 is absent",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_downstream_gate_and_phase_must_match_current_evidence(tmp_path: Path) -> None:
    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    next(gate for gate in checklist["gates"] if gate["id"] == "G2")["status"] = (
        "BLOCKED_UNKNOWN_IDENTITY"
    )
    next(phase for phase in checklist["phases"] if phase["id"] == "P3")["status"] = (
        "COMPLETE"
    )
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match=(
            "downstream gate G2 status must match current evidence: PASS"
        ),
    ):
        validate_research_contract(
            REPO_ROOT,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )


def test_checklist_rejects_stale_test_and_gate_evidence(tmp_path: Path) -> None:
    checklist = json.loads(
        (
            REPO_ROOT / ".omx/plans/computers-in-industry-submission-checklist.json"
        ).read_text(encoding="utf-8")
    )
    checklist["current_evidence"]["fresh_test_result"] = "128 passed"
    checklist["p0_validation"]["G1_status"] = "PENDING"
    checklist_path = tmp_path / "submission-checklist.json"
    checklist_path.write_text(json.dumps(checklist), encoding="utf-8")

    with pytest.raises(ContractValidationError) as exc_info:
        validate_research_contract(
            REPO_ROOT,
            checklist_path=checklist_path,
            verify_asset_hashes=False,
        )
    message = str(exc_info.value)
    assert "checklist G1 block status is stale" in message
    assert "checklist fresh test result must be 730 passed in 47.18s" in message


def test_duplicate_metric_id_is_rejected(tmp_path: Path) -> None:
    analysis = (REPO_ROOT / "research/analysis-plan.md").read_text(encoding="utf-8")
    duplicate = "| `false_step_advancement_rate` | Duplicate definition. | frame | frame | higher |\n"
    analysis = analysis.replace(
        "## 6. Models and estimators", duplicate + "\n## 6. Models and estimators"
    )
    analysis_path = tmp_path / "analysis-plan.md"
    analysis_path.write_text(analysis, encoding="utf-8")

    with pytest.raises(ContractValidationError, match="duplicate metric dictionary ID"):
        validate_research_contract(
            REPO_ROOT,
            analysis_path=analysis_path,
            verify_asset_hashes=False,
        )


def test_branch_alpha_allocation_is_exactly_half_family_alpha(tmp_path: Path) -> None:
    claim = yaml.safe_load(
        (REPO_ROOT / "research/claim-freeze.yaml").read_text(encoding="utf-8")
    )
    branches = claim["endpoints"]["primary_success_gate"]["branches"]
    branches[0]["alpha"] = 0.03
    branches[1]["alpha"] = 0.02
    claim_path = tmp_path / "claim-freeze.yaml"
    claim_path.write_text(yaml.safe_dump(claim, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ContractValidationError,
        match="each success branch alpha must remain frozen at 0.025",
    ):
        validate_research_contract(
            REPO_ROOT,
            claim_path=claim_path,
            verify_asset_hashes=False,
        )


def test_derived_asset_must_inherit_task_and_source(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    rows[1]["task_id"] = "OTHER-TASK"
    rows[1]["source_split"] = "train"
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="derived asset MECCANO-0009-CLIP-150-500 does not inherit task_id",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )


def test_ledger_schema_version_is_frozen(tmp_path: Path) -> None:
    rows, fieldnames = _ledger_rows()
    rows[1]["schema_version"] = "9.9.9"
    ledger_path = tmp_path / "dataset-ledger.csv"
    _write_ledger(ledger_path, rows, fieldnames)

    with pytest.raises(
        ContractValidationError,
        match="ledger row MECCANO-0009-CLIP-150-500 has unsupported schema_version",
    ):
        validate_research_contract(
            REPO_ROOT,
            ledger_path=ledger_path,
            verify_asset_hashes=False,
        )
