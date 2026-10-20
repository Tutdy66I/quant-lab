import contextlib
import csv
import datetime
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from strategies import paper_trader


class PaperTraderTests(unittest.TestCase):
    def test_net_return_includes_both_sides_of_fees(self):
        result = paper_trader.net_return_pct(200, 1)

        self.assertAlmostEqual(result, -99.5009995)

    def test_log_trade_deduplicates_a_replayed_signal(self):
        row = {
            "time_utc": "2026-10-19 00:10:00",
            "date": "2026-10-18",
            "action": "模拟买入",
            "price": 100.0,
            "amount_btc": 1.0,
            "cash_after": 0.0,
            "reason": "test",
        }

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.multiple(
                paper_trader,
                DATA=temp,
                TRADES_CSV=os.path.join(temp, "paper_trades.csv"),
            ):
                self.assertTrue(paper_trader.log_trade(row))
                self.assertFalse(paper_trader.log_trade(row))
                with open(paper_trader.TRADES_CSV, newline="", encoding="utf-8") as trade_file:
                    trades = list(csv.DictReader(trade_file))

        self.assertEqual(len(trades), 1)

    def test_run_once_catches_up_crossovers_in_order(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        days = [today - datetime.timedelta(days=offset) for offset in range(71, 0, -1)]
        prices = [100.0] * 60 + [200.0] + [1.0] * 10
        candles = [
            (
                int(
                    datetime.datetime.combine(
                        day, datetime.time(), tzinfo=datetime.timezone.utc
                    ).timestamp()
                ),
                price,
            )
            for day, price in zip(days, prices, strict=True)
        ]

        with tempfile.TemporaryDirectory() as temp:
            state_path = os.path.join(temp, "paper_state.json")
            trades_path = os.path.join(temp, "paper_trades.csv")
            with open(state_path, "w", encoding="utf-8") as state_file:
                json.dump(
                    {
                        "cash": 10000.0,
                        "pos": 0.0,
                        "in_pos": False,
                        "entry_px": 0,
                        "entry_date": "",
                        "last_signal_date": days[59].isoformat(),
                    },
                    state_file,
                )

            with (
                mock.patch.multiple(
                    paper_trader,
                    DATA=temp,
                    STATE_JSON=state_path,
                    TRADES_CSV=trades_path,
                ),
                mock.patch.object(paper_trader, "fetch_daily", return_value=candles),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                paper_trader.run_once()

            with open(trades_path, newline="", encoding="utf-8") as trades_file:
                trades = list(csv.DictReader(trades_file))
            with open(state_path, encoding="utf-8") as state_file:
                state = json.load(state_file)

        self.assertEqual([trade["action"] for trade in trades], ["模拟买入", "模拟卖出"])
        self.assertIn("本笔净收益-99.50%", trades[1]["reason"])
        self.assertAlmostEqual(state["cash"], 49.90005)
        self.assertFalse(state["in_pos"])
        self.assertEqual(state["last_signal_date"], days[-1].isoformat())

    def test_run_once_refuses_to_catch_up_when_state_is_outside_history(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        candles = [
            (
                int(
                    datetime.datetime.combine(
                        today - datetime.timedelta(days=offset),
                        datetime.time(),
                        tzinfo=datetime.timezone.utc,
                    ).timestamp()
                ),
                100.0,
            )
            for offset in range(62, 0, -1)
        ]

        with tempfile.TemporaryDirectory() as temp:
            state_path = os.path.join(temp, "paper_state.json")
            with open(state_path, "w", encoding="utf-8") as state_file:
                json.dump({"last_signal_date": "2020-01-01"}, state_file)
            with (
                mock.patch.multiple(
                    paper_trader,
                    DATA=temp,
                    STATE_JSON=state_path,
                    TRADES_CSV=os.path.join(temp, "paper_trades.csv"),
                ),
                mock.patch.object(paper_trader, "fetch_daily", return_value=candles),
            ):
                with self.assertRaisesRegex(RuntimeError, "cannot safely catch up"):
                    paper_trader.run_once()

    def test_fetch_daily_excludes_the_open_utc_candle(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        rows = []
        for offset in range(61, -1, -1):
            day = today - datetime.timedelta(days=offset)
            timestamp = int(
                datetime.datetime.combine(
                    day, datetime.time(), tzinfo=datetime.timezone.utc
                ).timestamp()
            )
            rows.append([timestamp, "100", "101", "99", "100", "100", "1", 1])
        response = io.BytesIO(
            json.dumps({"error": [], "result": {"XXBTZUSD": rows, "last": 1}}).encode()
        )

        with mock.patch.object(paper_trader.urllib.request, "urlopen", return_value=response):
            candles = paper_trader.fetch_daily()

        self.assertEqual(len(candles), 61)
        self.assertEqual(
            datetime.datetime.fromtimestamp(candles[-1][0], datetime.timezone.utc).date(),
            today - datetime.timedelta(days=1),
        )

    def test_fetch_daily_raises_on_api_errors(self):
        response = io.BytesIO(
            json.dumps({"error": ["EGeneral:Temporary lock"], "result": {}}).encode()
        )

        with mock.patch.object(paper_trader.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Kraken API error"):
                paper_trader.fetch_daily()

    def test_scheduler_targets_utc_0010(self):
        before = datetime.datetime(2026, 10, 19, 0, 9, tzinfo=datetime.timezone.utc)
        after = datetime.datetime(2026, 10, 19, 0, 11, tzinfo=datetime.timezone.utc)

        self.assertEqual(paper_trader.seconds_until_next_check(before), 60)
        self.assertEqual(paper_trader.seconds_until_next_check(after), 86340)

    def test_backtest_counts_only_completed_trades_and_applies_fees(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            run_root = pathlib.Path(temp)
            (run_root / "data").mkdir()
            (run_root / "strategies").mkdir()
            shutil.copy2(
                repo_root / "data" / "btc_usd_1d_raw.json",
                run_root / "data" / "btc_usd_1d_raw.json",
            )
            backtest_script = repo_root / "strategies" / "ma_cross_backtest.py"
            shutil.copy2(backtest_script, run_root / "strategies" / backtest_script.name)
            subprocess.run(
                [sys.executable, str(run_root / "strategies" / backtest_script.name)],
                cwd=run_root,
                check=True,
                stdout=subprocess.DEVNULL,
            )

            with open(run_root / "data" / "backtest_summary.json", encoding="utf-8") as summary_file:
                summary = json.load(summary_file)
            with open(run_root / "data" / "backtest_trades.csv", newline="", encoding="utf-8") as trades_file:
                trades = list(csv.DictReader(trades_file))

        completed_trades = [trade for trade in trades if trade["action"] == "卖出"]
        profitable_trades = [
            trade for trade in completed_trades if float(trade["return_pct"]) > 0
        ]
        self.assertEqual(summary["n_trades"], 6)
        self.assertEqual(len(profitable_trades), 2)
        self.assertEqual(summary["win_rate_pct"], 33.3)
        self.assertIn("净收益", profitable_trades[0]["reason"])

    def test_backtest_notebook_runs_top_to_bottom(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        notebook_path = repo_root / "notebooks" / "btc_ma_cross_backtest.ipynb"
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        namespace = {}
        original_cwd = os.getcwd()
        try:
            os.chdir(notebook_path.parent)
            with contextlib.redirect_stdout(io.StringIO()):
                for cell in notebook["cells"]:
                    if cell["cell_type"] == "code":
                        exec("".join(cell["source"]), namespace)
        finally:
            os.chdir(original_cwd)

        self.assertEqual(len(namespace["closed_returns"]), 6)
        self.assertEqual(sum(result > 0 for result in namespace["closed_returns"]), 2)


if __name__ == "__main__":
    unittest.main()

