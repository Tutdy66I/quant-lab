"""MA20/60 baseline using the shared previous-close / next-open engine.

Run as a module or script from any working directory. The snapshot's last row
is excluded because its close was not confirmed at capture time.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from strategies.execution import FEE, SLIPPAGE
from strategies.research import Config, load_snapshot, run_backtest


def baseline_result(data_path=ROOT / "data/btc_usd_1d_raw.json"):
    path = Path(data_path)
    candles = load_snapshot(path)
    config = Config()
    result = run_backtest(candles, config, config.warmup, fee=FEE, slippage=SLIPPAGE)
    result["summary"].update({
        "schema_version": 3,
        # Keep the original consumer-facing field names as aliases.
        "n_days": result["summary"]["days"],
        "total_ret_pct": result["summary"]["return_pct"],
        "n_trades": result["summary"]["completed_trades"],
        "fast": config.fast, "slow": config.slow, "fee": FEE, "slippage": SLIPPAGE,
        "signal": "previous_daily_close", "execution": "next_daily_open",
        "terminal_valuation": "liquidate_at_last_close_with_costs",
        "drawdown_valuation": "daily_close_including_initial_cash_and_terminal_costs",
        "last_row_policy": "exclude_unconfirmed_snapshot_tail",
        "data_sha256": hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "hash_normalization": "utf8_lf",
    })
    return result


def export_result(result, output_root=ROOT):
    directory = Path(output_root) / "data"
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for trade in result["trades"]:
        rows.append({
            "date": trade["entry_date"], "action": "买入",
            "price": trade["entry_price"], "return_pct": "",
            "reason": "前日 MA20 上穿 MA60，次日开盘加滑点买入",
            "signal_date": trade["signal_date"], "fee": trade["buy_fee"],
            "quantity": trade["quantity"], "forced": False,
        })
        rows.append({
            "date": trade["exit_date"], "action": "期末结算" if trade["forced"] else "卖出",
            "price": trade["exit_price"], "return_pct": trade["return_pct"],
            "reason": ("期末按收盘价加滑点结算" if trade["forced"] else "前日 MA20 下穿 MA60，次日开盘加滑点卖出")
                      + f"，本笔净收益 {trade['return_pct']:+.2f}%",
            "signal_date": "", "fee": trade["sell_fee"],
            "quantity": trade["quantity"], "forced": trade["forced"],
        })
    fields = ["date", "action", "price", "return_pct", "reason", "signal_date", "fee", "quantity", "forced"]
    with (directory / "backtest_trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (directory / "backtest_summary.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/btc_usd_1d_raw.json")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = baseline_result(args.data)
    export_result(result, args.output_root)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
