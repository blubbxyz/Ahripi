"""Private configuration and persistent, read-only dashboard data for the miner."""

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "username": "", "channels": [], "predictions": True,
    "bet_percentage": 1, "bet_max_points": 500, "bet_min_balance": 1000,
}
NAME = re.compile(r"^[a-z0-9_]{1,25}$")


def data_dir():
    return Path(os.getenv("TWITCH_DATA_DIR", str(ROOT / "data/twitch")))


def read_json(name, default=None):
    try:
        with (data_dir() / name).open() as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(name, value):
    folder = data_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
    os.replace(temporary, target)


def validate_config(value):
    if not isinstance(value, dict):
        raise ValueError("Settings must be an object.")
    config = {**DEFAULTS, **{k: value[k] for k in DEFAULTS if k in value}}
    if not isinstance(config["username"], str):
        raise ValueError("Enter a Twitch username.")
    config["username"] = config["username"].strip().lower()
    if not NAME.fullmatch(config["username"]):
        raise ValueError("Enter a valid Twitch username.")
    channels = config["channels"]
    if not isinstance(channels, list) or not 1 <= len(channels) <= 30:
        raise ValueError("Choose between 1 and 30 channels.")
    cleaned = []
    for name in channels:
        if not isinstance(name, str):
            raise ValueError("Channel names must be text.")
        name = re.sub(r"^https?://(?:www\.)?twitch\.tv/", "", name.strip().lower()).strip("/")
        if not NAME.fullmatch(name):
            raise ValueError("Use channel names or Twitch channel URLs.")
        if name not in cleaned:
            cleaned.append(name)
    config["channels"] = cleaned
    if type(config["predictions"]) is not bool:
        raise ValueError("Predictions must be enabled or disabled.")
    for key, low, high in [("bet_percentage", 1, 10), ("bet_max_points", 10, 10000),
                           ("bet_min_balance", 1000, 10000000)]:
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
    return config


def config():
    return {**DEFAULTS, **read_json("config.json")}


