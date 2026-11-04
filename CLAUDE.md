# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Owner preferences and current orientation

Read [AHRIPI_HELPER.md](AHRIPI_HELPER.md) for the October 2026 server review,
corrected commands, and existing uncommitted work. The owner uses this project to
learn: do not independently finish the IR/LCD projects, charts, or other exercises.
Preserve existing edits and follow the current user request; explicitly requested
implementation is welcome. The helper's snapshot is dated, so verify live state
before acting on it.

## Twitch feature (updated 27 October 2026)

Twitch is deployed to dev and production. Read `integrations/twitch/README.md`
before changing it. There is one `rpi-dashboard-twitch.service` worker from dev;
both apps read `/home/blubb/ahripi-dev/data/twitch`, with a production systemd
environment override. Keep this runtime and the local helper ignored by Git.
The Twitch page stays public. `data/twitch/visibility.json` privately configures
visitor identity redactions; admins retain original details. Preserve this file,
keep actual redaction names out of public source, and do not gate the whole page.
Prediction timing now reacts immediately to active messages; empty pools retry
on updates. `integrations/twitch/prediction_timing.py` guards concurrency and the
real deadline. Keep `FROM_END`/zero in the runner: `FROM_START`/zero discards events.
The Twitch chart offers All time (`hours=all`). Keep its lifetime balance samples;
the former 30-day purge is removed. Chart responses remain bounded to 180 points
with exact endpoints, and the station JS URL must match its content hash.

`backend/twitch_experiments.py` is the active paper-only comparison: most voters,
most points and least points, each on the same snapshot with 10 simulated points.
The comparison has no real submitter or automatic promotion. The optional
`backend/twitch_betting.py` pilot is OFF by default and requires explicit live
switch/rule/channel settings. It hard-caps stakes at 10 and daily reservations at
100/channel, keeps reserve/percentage/0.5% caps, one pending attempt and a 3-loss
cooldown. On 27 October the owner explicitly approved and enabled Most voters
on mooda only: 10 points/attempt, 100/day, 1,000 reserve and 0.5% balance cap.
Preserve that selection; other channels remain paper-only.
`backend/twitch_learning.py` and
its spending gates are archived; keep its rows intact, and do not reactivate real
betting without a separate explicit user request. The saved caps stay inactive.
The main miner
badge belongs in Backend Status (`twitch-badge`), not the wide summary card.
Twitch Station reuses the dashboard's DaisyUI 4 cards, spacing and controls.
Comments and the integration README explain the model and its limitations.

---

## Two-environment setup

This repo runs in two live instances simultaneously on the same Pi:

| Environment | Folder | Port | Domain |
|---|---|---|---|
| Production | `/var/www/ahripi-prod` | 5000 | www.pinode.dev |
| Development | `/home/blubb/ahripi-dev` | 5001 | dev.pinode.dev |

**Always develop in `/home/blubb/ahripi-dev`**, test there, then push to GitHub and pull into prod.

Never manually copy files between the two folders.

---

## Workflow

```bash
# Start a new feature (from ahripi-dev)
git switch main && git pull
git switch -c feature/name

# Test on dev (services are always running, changes take effect on restart)
sudo systemctl restart rpi-dashboard-app-dev.service

# Push and merge via GitHub PR (feature -> main)
git add collectors/lcd.py
git commit -m "add lcd collector"
git push -u origin feature/name

# After merge, update prod
cd /var/www/ahripi-prod
git pull
sudo systemctl restart rpi-dashboard-app-prod.service
```

---

## Service management

All services are managed with systemd. Service files live in `/etc/systemd/system/`.

```bash
# See everything at a glance
systemctl list-units --type=service | grep rpi-dashboard

# Live logs
journalctl -u rpi-dashboard-app-dev.service -f
journalctl -u rpi-dashboard-sensors-dev.service -f

# Restart after code change
sudo systemctl restart rpi-dashboard-app-dev.service

# After editing a .service file
sudo systemctl daemon-reload
sudo systemctl restart <service-name>
```

**Running services:**
- `rpi-dashboard-app-prod` / `rpi-dashboard-app-dev` — Flask backends
- `rpi-dashboard-system/network/sensors/weather/fun` — prod collectors (→ port 5000)
- `rpi-dashboard-system-dev/network-dev/sensors-dev/weather-dev/fun-dev` — dev collectors (→ port 5001)
- `rpi-dashboard-camera` — camera collector at `/home/blubb/ahripi-cam/`, port 5055 (shared by both envs)

