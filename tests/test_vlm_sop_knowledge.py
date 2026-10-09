from __future__ import annotations

import json
from pathlib import Path

import pytest

from display_project.vlm_sop import (
    SOPKnowledgeError,
    build_qwen_sop_review_request,
    load_sop_knowledge,
)


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "configs/vlm/sop_knowledge/industreal_official_paper_v1.yaml"


def test_industreal_pdf_knowledge_pack_is_truth_free_and_audit_only():
    pack = load_sop_knowledge(PACK)
    assert pack.authority == "audit_only"
    assert pack.status == "DRAFT_REFERENCE_ONLY"
    assert "front_wheel_assembly_installed" in pack.step_ids
    assert pack.step("front_wheel_assembly_installed").source_refs


def test_meccano_development_pack_matches_task_steps():
    pack = load_sop_knowledge(
        ROOT / "configs/vlm/sop_knowledge/meccano_task_config_v1.yaml"
    )
    assert pack.task_id == "meccano"
    assert pack.status == "DEVELOPMENT_ONLY_CONFIG_DERIVED"
    assert pack.step_ids == (
        "pick_components",
        "align_components",
        "insert_screw",
        "tighten_screw",
        "verify_assembly",
    )
    assert pack.authority == "audit_only"


def test_prompt_contains_only_relevant_cards_and_fixed_authority():
    pack = load_sop_knowledge(PACK)
    request = build_qwen_sop_review_request(
        pack,
        clip_frame=42,
        current_step="front_chassis_installed",
        expected_step="front_chassis_pin_installed",
        perception_summary={
            "objects": ["front chassis", "chassis pin"],
            "occluded": True,
        },
    )
    user_prompt = json.dumps(request["messages"][1], ensure_ascii=False)
    assert "front_chassis_pin_installed" in user_prompt
    assert "rear_wheel_assembly_installed" not in user_prompt
    schema = request["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["authority"] == {"const": "audit_only"}
    assert schema["properties"]["state_write_allowed"] == {"const": False}
    assert schema["properties"]["annotation_used"] == {"const": False}


@pytest.mark.parametrize(
    "unsafe",
    [
        {"ground_truth": "front_chassis_pin_installed"},
        {"nested": {"oracle": "front_chassis_pin_installed"}},
        {"source": "recording/PSR_labels.csv"},
    ],
)
def test_prompt_rejects_truth_or_label_material(unsafe):
    with pytest.raises(SOPKnowledgeError):
        build_qwen_sop_review_request(
            load_sop_knowledge(PACK),
            clip_frame=1,
            current_step="front_chassis_installed",
            expected_step="front_chassis_pin_installed",
            perception_summary=unsafe,
        )


def test_loader_rejects_non_audit_authority(tmp_path):
    payload = PACK.read_text(encoding="utf-8").replace(
        "authority: audit_only", "authority: state_writer", 1
    )
    path = tmp_path / "unsafe.yaml"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(SOPKnowledgeError, match="audit_only"):
        load_sop_knowledge(path)
