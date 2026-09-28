# matter-blind-controller

Minimal async Python app that monitors **IKEA Matter-over-Thread door/window sensors** used as position sensors for a blind (Jalousie). It connects to a local **python-matter-server**, discovers commissioned Matter devices, identifies contact sensors, and logs state changes such as:

```text
top: OPEN
bottom: CLOSED
```

This foundation does **not** control a blind motor yet.

## Architecture

```text
IKEA Thread sensors
        │
        ▼
SONOFF USB (OpenThread RCP) ──► OpenThread Border Router (OTBR)
                                        │
                              python-matter-server  (Docker, auto-restart)
                                        │
                             WebSocket ws://host:5580/ws
                                        │
                              matter-blind-controller
                                        │
                              logs: top: OPEN / …
```

- **OTBR** bridges Thread ↔ your LAN (IPv6).
- **Matter Server** owns the Matter fabric, commissioning, and subscriptions.
- This app is a **client only** (no Home Assistant dependency in application code).
- Contact sensors use Matter `BooleanState.StateValue`: `True` → `CLOSED`, `False` → `OPEN`.

> **Note:** `python-matter-server` is frozen at v8.1.2 and the project is migrating to [matterjs-server](https://github.com/matter-js/matterjs-server). The WebSocket client API used here remains the simplest path for a Raspberry Pi setup today.

## Requirements

- Raspberry Pi (or Linux host) with Python **3.12+**, Docker + Compose
- Compatible USB radio flashed as **OpenThread RCP** (see below)
- Bluetooth on the Pi (for first-time Thread commissioning over BLE)
- IPv6 enabled on the LAN (no aggressive multicast filtering)

### Compatible SONOFF sticks

| Stick | Chip | Thread as RCP? |
| --- | --- | --- |
| SONOFF ZBDongle-E | EFR32MG21 | Yes (flash OpenThread RCP) |
| SONOFF Dongle Plus MG24 / PMG24 | EFR32MG24 | Yes |
| SONOFF Dongle Lite MG21 | EFR32MG21 | Yes |
| SONOFF ZBDongle-P | CC2652P | **No** (Zigbee only) |

## 1. Set up a Thread Border Router

You need both pieces: **RCP firmware on the USB stick** + **OTBR software** on the Pi.

### 1.1 Flash OpenThread RCP firmware

1. Plug the SONOFF dongle into a PC (or the Pi).
2. Open the [SONOFF Dongle Flasher](https://dongle.sonoff.tech/) (browser) or use their Docker flasher.
3. Flash **OpenThread RCP** firmware (not Zigbee, not MultiPAN/CPC).
4. Unplug/replug the stick, then confirm the flasher still reports OpenThread RCP.

Typical serial settings for SONOFF RCP sticks:

- Baudrate: **460800**
- Hardware flow control: **off**

### 1.2 Find the serial device on the Pi

```bash
ls -l /dev/serial/by-id/
```

Prefer the stable `by-id` path, for example:

```text
/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_...-if00-port0
```

Also note your LAN interface name (Ethernet or Wi‑Fi):

```bash
ip -br link
# e.g. eth0 or wlan0
```

### 1.3 Enable host IPv6 forwarding (once)

OTBR needs the host to forward IPv6 / process Router Advertisements. Official helper (adjust interface):

```bash
curl -sSL https://raw.githubusercontent.com/openthread/ot-br-posix/refs/heads/main/etc/docker/border-router/setup-host \
  | INFRA_IF_NAME=eth0 bash
```

Replace `eth0` with your real backbone interface (`wlan0`, etc.).

### 1.4 Run OTBR + Matter Server (Docker, auto-restart)

```bash
mkdir -p ~/thread-matter/{otbr-data,matter-data}
cd ~/thread-matter
```

Create `docker-compose.yml`:

```yaml
# ~/thread-matter/docker-compose.yml
services:
  otbr:
    image: openthread/border-router:latest
    container_name: otbr
    restart: unless-stopped
    network_mode: host
    privileged: true
    cap_add:
      - NET_ADMIN
    environment:
      # Adjust serial path + backbone interface for your Pi
      OT_RCP_DEVICE: "spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800&uart-flow-control=0"
      OT_INFRA_IF: "eth0"
      OT_THREAD_IF: "wpan0"
      OT_LOG_LEVEL: "5"
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
      - /dev/net/tun:/dev/net/tun
    volumes:
      - ./otbr-data:/data

  matter-server:
    image: ghcr.io/matter-js/python-matter-server:stable
    container_name: matter-server
    restart: unless-stopped
    network_mode: host
    security_opt:
      - apparmor:unconfined
    volumes:
      - ./matter-data:/data
      - /run/dbus:/run/dbus:ro
    command: >
      --storage-path /data
      --paa-root-cert-dir /data/credentials
      --bluetooth-adapter 0
    depends_on:
      - otbr
```

**Before starting:** replace `/dev/ttyUSB0` with your real device (ideally the `/dev/serial/by-id/...` path in both `OT_RCP_DEVICE` and `devices:`), and set `OT_INFRA_IF` to `eth0` / `wlan0`.

Start both services:

```bash
docker compose up -d
docker compose ps
docker compose logs -f otbr
```

`restart: unless-stopped` keeps OTBR and Matter Server running after crashes and Pi reboots until you explicitly stop them.

Healthy OTBR logs mention the RCP / Spinel radio coming up (no endless `Wait for response timeout`). If you only see timeouts: wrong firmware, wrong baudrate, or flow control still on — reflash RCP and double-check the URL.

Matter WebSocket API: `ws://127.0.0.1:5580/ws`

### 1.5 Form a Thread network and copy the dataset TLV

Create a new Thread network on the border router:

```bash
docker exec -it otbr ot-ctl dataset init new
docker exec -it otbr ot-ctl dataset commit active
docker exec -it otbr ot-ctl ifconfig up
docker exec -it otbr ot-ctl thread start
docker exec -it otbr ot-ctl state
# expect: leader  (or router)
```

Export the **Active Operational Dataset** as a hex TLV (this is what Matter Server needs):

```bash
docker exec -it otbr ot-ctl dataset active -x
```

Copy the long hex string (often starts with `0e…`). That is your `THREAD_DATASET` for commissioning.

Optional: inspect human-readable settings with `docker exec -it otbr ot-ctl dataset active`.

## 2. Bind / commission the IKEA sensors

Do this **once per sensor**, into **this** Matter Server fabric.

### Prepare each sensor

1. If the sensor is already linked to Dirigera / Apple / Google / another controller, either:
   - use that controller’s **share / multi-admin pairing code**, or
   - factory-reset the sensor so it shows a fresh Matter QR code (`MT:…`) / numeric setup code.
2. Keep the sensor awake (press the button if needed) and within a few meters of the Pi during BLE commissioning.
3. Commission **one sensor at a time**.

### Load Thread credentials, then commission

On the Pi (with this project’s venv installed — see step 3):

```bash
source ~/matter-blind-controller/.venv/bin/activate   # or your venv path

export MATTER_URL="ws://127.0.0.1:5580/ws"
export THREAD_DATASET="0e08...."   # paste output of: ot-ctl dataset active -x
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

**If BLE fails:** ensure `/run/dbus` is mounted, `--bluetooth-adapter 0` matches your adapter (`bluetoothctl list`), and nothing else owns the Bluetooth radio. For a device already on Thread via multi-admin share, try:

```python
node = await client.commission_with_code(CODE, network_only=True)
```

### Verify nodes

Start this app (step 5). It logs every Matter node and which ones look like contact sensors. Map those `node_id`s to `top` / `bottom` / `left` / `right`.

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

## Troubleshooting (Thread / OTBR)

| Symptom | Likely fix |
| --- | --- |
| OTBR `Wait for response timeout` | Stick not on OpenThread RCP firmware; baud ≠ 460800; flow control still on |
| Commissioning fails immediately | `THREAD_DATASET` missing/wrong; run `ot-ctl dataset active -x` again |
| Device commissions but goes unavailable | Host IPv6 / RA setup; wrong `OT_INFRA_IF`; multicast filtering on the LAN |
| BLE commission never finds sensor | dbus mount, bluetooth adapter id, keep sensor awake next to the Pi |

More OS/network background: [python-matter-server OS requirements](https://github.com/matter-js/python-matter-server/blob/main/docs/os_requirements.md) and [OpenThread OTBR Docker](https://openthread.io/guides/border-router/docker/run).

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
