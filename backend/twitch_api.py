"""Public Twitch statistics; configuration and device login stay admin-only."""

import sqlite3
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request, send_from_directory, session

import twitch_store as store
import twitch_privacy as privacy

twitch = Blueprint("twitch", __name__)
WEB = Path(__file__).resolve().parents[1] / "web/twitch"


def admin_only(function):
    @wraps(function)
    def checked(*args, **kwargs):
        if session.get("is_admin") is not True:
            return jsonify(error="Admin login required."), 403
        return function(*args, **kwargs)
    return checked


@twitch.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@twitch.get("/twitch/")
def page():
    return send_from_directory(WEB, "index.html")


@twitch.get("/api/twitch")
def stats():
    try:
        snapshot = store.dashboard()
        if session.get("is_admin") is not True:
            snapshot = privacy.redact(snapshot, privacy.channel_aliases())
        return jsonify(snapshot)
    except (sqlite3.Error, OSError, ValueError):
        return jsonify(error="Twitch statistics are temporarily unavailable."), 503


@twitch.get("/api/twitch/history")
def history():
    try:
        hours = int(request.args.get("hours", "24"))
        if hours not in (1, 24, 168):
            raise ValueError("Choose 1, 24, or 168 hours.")
        channel = request.args.get("channel", "")
        if session.get("is_admin") is not True:
            channel = privacy.history_channel(channel, privacy.channel_aliases())
        return jsonify(store.history(channel, hours))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except (sqlite3.Error, OSError):
        return jsonify(error="History is temporarily unavailable."), 503


@twitch.get("/api/twitch/settings")
@admin_only
def settings():
    return jsonify(store.config())


@twitch.post("/api/twitch/settings")
@admin_only
def save_settings():
    origin = request.headers.get("Origin")
    if request.headers.get("X-Ahripi-Request") != "1" or (origin and urlsplit(origin).netloc != request.host):
        return jsonify(error="Use the settings form on this website."), 403
    try:
        settings = store.validate_config(request.get_json(silent=True))
        store.write_json("config.json", settings)
        return jsonify(status="ok", message="Saved. The miner will restart with these settings.")
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except OSError:
        return jsonify(error="Could not save settings."), 503


@twitch.get("/api/twitch/auth")
@admin_only
def auth():
    private = store.read_json("auth-status.json")
    return jsonify({key: private.get(key) for key in ("code", "url", "expires_at", "message")})
