"""Synthetic, chronological prediction streams; no Twitch login or live stakes."""

import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import twitch_store as store
import twitch_learning as learner


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"TWITCH_DATA_DIR":self.folder.name})
        self.environment.start()
        store.init_database()
        self.settings = store.validate_config({"username":"viewer", "channels":["channel"]})
        store.write_json("config.json", self.settings)
        self.start = int(time.time()) // 86400 * 86400 + 3600
        self.outcomes = [{"id":"yes","title":"Win","total_users":70,"total_points":40},
                         {"id":"no","title":"Lose","total_users":30,"total_points":60}]

    def tearDown(self):
        self.environment.stop()
        self.folder.cleanup()

    def decide(self, event, now, **overrides):
        arguments = dict(account="viewer",event_id=event,channel="channel",title="Pistol round?",
                         game="VALORANT",outcomes=self.outcomes,balance=100000,
                         settings=self.settings,now=now)
        arguments.update(overrides)
        return learner.decide(**arguments)

    def resolve(self, event, now, winner="yes", **overrides):
        payload = {"id":event,"status":"RESOLVED","winning_outcome_id":winner,"outcomes":self.outcomes}
        payload.update(overrides)
        return learner.resolve_event("viewer",payload,now)

    def train(self, count=20):
        # All seeds are paper: even this deliberately excellent fixture may not
        # spend points while creating the historical evidence under test.
        for index in range(count):
            event, now = f"trial{index}", self.start + index*120
            self.decide(event,now,settings={**self.settings,"predictions":False})
            self.resolve(event,now+60)
        return self.start+count*120

    def test_twenty_paper_trials_before_any_real_stake(self):
        for index in range(20):
            event, now = f"warmup{index}",self.start+index*120
            plan=self.decide(event,now)
            self.assertEqual((plan["mode"],plan["stake"]),("paper",0))
            self.resolve(event,now+60)
        plan=self.decide("eligible",self.start+2400)
        self.assertEqual((plan["mode"],plan["stake"]),("live",10))

    def test_evidence_is_not_shared_across_channels_accounts_games_or_questions(self):
        now=self.train()
        for changes in [{"channel":"another"},{"account":"another"},{"game":"Chess"},
                        {"title":"Completely different question?"}]:
            plan=self.decide(str(changes),now,**changes)
            self.assertEqual(plan["mode"],"paper")

    def test_duplicate_decision_and_restart_cannot_resubmit_stake(self):
        now=self.train()
        first=self.decide("once",now)
        self.assertEqual(first["stake"],10)
        store.init_database()
        again=self.decide("once",now+1)
        self.assertEqual((again["mode"],again["stake"]),("paper",0))
        other=self.decide("second",now+2)
        self.assertIn("earlier real stake",other["reason"])

    def test_budget_is_atomic_and_counts_reserved_even_unconfirmed_stakes(self):
        now=self.train()
        for index in range(10):
            event=f"live{index}"
            plan=self.decide(event,now+index*120)
            self.assertEqual((plan["mode"],plan["stake"]),("live",10))
            self.resolve(event,now+index*120+60)
        plan=self.decide("overbudget",now+1200)
        self.assertEqual(plan["stake"],0)
        self.assertIn("budget",plan["reason"])

    def test_reserve_percentage_and_hard_ceiling_are_enforced(self):
        now=self.train(100)
        plan=self.decide("confident",now,settings={**self.settings,"bet_max_points":10000})
        self.assertEqual(plan["stake"],50)
        self.resolve("confident",now+1)
        plan=self.decide("lowbalance",now+2,balance=1000)
        self.assertEqual(plan["stake"],0)
        self.assertIn("reserve",plan["reason"])

    def test_overpriced_outcome_does_not_pass_on_high_win_rate_alone(self):
        now=self.train()
        expensive=[{**self.outcomes[0],"total_points":99999},{**self.outcomes[1],"total_points":1}]
        plan=self.decide("expensive",now,outcomes=expensive)
        self.assertEqual((plan["mode"],plan["stake"]),("paper",0))
        self.assertIn("edge",plan["reason"])

    def test_cancelled_unresolved_and_unknown_winners_never_train(self):
        self.decide("pending",self.start)
        self.assertFalse(self.resolve("pending",self.start+1,winner="unknown"))
        self.assertFalse(self.resolve("pending",self.start+1,status="LOCKED"))
        self.assertTrue(self.resolve("pending",self.start+2,status="CANCELED"))
        self.assertFalse(self.resolve("pending",self.start+3))
        progress=learner.summary("viewer",["channel"])["channels"][0]
        self.assertEqual(progress["trials"],0)

    def test_entry_snapshot_and_winner_cannot_be_rewritten(self):
        self.decide("fixed",self.start)
        self.assertTrue(self.resolve("fixed",self.start+60))
        self.assertFalse(self.resolve("fixed",self.start+61,winner="no"))
        progress=learner.summary("viewer",["channel"])["channels"][0]
        self.assertEqual((progress["wins"],progress["paper_profit"]),(1,12))

    def test_future_and_stale_results_do_not_inflate_confidence(self):
        now=self.train()
        self.assertEqual(self.decide("past",self.start-1)["mode"],"paper")
        self.assertEqual(self.decide("old",now+91*86400)["mode"],"paper")

    def test_no_bet_when_three_confirmed_losses_trigger_cooldown(self):
        now=self.train()
        for index in range(3):
            event=f"loss{index}"
            plan=self.decide(event,now+index*120)
            self.assertEqual(plan["mode"],"live")
            self.resolve(event,now+index*120+60,winner="no")
            store.record_prediction("viewer",{"id":event,"channel":"channel","title":"Pistol round?",
                "status":"RESOLVED","stake":10,"confirmed":True,"result":"LOSE","profit":-10},at=now+index*120+60)
        plan=self.decide("cooldown",now+400)
        self.assertEqual(plan["stake"],0)
        self.assertIn("cooldown",plan["reason"])

    def test_missing_market_and_multichoice_fail_closed(self):
        for outcomes in [[],self.outcomes+[dict(self.outcomes[0])],
                         [{**row,"total_users":0} for row in self.outcomes]]:
            with self.assertRaises(ValueError):
                self.decide("invalid",self.start,outcomes=outcomes)

    def test_wilson_bound_penalizes_small_samples(self):
        self.assertEqual(learner.wilson_lower(0,0),0)
        self.assertLess(learner.wilson_lower(1,1),0.3)
        self.assertAlmostEqual(learner.wilson_lower(20,20),0.83887,places=4)

    def test_numeric_question_thresholds_are_not_conflated(self):
        self.assertNotEqual(learner.normalize("Get 10 kills?"),learner.normalize("Get 30 kills?"))

    def test_simulated_wins_never_count_as_real_profit(self):
        self.train()
        store.record_prediction("viewer",{"id":"trial0","channel":"channel","title":"Pistol round?",
            "status":"RESOLVED","stake":0,"confirmed":False})
        data=store.dashboard()
        self.assertEqual(data["totals"]["betting"]["profit"],0)
        self.assertEqual(data["totals"]["betting"]["bets"],0)
        self.assertEqual(data["predictions"][0]["trial_outcome"],"WIN")
        self.assertEqual(data["learning"]["channels"][0]["trials"],20)

    def event(self, identity):
        return SimpleNamespace(status="ACTIVE",event_id=identity,title="Pistol round?",
            streamer=SimpleNamespace(username="channel",channel_points=100000,
                                     stream=SimpleNamespace(game={"name":"VALORANT"})),
            bet=SimpleNamespace(outcomes=self.outcomes,decision={},
                                settings=SimpleNamespace(strategy="SMART",max_points=500)))

    def test_paper_and_invalid_data_never_reach_submission_callback(self):
        event=self.event("adapter-paper")
        submitted=[]
        learner.execute(event,self.settings,submitted.append,"FIRST","SECOND")
        self.assertEqual(submitted,[])
        self.assertEqual(event.bet.decision["amount"],0)
        event.bet.outcomes=[]
        with self.assertRaises(ValueError):
            learner.execute(event,self.settings,submitted.append,"FIRST","SECOND")
        self.assertEqual(submitted,[])

    def test_live_adapter_passes_bounded_stake_and_does_not_mutate_shared_settings(self):
        now=self.train()
        event=self.event("adapter-live")
        shared=event.bet.settings
        submitted=[]
        with patch.object(learner.time,"time",return_value=now):
            learner.execute(event,self.settings,submitted.append,"FIRST","SECOND")
        self.assertEqual(submitted,[event])
        self.assertEqual(event.bet.settings.max_points,10)
        self.assertEqual(event.bet.settings.strategy,"FIRST")
        self.assertEqual((shared.strategy,shared.max_points),("SMART",500))


if __name__=="__main__":
    unittest.main()
