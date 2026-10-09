from display_project.sop.task_config import load_task_config
from display_project.sop.visual_evidence import ObjectObservation, VisualEvidenceEngine


def _engine(confirm_frames=4, cooldown_frames=12):
    return VisualEvidenceEngine(
        task_config=load_task_config("configs/tasks/meccano.yaml"),
        confirm_frames=confirm_frames,
        cooldown_frames=cooldown_frames,
    )


def test_repeated_low_confidence_fastener_never_advances_state():
    engine = _engine(confirm_frames=2, cooldown_frames=0)
    items = [
        ObjectObservation("hand", 0.9, (20, 20, 60, 70)),
        ObjectObservation("metal bar", 0.8, (45, 35, 90, 60)),
        ObjectObservation("screw", 0.21, (50, 40, 56, 47)),
    ]

    first = engine.observe("insert_screw", items, frame_size=(100, 100))
    second = engine.observe("insert_screw", items, frame_size=(100, 100))

    assert first.candidate_step is None
    assert second.confirmed_step is None
    assert "small_fastener_missing" in second.reasons
