from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2

from display_project.config import ConfigError, load_config
from display_project.render import EventError, load_events, render_baseline
from display_project.video import VideoError, crop_video, probe_video


def _doctor(_: argparse.Namespace) -> int:
    report = {
        "python": sys.version.split()[0],
        "opencv": cv2.__version__,
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _validate(args: argparse.Namespace) -> int:
    events = load_events(args.events)
    print(json.dumps({"valid": True, "events": len(events)}, ensure_ascii=False))
    return 0


def _baseline(args: argparse.Namespace) -> int:
    if args.config:
        config = load_config(args.config)
        video, events, output = config.video.input, config.events.input, config.video.output
        if config.video.start_seconds or config.video.duration_seconds is not None:
            clip = output.with_name(f"{output.stem}.source{output.suffix}")
            crop_video(
                video,
                clip,
                start_seconds=config.video.start_seconds,
                duration_seconds=config.video.duration_seconds,
            )
            video = clip
    else:
        if not (args.video and args.events and args.output):
            raise ConfigError("baseline requires --config or --video, --events and --output")
        video, events, output = Path(args.video), Path(args.events), Path(args.output)
    metadata = probe_video(video)
    result = render_baseline(video, events, output)
    print(
        json.dumps(
            {
                "output": str(result.output),
                "frames": result.frames_written,
                "events": result.events_rendered,
                "fps": metadata.fps,
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="display-project")
    subcommands = parser.add_subparsers(dest="command", required=True)
    doctor = subcommands.add_parser("doctor", help="report local video runtime capabilities")
    doctor.set_defaults(handler=_doctor)
    validate = subcommands.add_parser("validate-events", help="validate baseline event JSON")
    validate.add_argument("events")
    validate.set_defaults(handler=_validate)
    baseline = subcommands.add_parser("baseline", help="render a baseline annotated MP4")
    baseline.add_argument("--config")
    baseline.add_argument("--video")
    baseline.add_argument("--events")
    baseline.add_argument("--output")
    baseline.set_defaults(handler=_baseline)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (ConfigError, EventError, VideoError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
