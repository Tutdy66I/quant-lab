"""BTC 双均线回测 - 第一版可解释策略
策略逻辑: MA20 上穿 MA60 做多,下穿平仓。只做多,不做空。
收盘后确认信号,下一根日线开盘成交,避免用生成信号的收盘价成交。
"""
import csv
import json
from datetime import datetime, timezone

# ---------- 读数据 ----------
with open('data/btc_usd_1d_raw.json') as f:
    raw = json.load(f)
# Kraken OHLC: [time, open, high, low, close, vwap, volume, count]
bars = sorted(
    [(r[0], float(r[1]), float(r[4])) for r in raw], key=lambda row: row[0]
)
dates = [datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%d') for t, _, _ in bars]
opens = [open_px for _, open_px, _ in bars]
px = [close_px for _, _, close_px in bars]
print(f"数据: {dates[0]} ~ {dates[-1]}, 共 {len(px)} 根日线")

# ---------- 策略参数 ----------
FAST, SLOW = 20, 60
FEE = 0.001  # 单边手续费 0.1%


def ma(series, n, i):
    return sum(series[i - n + 1:i + 1]) / n


# ---------- 回测 ----------
cash, pos = 10000.0, 0.0
trades, equity_curve = [], []
in_pos, entry_cash = False, 0.0
prev_diff, pending_action = None, None

for i in range(SLOW, len(px)):
    # 上一根 K 线收盘后确认的信号,在今天开盘成交。
    if pending_action == 'buy' and not in_pos:
        entry_cash = cash
        pos = cash * (1 - FEE) / opens[i]
        cash = 0.0
        in_pos = True
        trades.append({'date': dates[i], 'action': '买入',
                       'price': round(opens[i], 1),
                       'reason': f'MA{FAST}/MA{SLOW} 收盘金叉,次日开盘成交'})
    elif pending_action == 'sell' and in_pos:
        cash = pos * opens[i] * (1 - FEE)
        ret = (cash / entry_cash - 1) * 100
        trades.append({'date': dates[i], 'action': '卖出',
                       'price': round(opens[i], 1),
                       'return_pct': round(ret, 2),
                       'reason': f'MA{FAST}/MA{SLOW} 收盘死叉,次日开盘成交,本笔 {ret:+.2f}%'})
        pos, in_pos = 0.0, False
    pending_action = None

    equity_curve.append(cash + pos * px[i])

    # 以收盘数据产生信号,留给下一根 K 线开盘执行。
    f, s = ma(px, FAST, i), ma(px, SLOW, i)
    diff = f - s
    if prev_diff is not None and i + 1 < len(px):
        if prev_diff <= 0 < diff and not in_pos:
            pending_action = 'buy'
        elif prev_diff >= 0 > diff and in_pos:
            pending_action = 'sell'
    prev_diff = diff

# 回测期末按最后收盘价结算,并把退出手续费计入权益和单笔收益。
if in_pos:
    cash = pos * px[-1] * (1 - FEE)
    ret = (cash / entry_cash - 1) * 100
    trades.append({'date': dates[-1], 'action': '期末结算', 'price': round(px[-1], 1),
                   'return_pct': round(ret, 2),
                   'reason': f'回测结束仍持仓,按收盘价结算,本笔 {ret:+.2f}%'})
    pos, in_pos = 0.0, False
    equity_curve[-1] = cash

# ---------- 统计 ----------
final = cash
total_ret = (final / 10000 - 1) * 100
buy_hold = (px[-1] / px[SLOW] - 1) * 100
completed = [t for t in trades if 'return_pct' in t]
n_round = len(completed)
win_rate = sum(t['return_pct'] > 0 for t in completed) / n_round * 100 if n_round else 0

# 最大回撤
peak, max_dd = equity_curve[0], 0
for e in equity_curve:
    peak = max(peak, e)
    max_dd = max(max_dd, (peak - e) / peak * 100)

print(f"\n初始资金: 10000 USD")
print(f"期末资金: {final:,.0f} USD")
print(f"策略总收益: {total_ret:+.1f}%")
print(f"同期买入持有: {buy_hold:+.1f}%")
print(f"完成交易笔数: {n_round}, 胜率: {win_rate:.0f}%")
print(f"最大回撤: {max_dd:.1f}%")

with open('data/backtest_trades.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['date', 'action', 'price', 'return_pct', 'reason'])
    w.writeheader()
    for t in trades:
        t.setdefault('return_pct', '')
        w.writerow(t)
with open('data/backtest_summary.json', 'w') as f:
    json.dump({'start': dates[SLOW], 'end': dates[-1], 'n_days': len(px) - SLOW,
               'initial': 10000, 'final': round(final, 0), 'total_ret_pct': round(total_ret, 1),
               'buy_hold_pct': round(buy_hold, 1), 'n_trades': n_round,
               'win_rate_pct': round(win_rate, 0), 'max_dd_pct': round(max_dd, 1),
               'fast': FAST, 'slow': SLOW, 'fee': FEE,
               'execution': 'next_daily_open'}, f, indent=2)
print("\n交易明细已存 data/backtest_trades.csv,汇总存 data/backtest_summary.json")
