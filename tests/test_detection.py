import numpy as np

from display_project.detection.grounding import GroundedDetections, normalize_prompt


def test_normalize_prompt_rejects_empty_classes():
    try:
        normalize_prompt([" ", ""])
    except ValueError as exc:
        assert "class" in str(exc)
    else:
        raise AssertionError("empty prompt must be rejected")


def test_normalize_prompt_trims_and_deduplicates():
    assert normalize_prompt(["bolt", " screwdriver ", "bolt"]) == ["bolt", "screwdriver"]


def test_grounded_detections_rejects_mismatched_lengths():
    try:
        GroundedDetections(
            xyxy=np.zeros((2, 4)),
            confidence=np.ones(1),
            labels=["bolt", "nut"],
        )
    except ValueError as exc:
        assert "length" in str(exc)
    else:
        raise AssertionError("mismatched detections must be rejected")


def test_grounded_detections_accepts_empty_result():
    result = GroundedDetections.empty()
    assert result.xyxy.shape == (0, 4)
    assert result.confidence.shape == (0,)
    assert result.labels == []
