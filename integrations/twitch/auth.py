"""Twitch device authorization, token validation and refresh. Never logs tokens."""

import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import twitch_store as store

from TwitchChannelPointsMiner.constants import CLIENT_ID, USER_AGENTS

HEADERS = {"User-Agent": USER_AGENTS["Android"]["TV"],
           "Origin": "https://android.tv.twitch.tv", "Client-Id": CLIENT_ID}
SCOPES = "channel_read chat:read user_read"


def validate(token, username):
    response = requests.get("https://id.twitch.tv/oauth2/validate",
                            headers={"Authorization": "OAuth " + token}, timeout=20)
    if response.status_code == 401:
        return None
    response.raise_for_status()
    identity = response.json()
    if identity.get("login", "").lower() != username:
        raise ValueError("Authorize Twitch while signed in to the configured account.")
    return identity


def credentials(username, progress, stop=None):
    stop = stop or threading.Event()
    saved = store.read_json("credentials.json")
    if saved.get("username") == username and saved.get("access_token"):
        identity = validate(saved["access_token"], username)
        if identity and identity.get("expires_in", 0) > 900:
            store.write_json("auth-status.json", {})
            return {**saved, "user_id": identity["user_id"]}
        if saved.get("refresh_token"):
            response = requests.post("https://id.twitch.tv/oauth2/token", data={
                "client_id": CLIENT_ID, "grant_type": "refresh_token",
                "refresh_token": saved["refresh_token"]}, headers=HEADERS, timeout=20)
            if response.ok:
                renewed = {**saved, **response.json()}
                identity = validate(renewed["access_token"], username)
                if identity:
                    renewed["user_id"] = identity["user_id"]
                    store.write_json("credentials.json", renewed)
                    store.write_json("auth-status.json", {})
                    return renewed
        if identity:
            return {**saved, "user_id": identity["user_id"]}

    while not stop.is_set():
        response = requests.post("https://id.twitch.tv/oauth2/device",
                                 data={"client_id": CLIENT_ID, "scopes": SCOPES},
                                 headers=HEADERS, timeout=20)
        response.raise_for_status()
        device = response.json()
        expires = time.time() + device["expires_in"]
        store.write_json("auth-status.json", {"code": device["user_code"],
                         "url": "https://www.twitch.tv/activate", "expires_at": expires,
                         "message": "Sign in to Twitch as " + username + " to connect."})
        progress("needs_auth", "Waiting for Twitch browser authorization.")
        interval = max(5, device.get("interval", 5))
        while time.time() < expires and not stop.wait(interval):
            if store.config()["username"] != username:
                raise ValueError("Twitch account settings changed; reconnecting.")
            response = requests.post("https://id.twitch.tv/oauth2/token", data={
                "client_id": CLIENT_ID, "device_code": device["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                headers=HEADERS, timeout=20)
            payload = response.json()
            if response.ok and payload.get("access_token"):
                identity = validate(payload["access_token"], username)
                if identity is None:
                    raise ValueError("Twitch returned a token that could not be validated.")
                saved = {"username": username, "user_id": identity["user_id"],
                         "access_token": payload["access_token"],
                         "refresh_token": payload.get("refresh_token")}
                store.write_json("credentials.json", saved)
                store.write_json("auth-status.json", {})
                return saved
            message = str(payload.get("message", payload.get("error", ""))).lower()
            if "slow" in message:
                interval += 5
            elif "pending" in message:
                continue
            elif "expired" in message or "invalid device" in message:
                break
            elif "denied" in message:
                raise ValueError("Twitch authorization was declined.")
            elif response.status_code >= 500:
                interval = min(30, interval + 5)
            else:
                raise ValueError("Twitch authorization failed; retry from the admin settings.")
    raise KeyboardInterrupt


if __name__ == "__main__":
    def progress(state, message):
        store.write_json("status.json", {"state": state, "message": message,
                         "updated_at": time.time(), "started_at": time.time()})
        private = store.read_json("auth-status.json")
        print("TWITCH_ACTIVATE", private.get("url"), private.get("code"), flush=True)

    try:
        result = credentials(store.config()["username"], progress)
        print("TWITCH_CONNECTED", result["username"], flush=True)
    except (ValueError, requests.RequestException):
        print("Twitch authorization could not complete. Retry or check connectivity.", flush=True)
        raise SystemExit(1)
