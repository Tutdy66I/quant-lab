"""BTC 双均线回测 - 第一版可解释策略
策略逻辑: MA20 上穿 MA60 做多, 下穿平仓。只做多,不做空。
每笔信号都有中文说明,可解释。
"""
import json, csv
from datetime import datetime, timezone

# ---------- 读数据 ----------
with open('data/btc_usd_1d_raw.json', encoding='utf-8') as f:
    raw = json.load(f)
# Kraken OHLC: [time, open, high, low, close, vwap, volume, count]
closes = [(r[0], float(r[4])) for r in raw]
closes.sort(key=lambda x: x[0])
dates = [datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%d') for t, _ in closes]
px = [p for _, p in closes]
print(f"数据: {dates[0]} ~ {dates[-1]}, 共 {len(px)} 根日线")

# ---------- 策略参数 ----------
FAST, SLOW = 20, 60
FEE = 0.001  # 单边手续费 0.1%

def ma(series, n, i):
    return sum(series[i-n+1:i+1]) / n

# ---------- 回测 ----------
cash, pos = 10000.0, 0.0
trades, equity_curve = [], []
in_pos, entry_px, entry_date = False, 0, ''
prev_diff = None

for i in range(SLOW, len(px)):
    f, s = ma(px, FAST, i), ma(px, SLOW, i)
    diff = f - s
    # 金叉: 快线从下往上穿慢线 -> 买入
    if prev_diff is not None and prev_diff <= 0 and diff > 0 and not in_pos:
        pos = cash * (1 - FEE) / px[i]
        entry_px, entry_date = px[i], dates[i]
        cash = 0.0
        in_pos = True
        trades.append({'date': dates[i], 'action': '买入',
                       'price': round(px[i], 1),
                       'reason': f'MA{FAST}({f:,.0f}) 上穿 MA{SLOW}({s:,.0f}),趋势转多'})
    # 死叉: 快线从上往下穿慢线 -> 卖出
    elif prev_diff is not None and prev_diff >= 0 and diff < 0 and in_pos:
        cash = pos * px[i] * (1 - FEE)
        ret = ((px[i] / entry_px) * (1 - FEE) ** 2 - 1) * 100
        trades.append({'date': dates[i], 'action': '卖出',
                       'price': round(px[i], 1),
                       'return_pct': round(ret, 2),
                       'reason': f'MA{FAST}({f:,.0f}) 下穿 MA{SLOW}({s:,.0f}),趋势转空,本笔净收益 {ret:+.2f}%'})
        pos, in_pos = 0.0, False
    prev_diff = diff
    equity_curve.append(cash + pos * px[i])

# 期末如果还持仓,按收盘价结算
if in_pos:
    cash = pos * px[-1] * (1 - FEE)
    ret = ((px[-1] / entry_px) * (1 - FEE) ** 2 - 1) * 100
    trades.append({'date': dates[-1], 'action': '期末结算', 'price': round(px[-1], 1),
                   'return_pct': round(ret, 2),
                   'reason': f'回测结束仍持仓,按收盘价结算,本笔净收益 {ret:+.2f}%'})
    pos = 0.0

# ---------- 统计 ----------
final = cash
total_ret = (final / 10000 - 1) * 100
buy_hold = (px[-1] / px[SLOW] - 1) * 100
n_round = len([t for t in trades if t['action'] == '卖出'])
wins = [t for t in trades if t['action'] == '卖出' and t.get('return_pct', 0) > 0]
win_rate = len(wins) / n_round * 100 if n_round else 0

# 最大回撤
peak, max_dd = equity_curve[0], 0
for e in equity_curve:
    peak = max(peak, e)
    max_dd = max(max_dd, (peak - e) / peak * 100)

print(f"\n初始资金: 10000 USD")
print(f"期末资金: {final:,.0f} USD")
print(f"策略总收益: {total_ret:+.1f}%")
print(f"同期买入持有: {buy_hold:+.1f}%")
print(f"完成交易笔数: {n_round}, 胜率: {win_rate:.1f}%")
print(f"最大回撤: {max_dd:.1f}%")

with open('data/backtest_trades.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=['date', 'action', 'price', 'return_pct', 'reason'])
    w.writeheader()
    for t in trades:
        t.setdefault('return_pct', '')
        w.writerow(t)
with open('data/backtest_summary.json', 'w', encoding='utf-8') as f:
    json.dump({'start': dates[SLOW], 'end': dates[-1], 'n_days': len(px) - SLOW,
               'initial': 10000, 'final': round(final, 0), 'total_ret_pct': round(total_ret, 1),
               'buy_hold_pct': round(buy_hold, 1), 'n_trades': n_round,
               'win_rate_pct': round(win_rate, 1), 'max_dd_pct': round(max_dd, 1),
               'fast': FAST, 'slow': SLOW, 'fee': FEE}, f, indent=2)
print("\n交易明细已存 data/backtest_trades.csv, 汇总存 data/backtest_summary.json")
