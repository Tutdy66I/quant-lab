import concurrent.futures
import contextlib
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from strategies import market_data, paper_trader as trader
from strategies.research import Config, run_backtest


class PaperRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.prices = [100.0] * 60 + [200.0] + [1.0] * 22
        self.rows = []
        for index, close in enumerate(self.prices):
            opening = 150.0 if index == 61 else 90.0 if index == 63 else close
            self.rows.append([1704067200 + index * 86400, opening, max(opening, close), min(opening, close), close, close, 1, 1])

    def observation(self, count):
        now = datetime.fromtimestamp(self.rows[count - 1][0], timezone.utc) + timedelta(minutes=10)
        return now, market_data.snapshot_from_rows(self.rows[:count], now)

    def run_observation(self, count):
        now, snapshot = self.observation(count)
        with contextlib.redirect_stdout(io.StringIO()):
            return trader.run_once(self.directory, now, snapshot)

    def records(self, table):
        with trader.database(self.directory) as db:
            return [dict(row) for row in db.execute(f"SELECT * FROM {table}")]

    def test_catchup_matches_research_and_restarts_do_not_duplicate(self):
        self.run_observation(62)
        result = self.run_observation(len(self.rows))
        self.assertEqual(result["processed_days"], len(self.rows) - 62)
        self.assertEqual(self.run_observation(len(self.rows))["processed_days"], 0)
        research = run_backtest(market_data.parse_daily_rows(self.rows), Config(), 61)
        state = json.loads((self.directory / "state.json").read_text())
        self.assertAlmostEqual(state["cash"], research["summary"]["final"], places=5)
        self.assertEqual(state["pos"], 0)
        with (self.directory / "trades.csv").open(newline="", encoding="utf-8") as handle:
            fills = list(csv.DictReader(handle))
        self.assertEqual([row["action"] for row in fills], ["buy", "sell"])
        self.assertEqual(fills[0]["execution_date"], research["trades"][0]["entry_date"])
        self.assertEqual(fills[1]["execution_date"], research["trades"][0]["exit_date"])
        self.assertAlmostEqual(float(fills[1]["return_pct"]), research["trades"][0]["return_pct"])
        self.assertEqual(fills[1]["mode"], "daily_open_model")
        self.assertGreater(float(fills[1]["delay_seconds"]), 86400)

    def test_no_trade_day_still_records_health_equity_and_snapshot(self):
        now, snapshot = self.observation(len(self.rows))
        health = self.run_observation(len(self.rows))
        self.assertEqual(self.records("trades"), [])
        self.assertEqual(len(self.records("daily")), 1)
        stored = json.loads((self.directory / health["snapshot"]).read_text())
        self.assertEqual(stored, snapshot)
        self.assertEqual(health["latest_closed_date"], datetime.fromtimestamp(self.rows[-2][0], timezone.utc).date().isoformat())
        self.assertEqual(trader.read_status(self.directory, now)["status"], "ok")
        self.assertEqual(trader.read_status(self.directory, now + timedelta(days=2))["status"], "overdue")

    def test_current_close_does_not_influence_current_open_fill(self):
        now, snapshot = self.observation(62)
        snapshot["rows"][-1][2:5] = [10000, 150, 10000]
        snapshot = market_data.snapshot_from_rows(snapshot["rows"], now)
        with contextlib.redirect_stdout(io.StringIO()):
            trader.run_once(self.directory, now, snapshot)
        fill = json.loads(self.records("trades")[0]["payload"])
        self.assertEqual(fill["action"], "buy")
        self.assertAlmostEqual(fill["fill_price"], 150 * 1.0005)

    def test_disconnect_preserves_account_and_next_success_is_recovered(self):
        health = self.run_observation(62)
        before = (self.directory / "state.json").read_bytes()
        now, snapshot = self.observation(63)
        with mock.patch.object(trader, "fetch_snapshot", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                trader.run_once(self.directory, now)
        self.assertEqual(before, (self.directory / "state.json").read_bytes())
        error = trader.read_status(self.directory, now)
        self.assertEqual(error["status"], "error")
        self.assertEqual(error["last_success_at"], health["observed_at"])
        with contextlib.redirect_stdout(io.StringIO()):
            recovered = trader.run_once(self.directory, now, snapshot)
        self.assertEqual(recovered["status"], "recovered")

    def test_transaction_failure_rolls_back_fill_and_checkpoint_together(self):
        now, snapshot = self.observation(62)
        with mock.patch.object(trader, "set_meta", side_effect=RuntimeError("simulated interruption")):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                trader.run_once(self.directory, now, snapshot)
        self.assertEqual(self.records("trades"), [])
        self.assertEqual(self.records("daily"), [])
        self.run_observation(62)
        self.assertEqual(len(self.records("trades")), 1)

    def test_export_failure_after_commit_is_repairable_without_retrading(self):
        now, snapshot = self.observation(62)
        with mock.patch.object(trader, "export_views", side_effect=[OSError("projection failed"), None]):
            with self.assertRaisesRegex(OSError, "projection"):
                trader.run_once(self.directory, now, snapshot)
        self.assertEqual(len(self.records("trades")), 1)
        health = self.run_observation(62)
        self.assertEqual(health["processed_days"], 0)
        self.assertEqual(len(self.records("trades")), 1)
        self.assertTrue((self.directory / "trades.csv").exists())

    def test_concurrent_checks_commit_each_fill_once(self):
        now, snapshot = self.observation(62)
        with contextlib.redirect_stdout(io.StringIO()), concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: trader.run_once(self.directory, now, snapshot), range(2)))
        self.assertEqual(sum(row["processed_days"] for row in results), 1)
        self.assertEqual(len(self.records("trades")), 1)

    def test_confirmed_daily_mark_is_finalized_on_next_day(self):
        self.run_observation(62)
        first = self.records("daily")[0]
        self.assertEqual(first["final"], 0)
        self.run_observation(63)
        daily = self.records("daily")
        self.assertEqual(daily[0]["final"], 1)
        self.assertEqual(daily[1]["final"], 0)

    def test_bad_fingerprint_stale_capture_and_stale_bars_do_not_trade(self):
        now, snapshot = self.observation(62)
        snapshot["metadata"]["sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            trader.run_once(self.directory, now, snapshot)
        now, snapshot = self.observation(62)
        snapshot["metadata"]["fetched_at"] = (now - timedelta(hours=2)).isoformat()
        with self.assertRaisesRegex(ValueError, "capture time"):
            trader.run_once(self.directory, now, snapshot)
        now, snapshot = self.observation(62)
        with self.assertRaisesRegex(ValueError, "Stale or future"):
            trader.run_once(self.directory, now + timedelta(days=1), snapshot)
        self.assertEqual(self.records("trades"), [])

    def test_checkpoint_outside_history_and_model_change_require_attention(self):
        self.run_observation(62)
        with trader.database(self.directory) as db:
            state = trader.get_meta(db, "state", trader.INITIAL)
            state["last_execution_date"] = "2020-01-01"
            trader.set_meta(db, "state", state)
        with self.assertRaisesRegex(ValueError, "outside available history"):
            self.run_observation(63)
        with trader.database(self.directory) as db:
            trader.set_meta(db, "model", {**trader.MODEL, "fee": 0.1})
        with self.assertRaisesRegex(ValueError, "model has changed"):
            self.run_observation(63)

    def test_loop_retries_after_outage_and_accepts_graceful_stop(self):
        calls = 0
        def run(*_):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("offline")
            (self.directory / "STOP").touch()
        with (
            mock.patch.object(trader, "run_once", side_effect=run),
            mock.patch.object(trader.time, "monotonic", side_effect=[0, 61, 61]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            trader.run_loop(self.directory)
        self.assertEqual(calls, 2)

    def test_fetch_snapshot_rejects_api_errors_and_validates_pair(self):
        response = io.BytesIO(json.dumps({"error": ["EGeneral:Temporary lock"]}).encode())
        with mock.patch.object(market_data.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Kraken API error"):
                market_data.fetch_snapshot()
        now, expected = self.observation(62)
        response = io.BytesIO(json.dumps({"error": [], "result": {"last": 1, "XXBTZUSD": self.rows[:62]}}).encode())
        with mock.patch.object(market_data.urllib.request, "urlopen", return_value=response):
            self.assertEqual(market_data.fetch_snapshot(now), expected)

    def test_scheduler_aligns_hong_kong_and_utc_to_same_instant(self):
        before = datetime(2026, 10, 20, 8, 9, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(trader.seconds_until_next_check(before), 60)
        self.assertEqual(trader.seconds_until_next_check(before + timedelta(minutes=2)), 86340)


if __name__ == "__main__":
    unittest.main()
