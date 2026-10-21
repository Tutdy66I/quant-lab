"""Offline rolling validation and risk diagnostics using the existing engine.

No account access, market requests, observer controls or live rule changes.
"""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from strategies.execution import FEE, SLIPPAGE, buy_fill, sell_fill
from strategies.research import Config, INITIAL_CASH, candidates, load_snapshot, run_backtest


def fold_ranges(length, warmup, train_bars, test_bars):
    """Yield rolling [train_start, test_start, test_end); retain the final tail."""
    if (not all(type(n) is int for n in (length, warmup, train_bars, test_bars))
            or warmup < 1 or train_bars < 1 or test_bars < 1
            or length <= warmup + train_bars):
        raise ValueError("Require positive windows and at least one test bar after warm-up/training")
    for test_start in range(warmup + train_bars, length, test_bars):
        yield test_start - train_bars, test_start, min(test_start + test_bars, length)


def stitch(curves, initial=INITIAL_CASH):
    """Reinvest each fold's exact terminal equity; every fold starts flat."""
    capital, combined = initial, []
    for curve in curves:
        combined.extend(capital * value / INITIAL_CASH for value in curve)
        capital = combined[-1]
    return combined


def risk_summary(equity, initial=INITIAL_CASH):
    if not equity or not all(math.isfinite(v) and v > 0 for v in [initial, *equity]):
        raise ValueError("Require a nonempty positive finite equity curve and initial capital")
    returns = [after / before - 1 for before, after in zip([initial, *equity[:-1]], equity)]
    peak, drawdown, underwater, longest = initial, 0.0, 0, 0
    for value in equity:
        peak = max(peak, value)
        drawdown = max(drawdown, (1 - value / peak) * 100)
        underwater = underwater + 1 if value < peak else 0
        longest = max(longest, underwater)
    # Empirical mean of the worst ceil(5% * N) daily observations, not a forecast.
    tail_count = max(1, math.ceil(len(returns) * 0.05))
    return {
        "days": len(equity), "final": round(equity[-1], 6),
        "return_pct": round((equity[-1] / initial - 1) * 100, 4),
        "max_dd_pct": round(drawdown, 4), "max_underwater_days": longest,
        "worst_daily_return_pct": round(min(returns) * 100, 4),
        "worst_5pct_mean_daily_return_pct": round(statistics.mean(sorted(returns)[:tail_count]) * 100, 4),
        "daily_volatility_pct": round(statistics.pstdev(returns) * 100, 4),
    }


def hold_curve(candles, start, end):
    _, quantity, _ = buy_fill(INITIAL_CASH, candles[start].open)
    curve = [quantity * c.close for c in candles[start:end]]
    _, curve[-1], _ = sell_fill(quantity, candles[end - 1].close)
    return curve


