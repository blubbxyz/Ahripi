#!/usr/bin/env python3
"""Run the upstream miner separately from Flask; publish persistent statistics."""

import copy
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import twitch_store as store
from auth import credentials

log = logging.getLogger("ahripi.twitch")
STOP = threading.Event()
STATE_LOCK = threading.Lock()
STATE = {"state": "starting", "message": "Starting Twitch miner.", "started_at": time.time()}


def progress(state, message):
    with STATE_LOCK:
        STATE.update(state=state, message=message)


def heartbeat(initial_config):
    while not STOP.is_set():
        with STATE_LOCK:
            store.write_json("status.json", {**STATE, "updated_at": time.time()})
        if store.config() != initial_config:
            progress("restarting", "Applying updated settings.")
            os.kill(os.getpid(), signal.SIGTERM)
            return
        STOP.wait(5)


def prediction_snapshot(event):
    decision = event.bet.decision or {}
    choice = None
    if decision.get("choice") is not None:
        try:
            choice = event.bet.get_decision().get("title")
        except (KeyError, IndexError, TypeError):
            choice = str(decision["choice"])
    result = event.result.get("type")
    return {"id": str(event.event_id), "channel": event.streamer.username,
            "title": event.title, "status": event.status, "choice": choice,
            "stake": int(decision.get("amount", 0)), "confirmed": bool(event.bet_confirmed),
            "result": result, "profit": int(event.result.get("gained", 0)) if result else None}


def monitor(miner, settings):
    from TwitchChannelPointsMiner.constants import GQLOperations
    previous_minutes, previous_predictions = {}, {}
    next_poll, next_validation = 0, time.monotonic() + 3600
    while not STOP.wait(5):
        try:
            # A decision being calculated is not a confirmed wager. Keep them distinct.
            for event in list(miner.events_predictions.values()):
                snapshot = prediction_snapshot(event)
                if previous_predictions.get(snapshot["id"]) != snapshot:
                    store.record_prediction(settings["username"], snapshot)
                    previous_predictions[snapshot["id"]] = snapshot
            if time.monotonic() >= next_validation:
                progress("validating", "Checking Twitch authorization.")
                saved = credentials(settings["username"], progress, STOP)
                apply_credentials(miner.twitch, saved)
                next_validation = time.monotonic() + 3600
            if not miner.original_streamers or time.monotonic() < next_poll:
                continue
            next_poll = time.monotonic() + 60
            updated = 0
            for streamer in list(miner.streamers):
                if STOP.is_set():
                    return
                query = copy.deepcopy(GQLOperations.ChannelPointsContext)
                query["variables"] = {"channelLogin": streamer.username}
                response = miner.twitch.post_gql_request(query)
                try:
                    points = response["data"]["community"]["channel"]["self"]["communityPoints"]
                    balance = points["balance"]
                    if type(balance) is not int or balance < 0:
                        continue
                except (KeyError, TypeError):
                    continue
                # Polling also keeps health/bonus checks current if upstream push events lag.
                miner.twitch.check_streamer_online(streamer)
                if points.get("availableClaim"):
                    miner.twitch.claim_bonus(streamer, points["availableClaim"]["id"])
                minutes = float(streamer.stream.minute_watched)
                last_minutes = previous_minutes.get(streamer.username, 0)
                elapsed = minutes - last_minutes if minutes >= last_minutes else minutes
                previous_minutes[streamer.username] = minutes
                game = streamer.stream.game or {}
                store.record_channel(settings["username"], streamer.username, balance=balance,
                                     online=streamer.is_online, title=streamer.stream.title,
                                     game=game.get("displayName") or game.get("name"),
                                     watched_minutes=max(0, elapsed))
                updated += 1
            watcher = miner.minute_watcher_thread
            if updated and watcher and watcher.is_alive():
                online = sum(bool(s.is_online) for s in miner.streamers)
                progress("running", f"Monitoring {len(miner.streamers)} channels; {online} live.")
            else:
                progress("degraded", "Miner is running; some Twitch updates are unavailable.")
        except (requests.RequestException, ValueError, OSError, KeyError, TypeError):
            # Do not expose HTTP bodies/headers or tokens in public diagnostics.
            log.warning("Statistics update failed; retrying.")
            progress("degraded", "Waiting for fresh Twitch updates; retrying automatically.")


def apply_credentials(twitch, saved):
    login = twitch.twitch_login
    login.set_token(saved["access_token"])
    login.user_id = saved["user_id"]
    login.cookies = [{"name": "auth-token", "value": saved["access_token"]},
                     {"name": "persistent", "value": saved["user_id"]}]
    login.login_check_result = True


