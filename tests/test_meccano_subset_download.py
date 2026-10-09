from scripts.download_meccano_frame_subset import _video_id_from_member


def test_archive_member_video_id_is_normalized():
    assert _video_id_from_member("frames/02_00421.jpg") == "02"
    assert _video_id_from_member("0009_00100.jpg") == "09"
    assert _video_id_from_member("README.txt") is None
