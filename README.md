# matter-blind-controller

Minimal async Python app that monitors **IKEA Matter-over-Thread door/window sensors** used as position sensors for a blind (Jalousie). It connects to a local **python-matter-server**, discovers commissioned Matter devices, identifies contact sensors, and logs state changes such as:

```text
top: OPEN
bottom: CLOSED
```

This foundation does **not** control a blind motor yet.

## Architecture

```text
IKEA Thread sensors ──► SONOFF USB Thread dongle ──► python-matter-server
                                                         │
                                              WebSocket (ws://host:5580/ws)
                                                         │
                                              matter-blind-controller
                                                         │
                                              logs: top: OPEN / …
```

- The **Matter Server** owns the Thread radio, fabric, commissioning, and device subscriptions.
- This app is a **client only** (no Home Assistant dependency in application code).
- Contact sensors use Matter `BooleanState.StateValue`: `True` → `CLOSED`, `False` → `OPEN`.

> **Note:** `python-matter-server` is frozen at v8.1.2 and the project is migrating to [matterjs-server](https://github.com/matter-js/matterjs-server). The WebSocket client API used here remains the simplest path for a Raspberry Pi setup today.

## Requirements

- Raspberry Pi (or any Linux host) with Python **3.12+**
- SONOFF USB Thread / Zigbee-Thread dongle (or another supported 802.15.4 radio)
- A running **python-matter-server** instance with the four IKEA sensors already commissioned into its fabric

## Raspberry Pi setup

### 1. Matter Server (Docker)

On the Pi, plug in the SONOFF USB dongle, then run the official container with host networking (required for Matter/mDNS/Thread):

```bash
mkdir -p ~/matter-server/data
docker run -d \
  --name matter-server \
  --restart=unless-stopped \
  --security-opt apparmor=unconfined \
  -v ~/matter-server/data:/data \
  -v /run/dbus:/run/dbus:ro \
  --network=host \
  ghcr.io/matter-js/python-matter-server:stable
```

The WebSocket API listens on `ws://127.0.0.1:5580/ws` by default.

OS notes (Thread/Matter): prefer a standard Raspberry Pi OS / Linux network stack; container installs need host networking and correct IPv6/multicast support. See [python-matter-server OS requirements](https://github.com/matter-js/python-matter-server/blob/main/docs/os_requirements.md).

### 2. Commission the IKEA sensors

Commission the four door/window sensors into **this** Matter Server fabric (not only Apple/Google/HA unless you use multi-admin). Use the Matter Server’s commissioning API / UI / chip-tool workflow you already use for your fabric.

After commissioning, note each device’s **Matter node ID**.

### 3. Install this app

```bash
cd matter-blind-controller
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 4. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:

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

Replace `10`–`13` with your real node IDs. Optionally set `MATTER_BLIND_CONFIG` (see `.env.example`).

### 5. Run

```bash
matter-blind-controller --config config.yaml
# or
python -m matter_blind_controller --config config.yaml
```

On connect the app lists all Matter nodes and contact endpoints. Map those `node_id` values into `config.yaml`, restart, and you should see initial states plus live updates when magnets open/close.

Graceful shutdown: `Ctrl+C` / `SIGTERM`. Connection loss triggers exponential backoff reconnect.

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
- In-app commissioning UI
- Home Assistant integration code
