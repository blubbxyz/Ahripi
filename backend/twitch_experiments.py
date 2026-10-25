"""Three fixed paper rules on identical pre-result markets; no real submitter."""

import json
import math
import time

import twitch_store as store
from twitch_learning import market

STAKE = 10
RULES = (
    ("most_voters", "Most voters", "users", max),
    ("most_points", "Most points", "points", max),
    ("least_points", "Least points", "points", min),
)
LABELS = {key: label for key, label, _, _ in RULES}


def record(account, event_id, channel, title, outcomes, now=None):
    """Freeze all three choices together. Repeated messages never revise them."""
    snapshot = market(outcomes)
    now = int(time.time()) if now is None else int(now)
    with store.database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute("SELECT 1 FROM paper_events WHERE account=? AND event_id=?",
                                    (account, event_id)).fetchone()
        if not exists:
            connection.execute("INSERT INTO paper_events(account,event_id,channel,title,snapshot,created_at) VALUES(?,?,?,?,?,?)",
                               (account, event_id, channel, title, json.dumps(snapshot), now))
            for key, _, field, choose in RULES:
                target = choose(item[field] for item in snapshot)
                picks = [item for item in snapshot if item[field] == target]
                pick = picks[0] if len(picks) == 1 else None
                connection.execute("INSERT INTO paper_choices(account,event_id,strategy,choice_id,choice_title,result) VALUES(?,?,?,?,?,?)",
                    (account, event_id, key, pick["id"] if pick else None,
                     pick["title"] if pick else None, None if pick else "SKIPPED"))
        return [dict(row) for row in connection.execute(
            "SELECT strategy,choice_id,choice_title FROM paper_choices WHERE account=? AND event_id=? ORDER BY strategy",
            (account, event_id))]


def resolve_event(account, event, now=None):
    """Only explicit results settle a snapshot. Unpriced markets have no P/L."""
    status = str(event.get("status", "")).upper()
    if status not in ("RESOLVED", "CANCELED", "CANCELLED"):
        return False
    event_id = str(event.get("id", ""))
    with store.database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM paper_events WHERE account=? AND event_id=?",
                                 (account, event_id)).fetchone()
        if row is None or row["resolved_at"] is not None:
            return False
        cancelled = status in ("CANCELED", "CANCELLED")
        winner = event.get("winning_outcome_id")
        if not winner and isinstance(event.get("winning_outcome"), dict):
            winner = event["winning_outcome"].get("id")
        ids = {item["id"] for item in json.loads(row["snapshot"])}
        if not cancelled and str(winner) not in ids:
            return False
        final = None
        if not cancelled:
            try:
                candidate = market(event["outcomes"])
                if {item["id"] for item in candidate} == ids:
                    final = {item["id"]: item for item in candidate}
            except (KeyError, ValueError, TypeError):
                pass
        choices = connection.execute("SELECT strategy,choice_id FROM paper_choices WHERE account=? AND event_id=?",
                                     (account, event_id)).fetchall()
        for choice in choices:
            if choice["choice_id"] is None:
                continue
            won = str(winner) == choice["choice_id"]
            result = "CANCELLED" if cancelled else "WIN" if won else "LOSE"
            profit = 0 if cancelled else None
            # Exclude the entire unpriced market, including losing rules, so
            # strategies' ROI is measured on the same priced predictions.
            if final:
                pool = sum(item["points"] for item in final.values())
                selected = final[choice["choice_id"]]["points"]
                profit = math.floor(STAKE * (pool + STAKE) / (selected + STAKE)) - STAKE if won else -STAKE
            connection.execute("UPDATE paper_choices SET result=?,profit=? WHERE account=? AND event_id=? AND strategy=?",
                               (result, profit, account, event_id, choice["strategy"]))
        connection.execute("UPDATE paper_events SET resolved_at=?,winner_id=?,cancelled=? WHERE account=? AND event_id=?",
            (int(time.time()) if now is None else int(now), str(winner) if not cancelled else None,
             cancelled, account, event_id))
    return True


def execute(event, settings):
    """Record paper choices and set zero stake. There is no submission callback."""
    if event.status != "ACTIVE" or not settings["predictions"]:
        return None
    choices = record(settings["username"], str(event.event_id), event.streamer.username,
                     event.title, event.bet.outcomes)
    by_rule = {choice["strategy"]: choice for choice in choices}
    primary = next((by_rule[key] for key, _, _, _ in RULES if by_rule[key]["choice_id"]), None)
    index = next((index for index, item in enumerate(event.bet.outcomes)
                  if primary and str(item["id"]) == primary["choice_id"]), None)
    event.bet.decision = {"choice": index, "id": primary["choice_id"] if primary else None, "amount": 0}
    return {"mode": "paper", "stake": 0, "reason": "Comparing three fixed rules; real bets disabled."}


def summary(account, channels):
    result = {"mode": "paper_only", "stake": STAKE, "started_at": None, "events": 0,
              "strategies": [], "recent": {}}
    rows = {}
    if channels and (store.data_dir() / "stats.db").exists():
        with store.database(readonly=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='paper_events'").fetchone():
                placeholders = ",".join("?" for _ in channels)
                args = (account, *channels)
                extent = connection.execute(
                    f"SELECT COUNT(*),MIN(created_at) FROM paper_events WHERE account=? AND channel IN ({placeholders})", args).fetchone()
                result.update(events=extent[0], started_at=extent[1])
                totals = connection.execute(f"""
                    SELECT c.strategy,
                        SUM(c.result='WIN') AS wins, SUM(c.result='LOSE') AS losses,
                        SUM(c.result='CANCELLED') AS cancelled, SUM(c.result='SKIPPED') AS skipped,
                        SUM(c.result IS NULL) AS pending,
                        SUM(c.result IN ('WIN','LOSE') AND c.profit IS NOT NULL) AS priced,
                        SUM(c.result IN ('WIN','LOSE') AND c.profit IS NULL) AS unpriced,
                        SUM(CASE WHEN c.result IN ('WIN','LOSE') THEN c.profit END) AS profit
                    FROM paper_choices c JOIN paper_events e USING(account,event_id)
                    WHERE e.account=? AND e.channel IN ({placeholders}) GROUP BY c.strategy
                """, args)
                rows = {row["strategy"]: dict(row) for row in totals}
                recent = connection.execute(f"""
                    SELECT c.event_id,c.strategy,c.choice_title,c.result,c.profit
                    FROM paper_choices c WHERE c.account=? AND c.event_id IN (
                        SELECT event_id FROM paper_events WHERE account=? AND channel IN ({placeholders})
                        ORDER BY created_at DESC LIMIT 100)
                    ORDER BY c.strategy
                """, (account, *args))
                for row in recent:
                    choice = {key: row[key] for key in ("strategy", "choice_title", "result", "profit")}
                    choice["name"] = LABELS[row["strategy"]]
                    result["recent"].setdefault(row["event_id"], []).append(choice)
    for key, label, _, _ in RULES:
        row = rows.get(key, {})
        stats = {field: row.get(field) or 0 for field in
                 ("wins", "losses", "cancelled", "skipped", "pending", "priced", "unpriced")}
        stats.update(id=key, name=label, settled=stats["wins"] + stats["losses"],
                     profit=row.get("profit") if stats["priced"] else None,
                     roi=round((row.get("profit") or 0) / (stats["priced"] * STAKE) * 100, 2) if stats["priced"] else None)
        result["strategies"].append(stats)
    return result
