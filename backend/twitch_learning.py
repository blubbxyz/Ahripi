"""Conservative per-channel online calibration for *channel-point* predictions.

Every decision records both outcomes BEFORE the result. Once Twitch explicitly
names a winner, those snapshots train the same channel/game/question/outcome and
crowd-share bucket. No external model, historical backfill, or future pool data
is used to choose an outcome. Unknown/cancelled results never become wins.

Wilson's lower bound (z=1.96) is a conservative sample estimate, not a guarantee
that Twitch predictions are independent or that future returns match the past.
See integrations/twitch/README.md for the policy, assumptions and limits.
"""

import copy
import json
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import twitch_store as store

MIN_TRIALS = 20
MIN_MATCHES = 12
MAX_HISTORY = 100
MAX_AGE = 90 * 86400
MAX_STAKE = 50
DAILY_STAKES = 100
MIN_EDGE = 0.08
PAPER_STAKE = 10


def normalize(text):
    # Preserve numbers: "10 kills" and "30 kills" are different propositions.
    return re.sub(r"\s+", " ", str(text).casefold()).strip()[:180]


def wilson_lower(wins, count):
    if not count:
        return 0.0
    rate, z = wins / count, 1.96
    return max(0.0, (rate + z*z/(2*count) - z*math.sqrt(
        rate*(1-rate)/count + z*z/(4*count*count))) / (1 + z*z/count))


def bucket(share):
    return sum(share >= boundary for boundary in (0.35, 0.5, 0.65, 0.8))


def market(outcomes):
    """Take a bounded, JSON-only snapshot of two explicitly identified outcomes."""
    if not isinstance(outcomes, list) or len(outcomes) != 2:
        raise ValueError("Learning currently supports two-outcome predictions.")
    result = []
    for item in outcomes:
        users, points = int(item.get("total_users", 0)), int(item.get("total_points", 0))
        if users < 0 or points < 0 or not item.get("id") or not item.get("title"):
            raise ValueError("Prediction market data is incomplete.")
        result.append({"id": str(item["id"]), "title": str(item["title"])[:180],
                       "users": users, "points": points})
    if result[0]["id"] == result[1]["id"] or normalize(result[0]["title"]) == normalize(result[1]["title"]):
        raise ValueError("Prediction outcomes must be distinct.")
    users = sum(item["users"] for item in result)
    points = sum(item["points"] for item in result)
    if users == 0 or points == 0:
        raise ValueError("Waiting for a prediction pool with participants.")
    for item in result:
        item["share"] = item["users"] / users
    return result


def matching_history(rows, context, candidate):
    """Each resolved event contributes at most one observation for an outcome."""
    matches = []
    for row in rows:
        if row["context"] != context:
            continue
        for old in json.loads(row["snapshot"]):
            if (normalize(old["title"]) == normalize(candidate["title"])
                    and bucket(old["share"]) == bucket(candidate["share"])):
                matches.append(old["id"] == row["winner_id"])
                break
    return matches