def walk_forward(candles, configs=None, train_bars=252, test_bars=63, *, baseline_config=None):
    configs = list(candidates() if configs is None else configs)
    if not configs or len({c.name for c in configs}) != len(configs):
        raise ValueError("Require nonempty candidates with unique names")
    baseline = Config() if baseline_config is None else baseline_config
    warmup = max(c.warmup for c in [*configs, baseline])
    scenarios = {
        "double_fee": (FEE * 2, SLIPPAGE),
        "double_slippage": (FEE, SLIPPAGE * 2),
        "double_both": (FEE * 2, SLIPPAGE * 2),
    }
    curves = {name: [] for name in ("selected", "baseline", "buy_hold", *scenarios)}
    fixed_curves = {c.name: [] for c in configs}
    folds = []
    for train_start, test_start, test_end in fold_ranges(len(candles), warmup, train_bars, test_bars):
        # Explicit prefix restriction prevents training from even receiving test prices.
        training = {c.name: run_backtest(candles[:test_start], c, train_start)["summary"] for c in configs}
        selected = min(configs, key=lambda c: (-training[c.name]["return_pct"], c.name))
        tested = {c.name: run_backtest(candles[:test_end], c, test_start, test_end) for c in configs}
        chosen = tested[selected.name]
        reference = tested.get(baseline.name)
        if reference is None:
            reference = run_backtest(candles[:test_end], baseline, test_start, test_end)
        stress = {
            name: run_backtest(candles[:test_end], selected, test_start, test_end, fee=fee, slippage=slip)
            for name, (fee, slip) in scenarios.items()
        }
        curves["selected"].append(chosen["equity"])
        curves["baseline"].append(reference["equity"])
        curves["buy_hold"].append(hold_curve(candles, test_start, test_end))
        for name, result in stress.items():
            curves[name].append(result["equity"])
        for name, result in tested.items():
            fixed_curves[name].append(result["equity"])
        folds.append({
            "fold": len(folds) + 1,
            "train_indices": [train_start, test_start], "test_indices": [test_start, test_end],
            "partial_test": test_end - test_start < test_bars,
            "training": training, "selected": selected.name,
            "test": chosen["summary"], "baseline": reference["summary"],
            "test_trades": chosen["trades"],
            "stress": {name: result["summary"] for name, result in stress.items()},
        })
    return {
        "schema_version": 1,
        "assumptions": {
            "validation_status": "retrospective_previously_inspected_snapshot",
            "train_bars": train_bars, "test_bars": test_bars, "window": "rolling",
            "common_warmup_bars": warmup, "include_partial_tail": True,
            "selection": "highest_training_return_then_name",
            "signal": "previous_daily_close", "execution": "next_daily_open",
            "fee_per_side": FEE, "slippage_per_side": SLIPPAGE,
            "fold_boundary": "flat_start_and_terminal_liquidation_with_costs",
            "aggregation": "reinvest_exact_terminal_equity_across_disjoint_test_folds",
            "benchmark": "buy_at_each_fold_first_open_sell_at_last_close_with_same_costs",
            "risk_valuation": "daily_close_including_initial_cash_and_terminal_costs",
            "tail_risk": "mean_of_worst_ceil_5pct_daily_returns_not_forecast",
            "stress_selection": "freeze_base_cost_training_choice_no_reselection",
            "baseline": baseline.name,
        },
        "candidates": [{"name": c.name, "config": asdict(c)} for c in configs],
        "folds": folds,
        "selection_counts": dict(sorted(Counter(f["selected"] for f in folds).items())),
        "aggregate": {name: risk_summary(stitch(values)) for name, values in curves.items()},
        "fixed_candidate_diagnostics": {
            name: risk_summary(stitch(values)) for name, values in fixed_curves.items()
        },
    }


