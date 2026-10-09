from display_project.sop.demo_mapping import DEMO_STEPS, action_to_step


def test_demo_steps_are_five_unique_ordered_steps():
    assert len(DEMO_STEPS) == 5
    assert len(set(DEMO_STEPS)) == 5


def test_action_mapping_follows_demo_timeline():
    assert action_to_step("take_gray_perforated_bar", 164) == "pick_components"
    assert action_to_step("align_objects", 200) == "align_components"
    assert action_to_step("plug_screw", 280) == "insert_screw"
    assert action_to_step("take_bolt", 395) == "insert_bolt"
    assert action_to_step("tighten_bolt_with_hands", 430) == "tighten_fastener"


def test_action_mapping_does_not_guess_outside_expected_window():
    assert action_to_step("take_bolt", 250) is None
    assert action_to_step(None, 430) is None
