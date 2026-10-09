from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


class ContractValidationError(ValueError):
    """Raised when the frozen research contract is internally inconsistent."""


REQUIRED_CLAIM_FREEZE_METADATA = {
    "schema_version": "1.0.0",
    "document_id": "CII-SOP-P0-CLAIM-FREEZE",
    "freeze_version": "1.4.0",
    "freeze_date": "2026-08-21",
    "status": "P0_FROZEN_FORMAL_TEST_PROHIBITED",
}
REQUIRED_CLAIM_AUTHORITY = {
    "workplan": ".omx/plans/computers-in-industry-submission-workplan.md",
    "target_journal": "Computers in Industry",
    "working_title": (
        "Risk-Controlled and Reconfigurable Vision-Based SOP Conformance Monitoring "
        "for Manual Assembly"
    ),
}
REQUIRED_CLAIM_SCOPE = {
    "phase": "P0",
    "included": (
        "Research contract, dataset identity and contamination policy, and prospective analysis contract."
    ),
    "excluded": "P1 implementation, pilot, method selection, power freezing, and formal-test execution.",
    "current_evidence_status": "METHOD_AND_FORMAL_EVIDENCE_BLOCKED",
}
REQUIRED_HASH_CANONICALIZATION = "UTF8_LF"
REQUIRED_CLAIM_FREEZE_SHA256 = (
    "4B5DE6E63C65CD50312222599466F82DEA49D875B61900EF0127A2B4F90C0F6C"
)
REQUIRED_ANALYSIS_PLAN_SHA256 = (
    "CA4594869124B1CBDE290F860081D89023E4C843D76F3A707467C6AAA22E133B"
)
REQUIRED_DEVIATION_AUDIT_SHA256 = (
    "240DCB7E9916BEB01C6D08534890DD8D0E63A8B56C62DBED8448FED2B26415DA"
)
REQUIRED_ANALYSIS_CONTRACT = {
    "path": "research/analysis-plan.md",
    "sha256": REQUIRED_ANALYSIS_PLAN_SHA256,
    "hash_canonicalization": REQUIRED_HASH_CANONICALIZATION,
    "version": "1.2.0",
    "frozen_date": "2026-08-21",
    "status": "P0_FROZEN_FORMAL_TEST_PROHIBITED",
}
REQUIRED_PRIMARY_BRANCHES = {
    "A_FSAR_AT_MATCHED_COVERAGE": {
        "alpha": 0.025,
        "decision": (
            "Both comparator tests pass Holm adjustment within the branch and favor lower FSAR."
        ),
    },
    "B_COVERAGE_AT_MATCHED_FSAR": {
        "alpha": 0.025,
        "decision": (
            "Both comparator tests pass Holm adjustment within the branch and favor higher "
            "coverage after target and pairwise achieved-FSAR matching within delta_r."
        ),
    },
}
REQUIRED_OVERALL_DECISION = (
    "G3 statistical evidence passes if branch A or branch B passes, effect direction is "
    "strictly favorable in each task, and all pre-frozen floors, matching tolerances, and "
    "common-support rules pass. The two-branch rule and its failure process are simulated "
    "jointly for power."
)
REQUIRED_GATE_STATUSES = {
    "G0": "PASS",
    "G1": "BLOCKED_MISSING_FROZEN_ARTIFACTS",
    "G2": "PASS",
    "G3": "BLOCKED_BY_G1",
    "G4": "BLOCKED_BY_G3",
    "G5": "BLOCKED_BY_G3",
    "G6": "BLOCKED_BY_G1",
    "G7": "BLOCKED_BY_G3_G4_G5_G6",
    "G8": "BLOCKED_BY_G7",
}
REQUIRED_PHASE_STATUSES = {"P0": "COMPLETE", "P1": "PENDING", "P2": "COMPLETE"} | {
    f"P{index}": "BLOCKED" for index in range(3, 10)
}
REQUIRED_FRESH_TEST_RESULT = "730 passed in 47.18s"
REQUIRED_CHECKLIST_FROZEN_CLAIMS = [
    "C1_RISK_CONTROLLED_SOP_ADVANCEMENT",
    "C2_PROCESS_EVIDENCE_CERTIFICATES",
    "C3_NON_AUTHORITATIVE_VLM_AUDIT",
]
REQUIRED_CHECKLIST_PRIMARY_ENDPOINT = "false_step_advancement_rate"
REQUIRED_CHECKLIST_INDEPENDENT_UNIT = "operator_complete_execution_or_complete_video"
REQUIRED_FORMAL_START_RULES = {
    "frame_level_independence_allowed": False,
    "adjacent_frame_split_allowed": False,
    "development_data_in_test_allowed": False,
    "effect_peeking_allowed": False,
    "silent_failed_run_deletion_allowed": False,
    "VLM_can_advance_state": False,
}
REQUIRED_CLAIM_IDS = {"C1", "C2", "C3"}
REQUIRED_COMPARATORS = {"fixed_streak", "hmm_or_crf"}
REQUIRED_LEDGER_COLUMNS = {
    "schema_version",
    "asset_id",
    "record_type",
    "source_dataset",
    "source_path",
    "canonical_root_id",
    "canonical_group_id",
    "parent_asset_id",
    "derivation",
    "frame_start",
    "frame_end",
    "frame_count",
    "width",
    "height",
    "operator_id",
    "complete_job_id",
    "task_id",
    "error_types",
    "source_split",
    "project_split",
    "sha256",
    "hash_verified_on",
    "manually_viewed",
    "view_purpose",
    "annotation_status",
    "contamination_status",
    "formal_use_allowed",
    "split_locked",
    "independent_unit_eligible",
    "tombstone_id",
    "tombstone_irreversible",
    "notes",
}
REQUIRED_NONBLANK_LEDGER_FIELDS = REQUIRED_LEDGER_COLUMNS - {"tombstone_id"}
REQUIRED_RECORD_TYPES = {
    "CANONICAL_ROOT_VIDEO",
    "COMPLETE_JOB_VIDEO",
    "DERIVED_CLIP",
    "SYNTHETIC_VIDEO",
}
REQUIRED_SOURCE_SPLITS = {
    "train",
    "val",
    "validation",
    "test",
    "NOT_APPLICABLE",
}
REQUIRED_ANNOTATION_STATUS_TOKENS = {
    "OFFICIAL_ACTION_ANNOTATIONS_AVAILABLE",
    "CVAT_BATCH_SELECTED",
    "CVAT_ANNOTATION_COMPLETION_UNKNOWN",
    "DERIVED_EVENTS_AVAILABLE",
    "INHERITS_ROOT_ANNOTATION_AND_VIEW_HISTORY",
    "SCRIPTED_EVENTS_AVAILABLE",
    "OFFICIAL_RGB_VIDEO_AUDIT_PASS",
    "OFFICIAL_RGB_VIDEO_ACTION_LABELS_AVAILABLE",
    "OFFICIAL_RGB_VIDEO_ACTION_LABELS_UNAVAILABLE",
    "OFFICIAL_ACTION_ANNOTATIONS_UNAVAILABLE",
}
REQUIRED_CONTAMINATION_STATUS_TOKENS = {
    "OFFICIAL_TEST_SOURCE",
    "OFFICIAL_SOURCE",
    "DEVELOPMENT_REUSED",
    "MANUALLY_VIEWED",
    "DERIVED_FROM_CONTAMINATED_ROOT",
    "SYNTHETIC",
}
REQUIRED_INITIAL_LEDGER_ASSETS = {
    "MECCANO-0009": {
        "record_type": "CANONICAL_ROOT_VIDEO",
        "source_dataset": "MECCANO",
        "source_path": "data/meccano/subset/0009.mp4",
        "canonical_root_id": "MECCANO-0009",
        "canonical_group_id": "MECCANO::0009::IDENTITY_UNKNOWN",
        "parent_asset_id": "NONE",
        "frame_start": "0",
        "frame_end": "9097",
        "frame_count": "9098",
        "operator_id": "UNKNOWN",
        "complete_job_id": "UNKNOWN",
        "task_id": "MECCANO_ASSEMBLY",
        "source_split": "test",
        "project_split": "DEVELOPMENT_ONLY_CONTAMINATED",
        "manually_viewed": "YES",
        "width": "1920",
        "height": "1080",
        "sha256": "5BDB399ACD0A3C899608BB299AF599CBB3C79AF9F7750A28D30FAE89DBE42175",
        "hash_verified_on": "2026-07-16",
        "annotation_status": "OFFICIAL_ACTION_ANNOTATIONS_AVAILABLE; CVAT_BATCH_SELECTED; CVAT_ANNOTATION_COMPLETION_UNKNOWN",
        "contamination_status": "OFFICIAL_TEST_SOURCE; DEVELOPMENT_REUSED; MANUALLY_VIEWED",
        "formal_use_allowed": "NO",
        "independent_unit_eligible": "NO",
        "tombstone_id": "TOMBSTONE-MECCANO-0009",
        "tombstone_irreversible": "YES",
    },
    "MECCANO-0009-CLIP-150-500": {
        "record_type": "DERIVED_CLIP",
        "source_dataset": "MECCANO",
        "source_path": "data/meccano/official-demo/video.mp4",
        "canonical_root_id": "MECCANO-0009",
        "canonical_group_id": "MECCANO::0009::IDENTITY_UNKNOWN",
        "parent_asset_id": "MECCANO-0009",
        "frame_start": "150",
        "frame_end": "500",
        "frame_count": "351",
        "operator_id": "UNKNOWN",
        "complete_job_id": "UNKNOWN",
        "task_id": "MECCANO_ASSEMBLY",
        "source_split": "test",
        "project_split": "DEVELOPMENT_ONLY_CONTAMINATED",
        "manually_viewed": "YES",
        "width": "1920",
        "height": "1080",
        "sha256": "6CB019CA5EB559D592293B0EC077659B21361154BEA0200A8ED22C014D596641",
        "hash_verified_on": "2026-07-16",
        "annotation_status": "DERIVED_EVENTS_AVAILABLE; INHERITS_ROOT_ANNOTATION_AND_VIEW_HISTORY",
        "contamination_status": "OFFICIAL_TEST_SOURCE; DEVELOPMENT_REUSED; MANUALLY_VIEWED; DERIVED_FROM_CONTAMINATED_ROOT",
        "formal_use_allowed": "NO",
        "independent_unit_eligible": "NO",
        "tombstone_id": "TOMBSTONE-MECCANO-0009",
        "tombstone_irreversible": "YES",
    },
    "SYNTHETIC-DEMO-001": {
        "record_type": "SYNTHETIC_VIDEO",
        "source_dataset": "SCRIPT_GENERATED",
        "source_path": "data/meccano/subset/video.mp4",
        "canonical_root_id": "SYNTHETIC-DEMO-001",
        "canonical_group_id": "SYNTHETIC::DEMO_001::NOT_APPLICABLE",
        "parent_asset_id": "NONE",
        "frame_start": "0",
        "frame_end": "224",
        "frame_count": "225",
        "operator_id": "NOT_APPLICABLE",
        "complete_job_id": "SYNTHETIC-DEMO-001",
        "task_id": "SYNTHETIC_THREE_STEP_SOP",
        "source_split": "NOT_APPLICABLE",
        "project_split": "EXCLUDED_SYNTHETIC",
        "manually_viewed": "UNKNOWN",
        "width": "1280",
        "height": "720",
        "sha256": "24F804337FE4DB614D7554288987BE4DA04397E49811E1647D1F5406BEBFE760",
        "hash_verified_on": "2026-07-16",
        "annotation_status": "SCRIPTED_EVENTS_AVAILABLE",
        "contamination_status": "SYNTHETIC",
        "formal_use_allowed": "NO",
        "independent_unit_eligible": "NO",
        "tombstone_id": "TOMBSTONE-SYNTHETIC-DEMO-001",
        "tombstone_irreversible": "YES",
    },
}
REQUIRED_ALLOWED_PROJECT_SPLITS = {
    "DEVELOPMENT",
    "CALIBRATION",
    "PILOT",
    "FORMAL_TEST_SEALED",
    "DEVELOPMENT_ONLY_CONTAMINATED",
    "EXCLUDED_SYNTHETIC",
}
REQUIRED_AUDIT_STATUS = "COMPLETE_RESTRICTED_NO_FORMAL_DEVIATION_WHITELIST"
REQUIRED_AUDIT_CLASSIFICATIONS = {
    "SUPPORTED_DIRECT_LOCAL",
    "PARTIAL_DIRECT_COMPOSITE_ONLY",
    "DERIVABLE_PENDING_FROZEN_RULE",
    "OFFICIAL_SCHEMA_ONLY_NOT_LOCALLY_INGESTED",
    "UNSUPPORTED_BY_VERIFIED_SCHEMA",
    "NOT_LOCALLY_VERIFIABLE",
}
REQUIRED_AUDIT_SOURCE_IDS = {
    "MECCANO_LOCAL_ACTION_SCHEMA",
    "MECCANO_LOCAL_TEST_ACTIONS",
    "ASSEMBLY101_MISTAKE_ANNOTATIONS",
    "INDUSTREAL_OFFICIAL_SCHEMA",
}
REQUIRED_DEVIATION_DATASETS = {"MECCANO", "IndustReal", "Assembly101"}
REQUIRED_DEVIATION_COVERAGE = {
    "omission": {
        "MECCANO": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
        "IndustReal": "OFFICIAL_SCHEMA_ONLY_NOT_LOCALLY_INGESTED",
        "Assembly101": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
    },
    "skipped_step": {
        "MECCANO": "DERIVABLE_PENDING_FROZEN_RULE",
        "IndustReal": "DERIVABLE_PENDING_FROZEN_RULE",
        "Assembly101": "PARTIAL_DIRECT_COMPOSITE_ONLY",
    },
    "reverse_order": {
        "MECCANO": "DERIVABLE_PENDING_FROZEN_RULE",
        "IndustReal": "DERIVABLE_PENDING_FROZEN_RULE",
        "Assembly101": "PARTIAL_DIRECT_COMPOSITE_ONLY",
    },
    "wrong_part": {
        "MECCANO": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
        "IndustReal": "OFFICIAL_SCHEMA_ONLY_NOT_LOCALLY_INGESTED",
        "Assembly101": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
    },
    "wrong_tool": {
        "MECCANO": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
        "IndustReal": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
        "Assembly101": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
    },
    "incomplete_job": {
        "MECCANO": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
        "IndustReal": "DERIVABLE_PENDING_FROZEN_RULE",
        "Assembly101": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
    },
    "occlusion_or_operator_leave": {
        "MECCANO": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
        "IndustReal": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
        "Assembly101": "UNSUPPORTED_BY_VERIFIED_SCHEMA",
    },
    "repeated_or_irrelevant_action": {
        "MECCANO": "DERIVABLE_PENDING_FROZEN_RULE",
        "IndustReal": "DERIVABLE_PENDING_FROZEN_RULE",
        "Assembly101": "PARTIAL_DIRECT_COMPOSITE_ONLY",
    },
}
REQUIRED_OPERATING_POINT_SCHEDULE = {
    "p3_exploration": "DEVELOPMENT_TUNING_ONLY",
    "p4_threshold_selection": "CALIBRATION_ONLY_BEFORE_G3",
    "g3_evaluation": "HELD_OUT_DEVELOPMENT_ONLY_AFTER_P4_THRESHOLD_FREEZE",
    "post_g3_immutability": "THRESHOLDS_UNCHANGED_THROUGH_P5_PILOT_AND_FORMAL",
    "p5_pilot": "NUISANCE_POWER_RUNTIME_FINAL_N_ONLY_NO_METHOD_OR_THRESHOLD_SELECTION",
    "formal_retuning": "PROHIBITED",
}
REQUIRED_OPERATING_POINT_MANIFEST_FIELDS = {
    "c_star",
    "r_star",
    "delta_c",
    "delta_r",
    "common_coverage_support_min_width",
    "minimum_coverage",
    "maximum_confirmation_latency",
    "activated_deviation_recall_floor_or_inactive",
    "maximum_differential_failure",
    "threshold_grids",
    "selected_thresholds_by_branch_method_task",
    "deterministic_tie_breaks",
    "partition_hashes",
    "method_and_configuration_hashes",
}
REQUIRED_BRANCH_A_CONTRACT = {
    "selector": "MINIMIZE_ABS_ESTIMATED_COVERAGE_MINUS_C_STAR",
    "tie_breaks": ["LOWER_ESTIMATED_FSAR", "MORE_CONSERVATIVE_THRESHOLD"],
    "eligibility": [
        "EACH_METHOD_TASK_ABS_ACHIEVED_COVERAGE_MINUS_C_STAR_LE_DELTA_C",
        "PROPOSED_COMPARATOR_PAIRWISE_ACHIEVED_COVERAGE_DIFF_LE_DELTA_C",
    ],
}
REQUIRED_BRANCH_B_CONTRACT = {
    "selector": "MINIMIZE_ABS_ESTIMATED_FSAR_MINUS_R_STAR",
    "tie_breaks": ["HIGHER_ESTIMATED_COVERAGE", "MORE_CONSERVATIVE_THRESHOLD"],
    "eligibility": [
        "EACH_METHOD_TASK_ABS_ACHIEVED_FSAR_MINUS_R_STAR_LE_DELTA_R",
        "PROPOSED_COMPARATOR_PAIRWISE_ACHIEVED_FSAR_DIFF_LE_DELTA_R",
    ],
}
REQUIRED_COMMON_SUPPORT_CONTRACT = {
    "methods": ["proposed", "fixed_streak", "hmm_or_crf"],
    "minimum_task_count": 2,
    "cell_range": (
        "MIN_MAX_ATTAINABLE_COVERAGE_AFTER_EXCLUDING_ZERO_ADVANCE_OR_UNDEFINED_FSAR_POINTS"
    ),
    "intersection": (
        "LOWER_MAX_OF_ALL_CELL_LOWER_BOUNDS_UPPER_MIN_OF_ALL_CELL_UPPER_BOUNDS"
    ),
    "minimum_width": "NUMERIC_AND_FROZEN_BEFORE_G3",
    "c_star_rule": "MUST_LIE_INSIDE_COMMON_INTERSECTION",
    "r_star_rule": "AT_LEAST_ONE_POINT_WITHIN_DELTA_R_IN_EVERY_METHOD_TASK_CELL",
    "failure_rule": (
        "EMPTY_OR_TOO_NARROW_FAILS_BRANCH_A_RISK_COVERAGE_AUC_AND_G3_NO_EXTRAPOLATION"
    ),
}
REQUIRED_TASK_DIRECTION_RULE = (
    "BOTH_TASK_SPECIFIC_MARGINAL_CONTRASTS_STRICTLY_FAVOR_PROPOSED_"
    "SIGNIFICANCE_NOT_REQUIRED_PER_TASK"
)
INDEPENDENT_ROOT_RECORD_TYPES = {"CANONICAL_ROOT_VIDEO", "COMPLETE_JOB_VIDEO"}
UNKNOWN_IDENTITIES = {"", "UNKNOWN", "NONE", "NOT_APPLICABLE"}
RESTRICTED_IDENTITY_SPLITS = {"CALIBRATION", "PILOT", "FORMAL_TEST_SEALED"}
REQUIRED_IRREVERSIBLE_TOMBSTONES: dict[str, dict[str, Any]] = {
    "MECCANO-0009": {
        "tombstone_id": "TOMBSTONE-MECCANO-0009",
        "project_split": "DEVELOPMENT_ONLY_CONTAMINATED",
        "sha256": "5BDB399ACD0A3C899608BB299AF599CBB3C79AF9F7750A28D30FAE89DBE42175",
        "match_keys": {
            "canonical_root_id",
            "sha256_or_duplicate_content",
            "all_derivatives",
        },
    },
    "SYNTHETIC-DEMO-001": {
        "tombstone_id": "TOMBSTONE-SYNTHETIC-DEMO-001",
        "project_split": "EXCLUDED_SYNTHETIC",
        "sha256": "24F804337FE4DB614D7554288987BE4DA04397E49811E1647D1F5406BEBFE760",
        "match_keys": {
            "canonical_root_id",
            "sha256_or_duplicate_content",
            "all_derivatives",
        },
        "record_type": "SYNTHETIC_VIDEO",
        "source_dataset": "SCRIPT_GENERATED",
    },
}


