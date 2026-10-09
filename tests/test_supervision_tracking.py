from display_project.tracking.supervision_v2 import class_index


def test_class_index_accepts_combined_grounding_label():
    classes = ["hand", "screwdriver", "bolt", "metal bar"]
    assert class_index("screwdriver bolt", classes) == 1
    assert class_index("unknown", classes) == 0
