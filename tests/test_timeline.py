from display_project.data.timeline import clip_interval, frame_to_seconds


def test_frame_to_seconds_uses_zero_based_frame_numbers():
    assert frame_to_seconds(75, fps=25.0) == 3.0


def test_clip_interval_clamps_and_rebases_to_clip_start():
    assert clip_interval(90, 130, clip_start=100, clip_end=120) == {
        "start_frame": 0,
        "end_frame": 20,
    }
    assert clip_interval(10, 20, clip_start=100, clip_end=120) is None
