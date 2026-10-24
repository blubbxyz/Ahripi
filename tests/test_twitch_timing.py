"""Short prediction windows and duplicate messages, without Twitch/network calls."""

import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations/twitch"))
from prediction_timing import EarlyPredictions


class PredictionTimingTests(unittest.TestCase):
    def event(self, remaining=5, status="ACTIVE"):
        closes = datetime.now(timezone.utc) + timedelta(seconds=remaining)
        return SimpleNamespace(event_id="short-event", status=status,
            closing_bet_after=lambda now: (closes - now).total_seconds(),
            bet=SimpleNamespace(decision={}))

    def test_short_and_late_discovered_windows_evaluate_without_a_wait(self):
        for remaining in (5, 10, 2):
            with self.subTest(remaining=remaining):
                done = threading.Event()
                seen = []

                def evaluate(twitch, event):
                    seen.append(event.event_id)
                    event.bet.decision = {"amount": 0}
                    done.set()

                scheduler = EarlyPredictions(evaluate, threading.Event())
                self.assertTrue(scheduler.schedule(None, self.event(remaining)))
                self.assertTrue(done.wait(1), "Evaluation waited instead of starting on receipt")
                self.assertEqual(seen, ["short-event"])

    def test_empty_market_can_be_retried_on_the_next_update(self):
        first, done = threading.Event(), threading.Event()
        event = self.event()
        attempts = []

        def evaluate(twitch, event):
            attempts.append(1)
            if len(attempts) == 1:
                first.set()  # No decision: the opening market was empty.
            else:
                event.bet.decision = {"amount": 0}
                done.set()

        scheduler = EarlyPredictions(evaluate, threading.Event())
        scheduler.schedule(None, event)
        self.assertTrue(first.wait(1))
        scheduler.schedule(None, event)
        self.assertTrue(done.wait(1))
        self.assertEqual(len(attempts), 2)

    def test_duplicate_updates_cannot_evaluate_a_decided_event_twice(self):
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        event = self.event()
        attempts = []

        def evaluate(twitch, event):
            attempts.append(1)
            started.set()
            release.wait(2)
            event.bet.decision = {"amount": 10}
            done.set()

        scheduler = EarlyPredictions(evaluate, threading.Event())
        scheduler.schedule(None, event)
        self.assertTrue(started.wait(1))
        try:
            for _ in range(10):
                self.assertFalse(scheduler.schedule(None, event))
        finally:
            release.set()
        self.assertTrue(done.wait(1))
        self.assertFalse(scheduler.schedule(None, event))
        self.assertEqual(len(attempts), 1)

    def test_closed_expired_and_stopping_predictions_never_start(self):
        calls = []
        stop = threading.Event()
        scheduler = EarlyPredictions(lambda *args: calls.append(args), stop)
        for event in [self.event(status="LOCKED"), self.event(status="RESOLVED"), self.event(-1)]:
            self.assertFalse(scheduler.schedule(None, event))
        stop.set()
        self.assertFalse(scheduler.schedule(None, self.event()))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
