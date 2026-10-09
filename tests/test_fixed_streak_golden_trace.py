"""Golden replay for the frozen fixed-streak baseline.

This trace deliberately supplies geometry only: no action annotations, VLM
review, or ground-truth labels are passed to the visual-evidence engine.
"""

from display_project.sop.visual_evidence import ObjectObservation, VisualEvidenceEngine


def _observation(
    label: str, box: tuple[float, float, float, float]
) -> ObjectObservation:
    return ObjectObservation(label=label, confidence=0.9, xyxy=box)


def test_fixed_streak_golden_trace_preserves_four_frame_confirmation_and_cooldown():
    engine = VisualEvidenceEngine()

    assert (engine.confirm_frames, engine.cooldown_frames) == (4, 12)

    pick_evidence = [
        _observation("hand", (10, 10, 40, 45)),
        _observation("metal bar", (32, 20, 70, 40)),
    ]
    align_evidence = [
        _observation("hand", (10, 10, 40, 45)),
        _observation("hand", (60, 10, 90, 45)),
        _observation("metal bar", (32, 20, 55, 40)),
        _observation("partial assembly", (52, 20, 78, 40)),
    ]

    pick_trace = [
        engine.observe("pick_components", pick_evidence, frame_size=(100, 100))
        for _ in range(4)
    ]
    assert [result.confirmed_step for result in pick_trace] == [
        None,
        None,
        None,
        "pick_components",
    ]

    cooldown_trace = [
        engine.observe("align_components", align_evidence, frame_size=(100, 100))
        for _ in range(engine.cooldown_frames)
    ]
    assert all(result.candidate_step == "align_components" for result in cooldown_trace)
    assert all(result.confirmed_step is None for result in cooldown_trace)
    assert cooldown_trace[-1].streak == 1

    post_cooldown = [
        engine.observe("align_components", align_evidence, frame_size=(100, 100))
        for _ in range(engine.confirm_frames - 1)
    ]
    assert [result.confirmed_step for result in post_cooldown] == [
        None,
        None,
        "align_components",
    ]
