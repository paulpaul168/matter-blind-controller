# matter-blind-controller

Minimal async Python app that monitors **IKEA Matter-over-Thread door/window sensors** used as position sensors for a blind (Jalousie). It connects to a local **python-matter-server**, discovers commissioned Matter devices, identifies contact sensors, and logs state changes such as:

```text
top: OPEN
bottom: CLOSED
```

This foundation does **not** control a blind motor yet.

## Architecture

```text
IKEA Thread sensors ──► Thread network (SONOFF USB / OTBR)
                              │
                    python-matter-server  (Docker, auto-restart)
                              │
                   WebSocket ws://host:5580/ws
                              │
                    matter-blind-controller
                              │
                    logs: top: OPEN / …
```

- The **Matter Server** owns the Matter fabric, commissioning, and device subscriptions.
- This app is a **client only** (no Home Assistant dependency in application code).
- Contact sensors use Matter `BooleanState.StateValue`: `True` → `CLOSED`, `False` → `OPEN`.

> **Note:** `python-matter-server` is frozen at v8.1.2 and the project is migrating to [matterjs-server](https://github.com/matter-js/matterjs-server). The WebSocket client API used here remains the simplest path for a Raspberry Pi setup today.

## Requirements

- Raspberry Pi (or Linux host) with Python **3.12+**
- SONOFF USB Thread dongle (or another OpenThread RCP) plus a working **Thread Border Router** on the same LAN
- Bluetooth on the Pi (for first-time Thread commissioning over BLE)
- Docker with Compose

## 1. Matter Server (Docker, auto-restart)

Create a folder and `docker-compose.yml` on the Pi:

```bash
mkdir -p ~/matter-server/data
cd ~/matter-server
```

```yaml
# ~/matter-server/docker-compose.yml
services:
  matter-server:
    image: ghcr.io/matter-js/python-matter-server:stable
    container_name: matter-server
    restart: unless-stopped
    network_mode: host
    security_opt:
      - apparmor:unconfined
    volumes:
      - ./data:/data
      # Needed so the server can use host Bluetooth to commission Thread sensors
      - /run/dbus:/run/dbus:ro
    # Explicit command keeps BLE commissioning enabled after restarts
    command: >
      --storage-path /data
      --paa-root-cert-dir /data/credentials
      --bluetooth-adapter 0
```

Start it (and keep it running across reboots):

```bash
docker compose up -d
docker compose ps
docker compose logs -f matter-server
```

`restart: unless-stopped` means Docker restarts the container after crashes and after a Pi reboot, until you explicitly stop it (`docker compose stop`).

WebSocket API: `ws://127.0.0.1:5580/ws`

**Thread radio tip:** the Matter Server commissions devices onto a Thread network; it does not replace an OpenThread Border Router. Run OTBR with your SONOFF USB dongle (or use an existing border router on the LAN) and use that network’s **Active Operational Dataset (TLV)** below.

## 2. Bind / commission the IKEA sensors (easiest path)

Do this **once per sensor**, into **this** Matter Server fabric.

### Prepare each sensor

1. If the sensor is already linked to Dirigera / Apple / Google / another controller, either:
   - use that controller’s **share / multi-admin pairing code**, or
   - factory-reset the sensor so it shows a fresh Matter QR code (`MT:…`) / numeric setup code.
2. Keep the sensor awake (press the button if needed) and within a few meters of the Pi during BLE commissioning.
3. Commission **one sensor at a time**.

### Load Thread credentials, then commission

On the Pi (with this project’s venv installed — see step 3), set your Thread dataset TLV and the sensor pairing code:

```bash
source ~/matter-blind-controller/.venv/bin/activate   # or your venv path

export MATTER_URL="ws://127.0.0.1:5580/ws"
export THREAD_DATASET="0e08...."   # Active Operational Dataset TLV from your OTBR / Thread UI
export PAIRING_CODE="MT:Y.ABCDEFG123456789"   # QR payload, or 11-digit manual code
```

Run:

```bash
python - <<'PY'
import asyncio
import os

import aiohttp
from matter_server.client.client import MatterClient

URL = os.environ["MATTER_URL"]
DATASET = os.environ["THREAD_DATASET"]
CODE = os.environ["PAIRING_CODE"]

async def main() -> None:
    async with aiohttp.ClientSession() as session:
        async with MatterClient(URL, session) as client:
            await client.set_thread_operational_dataset(DATASET)
            print("Thread dataset set")
            node = await client.commission_with_code(CODE)
            print(f"Commissioned OK — node_id={node.node_id}")

asyncio.run(main())
PY
```

Repeat for each of the four sensors (new `PAIRING_CODE` each time). Write down the printed `node_id` values — you need them in `config.yaml`.

**Where to get `THREAD_DATASET`:** from your OpenThread Border Router / Thread integration UI as **Active dataset TLVs** (long hex string, often starting with `0e`).

**If BLE fails:** ensure `/run/dbus` is mounted, `--bluetooth-adapter 0` matches your adapter (`hciconfig` / `bluetoothctl list`), and nothing else has exclusive access to the dongle’s Bluetooth radio. For a device already on the Thread network via another controller’s multi-admin share, try the same script with:

```python
node = await client.commission_with_code(CODE, network_only=True)
```

### Verify nodes

Start this app (step 5). It logs every Matter node and which ones look like contact sensors. Match those `node_id`s to `top` / `bottom` / `left` / `right`.

## 3. Install this app

```bash
cd matter-blind-controller
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 4. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with the `node_id` values from commissioning:

```yaml
matter:
  url: ws://127.0.0.1:5580/ws
  reconnect_initial_seconds: 2
  reconnect_max_seconds: 60

logging:
  level: INFO

sensors:
  10: top
  11: bottom
  12: left
  13: right
```

Optional: set `MATTER_BLIND_CONFIG` (see `.env.example`).

## 5. Run

```bash
matter-blind-controller --config config.yaml
# or
python -m matter_blind_controller --config config.yaml
```

Expected log lines after mapping:

```text
top: OPEN
bottom: CLOSED
```

Graceful shutdown: `Ctrl+C` / `SIGTERM`. If the Matter Server is briefly down, this app reconnects with exponential backoff.

## Project layout

```text
src/matter_blind_controller/
  main.py      # CLI, logging, asyncio entry
  config.py    # YAML config
  matter.py    # MatterClient connect / discover / subscribe / reconnect
  sensors.py   # Contact sensor detection + OPEN/CLOSED mapping
config.example.yaml
pyproject.toml
```

## Out of scope (for now)

- Blind motor / Window Covering control
- Built-in commissioning UI inside this app
- Home Assistant integration code
