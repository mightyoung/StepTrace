from display_project.sop.task_config import load_task_config
from display_project.sop.visual_evidence import ObjectObservation, VisualEvidenceEngine


def _engine(confirm_frames=4, cooldown_frames=12):
    return VisualEvidenceEngine(
        task_config=load_task_config("configs/tasks/meccano.yaml"),
        confirm_frames=confirm_frames,
        cooldown_frames=cooldown_frames,
    )


def observation(label, box, confidence=0.9):
    return ObjectObservation(label=label, confidence=confidence, xyxy=box)


def test_expected_step_requires_stable_model_geometry_evidence():
    engine = _engine(confirm_frames=2, cooldown_frames=0)
    items = [
        observation("hand", (10, 10, 40, 45)),
        observation("metal bar", (32, 20, 70, 40)),
    ]

    first = engine.observe("pick_components", items, frame_size=(100, 100))
    second = engine.observe("pick_components", items, frame_size=(100, 100))

    assert first.candidate_step == "pick_components"
    assert first.confirmed_step is None
    assert second.confirmed_step == "pick_components"
    assert "hand_component_contact" in second.reasons


def test_out_of_order_visual_signal_cannot_confirm_an_unexpected_step():
    engine = _engine(confirm_frames=1, cooldown_frames=0)
    items = [
        observation("hand", (40, 20, 65, 55)),
        observation("metal bar", (50, 30, 85, 45)),
        observation("screw", (52, 32, 57, 38)),
    ]

    result = engine.observe("pick_components", items, frame_size=(100, 100))

    assert result.confirmed_step == "pick_components"
    assert result.confirmed_step != "insert_screw"
    assert result.candidate_step == "pick_components"


def test_large_false_fastener_box_is_rejected():
    engine = _engine(confirm_frames=1, cooldown_frames=0)
    items = [
        observation("hand", (10, 10, 50, 60)),
        observation("metal bar", (30, 25, 80, 50)),
        observation("screw", (0, 0, 90, 90)),
    ]

    result = engine.observe("insert_screw", items, frame_size=(100, 100))

    assert result.confirmed_step is None
    assert "small_fastener_missing" in result.reasons


def test_all_five_visual_rules_can_confirm_without_annotation_input():
    engine = _engine(confirm_frames=1, cooldown_frames=0)
    common = [
        observation("hand", (10, 10, 45, 55)),
        observation("hand", (50, 10, 90, 55)),
        observation("metal bar", (25, 28, 60, 42)),
        observation("metal bar", (55, 29, 88, 43)),
    ]

    pick = engine.observe("pick_components", common, frame_size=(100, 100))
    align = engine.observe("align_components", common, frame_size=(100, 100))
    screw = engine.observe(
        "insert_screw",
        common + [observation("screw", (42, 30, 47, 36))],
        frame_size=(100, 100),
    )
    tighten = engine.observe(
        "tighten_screw",
        common + [observation("screwdriver", (42, 4, 48, 38))],
        frame_size=(100, 100),
    )
    verify = engine.observe("verify_assembly", common, frame_size=(100, 100))

    assert [
        pick.confirmed_step,
        align.confirmed_step,
        screw.confirmed_step,
        tighten.confirmed_step,
        verify.confirmed_step,
    ] == [
        "pick_components",
        "align_components",
        "insert_screw",
        "tighten_screw",
        "verify_assembly",
    ]
