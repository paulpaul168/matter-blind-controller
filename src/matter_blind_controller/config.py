"""YAML configuration loading for the Matter blind controller."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class MatterConfig:
    """Connection and reconnect settings for the Matter Server."""

    url: str
    reconnect_initial_seconds: float = 2.0
    reconnect_max_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Logging settings."""

    level: str = "INFO"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Top-level application configuration."""

    matter: MatterConfig
    logging: LoggingConfig
    sensors: dict[int, str]


def load_config(path: Path | str) -> AppConfig:
    """Load and validate application config from a YAML file."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    return AppConfig(
        matter=_parse_matter(raw.get("matter")),
        logging=_parse_logging(raw.get("logging")),
        sensors=_parse_sensors(raw.get("sensors")),
    )


def _parse_matter(raw: Any) -> MatterConfig:
    if not isinstance(raw, dict):
        raise ConfigError("'matter' section is required and must be a mapping")

    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("'matter.url' must be a non-empty string")

    initial = raw.get("reconnect_initial_seconds", 2)
    maximum = raw.get("reconnect_max_seconds", 60)
    try:
        initial_f = float(initial)
        maximum_f = float(maximum)
    except (TypeError, ValueError) as exc:
        raise ConfigError("reconnect_*_seconds must be numbers") from exc

    if initial_f <= 0 or maximum_f <= 0:
        raise ConfigError("reconnect_*_seconds must be positive")
    if maximum_f < initial_f:
        raise ConfigError(
            "'matter.reconnect_max_seconds' must be >= reconnect_initial_seconds"
        )

    return MatterConfig(
        url=url.strip(),
        reconnect_initial_seconds=initial_f,
        reconnect_max_seconds=maximum_f,
    )


def _parse_logging(raw: Any) -> LoggingConfig:
    if raw is None:
        return LoggingConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'logging' must be a mapping when provided")

    level = raw.get("level", "INFO")
    if not isinstance(level, str) or not level.strip():
        raise ConfigError("'logging.level' must be a non-empty string")

    return LoggingConfig(level=level.strip().upper())


def _parse_sensors(raw: Any) -> dict[int, str]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("'sensors' must be a non-empty mapping of node_id to name")

    sensors: dict[int, str] = {}
    names_seen: set[str] = set()
    for key, value in raw.items():
        try:
            node_id = int(key)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid sensor node_id: {key!r}") from exc
        if node_id <= 0:
            raise ConfigError(f"Sensor node_id must be positive: {node_id}")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"Sensor name for node {node_id} must be a non-empty string")
        name = value.strip()
        if name in names_seen:
            raise ConfigError(f"Duplicate sensor name: {name}")
        if node_id in sensors:
            raise ConfigError(f"Duplicate sensor node_id: {node_id}")
        names_seen.add(name)
        sensors[node_id] = name

    return sensors
