"""Integration tests use a temporary runtime directory, never the live databases."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import twitch_store as store
from twitch_api import twitch


class TwitchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"TWITCH_DATA_DIR": self.folder.name})
        self.environment.start()
        self.settings = store.validate_config({"username": "test_viewer", "channels": ["test_channel"]})
        store.write_json("config.json", self.settings)
        store.init_database()
        self.app = Flask(__name__)
        self.app.secret_key = "unit-test-session-key"
        self.app.register_blueprint(twitch)
        self.client = self.app.test_client()

    def tearDown(self):
        self.environment.stop()
        self.folder.cleanup()

    def login(self):
        with self.client.session_transaction() as session:
            session["is_admin"] = True

    def sample(self, balance, at=None):
        store.record_channel("test_viewer", "test_channel", balance=balance, online=True,
                             title="A test stream", game="A game", watched_minutes=1, at=at)

    def test_first_balance_is_baseline_and_restarts_do_not_reset_totals(self):
        self.sample(50000)
        self.sample(50050)
        store.init_database()
        self.sample(49950)
        row = store.dashboard()["channels"][0]
        self.assertEqual((row["baseline"], row["balance"], row["earned"], row["spent"]),
                         (50000, 49950, 50, 100))
        self.assertEqual(row["watched_minutes"], 3)

    def test_unknown_balance_is_not_a_fake_zero(self):
        self.assertIsNone(self.client.get("/api/twitch").json["channels"][0]["balance"])

    def test_public_stats_never_expose_auth_or_unknown_status_fields(self):
        store.write_json("credentials.json", {"access_token": "PRIVATE_TOKEN"})
        store.write_json("auth-status.json", {"code": "PRIVATE_CODE", "url": "https://www.twitch.tv/activate"})
        store.write_json("status.json", {"state": "needs_auth", "updated_at": time.time(),
                                        "access_token": "PRIVATE_TOKEN", "code": "PRIVATE_CODE"})
        response = self.client.get("/api/twitch")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("PRIVATE", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_private_endpoints_require_admin(self):
        for path in ["/api/twitch/settings", "/api/twitch/auth"]:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post("/api/twitch/settings", json=self.settings).status_code, 403)

    def test_admin_gets_activation_code_without_tokens(self):
        store.write_json("auth-status.json", {"code": "TESTCODE", "access_token": "PRIVATE_TOKEN"})
        self.login()
        response = self.client.get("/api/twitch/auth")
        self.assertEqual(response.json["code"], "TESTCODE")
        self.assertNotIn("PRIVATE_TOKEN", response.text)

    def test_admin_save_requires_form_header_and_same_origin(self):
        self.login()
        self.assertEqual(self.client.post("/api/twitch/settings", json=self.settings).status_code, 403)
        self.assertEqual(self.client.post("/api/twitch/settings", json=self.settings,
            headers={"X-Ahripi-Request":"1", "Origin":"https://other.example"}).status_code, 403)
        response = self.client.post("/api/twitch/settings", json=self.settings,
                                   headers={"X-Ahripi-Request":"1"})
        self.assertEqual(response.status_code, 200)

    def test_invalid_settings_do_not_overwrite_working_configuration(self):
        self.login()
        for value in [None, [], {**self.settings, "channels":["../../private"]},
                      {**self.settings,"bet_percentage":True},
                      {**self.settings,"bet_max_points":100000},
                      {**self.settings,"predictions":"true"}]:
            response = self.client.post("/api/twitch/settings", json=value,
                                       headers={"X-Ahripi-Request":"1"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(store.config(), self.settings)

    def test_channel_urls_are_normalized_and_deduplicated(self):
        settings = store.validate_config({"username":"Test_Viewer", "channels":[
            "https://www.twitch.tv/Test_Channel/", "test_channel"]})
        self.assertEqual(settings["channels"], ["test_channel"])

    def test_predictions_are_updated_not_duplicated_and_profit_survives_restart(self):
        event = {"id":"prediction1", "channel":"test_channel", "title":"Win this game?",
                 "status":"ACTIVE", "choice":"Yes", "stake":100, "confirmed":False}
        store.record_prediction("test_viewer", event)
        self.assertEqual(store.dashboard()["totals"]["betting"]["bets"], 0)
        store.record_prediction("test_viewer", {**event,"confirmed":True})
        self.assertEqual(store.dashboard()["totals"]["betting"]["pending"], 1)
        store.record_prediction("test_viewer", {**event,"confirmed":True,"result":"WIN","profit":75})
        store.init_database()
        data = store.dashboard()
        self.assertEqual(len(data["predictions"]), 1)
        self.assertEqual(data["totals"]["betting"], {"bets":1,"wins":1,"losses":0,"profit":75,"pending":0})

    def test_history_is_public_bounded_and_rejects_unknown_channels(self):
        self.sample(10000, int(time.time()) - 600)
        self.sample(10020, int(time.time()))
        response = self.client.get("/api/twitch/history?channel=test_channel&hours=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([point["balance"] for point in response.json], [10000, 10020])
        self.assertEqual(self.client.get("/api/twitch/history?channel=unknown").status_code, 400)
        self.assertEqual(self.client.get("/api/twitch/history?channel=test_channel&hours=999999").status_code, 400)

    def test_confirmed_prediction_results_can_arrive_after_worker_restart(self):
        event = {"id":"restart_bet", "channel":"test_channel", "title":"Win?",
                 "status":"LOCKED", "choice":"Yes", "stake":100, "confirmed":True}
        for kind, payout, expected in [("WIN",175,75),("LOSE",0,-100),("REFUND",100,0)]:
            store.record_prediction("test_viewer", event)
            store.init_database()
            self.assertTrue(store.resolve_prediction("test_viewer", "restart_bet", {"type":kind,"points_won":payout}))
            self.assertEqual(store.dashboard()["predictions"][0]["profit"], expected)
        self.assertFalse(store.resolve_prediction("test_viewer", "unknown_event", {"type":"WIN","points_won":999}))

    def test_page_is_public_and_contains_no_configured_channel_or_credentials(self):
        response = self.client.get("/twitch/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("test_viewer", response.text)
        self.assertNotIn("test_channel", response.text)
        response.close()

    def test_new_history_shows_minute_updates_even_on_the_24_hour_window(self):
        now = int(time.time())
        self.sample(10000, now - 120)
        self.sample(10020, now)
        response = self.client.get("/api/twitch/history?channel=test_channel&hours=24")
        self.assertEqual(len(response.json), 2)

    def test_config_permissions_are_private(self):
        self.assertEqual((store.data_dir() / "config.json").stat().st_mode & 0o777, 0o600)

    def test_all_time_retains_samples_older_than_thirty_days(self):
        path = "/api/twitch/history?channel=test_channel&hours=all"
        self.assertEqual(self.client.get(path).json, [])
        now = int(time.time())
        self.sample(10000, now - 120 * 86400)
        self.sample(10020, now)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([point["balance"] for point in response.json], [10000, 10020])
        for hours in (1, 24, 168):
            response = self.client.get(f"/api/twitch/history?channel=test_channel&hours={hours}")
            self.assertEqual([point["balance"] for point in response.json], [10020])
        self.assertEqual(self.client.get("/api/twitch/history?channel=test_channel&hours=0").status_code, 400)

    def test_all_time_is_bounded_and_keeps_exact_first_and_last_readings(self):
        now = int(time.time())
        first = now - 120 * 86400
        self.sample(10000, first)
        with store.database() as connection:
            connection.executemany("INSERT INTO samples(account,channel,at,balance,delta) VALUES(?,?,?,?,?)",
                [("test_viewer", "test_channel", first + index * 10000, 10000 + index, 1)
                 for index in range(1, 1000)])
        self.sample(20000, now)
        response = self.client.get("/api/twitch/history?channel=test_channel&hours=all")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.json), 180)
        self.assertEqual(response.json[0], {"at": first, "balance": 10000})
        self.assertEqual(response.json[-1], {"at": now, "balance": 20000})
        times = [point["at"] for point in response.json]
        self.assertEqual(times, sorted(times))

    def test_all_time_preserves_visitor_history_redactions(self):
        names = self.redacted_channels()
        for index, name in enumerate(names, 1):
            response = self.client.get(f"/api/twitch/history?channel=redacted-channel-{index}&hours=all")
            self.assertEqual(response.status_code, 200)
            self.assertEqual([point["balance"] for point in response.json], [1000, 1020])
            self.assertEqual(self.client.get(f"/api/twitch/history?channel={name}&hours=all").status_code, 400)
        self.login()
        self.assertEqual(self.client.get(f"/api/twitch/history?channel={names[0]}&hours=all").status_code, 200)

    def redacted_channels(self):
        names = ["private_alpha", "private_beta"]
        self.settings = store.validate_config({**self.settings, "channels": ["test_channel", *names]})
        store.write_json("config.json", self.settings)
        store.write_json("visibility.json", {"redacted_channels": names})
        now = int(time.time())
        self.sample(50, now)
        for name in names:
            for at, balance in [(now - 120, 1000), (now, 1020)]:
                store.record_channel("test_viewer", name, balance=balance, online=True,
                                     title="Identifying stream title", game="Identifying game", at=at)
            store.record_prediction("test_viewer", {
                "id": name + "_event", "channel": name, "title": "Identifying prediction",
                "choice": "Identifying outcome", "stake": 10, "status": "ACTIVE", "confirmed": True,
            })
        store.write_json("status.json", {"state": "running", "updated_at": now,
                                        "message": "Checking PRIVATE_ALPHA and private_beta"})
        return names

    def test_visitor_redaction_covers_channels_predictions_learning_and_messages(self):
        names = self.redacted_channels()
        response = self.client.get("/api/twitch")
        self.assertEqual(response.status_code, 200)
        for identifying in [*names, "Identifying stream title", "Identifying game",
                            "Identifying prediction", "Identifying outcome"]:
            self.assertNotIn(identifying.lower(), response.text.lower())
        self.assertEqual(response.json["channels"][0]["name"], "test_channel")
        for index, channel in enumerate(response.json["channels"][1:], 1):
            self.assertEqual(channel["name"], f"redacted-channel-{index}")
            self.assertEqual(channel["display_name"], f"Redacted channel {index}")
            self.assertTrue(channel["redacted"])
            self.assertEqual(channel["balance"], 1020)
            self.assertIsNone(channel["title"])
            self.assertIsNone(channel["game"])
        self.assertTrue(all("event_id" not in event for event in response.json["predictions"]))
        self.assertEqual(response.json["learning"]["channels"][1]["name"], "redacted-channel-1")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_admin_keeps_original_details_and_totals_match_visitor_snapshot(self):
        names = self.redacted_channels()
        public = self.client.get("/api/twitch").json
        self.login()
        response = self.client.get("/api/twitch")
        self.assertEqual(response.json["totals"], public["totals"])
        self.assertEqual([row["name"] for row in response.json["channels"]], ["test_channel", *names])
        self.assertEqual(response.json["channels"][1]["game"], "Identifying game")
        self.assertEqual(response.json["predictions"][0]["title"], "Identifying prediction")
        self.assertEqual(store.config()["channels"], ["test_channel", *names])
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get("/api/twitch")
        for name in names:
            self.assertNotIn(name, response.text)

    def test_redacted_history_works_by_alias_and_real_names_require_admin(self):
        names = self.redacted_channels()
        for index, name in enumerate(names, 1):
            response = self.client.get(f"/api/twitch/history?channel=redacted-channel-{index}&hours=1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual([point["balance"] for point in response.json], [1000, 1020])
            response = self.client.get(f"/api/twitch/history?channel={name}&hours=1")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json, {"error": "Unknown channel."})
        self.assertEqual(self.client.get("/api/twitch/history?channel=test_channel").status_code, 200)
        self.login()
        for name in names:
            self.assertEqual(self.client.get(f"/api/twitch/history?channel={name}").status_code, 200)

    def test_invalid_visibility_file_never_falls_back_to_exposing_names(self):
        self.redacted_channels()
        for content in ["{broken", "[]", '{"redacted_channels": "invalid"}']:
            (store.data_dir() / "visibility.json").write_text(content)
            self.assertEqual(self.client.get("/api/twitch").status_code, 503)
            self.assertEqual(self.client.get("/api/twitch/history?channel=private_alpha").status_code, 400)
        self.login()
        self.assertEqual(self.client.get("/api/twitch").status_code, 200)

    def test_paper_comparison_choices_respect_visitor_redactions(self):
        import twitch_experiments as paper
        names = self.redacted_channels()
        outcomes = [{"id": "yes", "title": "Identifying paper choice", "total_users": 70, "total_points": 40},
                    {"id": "no", "title": "Other identifying choice", "total_users": 30, "total_points": 60}]
        paper.record("test_viewer", names[0] + "_event", names[0], "Private question", outcomes)
        response = self.client.get("/api/twitch")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Identifying paper choice", response.text)
        self.assertNotIn("Other identifying choice", response.text)
        self.assertTrue(all(name not in response.text for name in names))
        self.assertEqual(response.json["experiment"]["events"], 1)
        self.login()
        response = self.client.get("/api/twitch")
        self.assertIn("Identifying paper choice", response.text)

    def test_live_policy_keeps_redacted_channel_names_private(self):
        names = self.redacted_channels()
        settings = store.validate_config({**self.settings, "live_betting": True, "bet_channels": [names[0]]})
        store.write_json("config.json", settings)
        response = self.client.get("/api/twitch")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(names[0], response.text)
        self.assertEqual(response.json["policy"]["bet_channels"], ["redacted-channel-1"])
        self.assertEqual(response.json["live_betting"]["channels"][0]["name"], "redacted-channel-1")


if __name__ == "__main__":
    unittest.main()