def render_report(result):
    assumptions, folds = result["assumptions"], result["folds"]
    lines = [
        "# Quant Lab 2.0：Walk-forward 与策略风险分析", "",
        "运行 `python -m strategies.walk_forward` 离线重新生成本报告及 `data/walk_forward.json`；仅用 Python 3.10+ 标准库。", "",
        "## 固定研究协议", "",
        f"- 数据指纹（UTF-8/LF SHA-256）：`{result['data']['sha256']}`；剔除未确认末行后 {result['data']['closed_rows']} 根日线，最后日期 {result['data']['last_closed_date']}。",
        f"- 固定沿用既有 {len(result['candidates'])} 个候选；共同预热 {assumptions['common_warmup_bars']} 天，滚动训练 {assumptions['train_bars']} 天、验证 {assumptions['test_bars']} 天。训练窗可以重叠，验证窗互不重叠；保留不足一窗的末段并标记。",
        "- 每窗仅用验证起点之前的训练数据按训练净收益选参，收益相同按名称排序。验证结果不反馈到当窗选择。候选固定策略的验证表现仅作敏感性诊断，不能再据此挑选并宣称新的样本外冠军。",
        "- 沿用共享引擎：前收盘信号、次开盘成交；单边手续费 0.1%、滑点 0.05%；剔除快照未确认末行。",
        "- 每窗从空仓开始，窗末扣费与滑点清算；后窗按精确期末权益复投。固定基线和买入持有也按相同分窗及成本计算。窗口边界会额外清仓且可能错过跨窗趋势，这不是持续账户模拟。",
        "- 压力测试固定当窗原选参，分别将手续费、滑点、两者翻倍，不利用验证结果重新选参。",
        "- 风险按日收盘权益计算，含初始资金和期末成本；最长水下期含末尾未恢复区间。最差 5% 日收益均值取最差 ceil(N×5%) 个观测，不是未来损失保证或置信界限。", "",
        "## 逐窗结果", "",
        "| 窗口 | 训练日期 | 验证日期 | 天数 | 选参 | 验证收益 | 基线收益 | 验证回撤 | 自然退出 / 期末结算 |",
        "|---|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for fold in folds:
        train, test, base = fold['training'][fold['selected']], fold['test'], fold['baseline']
        tail = "（尾段）" if fold['partial_test'] else ""
        lines.append(f"| {fold['fold']}{tail} | {train['start']}—{train['end']} | {test['start']}—{test['end']} | {test['days']} | {fold['selected']} | {test['return_pct']:+.2f}% | {base['return_pct']:+.2f}% | {test['max_dd_pct']:.2f}% | {test['completed_trades']} / {test['terminal_liquidations']} |")
    lines += ["", "## 分窗复投风险与成本压力", "",
              "| 方案 | 净收益 | 最大回撤 | 最长水下天数 | 最差单日 | 最差 5% 日收益均值 | 日波动率 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    names = {"selected": "训练选参", "baseline": f"固定 {assumptions['baseline']}", "buy_hold": "分窗买入持有",
             "double_fee": "手续费翻倍", "double_slippage": "滑点翻倍", "double_both": "两者翻倍"}
    for name, risk in result['aggregate'].items():
        lines.append(f"| {names[name]} | {risk['return_pct']:+.2f}% | {risk['max_dd_pct']:.2f}% | {risk['max_underwater_days']} | {risk['worst_daily_return_pct']:+.2f}% | {risk['worst_5pct_mean_daily_return_pct']:+.2f}% | {risk['daily_volatility_pct']:.2f}% |")
    lines += ["", "## 固定候选敏感性诊断", "",
              "| 固定候选 | 同验证区间净收益 | 最大回撤 |", "|---|---:|---:|"]
    for name, risk in result['fixed_candidate_diagnostics'].items():
        lines.append(f"| {name} | {risk['return_pct']:+.2f}% | {risk['max_dd_pct']:.2f}% |")
    positive = sum(f['test']['return_pct'] > 0 for f in folds)
    wins = sum(f['test']['return_pct'] > f['baseline']['return_pct'] for f in folds)
    natural = sum(f['test']['completed_trades'] for f in folds)
    forced = sum(f['test']['terminal_liquidations'] for f in folds)
    lines += ["", "## 稳健性判断与下一步", "",
              f"- 共 {len(folds)} 窗，{positive} 窗收益为正，{wins} 窗超过同窗固定基线；自然退出 {natural} 笔、期末结算 {forced} 笔。小样本、零交易窗口和人工窗末结算均不能省略。",
              "- 训练选择次数：" + "，".join(f"{name}：{count}" for name, count in result['selection_counts'].items()) + "。",
              "- 这份快照此前已被查看，候选也已在同一历史数据研究过；滚动时间划分不消除研究者看过数据产生的偏差，不能称为真正未见样本或前瞻实测。",
              "- 本版固定一组窗口，不根据结果优化窗口长度。后续优先增加更长历史及不同市场区间、预先登记其他窗口的敏感性检验，并保留新增数据作前瞻评估；不能用本报告直接决定实盘或替换观察器策略。",
              "- 研究模块只读本地历史快照，输出研究 JSON/报告；纸面观察器独立运行，其规则、账户、数据库、计划任务和运行进程均不受影响，Hummingbot 继续保持暂停。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT / 'data/btc_usd_1d_raw.json')
    parser.add_argument('--output-root', type=Path, default=ROOT)
    parser.add_argument('--train-bars', type=int, default=252)
    parser.add_argument('--test-bars', type=int, default=63)
    args = parser.parse_args()
    candles = load_snapshot(args.data)
    result = walk_forward(candles, train_bars=args.train_bars, test_bars=args.test_bars)
    result['data'] = {
        'source_file': args.data.name,
        'sha256': hashlib.sha256(args.data.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        'hash_normalization': 'utf8_lf', 'closed_rows': len(candles),
        'last_closed_date': candles[-1].date,
        'last_row_policy': 'exclude_unconfirmed_snapshot_tail',
    }
    for directory in ('data', 'docs'):
        (args.output_root / directory).mkdir(parents=True, exist_ok=True)
    (args.output_root / 'data/walk_forward.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')
    (args.output_root / 'docs/Walk-forward风险报告.md').write_text(
        render_report(result), encoding='utf-8', newline='\n')
    print(f"Walk-forward complete: {len(result['folds'])} folds; {result['aggregate']['selected']['days']} test days")


if __name__ == '__main__':
    main()