def sha256_canonical_text(path: Path) -> str:
    """Hash UTF-8 text after normalizing CRLF and CR newlines to LF."""
    text = path.read_bytes().decode("utf-8")
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _resolve(repo_root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()


def _canonical_identity(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _is_unknown_identity(value: str) -> bool:
    return _canonical_identity(value).upper() in UNKNOWN_IDENTITIES


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ContractValidationError(f"{path} must contain a YAML mapping")
    return loaded


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ContractValidationError(
                f"checklist JSON contains duplicate key: {key}"
            )
        document[key] = value
    return document


def _read_ledger(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        duplicate_columns = sorted(
            {
                column
                for index, column in enumerate(columns)
                if column in columns[:index]
            }
        )
        if duplicate_columns:
            raise ContractValidationError(
                f"dataset ledger has duplicate ledger columns: {duplicate_columns}"
            )
        rows: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            clean_row: dict[str, str] = {}
            for key, value in row.items():
                if key is None:
                    raise ContractValidationError(
                        f"ledger row {row_number} has extra trailing cells"
                    )
                if value is None:
                    raise ContractValidationError(
                        f"ledger row {row_number} has missing cell values"
                    )
                if not isinstance(value, str):
                    raise ContractValidationError(
                        f"ledger row {row_number} has extra trailing cells"
                    )
                clean_row[key] = value
            rows.append(clean_row)
        return rows, columns


def _metric_rows(analysis_text: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    pattern = re.compile(r"^\|\s*`([^`]+)`(?:\s*\([^)]*\))?\s*\|")
    start_marker = "## 5. Metric dictionary"
    end_marker = "## 6. Models and estimators"
    if start_marker not in analysis_text or end_marker not in analysis_text:
        raise ContractValidationError(
            "analysis plan is missing metric dictionary section markers"
        )
    section = analysis_text.split(start_marker, 1)[1]
    section = section.split(end_marker, 1)[0]
    for line in section.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        if match.group(1) in rows:
            raise ContractValidationError(
                f"duplicate metric dictionary ID: {match.group(1)}"
            )
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows[match.group(1)] = cells
    return rows


def validate_research_contract(
    repo_root: str | Path,
    *,
    claim_path: str | Path = "research/claim-freeze.yaml",
    ledger_path: str | Path = "research/dataset-ledger.csv",
    analysis_path: str | Path = "research/analysis-plan.md",
    audit_path: str | Path | None = None,
    checklist_path: str
    | Path = ".omx/plans/computers-in-industry-submission-checklist.json",
    verify_asset_hashes: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    claim_file = _resolve(root, claim_path)
    ledger_file = _resolve(root, ledger_path)
    analysis_file = _resolve(root, analysis_path)
    checklist_file = _resolve(root, checklist_path)
    try:
        checklist = json.loads(
            checklist_file.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except json.JSONDecodeError as exc:
        raise ContractValidationError(f"{checklist_file} is not valid JSON") from exc
    if not isinstance(checklist, dict):
        raise ContractValidationError(f"{checklist_file} must contain a JSON object")
    claim = _load_yaml(claim_file)
    analysis_text = analysis_file.read_text(encoding="utf-8")
    issues: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            issues.append(message)

    claim_hash = sha256_canonical_text(claim_file)
    analysis_hash = sha256_canonical_text(analysis_file)
    require(
        claim_hash == REQUIRED_CLAIM_FREEZE_SHA256,
        "claim freeze canonical text does not match the frozen 1.4.0 contract",
    )
    require(
        analysis_hash == REQUIRED_ANALYSIS_PLAN_SHA256,
        "analysis plan canonical text does not match the frozen 1.2.0 contract",
    )
    analysis_contract = claim.get("analysis_contract", {})
    require(
        analysis_contract == REQUIRED_ANALYSIS_CONTRACT,
        "claim freeze analysis-contract reference is not the frozen 1.2.0 reference",
    )
    require(
        analysis_contract.get("sha256") == analysis_hash
        if isinstance(analysis_contract, dict)
        else False,
        "claim freeze analysis-plan SHA-256 does not match the analysis bytes",
    )

    claim_metadata = {
        field: claim.get(field) for field in REQUIRED_CLAIM_FREEZE_METADATA
    }
    require(
        claim_metadata == REQUIRED_CLAIM_FREEZE_METADATA
        and claim.get("authority") == REQUIRED_CLAIM_AUTHORITY
        and claim.get("scope") == REQUIRED_CLAIM_SCOPE,
        "claim freeze metadata differs from the frozen P0 contract",
    )

    claims = claim.get("claims", [])
    claim_ids = {entry.get("id") for entry in claims if isinstance(entry, dict)}
    require(claim_ids == REQUIRED_CLAIM_IDS, "claim IDs must be exactly C1, C2, and C3")

    audit_ref = claim.get("deviation_coverage_audit", {})
    declared_audit_path = audit_ref.get(
        "path", "research/deviation-coverage-audit.yaml"
    )
    declared_audit_ref = Path(str(declared_audit_path))
    require(
        str(declared_audit_path) == "research/deviation-coverage-audit.yaml"
        and not declared_audit_ref.is_absolute()
        and ".." not in declared_audit_ref.parts,
        "claim freeze deviation-audit path must remain the frozen repo-relative path",
    )
    audit_file = _resolve(root, audit_path or declared_audit_path)
    audit = _load_yaml(audit_file)
    audit_hash = sha256_canonical_text(audit_file)
    require(
        audit_hash == REQUIRED_DEVIATION_AUDIT_SHA256,
        "deviation audit canonical text does not match the frozen P0 evidence",
    )
    require(
        str(audit_ref.get("sha256", "")).upper() == audit_hash,
        "claim freeze deviation-audit SHA-256 does not match the audit bytes",
    )
    require(
        audit_ref.get("hash_canonicalization") == REQUIRED_HASH_CANONICALIZATION,
        "claim freeze deviation-audit hash canonicalization changed",
    )
    require(
        audit.get("content_hash_canonicalization") == REQUIRED_HASH_CANONICALIZATION,
        "deviation audit content-hash canonicalization changed",
    )
    require(
        audit_ref.get("status") == audit.get("status"),
        "claim freeze and deviation audit statuses differ",
    )
    require(
        audit.get("schema_version") == "1.0.0",
        "deviation audit schema version must remain 1.0.0",
    )
    require(
        audit.get("status") == REQUIRED_AUDIT_STATUS,
        "deviation audit status is not the frozen P0 status",
    )
    require(
        set(audit.get("classification_values", [])) == REQUIRED_AUDIT_CLASSIFICATIONS,
        "deviation audit classification values differ from the frozen P0 vocabulary",
    )
    coverage_matrix = audit.get("coverage_matrix", {})
    if not isinstance(coverage_matrix, dict):
        coverage_matrix = {}
        require(False, "deviation coverage_matrix must be a mapping")
    require(
        set(coverage_matrix) == set(REQUIRED_DEVIATION_COVERAGE),
        f"deviation categories must be exactly {sorted(REQUIRED_DEVIATION_COVERAGE)}",
    )
    required_matrix_fields = REQUIRED_DEVIATION_DATASETS | {"frozen_decision"}
    for category, expected_cells in REQUIRED_DEVIATION_COVERAGE.items():
        raw_cells = coverage_matrix.get(category, {})
        cells = raw_cells if isinstance(raw_cells, dict) else {}
        require(
            set(cells) == required_matrix_fields,
            f"deviation category {category} must contain exactly {sorted(required_matrix_fields)}",
        )
        for dataset, expected_classification in expected_cells.items():
            classification = cells.get(dataset)
            require(
                classification in REQUIRED_AUDIT_CLASSIFICATIONS,
                f"deviation category {category} has invalid {dataset} classification",
            )
            require(
                classification == expected_classification,
                f"deviation category {category} changed frozen {dataset} classification",
            )
        frozen_decision = str(cells.get("frozen_decision", "")).strip().upper()
        require(
            frozen_decision.startswith("EXCLUDED"),
            f"deviation category {category} must remain exclusionary while the whitelist is empty",
        )

    raw_sources = audit.get("sources", [])
    sources = (
        [source for source in raw_sources if isinstance(source, dict)]
        if isinstance(raw_sources, list)
        else []
    )
    require(
        len(sources) == len(raw_sources) if isinstance(raw_sources, list) else False,
        "every deviation-audit source must be a mapping",
    )
    source_ids = [str(source.get("source_id", "")) for source in sources]
    require(
        len(source_ids) == len(set(source_ids)),
        "deviation-audit source IDs must be unique",
    )
    sources_by_id = {str(source.get("source_id", "")): source for source in sources}
    require(
        REQUIRED_AUDIT_SOURCE_IDS <= set(sources_by_id),
        f"deviation audit is missing required evidence sources: {sorted(REQUIRED_AUDIT_SOURCE_IDS - set(sources_by_id))}",
    )
    for source_id in REQUIRED_AUDIT_SOURCE_IDS:
        source = sources_by_id.get(source_id, {})
        require(
            bool(source.get("kind")),
            f"deviation-audit source {source_id} is missing kind",
        )
        require(
            bool(source.get("sha256")),
            f"deviation-audit source {source_id} is missing SHA-256",
        )
        require(
            bool(source.get("verified_facts")),
            f"deviation-audit source {source_id} has no verified facts",
        )
        require(
            bool(
                source.get("path")
                or source.get("local_path")
                or source.get("local_zip")
            ),
            f"deviation-audit source {source_id} has no pinned local evidence",
        )

    claim_whitelist = audit_ref.get("frozen_formal_deviation_claim_whitelist", [])
    audit_whitelist = audit.get("frozen_formal_deviation_claim_whitelist", [])
    require(
        claim_whitelist == audit_whitelist,
        "claim freeze and audit deviation whitelists differ",
    )
    require(
        claim_whitelist == [] and audit_whitelist == [],
        "P0 deviation whitelist must remain empty until a versioned validator amendment",
    )

    verified_evidence_hashes = 0
    for source in sources:
        if not isinstance(source, dict) or not source.get("sha256"):
            continue
        local_ref = (
            source.get("path") or source.get("local_path") or source.get("local_zip")
        )
        if not local_ref:
            continue
        local_ref_path = Path(str(local_ref))
        require(
            not local_ref_path.is_absolute() and ".." not in local_ref_path.parts,
            f"audit evidence path must be repo-relative: {local_ref}",
        )
        local_file = _resolve(root, local_ref)
        try:
            local_file.relative_to(root)
        except ValueError:
            require(False, f"audit evidence path escapes the repository: {local_ref}")
        require(local_file.is_file(), f"audit evidence file is missing: {local_ref}")
        if local_file.is_file():
            require(
                sha256_file(local_file) == str(source["sha256"]).upper(),
                f"audit evidence SHA-256 mismatch: {local_ref}",
            )
            verified_evidence_hashes += 1

    conditional = {
        endpoint.get("id"): endpoint
        for endpoint in claim.get("endpoints", {}).get("conditional_endpoints", [])
        if isinstance(endpoint, dict)
    }
    deviation_endpoint = conditional.get("DEVIATION_RECALL", {})
    if not audit_whitelist:
        require(
            deviation_endpoint.get("reportable") is False,
            "empty deviation whitelist must make deviation recall non-reportable",
        )
        require(
            str(deviation_endpoint.get("status", "")).startswith("INACTIVE_"),
            "empty deviation whitelist must make deviation recall inactive",
        )
        require(
            "inactive and non-reportable" in analysis_text.lower(),
            "analysis plan must state that deviation recall is inactive and non-reportable",
        )

    for entry in claims:
        if not isinstance(entry, dict):
            continue
        require(
            bool(entry.get("frozen_claim")),
            f"claim {entry.get('id')} is missing frozen_claim",
        )
        require(
            bool(entry.get("required_evidence")),
            f"claim {entry.get('id')} is missing required_evidence",
        )
    require(bool(claim.get("stopping_rules")), "claim freeze is missing stopping rules")
    require(
        bool(claim.get("prohibited_claims")),
        "claim freeze is missing prohibited claims",
    )
    require(
        bool(claim.get("independent_unit", {}).get("primary")),
        "claim freeze is missing the independent unit",
    )

    endpoints = claim.get("endpoints", {})
    primary = endpoints.get("primary", {})
    require(
        set(primary.get("comparators", [])) == REQUIRED_COMPARATORS,
        "primary comparators must be fixed_streak and hmm_or_crf",
    )
    for field in ("metric", "unit", "cluster_level", "direction"):
        require(bool(primary.get(field)), f"primary endpoint is missing {field}")
    success_gate = endpoints.get("primary_success_gate", {})
    raw_branches = success_gate.get("branches", [])
    branches = [branch for branch in raw_branches if isinstance(branch, dict)]
    branch_ids = {branch.get("id") for branch in branches}
    require(
        len(raw_branches) == 2
        and len(branches) == 2
        and branch_ids == {"A_FSAR_AT_MATCHED_COVERAGE", "B_COVERAGE_AT_MATCHED_FSAR"},
        "primary success gate must contain exactly the matched-coverage and matched-FSAR branches",
    )
    try:
        family_alpha = float(str(success_gate.get("family_wise_alpha_two_sided")))
        branch_alphas = [float(str(branch.get("alpha"))) for branch in branches]
    except (TypeError, ValueError):
        family_alpha = math.nan
        branch_alphas = []
        require(False, "family and branch alpha values must be numeric")
    require(
        math.isfinite(family_alpha) and 0 < family_alpha < 1,
        "family alpha must be finite and between zero and one",
    )
    require(
        math.isclose(family_alpha, 0.05, rel_tol=0.0, abs_tol=1e-12),
        "family alpha must remain frozen at 0.05",
    )
    require(
        len(branch_alphas) == 2
        and all(math.isfinite(alpha) and alpha > 0 for alpha in branch_alphas),
        "branch alpha values must be finite and positive",
    )
    if len(branch_alphas) == 2 and math.isfinite(family_alpha):
        require(
            math.isclose(sum(branch_alphas), family_alpha, rel_tol=0.0, abs_tol=1e-12),
            "branch alpha allocation must equal the family alpha",
        )
    require(
        len(branch_alphas) == 2
        and all(
            math.isclose(alpha, 0.025, rel_tol=0.0, abs_tol=1e-12)
            for alpha in branch_alphas
        ),
        "each success branch alpha must remain frozen at 0.025",
    )
    branches_by_id = {
        str(branch.get("id")): branch for branch in branches if isinstance(branch, dict)
    }
    for branch_id, expected_branch in REQUIRED_PRIMARY_BRANCHES.items():
        branch = branches_by_id.get(branch_id, {})
        require(
            branch.get("decision") == expected_branch["decision"],
            f"success branch {branch_id} decision text changed",
        )
        require(
            branch.get("alpha") == expected_branch["alpha"],
            f"success branch {branch_id} alpha changed",
        )
    require(
        success_gate.get("overall_decision") == REQUIRED_OVERALL_DECISION,
        "primary success overall decision changed",
    )
    require(
        all("Holm" in str(branch.get("decision", "")) for branch in branches),
        "each success branch must use Holm adjustment",
    )

    operating_contract = success_gate.get("operating_point_contract", {})
    if not isinstance(operating_contract, dict):
        operating_contract = {}
        require(
            False,
            "primary success gate is missing a structured operating-point contract",
        )
    require(
        set(operating_contract)
        == {
            "stage_schedule",
            "required_manifest_fields",
            "branch_a",
            "branch_b",
            "common_coverage_support",
            "task_direction_rule",
        },
        "operating-point contract fields differ from the frozen P0 schema",
    )
    schedule = operating_contract.get("stage_schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}
    require(
        set(schedule) == set(REQUIRED_OPERATING_POINT_SCHEDULE),
        "operating-point schedule fields differ from the frozen P0 schema",
    )
    for stage, expected_stage in REQUIRED_OPERATING_POINT_SCHEDULE.items():
        require(
            schedule.get(stage) == expected_stage,
            f"operating-point schedule changed for {stage}",
        )
    require(
        set(operating_contract.get("required_manifest_fields", []))
        == REQUIRED_OPERATING_POINT_MANIFEST_FIELDS,
        "mechanism-gate manifest fields differ from the frozen P0 contract",
    )
    branch_a_contract = operating_contract.get("branch_a", {})
    if not isinstance(branch_a_contract, dict):
        branch_a_contract = {}
    require(
        branch_a_contract == REQUIRED_BRANCH_A_CONTRACT,
        "branch A operating-point selector or matching rules changed",
    )
    branch_b_contract = operating_contract.get("branch_b", {})
    if not isinstance(branch_b_contract, dict):
        branch_b_contract = {}
    require(
        branch_b_contract.get("selector") == REQUIRED_BRANCH_B_CONTRACT["selector"],
        "branch B selector must match achieved FSAR",
    )
    require(
        branch_b_contract == REQUIRED_BRANCH_B_CONTRACT,
        "branch B target, pairwise matching, or tie-break rules changed",
    )
    common_support = operating_contract.get("common_coverage_support", {})
    if not isinstance(common_support, dict):
        common_support = {}
    require(
        common_support.get("intersection")
        == REQUIRED_COMMON_SUPPORT_CONTRACT["intersection"],
        "common-support intersection rule changed",
    )
    require(
        common_support == REQUIRED_COMMON_SUPPORT_CONTRACT,
        "common-support methods, cells, minimum width, or failure rule changed",
    )
    require(
        operating_contract.get("task_direction_rule") == REQUIRED_TASK_DIRECTION_RULE,
        "task direction rule changed",
    )

    rows, ledger_columns = _read_ledger(ledger_file)
    ledger_column_set = set(ledger_columns)
    missing_columns = REQUIRED_LEDGER_COLUMNS - ledger_column_set
    extra_columns = ledger_column_set - REQUIRED_LEDGER_COLUMNS
    require(
        not missing_columns,
        f"dataset ledger is missing columns: {sorted(missing_columns)}",
    )
    require(
        not extra_columns,
        "dataset ledger columns must exactly match schema 1.0.0; "
        f"unexpected columns: {sorted(extra_columns)}",
    )
    data_boundary = claim.get("data_boundary", {})
    allowed_splits = set(data_boundary.get("allowed_project_splits", []))
    require(bool(allowed_splits), "claim freeze is missing allowed project splits")
    require(
        allowed_splits == REQUIRED_ALLOWED_PROJECT_SPLITS,
        f"allowed project splits must remain exactly {sorted(REQUIRED_ALLOWED_PROJECT_SPLITS)}",
    )
    by_asset: dict[str, dict[str, str]] = {}
    by_digest: dict[str, list[dict[str, str]]] = defaultdict(list)
    verified_asset_hashes = 0
    for row in rows:
        asset_id = row.get("asset_id", "")
        asset_key = _canonical_identity(asset_id)
        require(bool(asset_key), "every ledger row must have an asset_id")
        require(asset_key not in by_asset, f"duplicate ledger asset_id: {asset_id}")
        by_asset[asset_key] = row
        for field in REQUIRED_NONBLANK_LEDGER_FIELDS:
            require(
                bool(row.get(field, "").strip()),
                f"ledger row {asset_id} has blank {field}",
            )
        require(
            row.get("schema_version") == "1.0.0",
            f"ledger row {asset_id} has unsupported schema_version",
        )
        record_type = row.get("record_type", "")
        require(
            record_type in REQUIRED_RECORD_TYPES,
            f"ledger row {asset_id} has invalid record_type",
        )
        source_split = row.get("source_split", "")
        require(
            source_split in REQUIRED_SOURCE_SPLITS
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", source_split) is not None,
            f"ledger row {asset_id} has invalid source_split token",
        )
        for field, allowed_tokens in (
            ("annotation_status", REQUIRED_ANNOTATION_STATUS_TOKENS),
            ("contamination_status", REQUIRED_CONTAMINATION_STATUS_TOKENS),
        ):
            tokens = [
                token.strip()
                for token in row.get(field, "").split(";")
                if token.strip()
            ]
            require(
                bool(tokens)
                and len(tokens) == len(set(tokens))
                and all(
                    re.fullmatch(r"[A-Z][A-Z0-9_]*", token) is not None
                    and token in allowed_tokens
                    for token in tokens
                ),
                f"ledger row {asset_id} has invalid {field} token",
            )
        if row.get("source_dataset") == "IndustReal":
            # The audit retains UNKNOWN_OFFICIAL_UNLABELED; the ledger maps
            # archive-only recordings to NOT_APPLICABLE so they cannot enter a
            # train/val/test split.
            annotation_tokens = {
                token.strip()
                for token in row.get("annotation_status", "").split(";")
                if token.strip()
            }
            if row.get("source_split") == "NOT_APPLICABLE":
                require(
                    "OFFICIAL_ACTION_ANNOTATIONS_UNAVAILABLE" in annotation_tokens,
                    f"archive-only IndustReal row {asset_id} must declare labels unavailable",
                )
                require(
                    "OFFICIAL_ACTION_ANNOTATIONS_AVAILABLE" not in annotation_tokens,
                    f"archive-only IndustReal row {asset_id} cannot claim labels available",
                )
            else:
                require(
                    row.get("source_split") in {"train", "val", "test"},
                    f"IndustReal row {asset_id} has invalid source split",
                )
                require(
                    "OFFICIAL_ACTION_ANNOTATIONS_AVAILABLE" in annotation_tokens,
                    f"labeled IndustReal row {asset_id} must declare labels available",
                )
                require(
                    "OFFICIAL_ACTION_ANNOTATIONS_UNAVAILABLE" not in annotation_tokens,
                    f"labeled IndustReal row {asset_id} cannot declare labels unavailable",
                )
            expected_contamination = (
                "OFFICIAL_TEST_SOURCE"
                if row.get("source_split") == "test"
                else "OFFICIAL_SOURCE"
            )
            require(
                row.get("contamination_status") == expected_contamination,
                f"IndustReal row {asset_id} has split/contamination mismatch",
            )
        parsed_dimensions: dict[str, int] = {}
        for field in ("frame_start", "frame_end", "frame_count", "width", "height"):
            raw_value = row.get(field, "")
            if re.fullmatch(r"0|[1-9][0-9]*", raw_value) is None:
                require(
                    False,
                    f"ledger row {asset_id} has invalid non-negative integer {field}",
                )
                continue
            parsed_dimensions[field] = int(raw_value)
        if "frame_count" in parsed_dimensions:
            require(
                parsed_dimensions["frame_count"] > 0,
                f"ledger row {asset_id} frame_count must be positive",
            )
        for field in ("width", "height"):
            if field in parsed_dimensions:
                require(
                    parsed_dimensions[field] > 0,
                    f"ledger row {asset_id} {field} must be positive",
                )
        if {"frame_start", "frame_end", "frame_count"} <= parsed_dimensions.keys():
            require(
                parsed_dimensions["frame_end"] >= parsed_dimensions["frame_start"]
                and parsed_dimensions["frame_end"]
                - parsed_dimensions["frame_start"]
                + 1
                == parsed_dimensions["frame_count"],
                f"ledger row {asset_id} frame interval/count mismatch",
            )
        hash_verified_on = row.get("hash_verified_on", "")
        try:
            verified_date = date.fromisoformat(hash_verified_on)
        except ValueError:
            verified_date = None
        require(
            verified_date is not None and verified_date.isoformat() == hash_verified_on,
            f"ledger row {asset_id} has invalid hash_verified_on date",
        )
        for field in (
            "formal_use_allowed",
            "independent_unit_eligible",
            "tombstone_irreversible",
        ):
            require(
                row.get(field) in {"YES", "NO"},
                f"ledger row {asset_id} has invalid {field}",
            )
        require(
            row.get("manually_viewed") in {"YES", "NO", "UNKNOWN"},
            f"ledger row {asset_id} has invalid manually_viewed",
        )
        project_split = row.get("project_split", "")
        require(
            project_split in allowed_splits
            or project_split == "UNASSIGNED_BLOCKED_IDENTITY",
            f"ledger row {asset_id} has unrecognized project_split {project_split}",
        )
        require(
            row.get("split_locked") == "YES", f"ledger split is not locked: {asset_id}"
        )
        expected_formal_use = "YES" if project_split == "FORMAL_TEST_SEALED" else "NO"
        require(
            row.get("formal_use_allowed") == expected_formal_use,
            (
                f"formal row {asset_id} is not explicitly allowed for formal use"
                if project_split == "FORMAL_TEST_SEALED"
                else f"non-formal row {asset_id} cannot allow formal use"
            ),
        )
        if project_split in RESTRICTED_IDENTITY_SPLITS:
            prefix = (
                "formal row"
                if project_split == "FORMAL_TEST_SEALED"
                else "restricted row"
            )
            for field in (
                "source_dataset",
                "operator_id",
                "complete_job_id",
                "task_id",
                "source_split",
                "canonical_root_id",
                "canonical_group_id",
                "manually_viewed",
            ):
                require(
                    not _is_unknown_identity(row.get(field, "")),
                    f"{prefix} {asset_id} has unknown {field}",
                )
            error_types = _canonical_identity(row.get("error_types", ""))
            require(
                error_types not in {"", "unknown", "not_applicable"},
                f"{prefix} {asset_id} has unknown error_types",
            )
        is_synthetic = (
            row.get("record_type", "").strip().upper().startswith("SYNTHETIC")
            or row.get("source_dataset", "").strip().upper() == "SCRIPT_GENERATED"
        )
        if is_synthetic:
            require(
                project_split == "EXCLUDED_SYNTHETIC",
                f"synthetic row {asset_id} must remain EXCLUDED_SYNTHETIC",
            )
            require(
                row.get("formal_use_allowed") == "NO",
                f"synthetic row {asset_id} cannot allow formal use",
            )
            require(
                row.get("independent_unit_eligible") == "NO",
                f"synthetic row {asset_id} cannot be an independent unit",
            )
            require(
                row.get("tombstone_irreversible") == "YES",
                f"synthetic row {asset_id} must keep an irreversible tombstone",
            )
        digest = row.get("sha256", "").upper()
        valid_digest = bool(re.fullmatch(r"[0-9A-F]{64}", digest))
        require(valid_digest, f"invalid SHA-256 syntax for {asset_id}")
        if valid_digest:
            by_digest[digest].append(row)
        source_path = row.get("source_path", "")
        source_ref = Path(source_path)
        require(
            not source_ref.is_absolute() and ".." not in source_ref.parts,
            f"ledger source_path must be repo-relative: {asset_id}",
        )
        source_file = _resolve(root, source_path)
        try:
            source_file.relative_to(root)
        except ValueError:
            require(False, f"ledger source_path escapes the repository: {asset_id}")
        require(source_file.is_file(), f"ledger source file is missing: {source_path}")
        if verify_asset_hashes and source_file.is_file():
            require(
                sha256_file(source_file) == digest,
                f"ledger SHA-256 mismatch: {asset_id}",
            )
            verified_asset_hashes += 1

    for required_asset_id, expected_fields in REQUIRED_INITIAL_LEDGER_ASSETS.items():
        required_row = by_asset.get(_canonical_identity(required_asset_id))
        require(
            required_row is not None,
            f"required initial ledger asset {required_asset_id} is absent",
        )
        if required_row is None:
            continue
        for field, expected_value in expected_fields.items():
            actual_value = required_row.get(field, "")
            if field == "sha256":
                actual_value = actual_value.upper()
            require(
                actual_value == expected_value,
                f"required initial ledger asset {required_asset_id} changed {field}",
            )

    for digest, duplicate_rows in by_digest.items():
        if len(duplicate_rows) < 2:
            continue
        roots = {
            _canonical_identity(row.get("canonical_root_id", ""))
            for row in duplicate_rows
        }
        splits = {row.get("project_split", "") for row in duplicate_rows}
        require(len(roots) == 1, f"duplicate SHA-256 {digest} crosses canonical roots")
        require(len(splits) == 1, f"duplicate SHA-256 {digest} crosses project splits")
        tombstones = {
            _canonical_identity(row.get("tombstone_id", "")) for row in duplicate_rows
        }
        if any(tombstones):
            require(
                "" not in tombstones and len(tombstones) == 1,
                f"duplicate SHA-256 {digest} escapes an irreversible tombstone",
            )

    for row in rows:
        parent_id = row.get("parent_asset_id", "")
        child_id = row.get("asset_id", "")
        has_parent = not _is_unknown_identity(parent_id)
        is_derived = (
            has_parent
            or row.get("record_type", "").strip().upper().startswith("DERIVED_")
            or not _is_unknown_identity(row.get("derivation", ""))
        )
        if is_derived:
            require(
                row.get("independent_unit_eligible") == "NO",
                f"derived asset {child_id} cannot be an independent unit",
            )
        if row.get("independent_unit_eligible") == "YES":
            root_shape = (
                not has_parent
                and _canonical_identity(child_id)
                == _canonical_identity(row.get("canonical_root_id", ""))
                and row.get("record_type", "").strip().upper()
                in INDEPENDENT_ROOT_RECORD_TYPES
                and _is_unknown_identity(row.get("derivation", ""))
            )
            require(
                root_shape, f"independent unit {child_id} is not a canonical root asset"
            )
        if not has_parent:
            continue
        parent = by_asset.get(_canonical_identity(parent_id))
        require(
            parent is not None,
            f"derived asset {child_id} has missing parent {parent_id}",
        )
        if parent is None:
            continue
        for field in (
            "canonical_root_id",
            "canonical_group_id",
            "source_dataset",
            "operator_id",
            "complete_job_id",
            "task_id",
            "source_split",
        ):
            require(
                _canonical_identity(row.get(field, ""))
                == _canonical_identity(parent.get(field, "")),
                f"derived asset {child_id} does not inherit {field}",
            )
        for field in (
            "project_split",
            "formal_use_allowed",
            "tombstone_id",
            "tombstone_irreversible",
        ):
            require(
                row.get(field) == parent.get(field),
                f"derived asset {child_id} does not inherit {field}",
            )
        if parent.get("manually_viewed") == "YES":
            require(
                row.get("manually_viewed") == "YES",
                f"derived asset {child_id} does not inherit manual-view history",
            )
        parent_marks = {
            mark.strip()
            for mark in parent.get("contamination_status", "").split(";")
            if mark.strip()
        }
        child_marks = {
            mark.strip()
            for mark in row.get("contamination_status", "").split(";")
            if mark.strip()
        }
        require(
            parent_marks <= child_marks,
            f"derived asset {child_id} does not inherit contamination_status",
        )

    exclusions = data_boundary.get("known_permanent_exclusions", [])
    exclusions_by_asset = {
        _canonical_identity(str(exclusion.get("asset_id", ""))): exclusion
        for exclusion in exclusions
        if isinstance(exclusion, dict)
    }
    for asset_id, frozen in REQUIRED_IRREVERSIBLE_TOMBSTONES.items():
        exclusion = exclusions_by_asset.get(_canonical_identity(asset_id))
        require(
            exclusion is not None,
            f"required irreversible tombstone {asset_id} is absent from claim freeze",
        )
        if exclusion is not None:
            require(
                exclusion.get("irreversible") is True,
                f"required exclusion {asset_id} is no longer irreversible",
            )
            require(
                exclusion.get("tombstone_id") == frozen["tombstone_id"],
                f"required tombstone ID changed for {asset_id}",
            )
            require(
                exclusion.get("project_split") == frozen["project_split"],
                f"required exclusion split changed for {asset_id}",
            )
            require(
                exclusion.get("formal_use_allowed") is False,
                f"required exclusion {asset_id} now allows formal use",
            )
            if asset_id == "MECCANO-0009":
                require(
                    exclusion.get("mainline_use_allowed") is True,
                    "MECCANO-0009 is no longer admitted for disclosed mainline use",
                )
                require(
                    set(exclusion.get("forbidden_mainline_roles", []))
                    == {
                        "calibration evidence",
                        "pilot evidence",
                        "formal test evidence",
                        "unseen generalization evidence",
                    },
                    "MECCANO-0009 formal-evidence exclusions changed",
                )
            require(
                set(exclusion.get("match_keys", [])) == frozen["match_keys"],
                f"required tombstone match keys changed for {asset_id}",
            )
        excluded_row = by_asset.get(_canonical_identity(asset_id))
        require(
            excluded_row is not None,
            f"required irreversible tombstone {asset_id} is absent from dataset ledger",
        )
        if excluded_row is None:
            continue
        require(
            excluded_row.get("sha256", "").upper() == frozen["sha256"],
            f"required tombstone content hash changed for {asset_id}",
        )
        require(
            excluded_row.get("tombstone_id") == frozen["tombstone_id"],
            f"required ledger tombstone ID changed for {asset_id}",
        )
        require(
            excluded_row.get("tombstone_irreversible") == "YES",
            f"required ledger tombstone is reversible for {asset_id}",
        )
        require(
            excluded_row.get("project_split") == frozen["project_split"],
            f"required ledger exclusion split changed for {asset_id}",
        )
        require(
            excluded_row.get("formal_use_allowed") == "NO",
            f"required ledger exclusion {asset_id} allows formal use",
        )
        for field in ("record_type", "source_dataset"):
            if field in frozen:
                require(
                    excluded_row.get(field) == frozen[field],
                    f"required ledger {field} changed for {asset_id}",
                )
        for related in rows:
            exact_duplicate = related.get("sha256", "").upper() == frozen["sha256"]
            same_root = _canonical_identity(
                related.get("canonical_root_id", "")
            ) == _canonical_identity(asset_id)
            if same_root or exact_duplicate:
                require(
                    related.get("tombstone_id") == frozen["tombstone_id"],
                    f"asset {related.get('asset_id')} escapes tombstone {frozen['tombstone_id']}",
                )
                require(
                    related.get("project_split") == frozen["project_split"],
                    f"asset {related.get('asset_id')} escapes required exclusion split",
                )
                require(
                    related.get("formal_use_allowed") == "NO",
                    f"asset {related.get('asset_id')} escapes formal-use exclusion",
                )

    for exclusion in exclusions_by_asset.values():
        asset_id = str(exclusion.get("asset_id", ""))
        excluded_row = by_asset.get(_canonical_identity(asset_id))
        require(
            excluded_row is not None,
            f"irreversible exclusion is absent from ledger: {asset_id}",
        )
        if excluded_row is None:
            continue
        require(
            exclusion.get("irreversible") is True,
            f"exclusion is not irreversible: {asset_id}",
        )
        require(
            bool(exclusion.get("tombstone_id")),
            f"exclusion has no tombstone ID: {asset_id}",
        )
        require(
            excluded_row.get("tombstone_irreversible") == "YES",
            f"ledger tombstone is not irreversible: {asset_id}",
        )
        require(
            excluded_row.get("tombstone_id") == exclusion.get("tombstone_id"),
            f"tombstone mismatch for exclusion {asset_id}",
        )
        require(
            excluded_row.get("project_split") == exclusion.get("project_split"),
            f"irreversible exclusion {asset_id} must remain project_split {exclusion.get('project_split')}",
        )
        require(
            excluded_row.get("formal_use_allowed") == "NO",
            f"irreversible exclusion {asset_id} cannot allow formal use",
        )

    identity_splits: dict[tuple[str, str], set[str]] = defaultdict(set)
    identity_display: dict[tuple[str, str], str] = {}
    for row in rows:
        for field in (
            "canonical_root_id",
            "canonical_group_id",
            "operator_id",
            "complete_job_id",
        ):
            value = row.get(field, "")
            if not _is_unknown_identity(value):
                key = (field, _canonical_identity(value))
                identity_splits[key].add(row.get("project_split", ""))
                identity_display.setdefault(key, value.strip().upper())
    for key, splits in identity_splits.items():
        field, _ = key
        display = identity_display[key]
        require(
            len(splits) == 1,
            f"{field} {display} crosses project splits: {sorted(splits)}",
        )

    root_profiles: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    root_display: dict[str, str] = {}
    for row in rows:
        root_key = _canonical_identity(row.get("canonical_root_id", ""))
        if not root_key:
            continue
        root_display.setdefault(
            root_key, row.get("canonical_root_id", "").strip().upper()
        )
        for field in (
            "source_dataset",
            "canonical_group_id",
            "operator_id",
            "complete_job_id",
            "task_id",
            "project_split",
            "tombstone_id",
        ):
            root_profiles[root_key][field].add(_canonical_identity(row.get(field, "")))
    for root_key, profile in root_profiles.items():
        for field, values in profile.items():
            require(
                len(values) == 1,
                f"canonical root {root_display[root_key]} has inconsistent {field}",
            )

    formal_asset_rows = [
        row for row in rows if row.get("project_split") == "FORMAL_TEST_SEALED"
    ]
    formal_independent_units = [
        row
        for row in formal_asset_rows
        if row.get("independent_unit_eligible") == "YES"
    ]
    for row in formal_asset_rows:
        asset_id = row.get("asset_id", "")
        require(
            row.get("formal_use_allowed") == "YES",
            f"formal row {asset_id} is not explicitly allowed for formal use",
        )
        require(
            row.get("manually_viewed") == "NO",
            f"formal row {asset_id} was manually viewed",
        )
    formal_units_by_root: dict[str, list[dict[str, str]]] = defaultdict(list)
    formal_units_by_job: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in formal_independent_units:
        root_key = _canonical_identity(row.get("canonical_root_id", ""))
        formal_units_by_root[root_key].append(row)
        job_key = (
            _canonical_identity(row.get("operator_id", "")),
            _canonical_identity(row.get("complete_job_id", "")),
        )
        formal_units_by_job[job_key].append(row)
    formal_root_keys = {
        _canonical_identity(row.get("canonical_root_id", ""))
        for row in formal_asset_rows
    }
    for root_key in formal_root_keys:
        require(
            len(formal_units_by_root[root_key]) == 1,
            f"formal canonical root {root_display.get(root_key, root_key)} must have exactly one independent unit",
        )
    for job_key, units in formal_units_by_job.items():
        require(
            len(units) == 1,
            f"formal operator/job {job_key} has multiple independent units",
        )

    metrics = _metric_rows(analysis_text)
    for metric_id, metric_cells in metrics.items():
        require(
            len(metric_cells) == 5 and all(metric_cells),
            f"metric dictionary row is incomplete: {metric_id}",
        )
    claim_metric_ids = {primary.get("metric")}
    for endpoint in endpoints.get("key_secondary", []):
        if isinstance(endpoint, dict):
            claim_metric_ids.add(endpoint.get("metric"))
    for endpoint in endpoints.get("conditional_endpoints", []):
        if isinstance(endpoint, dict):
            claim_metric_ids.add(endpoint.get("metric"))
    supporting_metrics = endpoints.get("supporting", [])
    require(
        all(
            isinstance(metric_id, str) and metric_id for metric_id in supporting_metrics
        ),
        "supporting metric IDs must be non-empty strings",
    )
    claim_metric_ids.update(
        metric_id for metric_id in supporting_metrics if isinstance(metric_id, str)
    )
    for metric_id in sorted(
        metric_id for metric_id in claim_metric_ids if isinstance(metric_id, str)
    ):
        require(
            metric_id in metrics,
            f"metric dictionary is missing claim-listed metric {metric_id}",
        )
    if not audit_whitelist and "deviation_recall" in metrics:
        require(
            "non-reportable" in metrics["deviation_recall"][1],
            "deviation_recall metric row must remain non-reportable",
        )

    required_analysis_phrases = (
        "The first scheduled attempt after unsealing is the primary attempt.",
        "It is never imputed as FSAR zero.",
        "Bonferroni-across-branches and Holm-within-branch",
        "TOMBSTONE-MECCANO-0009",
        "Final numeric thresholds are frozen before G3 and remain unchanged through P5 pilot and formal testing.",
        "P5 pilot data are nuisance/power-only and cannot select or change methods, grids, thresholds, targets, tolerances, tie breaks, or endpoint floors.",
        "Branch B requires each method-task achieved FSAR to be within `delta_r` of `r*` and each Proposed-comparator achieved-FSAR difference to be within `delta_r`.",
        "The common coverage support interval is `[L, U]`, where `L` is the maximum of all six cell lower bounds and `U` is the minimum of all six cell upper bounds.",
        "If `U < L` or `U - L` is below the numeric minimum width frozen before G3, branch A, risk-coverage AUC, and G3 fail",
        "Each task-specific marginal Proposed-minus-comparator contrast is strictly favorable for both comparators",
    )
    for phrase in required_analysis_phrases:
        require(
            phrase in analysis_text, f"analysis plan is missing frozen rule: {phrase}"
        )

    g1_status = "BLOCKED_MISSING_FROZEN_ARTIFACTS"
    joint_gate_path = root / "research/audits/g1-g2-joint-gate.json"
    try:
        joint_gate = json.loads(
            joint_gate_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        joint_gate = {}
        require(False, f"joint G1/G2 gate is unreadable: {exc}")
    declared_decision_hash = joint_gate.get("decision_sha256")
    joint_body = {
        key: value for key, value in joint_gate.items() if key != "decision_sha256"
    }
    calculated_decision_hash = hashlib.sha256(
        json.dumps(
            joint_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    require(
        declared_decision_hash == calculated_decision_hash,
        "joint G1/G2 gate decision hash is stale",
    )
    for name, commitment in joint_gate.get("input_commitments", {}).items():
        if not isinstance(commitment, dict):
            require(False, f"joint gate input commitment is malformed: {name}")
            continue
        source = Path(str(commitment.get("path", "")))
        source = source if source.is_absolute() else (root / source)
        require(
            source.is_file()
            and sha256_file(source).lower()
            == str(commitment.get("file_sha256", "")).lower(),
            f"joint gate input commitment is stale: {name}",
        )
    g2 = joint_gate.get("g2", {})
    g2_pass = (
        isinstance(g2, dict)
        and g2.get("status") == "PASS"
        and isinstance(g2.get("checks"), dict)
        and bool(g2["checks"])
        and all(value is True for value in g2["checks"].values())
        and g2.get("failed_checks") == []
        and joint_gate.get("execute_g3_method_panel_allowed") is False
    )
    require(g2_pass, "joint G2 isolation gate must be a current PASS")
    g2_status = "PASS" if g2_pass else "BLOCKED_UNKNOWN_IDENTITY"
    g3_status = "BLOCKED_BY_G1" if g2_pass else "BLOCKED_BY_G1_G2"

    def index_checklist_records(
        raw_records: Any, *, kind: str, expected_count: int
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(raw_records, list):
            require(False, f"checklist {kind}s must be a list")
            return {}
        require(
            len(raw_records) == expected_count,
            f"checklist {kind}s must contain exactly {expected_count} entries",
        )
        indexed: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(raw_records):
            if not isinstance(record, dict):
                require(
                    False,
                    f"checklist {kind} entry {index} must be a mapping",
                )
                continue
            record_id = record.get("id")
            if (
                not isinstance(record_id, str)
                or not record_id.strip()
                or record_id != record_id.strip()
            ):
                require(
                    False,
                    f"checklist {kind} entry {index} ID must be a nonblank string without surrounding whitespace",
                )
                continue
            if record_id in indexed:
                require(False, f"duplicate checklist {kind} ID: {record_id}")
                continue
            indexed[record_id] = record
        return indexed

    gates = index_checklist_records(
        checklist.get("gates"),
        kind="gate",
        expected_count=len(REQUIRED_GATE_STATUSES),
    )
    phases = index_checklist_records(
        checklist.get("phases"),
        kind="phase",
        expected_count=len(REQUIRED_PHASE_STATUSES),
    )
    require(
        set(gates) == set(REQUIRED_GATE_STATUSES),
        "checklist gate IDs differ from G0-G8",
    )
    require(
        checklist.get("frozen_claims") == REQUIRED_CHECKLIST_FROZEN_CLAIMS,
        "checklist frozen claims differ from the P0 contract",
    )
    require(
        checklist.get("primary_endpoint") == REQUIRED_CHECKLIST_PRIMARY_ENDPOINT,
        "checklist primary endpoint differs from the P0 contract",
    )
    require(
        checklist.get("independent_unit") == REQUIRED_CHECKLIST_INDEPENDENT_UNIT,
        "checklist independent unit differs from the P0 contract",
    )
    require(
        checklist.get("formal_start_rules") == REQUIRED_FORMAL_START_RULES,
        "checklist formal start rules differ from the P0 contract",
    )
    for gate_id, expected_status in REQUIRED_GATE_STATUSES.items():
        require(
            gates.get(gate_id, {}).get("status") == expected_status,
            f"downstream gate {gate_id} status must match current evidence: {expected_status}",
        )
    require(
        set(phases) == set(REQUIRED_PHASE_STATUSES),
        "checklist phase IDs differ from P0-P9",
    )
    for phase_id, expected_status in REQUIRED_PHASE_STATUSES.items():
        require(
            phases.get(phase_id, {}).get("status") == expected_status,
            f"phase {phase_id} status must match current evidence: {expected_status}",
        )
    g0 = gates.get("G0", {})
    p0 = phases.get("P0", {})
    p0_validation = checklist.get("p0_validation", {})
    if not isinstance(p0_validation, dict):
        p0_validation = {}
    require(g0.get("status") == "PASS", "checklist G0 status must be PASS")
    require(p0.get("status") == "COMPLETE", "checklist P0 status must be COMPLETE")
    require(
        p0_validation.get("status") == "PASS",
        "checklist p0_validation status must be PASS",
    )
    require(
        p0_validation.get("audit_sha256") == audit_hash,
        "checklist deviation-audit SHA-256 is stale",
    )
    require(
        p0_validation.get("audit_hash_canonicalization")
        == REQUIRED_HASH_CANONICALIZATION,
        "checklist deviation-audit hash canonicalization is stale",
    )
    require(
        p0_validation.get("ledger_rows") == len(rows),
        "checklist ledger row count is stale",
    )
    require(
        p0_validation.get("formal_test_rows") == len(formal_independent_units),
        "checklist formal row count is stale",
    )
    require(
        p0_validation.get("metric_rows") == len(metrics),
        "checklist metric row count is stale",
    )
    require(
        p0_validation.get("verified_evidence_hashes") == verified_evidence_hashes,
        "checklist evidence-hash count is stale",
    )
    if verify_asset_hashes:
        require(
            p0_validation.get("verified_asset_hashes") == verified_asset_hashes,
            "checklist asset-hash count is stale",
        )
    require(
        p0_validation.get("G1_status") == g1_status,
        "checklist G1 block status is stale",
    )
    require(
        p0_validation.get("G2_status") == g2_status,
        "checklist G2 block status is stale",
    )
    require(
        p0_validation.get("G3_status") == g3_status,
        "checklist G3 block status is stale",
    )
    require(
        checklist.get("status") == "METHOD_AND_FORMAL_EVIDENCE_BLOCKED",
        "checklist must keep downstream evidence blocked",
    )
    require(
        checklist.get("current_evidence", {}).get("fresh_test_result")
        == REQUIRED_FRESH_TEST_RESULT,
        f"checklist fresh test result must be {REQUIRED_FRESH_TEST_RESULT}",
    )
    require(
        checklist.get("current_evidence", {}).get("formal_results_available") is False,
        "checklist cannot claim formal results at P0",
    )
    if issues:
        raise ContractValidationError(
            "research contract validation failed:\n- " + "\n- ".join(issues)
        )

    return {
        "status": "PASS",
        "claim_ids": sorted(claim_ids),
        "audit_sha256": audit_hash,
        "ledger_rows": len(rows),
        "formal_test_rows": len(formal_independent_units),
        "formal_asset_rows": len(formal_asset_rows),
        "metric_rows": len(metrics),
        "tombstones": sorted(
            {row.get("tombstone_id", "") for row in rows if row.get("tombstone_id")}
        ),
        "verified_asset_hashes": verified_asset_hashes,
        "verified_evidence_hashes": verified_evidence_hashes,
        "downstream_gates": {
            "G1": g1_status,
            "G2": g2_status,
            "G3": g3_status,
        },
    }
