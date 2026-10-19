import contextlib
import csv
import datetime
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from strategies import paper_trader


class PaperTraderTests(unittest.TestCase):
    def test_net_return_includes_both_sides_of_fees(self):
        result = paper_trader.net_return_pct(200, 1)

        self.assertAlmostEqual(result, -99.5009995)

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


if __name__ == "__main__":
    unittest.main()

