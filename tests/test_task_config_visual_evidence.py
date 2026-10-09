import pytest
from display_project.sop.task_config import (
    TaskConfigError,
    compile_task_config,
    load_task_config,
)
from display_project.sop.visual_evidence import ObjectObservation, VisualEvidenceEngine
from display_project.tracking.real_sop_zh import (
    _certificate_event_fields,
    render_real_sop_video_zh,
)


def test_meccano_config_drives_shared_engine():
    task = load_task_config("configs/tasks/meccano.yaml")
    items = [
        ObjectObservation("hand", 0.9, (10, 10, 40, 45)),
        ObjectObservation("metal bar", 0.9, (32, 20, 70, 40)),
    ]
    assert (
        VisualEvidenceEngine(task_config=task, confirm_frames=1, cooldown_frames=0)
        .observe("pick_components", items, frame_size=(100, 100))
        .confirmed_step
        == "pick_components"
    )


def test_industreal_and_unknown_predicate_fail_closed():
    task = load_task_config("configs/tasks/industreal.yaml")
    result = VisualEvidenceEngine(
        task_config=task, confirm_frames=1, cooldown_frames=0
    ).observe("retrieve_part", [], frame_size=(100, 100))
    assert result.confirmed_step is None and result.candidate_step is None
    with pytest.raises(TaskConfigError, match="unknown evidence predicate"):
        compile_task_config(
            {
                "schema_version": 1,
                "task_id": "x",
                "steps": [{"id": "a", "evidence": [{"predicate": "bogus"}]}],
            }
        )


def _evidence_config(predicate):
    return {
        "schema_version": 1,
        "task_id": "hostile",
        "steps": [{"id": "a", "evidence": [predicate]}],
    }


@pytest.mark.parametrize(
    "predicate",
    [
        {"predicate": "all", "predicates": [{"predicate": "count", "label": "x"}]},
        {
            "predicate": "all",
            "predicates": [{"predicate": "count", "label": "x", "min": 1, "wat": 2}],
        },
        {
            "predicate": "all",
            "predicates": [
                {
                    "predicate": "near",
                    "left": "x",
                    "right": "y",
                    "max_gap": float("nan"),
                }
            ],
        },
        {
            "predicate": "all",
            "predicates": [
                {"predicate": "near", "left": "x", "right": "y", "max_gap": True}
            ],
        },
        {
            "predicate": "all",
            "predicates": [{"predicate": "count", "label": "x", "min": 2, "max": 1}],
        },
    ],
)
def test_nested_predicates_validate_leaf_schema_fail_closed(predicate):
    with pytest.raises(TaskConfigError):
        compile_task_config(_evidence_config(predicate))


def test_nested_predicates_have_bounded_depth():
    predicate = {"predicate": "count", "label": "x", "min": 1}
    for _ in range(40):
        predicate = {"predicate": "all", "predicates": [predicate]}
    with pytest.raises(TaskConfigError, match="maximum nesting depth"):
        compile_task_config(_evidence_config(predicate))


def test_legacy_fixed_streak_is_explicit_and_nonbaseline_none_fails_closed():
    legacy = VisualEvidenceEngine.legacy_fixed_streak()
    assert legacy.legacy_baseline is True
    assert (legacy.confirm_frames, legacy.cooldown_frames) == (4, 12)
    with pytest.raises(ValueError, match="task_config is required"):
        VisualEvidenceEngine(confirm_frames=1, cooldown_frames=0)
    with pytest.raises(ValueError, match="legacy fixed_streak baseline is frozen"):
        VisualEvidenceEngine(legacy_baseline=True, confirm_frames=1, cooldown_frames=0)


def test_production_renderer_rejects_implicit_meccano_task(tmp_path):
    with pytest.raises(ValueError, match="task_config is required"):
        render_real_sop_video_zh(
            tmp_path / "missing.mp4",
            tmp_path / "out.mp4",
            tmp_path / "events.jsonl",
            None,
            None,
            font_path="font.ttf",
            decision_policy="fixed_streak",
        )


def test_fixed_streak_event_omits_all_certificate_fields(tmp_path):
    fields = _certificate_event_fields(None, tmp_path / "certificates.jsonl")

    assert fields == {
        "certificate_hash": None,
        "graph_digest": None,
        "certificate_artifact_ref": None,
    }
