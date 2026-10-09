from pathlib import Path

from display_project.data.meccano import (
    join_action_verbs,
    load_coco_annotations,
    load_hand_annotations,
    load_temporal_annotations,
    parse_frame_number,
)


FIXTURES = Path(__file__).parent / "fixtures" / "data"


def test_frame_names_with_five_or_six_digit_padding_become_integers():
    assert parse_frame_number("00010.jpg") == 10
    assert parse_frame_number("000010.jpg") == 10
    assert parse_frame_number(10) == 10


def test_temporal_rows_are_deduplicated_and_joined_by_interval_not_ids():
    actions = load_temporal_annotations(FIXTURES / "actions.csv", kind="action")
    verbs = load_temporal_annotations(FIXTURES / "verbs.csv", kind="verb")

    assert len(actions) == 2
    assert actions[0]["start_frame"] == 10
    assert actions[1]["end_frame"] == 30
    assert [row["verb_name"] for row in join_action_verbs(actions, verbs)] == ["take", "fit"]


def test_coco_boxes_are_clipped_and_categories_are_preserved_by_name():
    normalized = load_coco_annotations(FIXTURES / "objects.json")

    assert normalized == [
        {
            "annotation_id": 1,
            "image_id": 10,
            "video_id": "01",
            "frame": 10,
            "category_name": "bolt",
            "bbox": [0.0, 70.0, 100.0, 10.0],
            "image_width": 100,
            "image_height": 80,
        }
    ]


def test_vgg_hand_fields_are_parsed_and_extra_classes_are_retained():
    rows = load_hand_annotations(FIXTURES / "hands.csv", image_size=(50, 40))

    assert rows[0]["frame"] == 10
    assert rows[0]["category_name"] == "SX_Hand"
    assert rows[0]["bbox"] == [0.0, 5.0, 18.0, 30.0]
    assert rows[0]["is_hand"] is True
    assert rows[1]["category_name"] == "libretto_istruzioni"
    assert rows[1]["is_hand"] is False
    assert rows[1]["is_extra_class"] is True
    assert len(rows) == 2
