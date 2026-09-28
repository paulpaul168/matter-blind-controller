#!/usr/bin/env python3
"""Write jalousie contact-sensor states for HeizungLax (Python 3.12+).

Writes /home/opa/jalousie_state.json with top/bottom and zjal (1=auf, 2=zu, 0=laufend).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from matter_blind_controller import BlindSensors

STATE_FILE = Path("/home/opa/jalousie_state.json")
CONFIG = Path("/home/opa/matter-blind-controller/config.yaml")


def derive_zjal(top: str | None, bottom: str | None) -> int:
    if top == "CLOSED" and bottom != "CLOSED":
        return 1  # auf
    if bottom == "CLOSED" and top != "CLOSED":
        return 2  # zu
    return 0


async def main() -> None:
    sensors = BlindSensors(CONFIG)
    task = sensors.start_background()
    try:
        while True:
            st = sensors.states
            top = st.get("top")
            bottom = st.get("bottom")
            bats = sensors.batteries
            warns = sensors.battery_warnings
            payload = {
                "top": top,
                "bottom": bottom,
                "zjal": derive_zjal(top, bottom),
                "battery": bats,
                "battery_warn": any(warns.values()),
                "battery_warn_names": [n for n, w in warns.items() if w],
                "ts": time.time(),
            }
            STATE_FILE.write_text(json.dumps(payload), encoding="utf-8")
            await asyncio.sleep(0.5)
    finally:
        sensors.stop()
        await task


if __name__ == "__main__":
    asyncio.run(main())
