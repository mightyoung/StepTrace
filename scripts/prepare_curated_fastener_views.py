from __future__ import annotations

import argparse
import json
from pathlib import Path

from display_project.data.curated_yolo_training_views import (
    build_curated_fastener_view,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a positive-only curated MECCANO fastener view"
    )
    parser.add_argument("--source", default="data/yolo/meccano-fasteners-yolo11m")
    parser.add_argument("--source-qa")
    parser.add_argument("--mode", required=True, choices=("multiclass", "binary"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--train-min-frame-gap", type=int, default=3)
    parser.add_argument(
        "--link-mode", choices=("hardlink", "symlink", "copy"), default="hardlink"
    )
    args = parser.parse_args()
    report = build_curated_fastener_view(
        Path(args.source),
        Path(args.output),
        source_qa_path=args.source_qa,
        mode=args.mode,
        link_mode=args.link_mode,
        train_min_frame_gap=args.train_min_frame_gap,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
