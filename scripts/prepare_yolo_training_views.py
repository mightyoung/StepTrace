from __future__ import annotations

import argparse
import json
from pathlib import Path

from display_project.data.yolo_training_views import build_training_view


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a class-filtered YOLO training view")
    parser.add_argument("--source", default="data/yolo/meccano-small-objects")
    parser.add_argument("--source-qa")
    parser.add_argument("--profile", required=True, choices=("hand", "fasteners"))
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--link-mode", choices=("hardlink", "symlink", "copy"), default="hardlink"
    )
    args = parser.parse_args()
    report = build_training_view(
        Path(args.source),
        Path(args.output),
        source_qa_path=args.source_qa,
        profile=args.profile,
        link_mode=args.link_mode,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
