import pytest

from display_project.detection.grounding import result_text_labels


def test_zero_detections_ignore_transformers_empty_label_sentinel():
    assert result_text_labels({"text_labels": [""]}, detection_count=0) == []


def test_nonempty_detections_require_one_label_per_box():
    with pytest.raises(ValueError, match="matching length"):
        result_text_labels({"text_labels": ["hand"]}, detection_count=2)


def test_text_labels_are_preferred_over_integer_labels():
    result = {"text_labels": ["hand", "bolt"], "labels": [0, 3]}

    assert result_text_labels(result, detection_count=2) == ["hand", "bolt"]
