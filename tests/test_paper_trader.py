import contextlib
import csv
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
class BaselineTests(unittest.TestCase):
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
