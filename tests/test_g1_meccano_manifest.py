import json
from pathlib import Path

from display_project.experiments.g1_derivation import parse_action_csv
from scripts.build_g1_meccano_manifest import build


def test_official_jpg_frame_references_are_supported(tmp_path: Path):
    actions = tmp_path / "actions.csv"
    actions.write_text(
        "video_id,action_id,action_name,start_frame,end_frame\n"
        "0007,1,align_objects,00076.jpg,00120.jpg\n",
        encoding="utf-8",
    )
    parsed = parse_action_csv(actions)
    assert parsed[0]["start_frame"] == 76
    assert parsed[0]["end_frame"] == 120


def test_g1_manifest_excludes_test_and_freezes_proxy_boundary(
    tmp_path: Path,
):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "source_identity_manifest_status": "READY",
                "records": [
                    {
                        "video_id": video_id,
                        "dataset_split": (
                            "train"
                            if video_id in {"0014", "0016", "0018"}
                            else "val"
                        ),
                        "tombstone_id": "NONE",
                        "identity_values_invented": False,
                        "canonical_root_id": f"MECCANO-{video_id}",
                        "canonical_group_id": f"MECCANO::P-{video_id}",
                        "operator_id": f"P-{video_id}",
                        "complete_job_id": f"JOB-{video_id}",
                    }
                    for video_id in ("0014", "0016", "0018", "0007", "0017")
                ],
            }
        ),
        encoding="utf-8",
    )
    header = "video_id,action_id,action_name,start_frame,end_frame\n"
    rows = []
    for video_id in ("0014", "0016", "0018", "0007", "0017"):
        for index, action in enumerate(
            (
                "take_bolt",
                "align_objects",
                "plug_screw",
                "screw_screw_with_hands",
                "put_partial_model",
            )
        ):
            start = 100 + index * 20
            rows.append(
                f"{video_id},{index},{action},{start:05d}.jpg,"
                f"{start + 9:05d}.jpg\n"
            )
    train_actions = tmp_path / "train.csv"
    val_actions = tmp_path / "val.csv"
    train_actions.write_text(
        header
        + "".join(
            row
            for row in rows
            if row.startswith(("0014,", "0016,", "0018,"))
        ),
        encoding="utf-8",
    )
    val_actions.write_text(
        header
        + "".join(
            row for row in rows if row.startswith(("0007,", "0017,"))
        ),
        encoding="utf-8",
    )
    output = tmp_path / "manifest.json"
    merged = tmp_path / "actions.csv"
    payload = build(
        candidate_manifest=candidate,
        train_actions=train_actions,
        val_actions=val_actions,
        action_state_map=Path(
            "configs/g1/meccano-action-state-map.json"
        ),
        output=output,
        merged_actions_output=merged,
    )
    assert payload["status"] == "FROZEN_DEVELOPMENT_CALIBRATION"
    assert len(payload["records"]) == 5
    assert {row["video_id"] for row in payload["records"]} == {
        "0014",
        "0016",
        "0018",
        "0007",
        "0017",
    }
    assert all(row["formal_test"] is False for row in payload["records"])
    assert all(
        row["runtime_truth_store_allowed"] is False
        for row in payload["records"]
    )
    mapping = json.loads(
        Path("configs/g1/meccano-action-state-map.json").read_text(
            encoding="utf-8"
        )
    )
    assert "quality-verification" in mapping["proxy_labels"][
        "verify_assembly"
    ]["interpretation"]
