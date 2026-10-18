"""纸面交易引擎 - quant-lab
========================
模式: 纯模拟,不连接任何真实交易所,不下真实单。

逻辑: 与回测 notebook 完全一致的双均线策略 (MA20/MA60, 日线, 只做多)。
数据源: Kraken 公开 API 的 BTC/USD 日线 (无需 key)。

每笔模拟成交都带中文原因,可解释。
交易记录写入 ../data/paper_trades.csv,追加模式,不会覆盖历史。

用法:
    python3 strategies/paper_trader.py          # 跑一次 (用最新数据检查信号)
    python3 strategies/paper_trader.py --loop   # 常驻,每天 00:10 UTC 检查一次
"""
import json, csv, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
TRADES_CSV = os.path.join(DATA, 'paper_trades.csv')
STATE_JSON = os.path.join(DATA, 'paper_state.json')

FAST, SLOW, FEE = 20, 60, 0.001
START_CASH = 10000.0

def fetch_daily():
    url = 'https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    key = list(data['result'].keys())[0]
    bars = sorted([(r[0], float(r[1]), float(r[4])) for r in data['result'][key]])
    return bars

def ma(px, n, i):
    return sum(px[i-n+1:i+1]) / n

def load_state():
    if os.path.exists(STATE_JSON):
        with open(STATE_JSON) as f:
            return json.load(f)
    return {'cash': START_CASH, 'pos': 0.0, 'in_pos': False,
            'entry_px': 0, 'entry_date': '', 'entry_cash': START_CASH,
            'last_signal_date': ''}

def save_state(s):
    with open(STATE_JSON, 'w') as f:
        json.dump(s, f, indent=2)

def log_trade(row):
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['time_utc', 'date', 'action', 'price', 'amount_btc', 'cash_after', 'reason'])
        if new:
            w.writeheader()
        w.writerow(row)

def run_once():
    bars = fetch_daily()
    dates = [datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%d')
             for t, _, _ in bars]
    opens = [open_px for _, open_px, _ in bars]
    px = [close_px for _, _, close_px in bars]
    st = load_state()
    st.setdefault('entry_cash', START_CASH)
    # 只看最后一根已收盘的 K 线 (去掉正在走的当天)
    i = len(px) - 2
    execution_px = opens[i + 1]
    if dates[i] == st.get('last_signal_date'):
        print(f"[{dates[i]}] 本根 K 线已处理过,无新信号。")
        return
    f, s = ma(px, FAST, i), ma(px, SLOW, i)
    fp, sp = ma(px, FAST, i-1), ma(px, SLOW, i-1)
    diff, prev = f - s, fp - sp
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    if prev <= 0 < diff and not st['in_pos']:
        # 金叉买入
        amount = st['cash'] * (1 - FEE) / execution_px
        st.update({'pos': amount, 'cash': 0.0, 'in_pos': True,
                   'entry_px': execution_px, 'entry_date': dates[i + 1],
                   'entry_cash': st['cash'],
                   'last_signal_date': dates[i]})
        log_trade({'time_utc': now, 'date': dates[i + 1], 'action': '模拟买入',
                   'price': round(execution_px, 1), 'amount_btc': round(amount, 6),
                   'cash_after': 0,
                   'reason': f'MA{FAST}({f:,.0f})上穿MA{SLOW}({s:,.0f}),次日开盘成交,趋势转多'})
        print(f"[{dates[i + 1]}] 模拟买入 @ {execution_px:,.0f} (开盘)")
    elif prev >= 0 > diff and st['in_pos']:
        # 死叉卖出
        cash = st['pos'] * execution_px * (1 - FEE)
        ret = (cash / st['entry_cash'] - 1) * 100
        log_trade({'time_utc': now, 'date': dates[i + 1], 'action': '模拟卖出',
                   'price': round(execution_px, 1), 'amount_btc': round(st['pos'], 6),
                   'cash_after': round(cash, 2),
                   'reason': f'MA{FAST}({f:,.0f})下穿MA{SLOW}({s:,.0f}),次日开盘成交,趋势转空,本笔{ret:+.2f}%'})
        st.update({'pos': 0.0, 'cash': cash, 'in_pos': False,
                   'entry_cash': cash, 'last_signal_date': dates[i]})
        print(f"[{dates[i + 1]}] 模拟卖出 @ {execution_px:,.0f} (开盘), 本笔 {ret:+.2f}%")
    else:
        st['last_signal_date'] = dates[i]
        eq = st['cash'] + st['pos'] * execution_px
        print(f"[{dates[i]}] 无信号。当前权益 {eq:,.0f} USD ({'持仓' if st['in_pos'] else '空仓'})")
    save_state(st)

def next_check_utc(now=None):
    now = now or datetime.now(timezone.utc)
    next_check = now.replace(hour=0, minute=10, second=0, microsecond=0)
    if next_check <= now:
        next_check += timedelta(days=1)
    return next_check


if __name__ == '__main__':
    if '--loop' in sys.argv:
        print("纸面交易引擎常驻运行中,每天 00:10 UTC 检查一次。Ctrl+C 退出。")
        while True:
            next_check = next_check_utc()
            print(f"下次检查时间: {next_check:%Y-%m-%d %H:%M UTC}")
            time.sleep(max(0, (next_check - datetime.now(timezone.utc)).total_seconds()))
            try:
                run_once()
            except Exception as e:
                print(f"出错: {e}")
    else:
        run_once()
