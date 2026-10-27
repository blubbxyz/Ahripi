"""Explicitly enabled fixed-rule pilot: ten points, durable limits, no promotion."""

import copy
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import twitch_store as store
import twitch_experiments as paper

STAKE = 10
DAILY_BUDGET = 100


def midnight(now):
    return int(datetime.fromtimestamp(now, ZoneInfo("Europe/Berlin")).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())


def reserve(event, settings, now=None):
    """One immutable decision per event; reserve before touching the network."""
    now = int(time.time()) if now is None else int(now)
    account, identity, channel = settings["username"], str(event.event_id), event.streamer.username
    rule = settings["bet_strategy"]
    with store.database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute("SELECT * FROM bet_decisions WHERE account=? AND event_id=?",
                                      (account, identity)).fetchone()
        if previous:
            return {**dict(previous), "duplicate": True}
        choice = connection.execute("SELECT choice_id,choice_title FROM paper_choices WHERE account=? AND event_id=? AND strategy=?",
                                    (account, identity, rule)).fetchone()
        spent = connection.execute("SELECT COALESCE(SUM(stake),0) FROM bet_decisions WHERE account=? AND channel=? AND created_at>=?",
                                   (account, channel, midnight(now))).fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM bet_decisions WHERE account=? AND channel=? AND mode='live' AND closed_at IS NULL",
                                     (account, channel)).fetchone()[0]
        losses = connection.execute("SELECT result,updated_at FROM predictions WHERE account=? AND channel=? AND confirmed=1 AND result IN ('WIN','LOSE') ORDER BY updated_at DESC LIMIT 3",
                                    (account, channel)).fetchall()
        cooldown = len(losses) == 3 and all(row["result"] == "LOSE" for row in losses) and losses[0]["updated_at"] + 86400 > now
        balance = event.streamer.channel_points
        gates = [
            (not settings["predictions"] or not settings["live_betting"], "Real betting disabled."),
            (channel not in settings["bet_channels"], "This channel stays paper-only."),
            (not choice or not choice["choice_id"], "Selected rule tied; no wager."),
            (balance - STAKE < settings["bet_min_balance"], "Keeping the points reserve."),
            (STAKE > min(settings["bet_max_points"], int(balance * min(settings["bet_percentage"] / 100, 0.005))), "Stake exceeds the configured balance cap."),
            (bool(cooldown), "24-hour pause after three confirmed losses."),
            (bool(pending), "Waiting for the previous real attempt to settle."),
            (spent + STAKE > DAILY_BUDGET, "Daily channel stake budget reached."),
        ]
        reason = next((message for failed, message in gates if failed), None)
        mode, stake = ("paper", 0) if reason else ("live", STAKE)
        reason = reason or "Ten-point live attempt reserved."
        connection.execute("INSERT INTO bet_decisions(account,event_id,channel,strategy,choice_id,choice_title,mode,reason,stake,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (account, identity, channel, rule, choice["choice_id"] if choice else None,
             choice["choice_title"] if choice else None, mode, reason, stake, now))
    return {"mode": mode, "stake": stake, "choice_id": choice["choice_id"] if choice else None,
            "choice_title": choice["choice_title"] if choice else None, "reason": reason, "duplicate": False}


def execute(event, settings, submit, first_strategy, second_strategy, is_open):
    """Keep paper comparison running, then submit only a newly reserved live plan."""
    if not is_open(event) or not settings["predictions"]:
        return None
    paper.execute(event, settings)
    plan = reserve(event, settings)
    if plan["mode"] != "live":
        return plan
    index = next(index for index, outcome in enumerate(event.bet.outcomes)
                 if str(outcome["id"]) == plan["choice_id"])
    if plan["duplicate"]:
        # Restore submitted stake for accurate late confirmations/results, but
        # never submit again after a duplicate message or worker restart.
        event.bet.decision = {"choice": index, "id": plan["choice_id"], "amount": plan["stake"]}
        return plan
    if not is_open(event):
        close(settings["username"], str(event.event_id), "Window closed before submission.")
        return {**plan, "reason": "Window closed before submission."}
    event.bet.settings = copy.copy(event.bet.settings)
    event.bet.settings.strategy = first_strategy if index == 0 else second_strategy
    event.bet.settings.max_points = STAKE
    submit(event)
    return plan


def close(account, event_id, reason=None, now=None):
    with store.database() as connection:
        connection.execute("UPDATE bet_decisions SET closed_at=?,reason=COALESCE(?,reason) WHERE account=? AND event_id=? AND closed_at IS NULL",
                           (int(time.time()) if now is None else now, reason, account, event_id))


def confirm(account, event_id):
    """Recover confirmations even when a restart lost the upstream event object."""
    with store.database(readonly=True) as connection:
        row = connection.execute("SELECT d.channel,d.choice_title,d.stake,e.title FROM bet_decisions d JOIN paper_events e USING(account,event_id) WHERE d.account=? AND d.event_id=? AND d.mode='live'",
                                 (account, event_id)).fetchone()
    if row is None:
        return False
    store.record_prediction(account, {"id": event_id, "channel": row["channel"], "title": row["title"],
        "status": "ACTIVE", "choice": row["choice_title"], "stake": row["stake"], "confirmed": True})
    return True


def resolve_event(account, event):
    if str(event.get("status", "")).upper() in ("RESOLVED", "CANCELED", "CANCELLED"):
        close(account, str(event.get("id", "")))


def summary(settings):
    now = int(time.time())
    result = {"enabled": settings["live_betting"] and settings["predictions"],
              "strategy": settings["bet_strategy"], "stake": STAKE, "daily_budget": DAILY_BUDGET,
              "reserve": settings["bet_min_balance"], "channels": [], "recent": {}}
    if not (store.data_dir() / "stats.db").exists():
        return result
    with store.database(readonly=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='bet_decisions'").fetchone():
            return result
        for channel in settings["bet_channels"]:
            row = connection.execute("SELECT COALESCE(SUM(CASE WHEN created_at>=? THEN stake ELSE 0 END),0),COALESCE(SUM(mode='live' AND closed_at IS NULL),0) FROM bet_decisions WHERE account=? AND channel=?",
                                     (midnight(now), settings["username"], channel)).fetchone()
            result["channels"].append({"name": channel, "reserved_today": row[0], "pending": row[1]})
        for row in connection.execute("SELECT event_id,mode,reason,stake FROM bet_decisions WHERE account=? ORDER BY created_at DESC LIMIT 100", (settings["username"],)):
            result["recent"][row["event_id"]] = {key: row[key] for key in ("mode", "reason", "stake")}
    return result