Each dev collector service file sets `EnvironmentFile=/home/blubb/ahripi-dev/.env` and runs from the dev venv.

---

## Architecture

### Data flow

```
Hardware sensors
      │
      ▼
collectors/*.py  ──POST /api/sensors──►  backend/app.py  ──GET /api/sensors──►  web/script.js
  (every 1-5s)                           (in-memory STATE                       (polls every 1s)
                                          + SQLite writes)
```

- Collectors run as independent systemd services. They loop forever, read from hardware or external APIs, and POST JSON to the Flask backend.
- The backend keeps a global `STATE` dict in memory — the frontend reads from that dict, not directly from the database.
- The database (`data/database.db`) is for history only, queried by `/api/history`.

### Collector pattern

Every collector follows the same structure:
1. Read `API_BASE_URL` from env (set in `.env`)
2. Loop forever: gather data → POST to `/api/<name>`
3. On POST failure: exponential backoff up to `MAX_BACKOFF=60s`
4. Graceful exit on `KeyboardInterrupt`

When adding a new collector, match this pattern and create a matching systemd service file for both dev and prod.

### Backend (`backend/app.py`)

- Flask on the port in `.env` (`PORT=5001` for dev, `5000` for prod)
- `STATE` dict is the source of truth for the dashboard — collectors POST updates into it, frontend reads it
- `HISTORY_MAP` maps sensor names to `(table, column)` pairs for the three data tiers
- `init_db()` runs at startup and creates all tables if missing
- Camera is proxied to the separate `ahripi-cam` service on port 5055 — the main Flask app never talks to the camera hardware directly

### Data retention tiers (`backend/downsample.py`)

| Tier | Table suffix | Kept | Granularity |
|---|---|---|---|
| Raw | `sensor_readings` etc. | 30 days | per-read (~1s) |
| Minutely | `_minutely` | 1 year | 1-minute averages |
| Hourly | `_hourly` | forever | 1-hour averages |

`downsample.py` is run by cron (daily) to roll old raw data into minutely and old minutely into hourly. The `/api/history` endpoint selects the right tier automatically based on the requested time range.

### Frontend (`web/`)

- No framework — plain HTML + vanilla JS + DaisyUI (CDN) + Tailwind (CDN)
- `script.js` polls all API endpoints every 1 second via `setInterval(update, 1000)`
- Comments poll every 10 seconds
- Admin state (`window.__isAdmin`) is stored in a Flask session cookie; `SESSION_COOKIE_SECURE=True` so it only works over HTTPS in prod
- The hidden `#website` field in the comment form is a honeypot for bots
- `web2/` is the camera page, served under `/site2/`

---

## Hardware

| Component | Interface | Notes |
|---|---|---|
| DHT11 | GPIO pin 17 | temp + humidity, read by `Freenove_DHT.py` |
| BMP180 | I2C | pressure + temp, full driver in `collectors/sensors.py` |
| Pi Camera | CSI | managed by `ahripi-cam` service (Picamera2) |
| GPIO Button | GPIO pin 18 | used in `collectors/Camera.py` |
| I2C LCD 1602 | I2C 0x27 or 0x3F | **stub only** — `collectors/lcd.py` is empty |
| IR receiver | GPIO | **stub only** — `collectors/ir.py` is empty |

### Freenove Kit

Hardware reference code and datasheets are at `/home/blubb/Freenove_Kit/`.
LCD example: `/home/blubb/Freenove_Kit/Code/Python_GPIOZero_Code/20.1.1_I2CLCD1602/`

**Known bug in Freenove's `LCD1602.py`:** line 86 reads `self.buswrite_byte(...)` — it should be `self.bus.write_byte(...)`. Fix this before using it.

Check LCD I2C address with: `i2cdetect -y 1`

---

## Environment variables (`.env`)

```
PORT=5001
API_BASE_URL=http://localhost:5001
SECRET_KEY=...
ADMIN_PASSWORD=...
AUTO_APPROVE_COMMENTS=1
```

Prod `.env` is at `/var/www/ahripi-prod/.env` (PORT=5000).

---

## Installing dependencies

```bash
# Dev
source /home/blubb/ahripi-dev/venv/bin/activate
pip install -r requirements.txt

# Prod
source /var/www/ahripi-prod/venv/bin/activate
pip install -r requirements.pip.txt
```

Note: prod uses `requirements.pip.txt`, dev uses `requirements.txt`.
