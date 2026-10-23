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


if __name__ == "__main__":
    unittest.main()