@contextmanager
def database(readonly=False):
    path = data_dir() / "stats.db"
    if readonly:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        connection.execute("PRAGMA query_only=ON")
    else:
        connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_database():
    data_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    with database() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                account TEXT NOT NULL, name TEXT NOT NULL, balance INTEGER NOT NULL,
                baseline INTEGER NOT NULL, earned INTEGER NOT NULL DEFAULT 0,
                spent INTEGER NOT NULL DEFAULT 0, online INTEGER NOT NULL DEFAULT 0,
                title TEXT, game TEXT, watched_minutes REAL NOT NULL DEFAULT 0,
                first_seen INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(account, name)
            );
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY, account TEXT NOT NULL, channel TEXT NOT NULL,
                at INTEGER NOT NULL, balance INTEGER NOT NULL, delta INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_channel_time ON samples(account, channel, at);
            CREATE TABLE IF NOT EXISTS predictions (
                account TEXT NOT NULL, event_id TEXT NOT NULL, channel TEXT NOT NULL,
                title TEXT NOT NULL, status TEXT, choice TEXT, stake INTEGER NOT NULL,
                confirmed INTEGER NOT NULL, result TEXT, profit INTEGER,
                first_seen INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(account, event_id)
            );
        """)
    (data_dir() / "stats.db").chmod(0o600)


def record_channel(account, channel, *, balance, online, title, game, watched_minutes=0, at=None):
    at = int(time.time()) if at is None else at
    with database() as connection:
        old = connection.execute("SELECT * FROM channels WHERE account=? AND name=?", (account, channel)).fetchone()
        delta = balance - old["balance"] if old else 0
        connection.execute("""
            INSERT INTO channels(account,name,balance,baseline,earned,spent,online,title,game,
                                 watched_minutes,first_seen,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account,name) DO UPDATE SET
                balance=excluded.balance, earned=channels.earned+excluded.earned,
                spent=channels.spent+excluded.spent, online=excluded.online,
                title=excluded.title, game=excluded.game,
                watched_minutes=channels.watched_minutes+excluded.watched_minutes,
                updated_at=excluded.updated_at
        """, (account, channel, balance, balance, max(delta, 0), max(-delta, 0),
              bool(online), title, game, watched_minutes, at, at))
        connection.execute("INSERT INTO samples(account,channel,at,balance,delta) VALUES(?,?,?,?,?)",
                           (account, channel, at, balance, delta))
        connection.execute("DELETE FROM samples WHERE account=? AND channel=? AND at<?",
                           (account, channel, at - 30 * 86400))


def record_prediction(account, event, at=None):
    at = int(time.time()) if at is None else at
    with database() as connection:
        connection.execute("""
            INSERT INTO predictions(account,event_id,channel,title,status,choice,stake,confirmed,
                                    result,profit,first_seen,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account,event_id) DO UPDATE SET
                status=excluded.status,choice=excluded.choice,stake=excluded.stake,
                confirmed=excluded.confirmed,result=excluded.result,profit=excluded.profit,
                updated_at=excluded.updated_at
        """, (account, event["id"], event["channel"], event["title"], event["status"],
              event.get("choice"), event.get("stake", 0), bool(event.get("confirmed")),
              event.get("result"), event.get("profit"), at, at))


def dashboard():
    settings = config()
    worker = read_json("status.json")
    # Never return private auth state, cookies, tokens, raw logs, or arbitrary status keys.
    worker = {k: worker.get(k) for k in ("state", "message", "updated_at", "started_at", "session_id")}
    worker["stale"] = not worker["updated_at"] or time.time() - worker["updated_at"] > 90
    midnight = int(datetime.now(ZoneInfo("Europe/Berlin")).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    channels, predictions, today = [], [], 0
    if (data_dir() / "stats.db").exists():
        with database(readonly=True) as connection:
            channels = [dict(row) for row in connection.execute(
                "SELECT * FROM channels WHERE account=?", (settings["username"],))
                if row["name"] in settings["channels"]]
            predictions = [dict(row) for row in connection.execute(
                "SELECT * FROM predictions WHERE account=? ORDER BY updated_at DESC LIMIT 100",
                (settings["username"],)) if row["channel"] in settings["channels"]]
            if settings["channels"]:
                placeholders = ",".join("?" for _ in settings["channels"])
                today = connection.execute(
                    f"SELECT COALESCE(SUM(MAX(delta,0)),0) FROM samples WHERE account=? AND at>=? AND channel IN ({placeholders})",
                    (settings["username"], midnight, *settings["channels"])).fetchone()[0]
    by_name = {row["name"]: row for row in channels}
    ordered = [by_name.get(name, {"name": name, "balance": None, "earned": 0,
               "spent": 0, "online": False, "watched_minutes": 0, "updated_at": None})
               for name in settings["channels"]]
    for row in ordered:
        row.pop("account", None)
        row["stale"] = not row["updated_at"] or time.time() - row["updated_at"] > 180
    for row in predictions:
        row.pop("account", None)
    # Totals include all stored predictions, not only the activity feed's last 100.
    betting = {"bets": 0, "wins": 0, "losses": 0, "profit": 0, "pending": 0}
    if (data_dir() / "stats.db").exists() and settings["channels"]:
        with database(readonly=True) as connection:
            placeholders = ",".join("?" for _ in settings["channels"])
            rows = connection.execute(
                f"SELECT confirmed,result,profit FROM predictions WHERE account=? AND channel IN ({placeholders})",
                (settings["username"], *settings["channels"]))
            for row in rows:
                betting["bets"] += bool(row["confirmed"])
                betting["wins"] += row["result"] == "WIN"
                betting["losses"] += row["result"] == "LOSE"
                betting["profit"] += row["profit"] or 0
                betting["pending"] += bool(row["confirmed"] and not row["result"])
    return {"worker": worker, "channels": ordered, "predictions": predictions,
            "totals": {"balance": sum(row["balance"] or 0 for row in ordered),
                       "earned": sum(row["earned"] for row in ordered), "today": today,
                       "watched_minutes": sum(row["watched_minutes"] for row in ordered),
                       "betting": betting},
            "policy": {k: settings[k] for k in ("predictions", "bet_percentage", "bet_max_points", "bet_min_balance")}}


def history(channel, hours=24):
    settings = config()
    if channel not in settings["channels"]:
        raise ValueError("Unknown channel.")
    if not (data_dir() / "stats.db").exists():
        return []
    with database(readonly=True) as connection:
        since = int(time.time()) - hours * 3600
        extent = connection.execute(
            "SELECT MIN(at),MAX(at) FROM samples WHERE account=? AND channel=? AND at>=?",
            (settings["username"], channel, since)).fetchone()
        if extent[0] is None:
            return []
        # Use the available span so a new installation needn't wait eight minutes
        # for a second point merely because the selected window is 24 hours.
        bucket = max(60, (extent[1] - extent[0]) // 180)
        rows = connection.execute("""
            SELECT s.at,s.balance FROM samples AS s JOIN (
                SELECT MAX(id) AS id FROM samples
                WHERE account=? AND channel=? AND at>=?
                GROUP BY CAST(at / ? AS INTEGER)
            ) AS recent ON s.id=recent.id ORDER BY s.at
        """, (settings["username"], channel, since, bucket))
        return [dict(row) for row in rows]
