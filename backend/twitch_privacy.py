"""Visitor-only channel aliases; the miner's private configuration stays intact."""

import json
import re

import twitch_store as store


def channel_aliases():
    """Keep the redaction list in ignored runtime data, out of public source."""
    try:
        with (store.data_dir() / "visibility.json").open() as handle:
            settings = json.load(handle)
    except FileNotFoundError:
        return {}
    names = settings.get("redacted_channels") if isinstance(settings, dict) else None
    if not isinstance(names, list) or any(
        not isinstance(name, str) or not store.NAME.fullmatch(name) for name in names
    ):
        raise ValueError("Channel visibility settings are unavailable.")
    return {name: f"redacted-channel-{index}" for index, name in
            enumerate(dict.fromkeys(names), 1)}


def label(alias):
    return "Redacted channel " + alias.rsplit("-", 1)[1]


def redact(snapshot, aliases):
    """Redact before sending JSON, including identifying metadata and event IDs."""
    if not aliases:
        return snapshot
    for channel in snapshot["channels"]:
        alias = aliases.get(channel["name"])
        if alias:
            channel.update(name=alias, display_name=label(alias), redacted=True,
                           title=None, game=None)
    for event in snapshot["predictions"]:
        alias = aliases.get(event["channel"])
        if alias:
            event.update(channel=alias, channel_display_name=label(alias))
            event.pop("event_id", None)
            for choice in event.get("paper_choices", []):
                if choice.get("choice_title"):
                    choice["choice_title"] = "Redacted"
            for key in ("title", "choice", "trial_choice", "learning_reason"):
                if event.get(key):
                    event[key] = "Redacted"
    for channel in snapshot["learning"]["channels"]:
        alias = aliases.get(channel["name"])
        if alias:
            channel.update(name=alias, display_name=label(alias),
                           reason="Details hidden for visitors.")
    snapshot["policy"]["bet_channels"] = [aliases.get(name, name) for name in snapshot["policy"].get("bet_channels", [])]
    for channel in snapshot.get("live_betting", {}).get("channels", []):
        if channel["name"] in aliases:
            channel["name"] = aliases[channel["name"]]

    # Worker messages and other channels' free text can also mention a name.
    pattern = re.compile("|".join(re.escape(name) for name in sorted(aliases, key=len, reverse=True)), re.I)

    def scrub(value):
        if isinstance(value, str):
            return pattern.sub("Redacted channel", value)
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    return scrub(snapshot)


def history_channel(channel, aliases):
    """Anonymous history uses aliases; guessing a redacted name reveals nothing."""
    if channel in aliases:
        raise ValueError("Unknown channel.")
    return {alias: name for name, alias in aliases.items()}.get(channel, channel)
