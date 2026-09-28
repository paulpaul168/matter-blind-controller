"""Embeddable API: run the Matter watcher and read sensor states as variables."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from matter_blind_controller.config import AppConfig, load_config
from matter_blind_controller.matter import MatterController
from matter_blind_controller.sensors import SensorRegistry

LOGGER = logging.getLogger(__name__)


class BlindSensors:
    """Background Matter client with readable OPEN/CLOSED state per sensor name.

    Example::

        sensors = BlindSensors("config.yaml")
        task = asyncio.create_task(sensors.run())
        await sensors.wait_ready()
        print(sensors.top)       # "OPEN" | "CLOSED" | None
        print(sensors.states)    # {"top": "OPEN", "bottom": "CLOSED"}
        sensors.stop()
        await task
    """

    def __init__(self, config: str | Path | AppConfig) -> None:
        if isinstance(config, AppConfig):
            self._config = config
        else:
            self._config = load_config(config)
        self._registry = SensorRegistry(self._config.sensors)
        self._controller = MatterController(self._config, self._registry)
        self._ready = asyncio.Event()
        self._run_task: asyncio.Task[None] | None = None

    @property
    def states(self) -> dict[str, str | None]:
        """Copy of ``{name: "OPEN"|"CLOSED"|None}``."""
        return self._registry.states

    @property
    def batteries(self) -> dict[str, int | None]:
        """Battery percent 0–100 per sensor name."""
        return self._registry.batteries

    @property
    def battery_warnings(self) -> dict[str, bool]:
        """Low-battery flags per sensor name."""
        return self._registry.battery_warnings

    def get(self, name: str) -> str | None:
        """Return current state for a configured sensor name."""
        return self._registry.get(name)

    def __getattr__(self, name: str) -> str | None:
        # Allow sensors.top / sensors.bottom once configured.
        try:
            return self._registry.get(name)
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __getitem__(self, name: str) -> str | None:
        return self._registry.get(name)

    async def run(self) -> None:
        """Connect and keep updating states until :meth:`stop` is called."""
        # Mark ready after a short delay once run_forever is progressing;
        # also set ready as soon as any state is known.
        poll = asyncio.create_task(self._watch_ready())
        try:
            await self._controller.run_forever()
        finally:
            poll.cancel()
            try:
                await poll
            except asyncio.CancelledError:
                pass

    async def _watch_ready(self) -> None:
        while not self._ready.is_set():
            if any(v is not None for v in self._registry.states.values()):
                self._ready.set()
                return
            await asyncio.sleep(0.2)

    async def wait_ready(self, timeout: float | None = 60.0) -> None:
        """Wait until at least one sensor has a known state."""
        if timeout is None:
            await self._ready.wait()
            return
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    def stop(self) -> None:
        """Request disconnect and stop the background loop."""
        self._controller.request_stop()

    def start_background(self) -> asyncio.Task[None]:
        """Create a task for :meth:`run` on the current event loop."""
        if self._run_task is not None and not self._run_task.done():
            return self._run_task
        self._run_task = asyncio.create_task(self.run(), name="blind-sensors")
        return self._run_task