def main():
    os.umask(0o077)
    store.init_database()
    settings = store.validate_config(store.config())
    os.chdir(store.data_dir())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Upstream changes the root logger to DEBUG. Keep existing handlers at INFO
    # so websocket authentication frames and large API bodies never reach stdout.
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.INFO)

    def stop(signum, frame):
        STOP.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=heartbeat, args=(settings,), daemon=True, name="Twitch heartbeat").start()

    miner = None
    try:
        saved = credentials(settings["username"], progress, STOP)
        progress("starting", "Connected to Twitch; loading channels.")
        from TwitchChannelPointsMiner import TwitchChannelPointsMiner
        from TwitchChannelPointsMiner.classes.Chat import ChatPresence
        from TwitchChannelPointsMiner.classes.Settings import Priority
        from TwitchChannelPointsMiner.classes.Twitch import Twitch
        from TwitchChannelPointsMiner.classes.WebSocketsPool import WebSocketsPool
        from TwitchChannelPointsMiner.classes.entities.Bet import BetSettings, DelayMode, Strategy
        from TwitchChannelPointsMiner.classes.entities.Streamer import StreamerSettings
        from TwitchChannelPointsMiner.logger import LoggerSettings

        # Keep upstream network calls bounded. This process is isolated from Flask.
        original_request = requests.sessions.Session.request

        def bounded_request(self, *args, **kwargs):
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = (10, 30)
            return original_request(self, *args, **kwargs)

        requests.sessions.Session.request = bounded_request

        # The wrapper manages validated/refreshable OAuth credentials; upstream
        # otherwise loads a stale pickle without checking it on restart.
        def login(twitch):
            apply_credentials(twitch, saved)

        Twitch.login = login

        original_message = WebSocketsPool.on_message

        def observe_message(websocket, message):
            original_message(websocket, message)
            try:
                payload = json.loads(message)
                if payload.get("type") != "MESSAGE":
                    return
                envelope = payload["data"]
                if not envelope.get("topic", "").startswith("predictions-user-v1."):
                    return
                event_message = json.loads(envelope["message"])
                prediction = event_message["data"]["prediction"]
                event_id = prediction["event_id"]
                event = websocket.events_predictions.get(event_id)
                if event is not None:
                    # Persist confirmation/results immediately, not just on the
                    # five-second timer, so a restart doesn't lose a confirmed bet.
                    store.record_prediction(settings["username"], prediction_snapshot(event))
                if event_message["type"] == "prediction-result":
                    store.resolve_prediction(settings["username"], event_id, prediction["result"])
            except (ValueError, KeyError, TypeError, OSError):
                log.warning("Could not record a prediction update.")

        WebSocketsPool.on_message = staticmethod(observe_message)
        miner = TwitchChannelPointsMiner(
            username=settings["username"], enable_analytics=False,
            claim_drops_startup=False, priority=[Priority.STREAK, Priority.ORDER],
            logger_settings=LoggerSettings(save=True, console_level=logging.INFO,
                file_level=logging.INFO, emoji=False, colored=False, auto_clear=True,
                time_zone="Europe/Berlin"),
            streamer_settings=StreamerSettings(
                make_predictions=settings["predictions"], follow_raid=True,
                claim_drops=False, claim_moments=False, watch_streak=True,
                community_goals=False, chat=ChatPresence.NEVER,
                bet=BetSettings(strategy=Strategy.SMART, percentage=settings["bet_percentage"],
                    max_points=settings["bet_max_points"], minimum_points=settings["bet_min_balance"],
                    # Streamers can lock predictions early; don't wait until the
                    # final seconds of an advertised window to submit a wager.
                    stealth_mode=False, delay_mode=DelayMode.FROM_START, delay=15)))
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.INFO)
        with STATE_LOCK:
            STATE["session_id"] = miner.session_id

        def graceful_stop(signum, frame):
            STOP.set()
            progress("stopping", "Stopping Twitch miner.")
            miner.end(signum, frame)
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, graceful_stop)
        signal.signal(signal.SIGINT, graceful_stop)
        threading.Thread(target=monitor, args=(miner, settings), daemon=True,
                         name="Ahripi Twitch statistics").start()
        miner.mine(settings["channels"], followers=False)
    except KeyboardInterrupt:
        pass
    except (requests.RequestException, ValueError):
        progress("error", "Twitch connection or authorization failed; retrying automatically.")
        log.error("Twitch connection or authorization failed.")
        return 1
    except Exception:
        progress("error", "Miner stopped unexpectedly; restarting automatically.")
        log.exception("Miner stopped unexpectedly")
        return 1
    finally:
        STOP.set()
        with STATE_LOCK:
            store.write_json("status.json", {**STATE, "updated_at": time.time()})
        if miner and miner.running:
            miner.end(0, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
