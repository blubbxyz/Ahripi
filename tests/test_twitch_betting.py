"""Small live pilots: synthetic submissions, durable limits and preserved stats."""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import twitch_store as store
import twitch_experiments as paper
import twitch_betting as betting


class LivePilotTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"TWITCH_DATA_DIR": self.folder.name})
        self.environment.start()
        store.init_database()
        self.settings = store.validate_config({"username": "viewer", "channels": ["channel"],
                                               "live_betting": True, "bet_channels": ["channel"]})
        store.write_json("config.json", self.settings)
        self.outcomes = [{"id": "yes", "title": "Win", "total_users": 70, "total_points": 40},
                         {"id": "no", "title": "Lose", "total_users": 30, "total_points": 60}]
        self.now = int(time.time())

    def tearDown(self):
        self.environment.stop()
        self.folder.cleanup()

    def event(self, identity="event", channel="channel", balance=100000, outcomes=None):
        return SimpleNamespace(event_id=identity, status="ACTIVE", title="Who wins?",
            streamer=SimpleNamespace(username=channel, channel_points=balance),
            bet=SimpleNamespace(outcomes=self.outcomes if outcomes is None else outcomes, decision={},
                settings=SimpleNamespace(strategy="SMART", max_points=500, percentage=1)))

    def reserve(self, identity="event", now=None, **changes):
        event = self.event(identity, **changes)
        paper.record("viewer", identity, event.streamer.username, event.title, event.bet.outcomes)
        return betting.reserve(event, self.settings, self.now if now is None else now)

    def execute(self, event, settings=None, open_check=None):
        sent = []
        def submit(selected):
            index = 0 if selected.bet.settings.strategy == "FIRST" else 1
            selected.bet.decision = {"choice": index, "id": selected.bet.outcomes[index]["id"],
                                     "amount": selected.bet.settings.max_points}
            sent.append(dict(selected.bet.decision))
        with patch.object(betting.time, "time", return_value=self.now):
            plan = betting.execute(event, settings or self.settings, submit, "FIRST", "SECOND",
                                   open_check or (lambda item: item.status == "ACTIVE"))
        return plan, sent

    def test_explicit_enable_selects_rule_and_exact_ten_point_stake(self):
        event = self.event()
        shared = event.bet.settings
        plan, sent = self.execute(event)
        self.assertEqual((plan["mode"], plan["stake"]), ("live", 10))
        self.assertEqual(sent, [{"choice": 0, "id": "yes", "amount": 10}])
        self.assertEqual((shared.strategy, shared.max_points), ("SMART", 500))

    def test_other_rule_is_used_without_falling_back_to_the_paper_primary(self):
        plan, sent = self.execute(self.event(), {**self.settings, "bet_strategy": "most_points"})
        self.assertEqual(plan["choice_id"], "no")
        self.assertEqual(sent[0]["id"], "no")

    def test_off_master_off_and_unselected_channels_never_submit(self):
        for index, settings in enumerate([{**self.settings, "live_betting": False},
                                          {**self.settings, "predictions": False},
                                          {**self.settings, "bet_channels": ["another"]}]):
            _, sent = self.execute(self.event(str(index)), settings)
            self.assertEqual(sent, [])

    def test_ties_do_not_switch_to_another_rule(self):
        tied = [{**outcome, "total_users": 50} for outcome in self.outcomes]
        plan, sent = self.execute(self.event(outcomes=tied))
        self.assertEqual(plan["stake"], 0)
        self.assertIn("tied", plan["reason"])
        self.assertEqual(sent, [])

    def test_reserve_balance_percentage_and_configured_caps_apply(self):
        for index, balance in enumerate([1000, 1900]):
            plan, sent = self.execute(self.event(str(index), balance=balance))
            self.assertEqual((plan["stake"], sent), (0, []))
        plan, sent = self.execute(self.event("cap"), {**self.settings, "bet_max_points": 5})
        self.assertEqual((plan["stake"], sent), (0, []))

    def test_duplicate_and_restart_restore_stake_without_resubmitting(self):
        event = self.event()
        self.assertEqual(len(self.execute(event)[1]), 1)
        store.init_database()
        restarted = self.event()
        plan, sent = self.execute(restarted)
        self.assertTrue(plan["duplicate"])
        self.assertEqual(sent, [])
        self.assertEqual(restarted.bet.decision["amount"], 10)

    def test_only_one_unsettled_attempt_per_channel(self):
        self.assertEqual(self.reserve("first")["stake"], 10)
        second = self.reserve("second")
        self.assertEqual(second["stake"], 0)
        self.assertIn("previous", second["reason"])
        betting.close("viewer", "first", now=self.now + 1)
        self.assertEqual(self.reserve("third", now=self.now + 2)["stake"], 10)

    def test_budget_counts_unconfirmed_attempts_and_resets_at_berlin_midnight(self):
        late = int(datetime(2026, 10, 27, 23, 59, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        for index in range(10):
            identity = str(index)
            self.assertEqual(self.reserve(identity, now=late)["stake"], 10)
            betting.close("viewer", identity, now=late + 1)
        blocked = self.reserve("over", now=late + 2)
        self.assertEqual(blocked["stake"], 0)
        self.assertIn("budget", blocked["reason"])
        self.assertEqual(self.reserve("tomorrow", now=late + 120)["stake"], 10)

    def test_three_confirmed_losses_pause_live_betting(self):
        for index in range(3):
            store.record_prediction("viewer", {"id": str(index), "channel": "channel", "title": "Loss",
                "status": "RESOLVED", "stake": 10, "confirmed": True, "result": "LOSE", "profit": -10},
                at=self.now - index)
        plan = self.reserve()
        self.assertEqual(plan["stake"], 0)
        self.assertIn("24-hour", plan["reason"])

    def test_window_closing_during_reservation_never_reaches_submitter(self):
        checks = iter([True, False])
        plan, sent = self.execute(self.event(), open_check=lambda item: next(checks))
        self.assertEqual(sent, [])
        self.assertIn("closed", plan["reason"])
        with store.database(readonly=True) as connection:
            self.assertIsNotNone(connection.execute("SELECT closed_at FROM bet_decisions").fetchone()[0])

    def test_stale_snapshots_cannot_erase_confirmed_stake_or_result(self):
        event = {"id": "saved", "channel": "channel", "title": "Saved", "status": "RESOLVED",
                 "choice": "Win", "stake": 10, "confirmed": True, "result": "WIN", "profit": 5}
        store.record_prediction("viewer", event)
        store.record_prediction("viewer", {**event, "status": "ACTIVE", "stake": 0,
            "choice": "Lose", "confirmed": False, "result": None, "profit": None})
        row = store.dashboard()["predictions"][0]
        self.assertEqual((row["stake"], row["choice"], row["confirmed"], row["result"], row["profit"]),
                         (10, "Win", 1, "WIN", 5))

    def test_only_confirmations_count_as_actual_bets_and_paper_choice_can_change(self):
        event = {"id": "saved", "channel": "channel", "title": "Saved", "status": "ACTIVE",
                 "choice": "Win", "stake": 0, "confirmed": False}
        store.record_prediction("viewer", event)
        store.record_prediction("viewer", {**event, "stake": 10, "choice": "Lose"})
        self.assertEqual(store.dashboard()["totals"]["betting"]["bets"], 0)
        store.record_prediction("viewer", {**event, "stake": 10, "choice": "Lose", "confirmed": True})
        snapshot = store.dashboard()
        self.assertEqual(snapshot["totals"]["betting"]["bets"], 1)
        self.assertEqual(snapshot["predictions"][0]["choice"], "Lose")
        self.assertTrue(store.resolve_prediction("viewer", "saved", {"type": "WIN", "points_won": 25}))
        self.assertEqual(store.dashboard()["totals"]["betting"]["profit"], 15)

    def test_config_defaults_off_and_rejects_unconfigured_live_channels(self):
        self.assertFalse(store.validate_config({"username": "viewer", "channels": ["channel"]})["live_betting"])
        for changes in [{"bet_channels": []}, {"bet_channels": ["unknown"]}, {"live_betting": "yes"}, {"bet_strategy": "SMART"}]:
            with self.assertRaises(ValueError):
                store.validate_config({**self.settings, **changes})

    def test_confirmation_after_crash_recovers_record_from_the_reservation(self):
        self.assertEqual(self.reserve("crash")["stake"], 10)
        self.assertEqual(store.dashboard()["totals"]["betting"]["bets"], 0)
        store.init_database()
        self.assertTrue(betting.confirm("viewer", "crash"))
        self.assertTrue(store.resolve_prediction("viewer", "crash", {"type": "WIN", "points_won": 25}))
        betting.close("viewer", "crash")
        self.assertEqual(store.dashboard()["totals"]["betting"]["profit"], 15)
        self.assertEqual(store.dashboard()["totals"]["betting"]["bets"], 1)
        self.assertFalse(betting.confirm("viewer", "unknown"))


if __name__ == "__main__":
    unittest.main()
