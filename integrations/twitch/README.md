# Twitch station

Public statistics at **https://www.pinode.dev/twitch/** and
**https://dev.pinode.dev/twitch/**. The main dashboard's two-column card and
sidebar link here. All configured channels are visible, as requested. Twitch
authorization and settings require the existing dashboard admin login.

The worker runs independently of Flask as `rpi-dashboard-twitch.service`. It uses
[rdavydov's successor](https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2),
which the [original repository](https://github.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2)
recommends. The dependency revision is pinned in `requirements.txt`.

## What is shown

### Dashboard styling and health placement

`web/twitch/index.html` uses the same DaisyUI 4 and Tailwind CDN setup as
`web/index.html`: `card bg-base-200 shadow-xl`, `card-body`, `card-title`,
`btn-primary`, `badge`, and the same settings drawer and light/dark control.
At desktop width the layout matches the dashboard's 24rem columns and 1rem gaps;
the detailed page collapses to two/one columns on smaller screens. Its CSS only
adds layout, chart geometry and colors from DaisyUI's theme tokens. There is no
separate palette, brand header, oversized typography or external font system.

The main page's miner badge is **only in Backend Status**, identified by
`#twitch-badge`. `web/twitch-summary.js` updates that badge every ten seconds and
the compact numbers in the Twitch card. Fresh + `running` means live; delayed,
login-needed, stale and failed requests cannot leave the badge green.

The detailed page also has a Backend Status card, plus the Channel Learning table.
`web/twitch/script.js` renders all data as text, styles statuses using DaisyUI
badges and draws its SVG chart with CSS theme tokens. Form/input IDs are the JS
contract; keep them in sync when editing the HTML.

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

## Learning and prediction defaults

Automatic prediction observation is enabled, but **new channels spend zero points
until they pass the paper-trial gates**. The old unconditional SMART strategy has
been replaced by `backend/twitch_learning.py`; upstream only submits a selected
outcome after that module authorizes a bounded real stake.

- Record both outcomes and pool/voter counts 15 seconds after opening. Entry
  snapshots are immutable and keyed by account + event ID.
- Resolve trials only from an explicit Twitch winner or cancellation. A cancelled
  or unresolved event never becomes a training win. Missing outcome data stays
  unresolved; it cannot promote the model to real betting.
- Learn separately for each account/channel/game/normalized question/outcome
  label/crowd-share bucket. Numbers in questions are preserved: 10 kills and 30
  kills are different propositions. No training is imported from other channels.
- Consider at most the last 100 resolved trials in the last 90 days. Need at least
  **20 channel trials** and **12 matching outcome observations** before real stakes.
- Use the Wilson lower bound with z=1.96, rather than raw win rate, as the
  conservative win estimate. Require an estimated edge of at least **8%** after
  adding our proposed stake to the pool, plus positive recent simulated P/L.
- Initially allow **10-point** stakes. At 50 matching observations and 15% estimated
  edge, allow up to 25; at 100 matches and 25% edge, up to 50. Recheck value after
  stake dilution. Every gate must still pass on every prediction.
- Enforce the user's percentage/stake limits AND a **0.5% balance cap**, a hard
  **50-point ceiling**, and the configured **1,000-point remaining reserve**.
- Require at least 20 participants for a real stake. Currently only two-outcome
  predictions are supported; missing/unsupported data means no wager.
- Reserve at most **100 points of submitted stakes per channel per Berlin day**.
  Reservations count even for rejected/unconfirmed requests; this is not a net
  loss budget. Allow at most one unresolved live reservation per channel.
- After three consecutive confirmed losses, use paper trials for a **24-hour
  cooldown**. Failures never fall back to an unrestricted upstream strategy.
- Use a 10-point hypothetical stake to estimate paper P/L from final pools.
  Missing final pools leave the paper return unknown. Simulated money never
  increments confirmed bets, real P/L, balances or actual win-rate counters.

This is transparent online calibration, not an AI that understands the stream or
a tested guarantee of profit. Sparse matching data can keep a channel in paper
mode indefinitely. The Wilson calculation assumes a binomial sample; changing
audiences, related questions, pool movement after entry, and selection effects
mean its bound is not a guarantee of future performance. See the
[NIST confidence-interval reference](https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
The UI calls it a “lower estimate” and shows trial counts and skip reasons.

### Worker settings

- Consider an active prediction 15 seconds after it opens, rather than waiting
  for the final seconds when a streamer may already have locked it.
- Community-goal contributions, drops, and chat participation disabled.
- TLS certificate verification remains enabled.

Admins can lower the stake limits on the page. The existing config schema accepts
1–10% and 10–10,000 points per bet for compatibility, but **cannot bypass the
learner's stricter ceilings or paper gates**. The default stake cap is now 50.
Turning predictions off also stops new trials; mining continues.
Changing settings restarts the worker;
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
inventing activity. `tests/test_twitch_learning.py` adds chronological simulated
streams covering zero-spend warm-up, channel/context isolation, immutable labels,
future/stale-result exclusion, pool pricing, reserves/budgets, loss cooldowns,
restart idempotency, and the adapter that blocks submission during paper mode.

Browser verification covers the main badge location, matching computed card
styles, dark/light charts, small-screen layout and real admin login/logout.

## Code map and model persistence

- `backend/twitch_store.py`: additive SQLite schema, public snapshot and histories.
- `backend/twitch_learning.py`: model, policy, atomic stake reservations, result
  settlement and public explanation. No network calls or vendor imports.
- `integrations/twitch/runner.py`: the fail-closed adapter replaces
  `Twitch.make_predictions`; a per-event settings copy selects the learned outcome.
  The channel-prediction websocket observes results even for paper trials, while
  user-prediction messages confirm and settle real bets.
- `learning_trials` in the existing private `stats.db`: pre-result pool snapshot,
  selected outcome, context, paper/live reason, reserved stake and explicit result.
  Its primary key prevents duplicate evaluation/spending after restart. Account
  and channel indexes preserve isolation. No existing history tables are removed.

If the worker misses a result while offline and Twitch does not replay it, that
trial stays unresolved rather than inventing a label. An unresolved real
reservation blocks another real bet on that channel. Inspect the trial/history
before changing state; never delete runtime data to “improve” model confidence.

## Deployment scope

This feature is deployed to **dev and production** through GitHub main/pulls.
There is **one worker**, running the code from the dev checkout. Production reads
the same runtime directory using
`/etc/systemd/system/rpi-dashboard-app-prod.service.d/twitch.conf`:

```ini
[Service]
Environment=TWITCH_DATA_DIR=/home/blubb/ahripi-dev/data/twitch
```

For learning/store changes, restart the worker and both Flask apps after pulling
the corresponding code. Plain HTML/CSS/JS updates only need a browser refresh.
No hardware collectors need restarting. The Pi's `origin` uses its existing SSH
deploy key for pushes; fetches still use HTTPS. Keep the helper, credentials and
runtime files Git-ignored, and preserve unrelated learning work when staging.