def decide(account, event_id, channel, title, game, outcomes, balance, settings, now=None):
    """Return an immutable paper/live decision; atomically reserve any real stake.

    Reservations count even if Twitch rejects the request. There is no retry of
    a live event after a process restart, and at most one unsettled live reservation
    per channel. Failure at any gate falls back to a zero-spend paper trial.
    """
    now = int(time.time()) if now is None else int(now)
    snapshot = market(outcomes)
    context = normalize(game) + " | " + normalize(title)
    midnight = int(datetime.fromtimestamp(now, ZoneInfo("Europe/Berlin")).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    with store.database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute("SELECT * FROM learning_trials WHERE account=? AND event_id=?",
                                      (account, event_id)).fetchone()
        if previous:
            # Idempotency: preserve the old choice but never submit a second bet.
            return {**dict(previous), "mode": "paper", "stake": 0,
                    "reason": "Already evaluated; no duplicate wager."}
        rows = connection.execute("""
            SELECT * FROM learning_trials WHERE account=? AND channel=?
                AND resolved_at<=? AND resolved_at>=? AND outcome IN ('WIN','LOSE')
            ORDER BY resolved_at DESC LIMIT ?
        """, (account, channel, now, now-MAX_AGE, MAX_HISTORY)).fetchall()
        candidates = []
        pool = sum(item["points"] for item in snapshot)
        for index, item in enumerate(snapshot):
            matches = matching_history(rows, context, item)
            lower = wilson_lower(sum(matches), len(matches))
            # Include a hypothetical stake in the pool: advertised odds overstate
            # the payout when our own bet materially dilutes a small market.
            price = (item["points"] + PAPER_STAKE) / (pool + PAPER_STAKE)
            edge = lower / price - 1
            candidates.append({"index": index, "lower": lower, "edge": edge, "matches": len(matches)})
        qualified = [item for item in candidates if item["matches"] >= MIN_MATCHES and item["edge"] >= MIN_EDGE]
        chosen = max(qualified, key=lambda item:item["edge"]) if qualified else max(
            candidates, key=lambda item:snapshot[item["index"]]["users"])
        candidate = snapshot[chosen["index"]]
        # Increase only with evidence for THIS comparable outcome, not merely
        # because the channel has accumulated unrelated trials.
        stake = 50 if chosen["matches"] >= 100 and chosen["edge"] >= 0.25 else (
            25 if chosen["matches"] >= 50 and chosen["edge"] >= 0.15 else 10)
        stake = min(stake, MAX_STAKE, settings["bet_max_points"],
                    int(balance * min(settings["bet_percentage"]/100, 0.005)))
        # Recheck the conservative edge at the ACTUAL proposed stake.
        edge = chosen["lower"] / ((candidate["points"]+stake)/(pool+stake)) - 1 if stake else -1
        reserved = connection.execute("""
            SELECT COALESCE(SUM(stake),0) FROM learning_trials
            WHERE account=? AND channel=? AND mode='live' AND created_at>=?
        """, (account, channel, midnight)).fetchone()[0]
        pending = connection.execute("""
            SELECT COUNT(*) FROM learning_trials WHERE account=? AND channel=?
                AND mode='live' AND resolved_at IS NULL
        """, (account, channel)).fetchone()[0]
        losses = connection.execute("""
            SELECT p.result,p.updated_at FROM predictions p JOIN learning_trials t
                ON p.account=t.account AND p.event_id=t.event_id
            WHERE p.account=? AND p.channel=? AND p.confirmed=1 AND p.result IN ('WIN','LOSE')
            ORDER BY p.updated_at DESC LIMIT 3
        """, (account, channel)).fetchall()
        cooldown = len(losses)==3 and all(row['result']=='LOSE' for row in losses) and losses[0]['updated_at']+86400>now
        reason = "Confidence and pool value passed; small stake reserved."
        gates = [
            (not settings["predictions"], "Predictions disabled."),
            (len(rows)<MIN_TRIALS, f"Paper trials: {len(rows)}/{MIN_TRIALS} resolved on this channel."),
            (chosen["matches"]<MIN_MATCHES, f"Need {MIN_MATCHES} similar resolved outcomes; have {chosen['matches']}."),
            (not qualified or edge<MIN_EDGE, "Conservative estimated edge is too small."),
            (sum(row["paper_profit"] or 0 for row in rows)<=0, "Recent simulated results are not profitable."),
            (sum(item["users"] for item in snapshot)<20, "Too few market participants for a real stake."),
            (balance-stake<settings["bet_min_balance"], "Keeping the minimum points reserve."),
            (stake<10, "Stake cap is below Twitch's minimum."),
            (bool(cooldown), "24-hour cooldown after three confirmed losses."),
            (bool(pending), "Waiting for an earlier real stake to settle."),
            (reserved+stake>DAILY_STAKES, "Daily channel stake budget reached."),
        ]
        failed = next((message for condition,message in gates if condition), None)
        mode = "paper" if failed else "live"
        actual_stake = 0 if failed else stake
        connection.execute("""
            INSERT INTO learning_trials(account,event_id,channel,title,context,snapshot,
                choice_id,choice_title,mode,reason,stake,lower_bound,edge,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (account,event_id,channel,title,context,json.dumps(snapshot),candidate["id"],candidate["title"],
              mode,failed or reason,actual_stake,chosen["lower"],edge,now))
    return {"mode":mode,"stake":actual_stake,"choice_id":candidate["id"],
            "choice_title":candidate["title"],"index":chosen["index"],"reason":failed or reason,
            "lower_bound":chosen["lower"],"edge":edge}


def resolve_event(account, event, now=None):
    """Only explicit winners/cancellations settle a pre-existing trial.

    Final pool sizes estimate paper returns; they NEVER change the saved entry
    snapshot or its chosen outcome. Missing result fields remain unresolved.
    """
    status = str(event.get("status", "")).upper()
    if status not in ("RESOLVED", "CANCELED", "CANCELLED"):
        return False
    with store.database() as connection:
        row = connection.execute("SELECT * FROM learning_trials WHERE account=? AND event_id=?",
                                 (account,str(event.get("id", "")))).fetchone()
        if not row or row["resolved_at"] is not None:
            return False
        winner = event.get("winning_outcome_id")
        if not winner and isinstance(event.get("winning_outcome"), dict):
            winner = event["winning_outcome"].get("id")
        cancelled = status in ("CANCELED", "CANCELLED")
        if not cancelled:
            ids = {item["id"] for item in json.loads(row["snapshot"])}
            if str(winner) not in ids:
                return False
        won = str(winner) == row["choice_id"]
        profit = 0 if cancelled else -PAPER_STAKE
        if won and not cancelled:
            try:
                final = market(event["outcomes"])
                selected = next(item for item in final if item["id"]==row["choice_id"])
                profit = math.floor(PAPER_STAKE*(sum(item["points"] for item in final)+PAPER_STAKE)/(selected["points"]+PAPER_STAKE))-PAPER_STAKE
            except (KeyError, ValueError, StopIteration, TypeError):
                # Winner known but final pools unknown: learn the outcome, not a
                # made-up return. Missing ROI evidence also blocks promotion.
                profit = None
        connection.execute("""
            UPDATE learning_trials SET resolved_at=?,winner_id=?,outcome=?,paper_profit=?
            WHERE account=? AND event_id=? AND resolved_at IS NULL
        """, (int(time.time()) if now is None else now, str(winner) if not cancelled else None,
              "CANCELLED" if cancelled else "WIN" if won else "LOSE",profit,account,row["event_id"]))
    return True


def execute(event, settings, submit, first_strategy, second_strategy):
    """Adapter boundary: the submit callback is reachable ONLY for a live plan.

    Upstream's strategy selector is given the learned outcome and bounded stake;
    it must not choose another outcome or reuse shared settings from a channel.
    Exceptions propagate to the runner's fail-closed handler, never to a fallback.
    """
    if event.status != "ACTIVE":
        return None
    game = event.streamer.stream.game or {}
    plan = decide(settings["username"], str(event.event_id), event.streamer.username,
                  event.title, game.get("name", ""), event.bet.outcomes,
                  event.streamer.channel_points, settings)
    choice = next(index for index,item in enumerate(event.bet.outcomes)
                  if str(item["id"]) == plan["choice_id"])
    if plan["mode"] == "live":
        event.bet.settings = copy.copy(event.bet.settings)
        event.bet.settings.strategy = first_strategy if choice==0 else second_strategy
        event.bet.settings.max_points = plan["stake"]
        submit(event)
    else:
        event.bet.decision = {"choice":choice,"id":plan["choice_id"],"amount":0}
    return plan


def summary(account, channels):
    """Public explanation of learning progress; no credentials/raw payloads."""
    result = {"min_trials":MIN_TRIALS,"min_matches":MIN_MATCHES,"max_stake":MAX_STAKE,
              "daily_stakes":DAILY_STAKES,"channels":[],"recent":[]}
    if not (store.data_dir()/"stats.db").exists():
        return result
    with store.database(readonly=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='learning_trials'").fetchone():
            return result  # Read old schemas safely during a rolling deployment.
        for channel in channels:
            rows = connection.execute("SELECT * FROM learning_trials WHERE account=? AND channel=? ORDER BY created_at DESC",
                                      (account,channel)).fetchall()
            resolved = [row for row in rows if row["outcome"] in ("WIN","LOSE")]
            latest = rows[0] if rows else None
            result["channels"].append({"name":channel,"trials":len(resolved),
                "wins":sum(row["outcome"]=="WIN" for row in resolved),
                "paper_profit":sum(row["paper_profit"] or 0 for row in resolved),
                "mode":latest["mode"] if latest else "paper",
                "reason":latest["reason"] if latest else "Waiting for the first prediction trial.",
                "lower_bound":latest["lower_bound"] if latest else None,
                "edge":latest["edge"] if latest else None})
        result["recent"] = [dict(row) for row in connection.execute(
            "SELECT event_id,mode,reason,outcome,paper_profit,choice_title FROM learning_trials WHERE account=? ORDER BY created_at DESC LIMIT 100",
            (account,))]
    return result
