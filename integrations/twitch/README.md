# Twitch station

Public statistics at **https://dev.pinode.dev/twitch/**. The main dashboard's
sidebar links here. All configured channels are visible, as requested. Twitch
authorization and settings require the existing dashboard admin login.

The worker runs independently of Flask as `rpi-dashboard-twitch.service`. It uses
[rdavydov's successor](https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2),
which the [original repository](https://github.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2)
recommends. The dependency revision is pinned in `requirements.txt`.

## What is shown

- Current balance and live/offline status for each configured channel.
- Balance changes since this installation first recorded each channel.
- A 1-hour / 24-hour / 7-day balance graph, retained for 30 days.
- Watch time reported by the miner, accumulated across worker restarts.
- Prediction title, chosen outcome, considered versus confirmed stake, result,
  wins/losses, and net prediction profit after subtracting the stake.
- Worker heartbeat, current settings, and pending confirmed bets.

The page polls every five seconds. Balance snapshots refresh approximately once
per minute; prediction observations are saved every five seconds. Aggregate
balances add independent channel currencies; points cannot be moved between channels.

The first balance is a baseline, not newly earned points. Balance increases can
include payouts, refunds, or other account activity between polls; they are not
an exact accounting of farming rewards. Existing Twitch balances predate tracking.
Predictions use the upstream worker's observed/confirmed events; an outcome being
considered does not imply that Twitch accepted a wager. A real bet/result cycle
can only be verified when a monitored channel actually runs a prediction.

The upstream miner selects up to **two streams simultaneously** according to its
streak/order priorities. All configured channels remain on the watchlist.

## Runtime files — private and ignored by Git

All state lives under `/home/blubb/ahripi-dev/data/twitch/`:

| File | Purpose |
| --- | --- |
| `config.json` | Account, channels, prediction limits |
| `credentials.json` | Private OAuth access/refresh tokens; never served by Flask |
| `auth-status.json` | Temporary activation code, only available through an admin API |
| `status.json` | Worker heartbeat; public API returns a strict subset of fields |
| `stats.db` | Separate SQLite database for balances, history and predictions |
| `logs/` | Upstream logs, INFO level, rotated daily with seven backups |

Directory permissions are private; config/token files are mode 0600. The original
sensor/comments database is not used by this integration. The public API reads
SQLite without write permissions. No public route starts a miner or changes settings.

The local `AHRIPI_HELPER.md` is also explicitly ignored by Git.

## Prediction defaults

- Automatic predictions enabled.
- Upstream SMART strategy, at most **1%** of the current channel balance.
- Hard configuration cap: **500 points per bet** by default.
- Require a balance of **1,000 points** before betting.
- Community-goal contributions, drops, and chat participation disabled.
- TLS certificate verification remains enabled.

Admins can change the settings on the page. The supported configuration ranges
are 1–10% and 10–10,000 points per bet. Changing settings restarts the worker;
saved statistics survive. The settings form requires the admin session and a
same-origin request header.

## Current installation on this Pi

Upstream source and its isolated virtual environment are at `/home/blubb/twitch-miner`.
This environment is separate from the hardware dashboard's virtual environments.
The service starts after network connectivity and restarts on exit. Its filesystem
write access is limited to the private Twitch runtime directory.

```bash
systemctl status rpi-dashboard-twitch.service --no-pager
journalctl -u rpi-dashboard-twitch.service -n 40 --no-pager
sudo systemctl restart rpi-dashboard-twitch.service
```

Flask changes require a separate dev-app restart:

```bash
sudo systemctl restart rpi-dashboard-app-dev.service
```

Do not start another worker manually while the service is running. Both would
act on the same Twitch account and write statistics concurrently.

## Reinstalling the integration

From the dev checkout, create the separate environment, install pinned dependencies,
and create the runtime directory before enabling the service:

```bash
python3 -m venv /home/blubb/twitch-miner/venv
/home/blubb/twitch-miner/venv/bin/python -m pip install -r integrations/twitch/requirements.txt
install -d -m 700 /home/blubb/ahripi-dev/data/twitch
sudo install -m 644 deploy/rpi-dashboard-twitch.service.example /etc/systemd/system/rpi-dashboard-twitch.service
sudo systemctl daemon-reload
```

The unit is stored with `.example` because this repository ignores installed
`*.service` files. Paths and user names in the unit target this Pi.

Before starting the worker, open the dev website's Twitch page, log in as admin,
and save a Twitch username and at least one channel. Then:

```bash
sudo systemctl enable --now rpi-dashboard-twitch.service
```

Open admin settings on the page and enter the displayed code at
**https://www.twitch.tv/activate**, signed in as the configured Twitch account.
You do not enter a Twitch password into PiNode or commit a token to Git. The
wrapper validates the returned account, refreshes tokens where supported, and
checks authorization hourly. Expired/declined authorization is retried; a fresh
activation code is visible only to an admin.

On a new checkout, publish the Flask routes by restarting the dev app after the
integration's Python modules and website files have been installed.

## APIs

| Route | Access |
| --- | --- |
| `GET /api/twitch` | Public status, totals, channels, latest prediction activity |
| `GET /api/twitch/history?channel=NAME&hours=24` | Public history; hours limited to 1, 24, or 168 |
| `GET /api/twitch/settings` | Admin |
| `POST /api/twitch/settings` | Admin, JSON and `X-Ahripi-Request: 1` |
| `GET /api/twitch/auth` | Admin; activation code, never access/refresh tokens |

Responses use `Cache-Control: no-store`. Cookies and runtime files have no static
website route. Keep raw logs private; they are not part of the public activity feed.

## Validation

Tests use temporary SQLite databases and configuration, never the live account:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

They cover auth boundaries, public-response secret filtering, settings validation,
baseline/gain tracking, prediction confirmation/results, persistence across database
reinitialization, and bounded history. Live point earning depends on Twitch, a
stream being live, and the upstream miner; the UI marks stale data rather than
inventing activity.

## Deployment scope

This feature is deployed to **dev**. Production's app and collectors are untouched.
Deploy production later through the normal GitHub merge/pull workflow; install the
worker only once on this Pi. If production should read the same worker, set its
`TWITCH_DATA_DIR` to the existing runtime directory rather than running a second miner.
