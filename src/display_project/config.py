from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a demonstration configuration is invalid."""


@dataclass(frozen=True)
class VideoConfig:
    input: Path
    output: Path
    start_seconds: float = 0.0
    duration_seconds: float | None = None


@dataclass(frozen=True)
class EventsConfig:
    input: Path


@dataclass(frozen=True)
class AppConfig:
    video: VideoConfig
    events: EventsConfig


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _path(base: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty path")
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration does not exist: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    root = _mapping(raw, "configuration")
    video = _mapping(root.get("video"), "video")
    events = _mapping(root.get("events"), "events")
    base = config_path.parent
    start = float(video.get("start_seconds", 0.0))
    duration_raw = video.get("duration_seconds")
    duration = None if duration_raw is None else float(duration_raw)
    if start < 0 or (duration is not None and duration <= 0):
        raise ConfigError("start_seconds must be non-negative and duration_seconds positive")
    return AppConfig(
        video=VideoConfig(
            input=_path(base, video.get("input"), "video.input"),
            output=_path(base, video.get("output"), "video.output"),
            start_seconds=start,
            duration_seconds=duration,
        ),
        events=EventsConfig(input=_path(base, events.get("input"), "events.input")),
    )
