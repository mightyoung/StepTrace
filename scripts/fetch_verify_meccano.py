import argparse
import json
from display_project.data.meccano_registry import register_meccano_video

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--video-id", default="0009")
    p.add_argument("--source-split", default="UNKNOWN")
    p.add_argument("--official-source")
    p.add_argument("--expected-sha256")
    p.add_argument("--expected-width", type=int)
    p.add_argument("--expected-height", type=int)
    p.add_argument("--expected-fps", type=float)
    p.add_argument("--expected-frames", type=int)
    p.add_argument("--output")
    a = p.parse_args()
    result = register_meccano_video(
        a.path,
        video_id=a.video_id,
        source_split=a.source_split,
        official_source=a.official_source,
        expected_sha256=a.expected_sha256,
        expected_width=a.expected_width,
        expected_height=a.expected_height,
        expected_fps=a.expected_fps,
        expected_frames=a.expected_frames,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if a.output:
        open(a.output, "w", encoding="utf-8").write(text + "\n")
