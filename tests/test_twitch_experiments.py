"""Fixed paper strategies share pre-result data and never submit real stakes."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import twitch_store as store
import twitch_learning as legacy
import twitch_experiments as paper


class PaperComparisonTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"TWITCH_DATA_DIR": self.folder.name})
        self.environment.start()
        store.init_database()
        self.settings = store.validate_config({"username": "viewer", "channels": ["channel"]})
        store.write_json("config.json", self.settings)
        self.outcomes = [{"id": "yes", "title": "Win", "total_users": 70, "total_points": 40},
                         {"id": "no", "title": "Lose", "total_users": 30, "total_points": 60}]
        self.now = int(time.time())

    def tearDown(self):
        self.environment.stop()
        self.folder.cleanup()

    def record(self, identity="event", **overrides):
        args = dict(account="viewer", event_id=identity, channel="channel", title="Who wins?",
                    outcomes=self.outcomes, now=self.now)
        args.update(overrides)
        return paper.record(**args)

    def resolve(self, identity="event", **overrides):
        event = {"id": identity, "status": "RESOLVED", "winning_outcome_id": "yes", "outcomes": self.outcomes}
        event.update(overrides)
        return paper.resolve_event("viewer", event, now=self.now + 60)

    def scores(self):
        return {row["id"]: row for row in paper.summary("viewer", ["channel"])["strategies"]}

    def test_three_rules_freeze_distinct_choices_on_one_snapshot(self):
        choices = {row["strategy"]: row["choice_id"] for row in self.record()}
        self.assertEqual(choices, {"most_voters": "yes", "most_points": "no", "least_points": "yes"})
        with store.database(readonly=True) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_events").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_choices").fetchone()[0], 3)
        self.assertEqual(paper.summary("viewer", ["channel"])["events"], 1)

    def test_returns_and_roi_use_independent_ten_point_stakes(self):
        self.record()
        self.assertTrue(self.resolve())
        self.record("second")
        self.assertTrue(self.resolve("second", winning_outcome_id="no"))
        scores = self.scores()
        self.assertEqual((scores["most_voters"]["profit"], scores["most_voters"]["roi"]), (2, 10))
        self.assertEqual((scores["most_points"]["profit"], scores["most_points"]["roi"]), (-5, -25))
        self.assertEqual(scores["least_points"]["profit"], 2)
        self.assertTrue(all((row["wins"], row["losses"], row["settled"]) == (1, 1, 2) for row in scores.values()))

    def test_duplicates_and_restarts_do_not_rewrite_entry_or_result(self):
        original = self.record()
        store.init_database()
        changed = [{**self.outcomes[0], "total_users": 1}, {**self.outcomes[1], "total_users": 99}]
        self.assertEqual(self.record(outcomes=changed, now=self.now + 20), original)
        self.assertTrue(self.resolve())
        self.assertFalse(self.resolve(winning_outcome_id="no"))
        self.assertEqual(self.scores()["most_voters"]["profit"], 12)
        with store.database(readonly=True) as connection:
            saved = json.loads(connection.execute("SELECT snapshot FROM paper_events").fetchone()[0])
        self.assertEqual(saved[0]["users"], 70)

    def test_ties_are_skipped_independently_without_favoring_outcome_one(self):
        tied = [{**row, "total_users": 50} for row in self.outcomes]
        choices = {row["strategy"]: row["choice_id"] for row in self.record(outcomes=tied)}
        self.assertIsNone(choices["most_voters"])
        self.assertEqual(choices["most_points"], "no")
        self.assertTrue(self.resolve())
        self.assertEqual(self.scores()["most_voters"]["skipped"], 1)
        self.assertEqual(self.scores()["most_voters"]["settled"], 0)

    def test_missing_final_pools_exclude_every_rule_from_profit_and_roi(self):
        self.record()
        self.assertTrue(self.resolve(outcomes=[]))
        scores = self.scores()
        self.assertTrue(all(row["unpriced"] == 1 and row["profit"] is None and row["roi"] is None for row in scores.values()))
        self.assertEqual(scores["most_voters"]["wins"], 1)
        self.assertEqual(scores["most_points"]["losses"], 1)

    def test_unknown_winners_and_locked_events_do_not_settle(self):
        self.record()
        self.assertFalse(self.resolve(winning_outcome_id="unknown"))
        self.assertFalse(self.resolve(status="LOCKED"))
        self.assertTrue(all(row["pending"] == 1 for row in self.scores().values()))

    def test_cancelled_predictions_are_separate_from_wins_losses_and_roi(self):
        self.record()
        self.assertTrue(self.resolve(status="CANCELED"))
        self.assertTrue(all(row["cancelled"] == 1 and row["settled"] == 0 and row["roi"] is None for row in self.scores().values()))

    def test_disabled_and_invalid_markets_do_not_create_comparison_rows(self):
        event = SimpleNamespace(status="ACTIVE", event_id="off", title="Who wins?",
                                streamer=SimpleNamespace(username="channel"),
                                bet=SimpleNamespace(outcomes=self.outcomes, decision={}))
        self.assertIsNone(paper.execute(event, {**self.settings, "predictions": False}))
        for outcomes in [[], self.outcomes * 2, [{**row, "total_points": 0} for row in self.outcomes]]:
            with self.assertRaises(ValueError):
                self.record(outcomes=outcomes)
        self.assertEqual(paper.summary("viewer", ["channel"])["events"], 0)

    def test_successful_paper_history_never_promotes_or_inflates_real_stats(self):
        for index in range(25):
            self.record(str(index))
            self.resolve(str(index))
        event = SimpleNamespace(status="ACTIVE", event_id="next", title="Different question",
                                streamer=SimpleNamespace(username="channel"),
                                bet=SimpleNamespace(outcomes=self.outcomes, decision={}))
        plan = paper.execute(event, {**self.settings, "bet_max_points": 10000})
        self.assertEqual((plan["mode"], plan["stake"], event.bet.decision["amount"]), ("paper", 0, 0))
        store.record_prediction("viewer", {"id": "next", "channel": "channel", "title": event.title,
            "status": "ACTIVE", "choice": "Win", "stake": 0, "confirmed": False})
        snapshot = store.dashboard()
        self.assertEqual(snapshot["totals"]["betting"]["bets"], 0)
        self.assertEqual(snapshot["totals"]["betting"]["profit"], 0)
        self.assertEqual(len(snapshot["predictions"][0]["paper_choices"]), 3)
        self.assertEqual(snapshot["experiment"]["mode"], "paper_only")
        self.assertEqual(snapshot["learning"]["channels"][0]["trials"], 0)

    def test_previous_learner_rows_are_preserved_and_not_backfilled(self):
        legacy.decide("viewer", "old", "channel", "Who wins?", "Game", self.outcomes,
                      10000, {**self.settings, "predictions": False}, now=self.now)
        legacy.resolve_event("viewer", {"id": "old", "status": "RESOLVED", "winning_outcome_id": "yes", "outcomes": self.outcomes})
        with store.database(readonly=True) as connection:
            before = tuple(connection.execute("SELECT * FROM learning_trials").fetchone())
        self.assertEqual(paper.summary("viewer", ["channel"])["events"], 0)
        self.record()
        self.resolve()
        with store.database(readonly=True) as connection:
            after = tuple(connection.execute("SELECT * FROM learning_trials").fetchone())
        self.assertEqual(before, after)

    def test_comparison_is_scoped_to_account_and_configured_channels(self):
        self.record()
        self.record("other-channel", channel="another")
        self.record("other-account", account="another")
        self.assertEqual(paper.summary("viewer", ["channel"])["events"], 1)
        self.assertEqual(paper.summary("viewer", [])["events"], 0)
        self.assertEqual(paper.summary("absent", ["channel"])["events"], 0)


if __name__ == "__main__":
    unittest.main()
