"""Evaluate prediction messages without waiting or blocking websocket updates."""

import threading
from datetime import datetime, timezone


def prediction_open(event):
    return (event.status == "ACTIVE" and
            event.closing_bet_after(datetime.now(timezone.utc)) > 0)


class EarlyPredictions:
    def __init__(self, evaluate, stop):
        self.evaluate = evaluate
        self.stop = stop
        self.lock = threading.Lock()
        self.pending = {}

    def schedule(self, twitch, event):
        if self.stop.is_set() or not prediction_open(event) or event.bet.decision:
            return False
        key = str(event.event_id)
        with self.lock:
            if key in self.pending:
                # An update can arrive while an empty market is being checked.
                # Recheck its latest data afterwards, without concurrent bets.
                self.pending[key] = True
                return False
            self.pending[key] = False
        thread = threading.Thread(target=self.run, args=(key, twitch, event),
                                  daemon=True, name="Twitch prediction evaluation")
        thread.start()
        return True

    def run(self, key, twitch, event):
        try:
            if not self.stop.is_set() and prediction_open(event) and not event.bet.decision:
                self.evaluate(twitch, event)
        finally:
            with self.lock:
                updated = self.pending.pop(key)
            if updated:
                self.schedule(twitch, event)
