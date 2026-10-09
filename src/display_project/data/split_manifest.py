"""Canonical split assignment with no root/operator/job/video leakage."""

from __future__ import annotations
from collections import defaultdict
from typing import Any, Iterable, Mapping

ALLOWED_SPLITS = {
    "DEVELOPMENT",
    "DEVELOPMENT_TUNING",
    "DEVELOPMENT_G3_EVALUATION",
    "CALIBRATION",
    "PILOT",
    "FORMAL_TEST_SEALED",
    "DEVELOPMENT_ONLY_CONTAMINATED",
    "EXCLUDED_SYNTHETIC",
    # Preserved rows whose identity audit failed.  These rows remain blocked
    # from every restricted split; allowing the label here keeps them visible
    # in versioned candidate manifests without silently reassigning them.
    "UNASSIGNED_BLOCKED_IDENTITY",
}


def build_split_manifest(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = [dict(row) for row in rows]
    root_groups: defaultdict[str, set[str]] = defaultdict(set)
    for r in normalized_rows:
        rid = str(r.get("canonical_root_id", ""))
        sp = str(r.get("project_split", "UNKNOWN"))
        if rid not in {"", "UNKNOWN", "NONE", "NOT_APPLICABLE"} and sp != "UNKNOWN":
            root_groups[rid].add(sp)
    if any(len(v) > 1 for v in root_groups.values()):
        raise ValueError("canonical_root_id crosses splits")
    root_splits: dict[str, str] = {
        str(r.get("canonical_root_id")): str(r.get("project_split"))
        for r in normalized_rows
        if str(r.get("canonical_root_id", ""))
        not in {"", "UNKNOWN", "NONE", "NOT_APPLICABLE"}
        and str(r.get("project_split", "UNKNOWN")) != "UNKNOWN"
    }
    for row in normalized_rows:
        if (
            str(row.get("project_split", "UNKNOWN")) == "UNKNOWN"
            and str(row.get("canonical_root_id", "")) in root_splits
        ):
            row["project_split"] = root_splits[str(row["canonical_root_id"])]
    groups: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for row in normalized_rows:
        split = str(row.get("project_split", "UNKNOWN"))
        if split not in ALLOWED_SPLITS:
            raise ValueError(f"unknown project split: {split}")
        for field in (
            "canonical_root_id",
            "operator_id",
            "complete_job_id",
            "video_id",
            "asset_id",
        ):
            value = str(row.get(field, "UNKNOWN"))
            industreal_complete_job_fallback = (
                str(row.get("source_dataset", "")).replace("_", "").replace("-", "").casefold()
                == "industreal"
                and field == "operator_id"
                and value.upper() == "UNKNOWN"
                and row.get("cluster_level") == "complete_job"
                and row.get("operator_identity_status") == "UNKNOWN_DISCLOSED"
            )
            if value in {"", "UNKNOWN", "NONE", "NOT_APPLICABLE"} and split in {
                "DEVELOPMENT_G3_EVALUATION",
                "CALIBRATION",
                "PILOT",
                "FORMAL_TEST_SEALED",
            } and not industreal_complete_job_fallback:
                raise ValueError(f"unknown {field} blocks restricted split")
            if value not in {"", "UNKNOWN", "NONE", "NOT_APPLICABLE"}:
                groups[(field, value)].add(split)
        tombstone = str(row.get("tombstone_id", "")).upper()
        root_or_asset = {
            str(row.get("canonical_root_id", "")).upper(),
            str(row.get("asset_id", "")).upper(),
        }
        if (
            tombstone == "TOMBSTONE-MECCANO-0009" or "MECCANO-0009" in root_or_asset
        ) and split in {
            "DEVELOPMENT_G3_EVALUATION",
            "CALIBRATION",
            "PILOT",
            "FORMAL_TEST_SEALED",
        }:
            raise ValueError("MECCANO-0009 tombstone is forbidden in restricted splits")
        if str(
            row.get("error_types", row.get("error_type", ""))
        ).upper() == "UNKNOWN" and split in {
            "DEVELOPMENT_G3_EVALUATION",
            "CALIBRATION",
            "PILOT",
            "FORMAL_TEST_SEALED",
        } and not (
            str(row.get("source_dataset", "")).replace("_", "").replace("-", "").casefold()
            == "industreal"
            and row.get("cluster_level") == "complete_job"
            and row.get("operator_identity_status") == "UNKNOWN_DISCLOSED"
            and row.get("truth_capability") == "ACTION_STEP_STATE_ONLY"
        ):
            raise ValueError("UNKNOWN error labels are forbidden in restricted splits")
    conflicts = [
        f"{k[0]} {k[1]} crosses splits: {sorted(v)}"
        for k, v in groups.items()
        if len(v) > 1
    ]
    if conflicts:
        raise ValueError("; ".join(conflicts))
    return sorted(normalized_rows, key=lambda r: str(r.get("asset_id", "")))


def build_candidate_split_manifest(
    rows: Iterable[Mapping[str, Any]], *, formal_admission: bool = False
) -> list[dict[str, Any]]:
    """Build a candidate manifest without filling in missing identity."""
    result = build_split_manifest(rows)
    restricted = {
        "DEVELOPMENT_G3_EVALUATION",
        "CALIBRATION",
        "PILOT",
        "FORMAL_TEST_SEALED",
    }
    identity_fields = ("operator_id", "complete_job_id", "canonical_root_id", "video_id")
    for row in result:
        if str(row.get("project_split", "UNKNOWN")) in restricted:
            unknown = [
                field
                for field in identity_fields
                if str(row.get(field, "UNKNOWN"))
                in {"", "UNKNOWN", "NONE", "NOT_APPLICABLE"}
            ]
            if (
                str(row.get("source_dataset", "")).replace("_", "").replace("-", "").casefold()
                == "industreal"
                and row.get("cluster_level") == "complete_job"
                and row.get("operator_identity_status") == "UNKNOWN_DISCLOSED"
            ):
                unknown = [field for field in unknown if field != "operator_id"]
            if unknown:
                raise ValueError(
                    "unknown identity blocks formal admission: " + ",".join(unknown)
                )
    by_asset = {str(row.get("asset_id", "")): row for row in result}
    for row in result:
        parent = str(row.get("parent_asset_id", ""))
        if parent not in {"", "NONE", "UNKNOWN", "NOT_APPLICABLE"}:
            parent_row = by_asset.get(parent)
            if parent_row is not None and str(parent_row.get("project_split")) != str(
                row.get("project_split")
            ):
                raise ValueError("adjacent-frame parent lineage crosses splits")
    return result
