import contextlib
import csv
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from strategies.ma_cross_backtest import baseline_result


class BaselineTests(unittest.TestCase):
    def test_backtest_counts_only_completed_trades_and_applies_fees(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            run_root = pathlib.Path(temp)
            backtest_script = repo_root / "strategies" / "ma_cross_backtest.py"
            subprocess.run(
                [sys.executable, str(backtest_script), "--output-root", str(run_root)],
                cwd=run_root,
                check=True,
                stdout=subprocess.DEVNULL,
            )

            with open(run_root / "data" / "backtest_summary.json", encoding="utf-8") as summary_file:
                summary = json.load(summary_file)
            with open(run_root / "data" / "backtest_trades.csv", newline="", encoding="utf-8") as trades_file:
                trades = list(csv.DictReader(trades_file))
            for relative in ("data/backtest_summary.json", "data/backtest_trades.csv"):
                self.assertEqual(
                    (run_root / relative).read_text(encoding="utf-8"),
                    (repo_root / relative).read_text(encoding="utf-8"), relative,
                )

        completed_trades = [trade for trade in trades if trade["action"] == "卖出"]
        profitable_trades = [
            trade for trade in completed_trades if float(trade["return_pct"]) > 0
        ]
        self.assertEqual(summary["n_trades"], 6)
        self.assertEqual(len(profitable_trades), 2)
        self.assertAlmostEqual(summary["win_rate_pct"], 100 / 3, places=3)
        self.assertIn("净收益", profitable_trades[0]["reason"])
        self.assertEqual(summary["end"], "2026-10-13")
        self.assertAlmostEqual(summary["total_ret_pct"], 2.2855)
        self.assertAlmostEqual(summary["buy_hold_pct"], -12.5103)
        self.assertEqual(summary["execution"], "next_daily_open")
        self.assertEqual(summary["terminal_liquidations"], 1)
        self.assertAlmostEqual(sum(float(t["fee"]) for t in trades), summary["fees_paid"], places=5)
        dates = {t["date"] for t in trades}
        self.assertNotIn("2026-10-14", dates)
        entries = [t for t in trades if t["action"] == "买入"]
        self.assertTrue(all(t["signal_date"] < t["date"] for t in entries))

    def test_backtest_notebook_runs_top_to_bottom(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        notebook_path = repo_root / "notebooks" / "btc_ma_cross_backtest.ipynb"
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        namespace = {}
        original_cwd = os.getcwd()
        try:
            os.chdir(notebook_path.parent)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                for cell in notebook["cells"]:
                    if cell["cell_type"] == "code":
                        exec("".join(cell["source"]), namespace)
        finally:
            os.chdir(original_cwd)

        self.assertEqual(namespace["result"], baseline_result())
        self.assertEqual(len(namespace["closed_returns"]), 6)
        self.assertEqual(sum(result > 0 for result in namespace["closed_returns"]), 2)
        text = output.getvalue()
        self.assertNotRegex(text, r"\?{2,}|\ufffd")
        for label in ("回测区间", "数据 SHA-256", "信号", "买入", "卖出", "期末结算",
                      "初始", "期末", "策略收益", "同期买入持有", "完成交易", "胜率", "最大回撤"):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
