import json
import math
import pathlib
import subprocess
import sys
import tempfile
import unittest

from strategies.research import Candle, Config, experiment, load_snapshot, run_backtest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def candles_from_prices(prices, opens=None):
    opens = prices if opens is None else opens
    return [
        Candle(1704067200 + index * 86400, opening, max(opening, close), min(opening, close), close)
        for index, (opening, close) in enumerate(zip(opens, prices, strict=True))
    ]


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.prices = [100, 100, 100, 120, 120, 80, 80, 80]
        self.opens = [100, 100, 100, 110, 150, 110, 90, 80]
        self.candles = candles_from_prices(self.prices, self.opens)

    def test_fill_is_next_open_and_includes_both_costs(self):
        result = run_backtest(self.candles, Config(2, 3), 4)
        trade, = result["trades"]
        self.assertEqual(trade["signal_date"], "2024-01-04")
        self.assertEqual(trade["entry_date"], "2024-01-05")
        self.assertEqual(trade["exit_date"], "2024-01-07")
        expected_final = 10000 * 0.999 / (150 * 1.0005) * (90 * 0.9995) * 0.999
        self.assertAlmostEqual(result["summary"]["final"], expected_final, places=5)
        self.assertAlmostEqual(trade["pnl"], expected_final - 10000)
        self.assertEqual(result["summary"]["completed_trades"], 1)
        self.assertEqual(result["summary"]["win_rate_pct"], 0)

    def test_current_and_future_closes_cannot_change_entry_at_todays_open(self):
        altered = self.prices[:4] + [1, 1000, 1, 1000]
        a = run_backtest(self.candles, Config(2, 3), 4)["trades"][0]
        b = run_backtest(candles_from_prices(altered, self.opens), Config(2, 3), 4)["trades"][0]
        for field in ("signal_date", "entry_date", "entry_price", "quantity"):
            self.assertEqual(a[field], b[field], field)

    def test_close_loss_waits_for_following_open_and_allows_gap_loss(self):
        opens = self.opens[:]
        opens[5] = 60
        result = run_backtest(candles_from_prices(self.prices, opens), Config(2, 3, close_loss=0.10), 4)
        trade, = result["trades"]
        self.assertEqual(trade["reason"], "close_loss")
        self.assertEqual(trade["exit_date"], "2024-01-06")
        self.assertLess(trade["return_pct"], -50)

    def test_volatility_filter_skips_entry_without_delayed_purchase(self):
        result = run_backtest(self.candles, Config(2, 3, max_daily_vol=0.01, vol_window=2), 4)
        self.assertEqual(result["summary"]["blocked_entries"], 1)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["summary"]["final"], 10000)

    def test_terminal_liquidation_is_costed_but_excluded_from_win_rate(self):
        result = run_backtest(self.candles, Config(2, 3), 4, 5, fee=0.01, slippage=0.02)
        expected = 10000 * 0.99 / (150 * 1.02) * (120 * 0.98) * 0.99
        self.assertAlmostEqual(result["summary"]["final"], expected, places=5)
        self.assertAlmostEqual(result["summary"]["max_dd_pct"], (1 - expected / 10000) * 100, places=3)
        self.assertIsNone(result["summary"]["win_rate_pct"])
        self.assertEqual(result["summary"]["completed_trades"], 0)
        self.assertEqual(result["summary"]["terminal_liquidations"], 1)
        self.assertTrue(result["trades"][0]["forced"])

    def test_holdout_starts_flat_without_inheriting_training_position(self):
        result = run_backtest(self.candles, Config(2, 3), 5)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["summary"]["final"], 10000)

    def test_holdout_prices_do_not_affect_training_or_selection(self):
        prices = [100, 100, 100, 120, 120, 80, 80, 80] * 10
        original = candles_from_prices(prices)
        configs = [Config(2, 3), Config(2, 4)]
        a, _ = experiment(original, configs)
        start = max(config.warmup for config in configs)
        split = start + int((len(original) - start) * 0.7)
        changed = prices[:split] + [prices[i] * (1 + (i - split) * 0.2) for i in range(split, len(prices))]
        b, _ = experiment(candles_from_prices(changed), configs)
        self.assertEqual(a["selected_on_train"], b["selected_on_train"])
        self.assertEqual([row["train"] for row in a["candidates"]], [row["train"] for row in b["candidates"]])
        self.assertNotEqual([row["holdout"] for row in a["candidates"]], [row["holdout"] for row in b["candidates"]])

    def test_snapshot_removes_unconfirmed_tail_and_rejects_bad_prices_or_gaps(self):
        raw = [[c.timestamp, c.open, c.high, c.low, c.close, 0, 1, 1] for c in self.candles]
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "snapshot.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(len(load_snapshot(path)), len(raw) - 1)
            for value in (0, -1, math.nan):
                broken = [row[:] for row in raw]
                broken[2][4] = value
                path.write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_snapshot(path)
            path.write_text(json.dumps(raw[:2] + raw[3:]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contiguous"):
                load_snapshot(path)

    def test_saved_experiment_is_reproducible_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run(
                [sys.executable, str(ROOT / "strategies/research.py"), "--output-root", temp],
                cwd=temp, check=True, stdout=subprocess.DEVNULL,
            )
            for relative in ("data/param_sweep.json", "data/research_trades.csv", "docs/策略迭代报告.md"):
                self.assertEqual(
                    (pathlib.Path(temp) / relative).read_text(encoding="utf-8"),
                    (ROOT / relative).read_text(encoding="utf-8"), relative,
                )


if __name__ == "__main__":
    unittest.main()
