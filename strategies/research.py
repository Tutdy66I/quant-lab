"""Reproducible daily-candle research; uses only the Python standard library.

Run from the repository root: python -m strategies.research
Signals use the previous close; fills use the following open with modeled costs.
"""

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies.execution import buy_fill, crossover, sell_fill
from strategies.market_data import Candle, parse_daily_rows


ROOT = Path(__file__).resolve().parents[1]
INITIAL_CASH = 10000.0
KRAKEN_DOCS = "https://docs.kraken.com/api-reference/market-data/get-ohlc-data"


@dataclass(frozen=True)
class Config:
    fast: int = 20
    slow: int = 60
    close_loss: float | None = None
    max_daily_vol: float | None = None
    vol_window: int = 20

    def __post_init__(self):
        if not 1 <= self.fast < self.slow:
            raise ValueError("Require 1 <= fast < slow")
        if self.close_loss is not None and not 0 < self.close_loss < 1:
            raise ValueError("close_loss must be between 0 and 1")
        if self.max_daily_vol is not None and not 0 < self.max_daily_vol < 1:
            raise ValueError("max_daily_vol must be between 0 and 1")
        if self.vol_window < 2:
            raise ValueError("vol_window must be at least 2")

    @property
    def name(self):
        suffix = ""
        if self.close_loss is not None:
            suffix += f"_loss{self.close_loss:g}"
        if self.max_daily_vol is not None:
            suffix += f"_vol{self.max_daily_vol:g}"
        return f"ma{self.fast}_{self.slow}{suffix}"

    @property
    def warmup(self):
        return max(self.slow + 1, self.vol_window + 1 if self.max_daily_vol else 0)


