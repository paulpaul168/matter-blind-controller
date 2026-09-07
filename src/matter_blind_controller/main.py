"""CLI entrypoint for the Matter blind controller."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from matter_blind_controller.config import AppConfig, ConfigError, load_config
from matter_blind_controller.matter import MatterController
from matter_blind_controller.sensors import SensorRegistry

LOGGER = logging.getLogger("matter_blind_controller")


def main(argv: list[str] | None = None) -> None:
    """Parse args, configure logging, and run the async controller."""
    args = _parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    _configure_logging(config.logging.level)
    LOGGER.info("Starting matter-blind-controller (config=%s)", args.config)

    try:
        asyncio.run(_async_main(config))
    except KeyboardInterrupt:
        LOGGER.info("Interrupted")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="matter-blind-controller",
        description=(
            "Monitor Matter contact sensors for blind position via a local "
            "python-matter-server WebSocket API."
        ),
    )
    default_config = os.environ.get("MATTER_BLIND_CONFIG", "config.yaml")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(default_config),
        help="Path to YAML config (default: %(default)s or $MATTER_BLIND_CONFIG)",
    )
    return parser.parse_args(argv)


def _configure_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        print(f"Invalid logging level: {level}", file=sys.stderr)
        raise SystemExit(2)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric)


async def _async_main(config: AppConfig) -> None:
    registry = SensorRegistry(config.sensors)
    controller = MatterController(config, registry)
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, controller.request_stop)
        except NotImplementedError:
            # Signal handlers are not available on all platforms.
            pass

    await controller.run_forever()


if __name__ == "__main__":
    main()
