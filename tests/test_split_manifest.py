import pytest
from display_project.data.split_manifest import (
    build_split_manifest,
    build_candidate_split_manifest,
)


def _row(asset, split, root="root", op="op", job="job", video="video"):
    return {
        "asset_id": asset,
        "project_split": split,
        "canonical_root_id": root,
        "operator_id": op,
        "complete_job_id": job,
        "video_id": video,
    }


def test_split_manifest_forbids_canonical_root_operator_job_video_cross_split():
    with pytest.raises(ValueError):
        build_split_manifest([_row("a", "DEVELOPMENT"), _row("b", "PILOT")])


def test_split_manifest_inherits_split_for_derived_assets():
    rows = [_row("root", "DEVELOPMENT"), _row("derived", "DEVELOPMENT", root="root")]
    out = build_split_manifest(rows)
    assert [r["project_split"] for r in out] == ["DEVELOPMENT", "DEVELOPMENT"]


def test_split_manifest_unknown_identity_blocks_restricted_split():
    with pytest.raises(ValueError):
        build_split_manifest([_row("x", "PILOT", root="UNKNOWN")])


def test_split_manifest_forbids_tombstoned_meccano_0009_in_restricted_split():
    row = _row("MECCANO-0009", "PILOT", root="MECCANO-0009")
    row["tombstone_id"] = "TOMBSTONE-MECCANO-0009"
    with pytest.raises(ValueError, match="0009"):
        build_split_manifest([row])


def test_candidate_unknown_identity_blocks_formal_admission_without_invention():
    row = _row("x", "PILOT", root="UNKNOWN", op="UNKNOWN", job="UNKNOWN", video="v")
    with pytest.raises(ValueError, match="unknown"):
        build_candidate_split_manifest([row], formal_admission=True)
    assert row["canonical_root_id"] == "UNKNOWN"


def test_candidate_parent_lineage_cannot_cross_split():
    root = _row("root", "DEVELOPMENT")
    child = _row("child", "PILOT")
    child["canonical_root_id"] = "child-root"
    child["parent_asset_id"] = "root"
    with pytest.raises(ValueError, match="crosses splits"):
        build_candidate_split_manifest([root, child])


def test_g3_evaluation_split_requires_complete_identity():
    valid = _row("g3", "DEVELOPMENT_G3_EVALUATION")
    assert build_split_manifest([valid])[0]["project_split"] == (
        "DEVELOPMENT_G3_EVALUATION"
    )
    with pytest.raises(ValueError, match="unknown operator_id"):
        build_split_manifest(
            [_row("g3", "DEVELOPMENT_G3_EVALUATION", op="UNKNOWN")]
        )


def test_g3_evaluation_split_rejects_meccano_0009():
    row = _row(
        "MECCANO-0009",
        "DEVELOPMENT_G3_EVALUATION",
        root="MECCANO-0009",
    )
    row["tombstone_id"] = "TOMBSTONE-MECCANO-0009"
    with pytest.raises(ValueError, match="0009"):
        build_split_manifest([row])
