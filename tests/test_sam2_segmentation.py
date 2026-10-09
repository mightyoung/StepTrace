import numpy as np
import pytest

from display_project.segmentation.sam2 import SegmentationResult, normalize_boxes


def test_segmentation_result_empty_preserves_frame_shape():
    result = SegmentationResult.empty(height=1080, width=1920)

    assert result.masks.shape == (0, 1080, 1920)
    assert result.masks.dtype == np.bool_
    assert result.scores.shape == (0,)


def test_segmentation_result_rejects_mismatched_scores():
    with pytest.raises(ValueError, match="matching length"):
        SegmentationResult(
            masks=np.zeros((2, 20, 30), dtype=bool),
            scores=np.ones(1, dtype=np.float32),
        )


def test_normalize_boxes_clips_to_image_bounds():
    boxes = normalize_boxes(
        np.asarray([[-10, 5, 120, 80], [30, 40, 60, 90]], dtype=np.float32),
        width=100,
        height=70,
    )

    np.testing.assert_array_equal(
        boxes,
        np.asarray([[0, 5, 99, 69], [30, 40, 60, 69]], dtype=np.float32),
    )


def test_normalize_boxes_rejects_degenerate_box_after_clipping():
    with pytest.raises(ValueError, match="positive area"):
        normalize_boxes(
            np.asarray([[150, 10, 200, 20]], dtype=np.float32),
            width=100,
            height=70,
        )