def load_snapshot(path):
    """Drop the last row, whose close was not confirmed at snapshot capture."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_daily_rows(raw)[:-1]


def run_backtest(candles, config, start, end=None, fee=0.001, slippage=0.0005):
    """Run [start, end), starting flat; earlier candles are warm-up only.

    close_loss is evaluated at a daily close and filled at the next open.
    It is not an intraday stop order and cannot cap realized losses at the threshold.
    """
    end = len(candles) if end is None else end
    if not config.warmup <= start < end <= len(candles):
        raise ValueError("Invalid backtest interval or insufficient warm-up")
    if not all(math.isfinite(cost) and 0 <= cost < 1 for cost in (fee, slippage)):
        raise ValueError("fee and slippage must be finite values in [0, 1)")
    closes = [c.close for c in candles]
    cash, quantity = INITIAL_CASH, 0.0
    entry = None
    trades, equity = [], []
    blocked_entries = 0

    def sell(index, raw_price, reason, forced=False):
        nonlocal cash, quantity, entry
        fill, cash, sell_fee = sell_fill(quantity, raw_price, fee, slippage)
        trades.append({
            **entry,
            "exit_date": candles[index].date,
            "exit_price": fill,
            "sell_fee": sell_fee,
            "pnl": cash - entry["capital"],
            "return_pct": (cash / entry["capital"] - 1) * 100,
            "reason": reason,
            "forced": forced,
        })
        quantity, entry = 0.0, None

    for index in range(start, end):
        signal_index = index - 1
        signal = crossover(closes, config.fast, config.slow, signal_index)
        crossed_up, crossed_down = signal == "buy", signal == "sell"
        loss_exit = (
            entry is not None and config.close_loss is not None
            and closes[signal_index] <= entry["entry_price"] * (1 - config.close_loss)
        )
        if entry is not None and (crossed_down or loss_exit):
            sell(index, candles[index].open, "close_loss" if loss_exit else "cross_down")
        elif entry is None and crossed_up:
            allowed = True
            if config.max_daily_vol is not None:
                returns = [
                    closes[j] / closes[j - 1] - 1
                    for j in range(signal_index - config.vol_window + 1, signal_index + 1)
                ]
                allowed = statistics.pstdev(returns) <= config.max_daily_vol
            if allowed:
                fill, quantity, buy_fee = buy_fill(cash, candles[index].open, fee, slippage)
                entry = {
                    "signal_date": candles[signal_index].date,
                    "entry_date": candles[index].date,
                    "entry_price": fill,
                    "quantity": quantity,
                    "capital": cash,
                    "buy_fee": buy_fee,
                }
                cash = 0.0
            else:
                blocked_entries += 1
        equity.append(cash + quantity * candles[index].close)

    if entry is not None:
        sell(end - 1, candles[end - 1].close, "period_end", forced=True)
        equity[-1] = cash

    peak, max_drawdown = INITIAL_CASH, 0.0
    for value in equity:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
    completed = [trade for trade in trades if not trade["forced"]]
    hold_final = (
        INITIAL_CASH * (1 - fee) / (candles[start].open * (1 + slippage))
        * candles[end - 1].close * (1 - slippage) * (1 - fee)
    )
    summary = {
        "start": candles[start].date,
        "end": candles[end - 1].date,
        "days": end - start,
        "initial": INITIAL_CASH,
        "final": round(cash, 6),
        "return_pct": round((cash / INITIAL_CASH - 1) * 100, 4),
        "buy_hold_pct": round((hold_final / INITIAL_CASH - 1) * 100, 4),
        "max_dd_pct": round(max_drawdown, 4),
        "completed_trades": len(completed),
        "terminal_liquidations": len(trades) - len(completed),
        "win_rate_pct": round(sum(t["pnl"] > 0 for t in completed) / len(completed) * 100, 4) if completed else None,
        "fees_paid": round(sum(t["buy_fee"] + t["sell_fee"] for t in trades), 6),
        "blocked_entries": blocked_entries,
        "close_loss_exits": sum(t["reason"] == "close_loss" for t in completed),
    }
    return {"summary": summary, "trades": trades, "equity": equity}


def candidates():
    grid = [Config(fast=fast, slow=slow) for fast in (10, 20, 30) for slow in (40, 60, 90)]
    return grid + [Config(close_loss=0.10), Config(max_daily_vol=0.04)]


def experiment(candles, configs=None, train_fraction=0.7):
    configs = candidates() if configs is None else list(configs)
    if not configs or not 0 < train_fraction < 1:
        raise ValueError("Require candidates and a training fraction in (0, 1)")
    start = max(config.warmup for config in configs)
    split = start + int((len(candles) - start) * train_fraction)
    if not start < split < len(candles):
        raise ValueError("Not enough candles for both training and holdout")
    rows, trades = [], []
    for config in configs:
        row = {"name": config.name, "config": asdict(config)}
        for period, left, right in (("train", start, split), ("holdout", split, len(candles))):
            result = run_backtest(candles, config, left, right)
            row[period] = result["summary"]
            trades.extend({"candidate": config.name, "period": period, **trade} for trade in result["trades"])
        rows.append(row)
    # Selection deliberately reads training results only. Names break exact ties.
    selected = sorted(rows, key=lambda row: (-row["train"]["return_pct"], row["name"]))[0]["name"]
    return {
        "schema_version": 2,
        "assumptions": {
            "fee_per_side": 0.001,
            "slippage_per_side": 0.0005,
            "signal": "previous_daily_close",
            "execution": "next_daily_open",
            "terminal_valuation": "liquidate_at_last_close_with_costs",
            "drawdown_valuation": "daily_close_equity_including_initial_cash_and_terminal_costs",
            "period_initial_position": "flat",
            "win_rate": "natural_exits_only_excludes_terminal_liquidation",
            "train_fraction": train_fraction,
            "common_warmup_bars": start,
            "selection_rule": "highest_training_return_then_name",
            "holdout_status": "historical_time_split_not_prospective_validation",
        },
        "selected_on_train": selected,
        "candidates": rows,
    }, trades


def render_report(result):
    rows = result["candidates"]
    selected = next(row for row in rows if row["name"] == result["selected_on_train"])
    baseline = next(row for row in rows if row["name"] == "ma20_60")
    train, holdout = baseline["train"], baseline["holdout"]
    lines = [
        "# 第三周：可复现策略实验", "",
        "运行 `python -m strategies.research` 可重新生成本报告、`data/param_sweep.json` 与 `data/research_trades.csv`。仅依赖 Python 3.10+ 标准库，无需联网。", "",
        "## 数据和实验口径", "",
        f"- 本地快照按 UTF-8/LF 统一换行后的 SHA-256：`{result['data']['sha256']}`。",
        f"- 保留 {result['data']['closed_rows']} 根日线；原始快照最后一根（{result['data']['excluded_last_date']}）缺少采集时已收盘的证据，保守剔除。",
        f"- Kraken 明确说明 OHLC 响应最后一根尚未确认收盘：[官方说明]({KRAKEN_DOCS})。本地文件缺少采集元数据，剔除末行是防御性处理，不代表已核验该快照来源。",
        f"- 所有候选统一使用 {result['assumptions']['common_warmup_bars']} 根预热数据。训练区间 {train['start']}—{train['end']}（{train['days']} 天），留出区间 {holdout['start']}—{holdout['end']}（{holdout['days']} 天）。",
        "- 按时间 70% / 30% 划分；两个区间各自从 10,000 USD、空仓开始，留出区间可读取之前的价格作预热，不继承训练区间的持仓和资金。",
        "- 信号在前一根日线收盘后计算，下一根开盘成交。单边手续费 0.1%、单边不利滑点 0.05%，买入持有也扣同样费用。",
        "- 期末持仓按最后收盘价扣费与滑点结算；收益和回撤包含该结算，完成交易笔数与胜率只统计自然退出。没有完成交易时胜率为 null。",
        "- 回撤按日收盘权益计算，纳入起始资金与期末结算费用，不覆盖日内最低权益。",
        "- 10% 收盘亏损退出：收盘价相对实际买入价跌到阈值，下一根开盘退出；跳空可使亏损超过 10%。",
        "- 波动过滤：过去 20 个日收益的总体标准差超过 4% 时跳过金叉，不延迟补买；退出规则保持死叉。", "",
        "## 参数与改进对比", "",
        "预先固定 9 组均线、基线增加 10% 收盘亏损退出、基线增加 4% 波动上限，共 11 个候选。按训练区间收益最高选择，收益相同时按名称排序。", "",
        "| 候选 | 训练收益 | 训练回撤 | 留出收益 | 留出回撤 | 留出完成交易 | 留出期末结算 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        a, b = row["train"], row["holdout"]
        lines.append(f"| {row['name']} | {a['return_pct']:+.2f}% | {a['max_dd_pct']:.2f}% | {b['return_pct']:+.2f}% | {b['max_dd_pct']:.2f}% | {b['completed_trades']} | {b['terminal_liquidations']} |")
    loss = next(row for row in rows if row["name"] == "ma20_60_loss0.1")
    vol = next(row for row in rows if row["name"] == "ma20_60_vol0.04")
    lines += [
        "", "## 结果与下一步", "",
        f"- 训练区间选出 `{selected['name']}`：训练收益 {selected['train']['return_pct']:+.2f}%，留出收益 {selected['holdout']['return_pct']:+.2f}%，留出回撤 {selected['holdout']['max_dd_pct']:.2f}%。",
        f"- 同期基线 MA20/60 留出收益 {holdout['return_pct']:+.2f}%，买入持有 {holdout['buy_hold_pct']:+.2f}%。",
        f"- 收盘亏损退出在训练区间触发 {loss['train']['close_loss_exits']} 次、留出区间触发 {loss['holdout']['close_loss_exits']} 次；留出收益相对基线改变 {loss['holdout']['return_pct'] - holdout['return_pct']:+.2f} 个百分点。",
        f"- 波动过滤在训练区间跳过 {vol['train']['blocked_entries']} 次买入、留出区间跳过 {vol['holdout']['blocked_entries']} 次；没有触发时，不能用相同收益声称过滤有效。",
        "- 不能直接与 README 的第一版回测收益比较：第一版使用同根收盘成交，这里改为下一根开盘，并增加滑点、剔除快照末行、拆分区间。",
        "- 这份历史数据此前已被查看，留出结果属于回顾性时间划分，不能称为真正未见样本。候选少、交易少、行情区间短，结果不支持据此决定实盘或自动替换纸面策略。",
        "- 下一步先固定候选和规则，在新增数据上持续纸面观察并记录信号时间、模拟成交价、费用与权益；Hummingbot 运行验证和四周实测复盘仍待完成。", "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/btc_usd_1d_raw.json")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    candles = load_snapshot(args.data)
    result, trades = experiment(candles)
    result["data"] = {
        "source_file": args.data.name,
        "sha256": hashlib.sha256(args.data.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "hash_normalization": "utf8_lf",
        "closed_rows": len(candles),
        "excluded_last_date": datetime.fromtimestamp(candles[-1].timestamp + 86400, timezone.utc).date().isoformat(),
        "last_row_policy": "exclude_unconfirmed_snapshot_tail",
    }
    data_dir, docs_dir = args.output_root / "data", args.output_root / "docs"
    data_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "param_sweep.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    fields = ["candidate", "period", "signal_date", "entry_date", "entry_price", "quantity", "capital", "buy_fee", "exit_date", "exit_price", "sell_fee", "pnl", "return_pct", "reason", "forced"]
    with (data_dir / "research_trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(trades)
    report_path = docs_dir / "策略迭代报告.md"
    report_path.write_text(render_report(result), encoding="utf-8", newline="\n")
    print(f"Research complete: {len(result['candidates'])} candidates; selected on train: {result['selected_on_train']}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
