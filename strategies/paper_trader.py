"""纸面交易引擎 - quant-lab
========================
模式: 纯模拟,不连接任何真实交易所,不下真实单。

逻辑: 与回测 notebook 一致的双均线策略 (MA20/MA60, 日线, 只做多)。
数据源: Kraken 公开 API 的 BTC/USD 日线 (无需 key)。

每笔模拟成交都带中文原因,可解释。
交易记录写入 ../data/paper_trades.csv,追加模式,不会覆盖历史。

用法:
    python3 strategies/paper_trader.py          # 跑一次 (检查最近已收盘 K 线)
    python3 strategies/paper_trader.py --loop   # 常驻,每天 00:10 UTC 检查一次
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
TRADES_CSV = os.path.join(DATA, "paper_trades.csv")
STATE_JSON = os.path.join(DATA, "paper_state.json")

FAST, SLOW, FEE = 20, 60, 0.001
START_CASH = 10000.0
STATE_DEFAULTS = {
    "cash": START_CASH,
    "pos": 0.0,
    "in_pos": False,
    "entry_px": 0,
    "entry_date": "",
    "last_signal_date": "",
}


def fetch_daily():
    url = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440"
    req = urllib.request.Request(url, headers={"User-Agent": "quant-lab paper trader"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read())

    errors = payload.get("error", [])
    if errors:
        raise RuntimeError(f"Kraken API error: {', '.join(errors)}")

    result = payload.get("result", {})
    pair_keys = [key for key in result if key != "last"]
    if not pair_keys:
        raise RuntimeError("Kraken API response contains no OHLC candles")

    closes = sorted((int(row[0]), float(row[4])) for row in result[pair_keys[0]])
    today_utc = datetime.now(timezone.utc).date()
    closed_candles = [
        (timestamp, price)
        for timestamp, price in closes
        if datetime.fromtimestamp(timestamp, tz=timezone.utc).date() < today_utc
    ]
    if len(closed_candles) < SLOW + 1:
        raise RuntimeError(
            f"Need at least {SLOW + 1} closed daily candles; received {len(closed_candles)}"
        )
    return closed_candles


def ma(prices, window, index):
    return sum(prices[index - window + 1 : index + 1]) / window


def net_return_pct(entry_price, exit_price):
    """Return percentage after the modeled buy-side and sell-side fees."""
    return ((exit_price / entry_price) * (1 - FEE) ** 2 - 1) * 100


def load_state():
    state = STATE_DEFAULTS.copy()
    if os.path.exists(STATE_JSON):
        with open(STATE_JSON, encoding="utf-8") as state_file:
            saved_state = json.load(state_file)
        if not isinstance(saved_state, dict):
            raise ValueError("Paper trading state must be a JSON object")
        state.update(saved_state)
    return state


def save_state(state):
    os.makedirs(DATA, exist_ok=True)
    temp_path = f"{STATE_JSON}.tmp"
    with open(temp_path, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2)
        state_file.write("\n")
    os.replace(temp_path, STATE_JSON)


def log_trade(row):
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(TRADES_CSV) and os.path.getsize(TRADES_CSV) > 0:
        with open(TRADES_CSV, newline="", encoding="utf-8") as trade_file:
            existing_trades = csv.DictReader(trade_file)
            if any(
                trade.get("date") == row["date"]
                and trade.get("action") == row["action"]
                for trade in existing_trades
            ):
                return False

    is_new_file = not os.path.exists(TRADES_CSV)
    fields = [
        "time_utc",
        "date",
        "action",
        "price",
        "amount_btc",
        "cash_after",
        "reason",
    ]
    with open(TRADES_CSV, "a", newline="", encoding="utf-8") as trade_file:
        writer = csv.DictWriter(trade_file, fieldnames=fields)
        if is_new_file:
            writer.writeheader()
        writer.writerow(row)
    return True


def run_once():
    closes = fetch_daily()
    dates = [
        datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        for timestamp, _ in closes
    ]
    prices = [price for _, price in closes]
    state = load_state()
    last_signal_date = state.get("last_signal_date")
    if last_signal_date:
        if last_signal_date not in dates:
            raise RuntimeError(
                f"Last processed candle {last_signal_date} is outside the available "
                f"history ({dates[0]} to {dates[-1]}); cannot safely catch up"
            )
        start_index = dates.index(last_signal_date) + 1
        if start_index < SLOW:
            raise RuntimeError(
                "Not enough candle history to safely catch up from "
                f"{last_signal_date}; need {SLOW} warm-up candles"
            )
    else:
        # A fresh paper account starts from today's latest signal, not a replay
        # of every historical crossover in the downloaded data.
        start_index = len(prices) - 1

    if start_index >= len(prices):
        print(f"[{dates[-1]}] 本根 K 线已处理过,无新信号。")
        return

    for index in range(start_index, len(prices)):
        fast_ma = ma(prices, FAST, index)
        slow_ma = ma(prices, SLOW, index)
        previous_fast_ma = ma(prices, FAST, index - 1)
        previous_slow_ma = ma(prices, SLOW, index - 1)
        difference = fast_ma - slow_ma
        previous_difference = previous_fast_ma - previous_slow_ma
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if previous_difference <= 0 < difference and not state["in_pos"]:
            amount = state["cash"] * (1 - FEE) / prices[index]
            state.update(
                {
                    "pos": amount,
                    "cash": 0.0,
                    "in_pos": True,
                    "entry_px": prices[index],
                    "entry_date": dates[index],
                    "last_signal_date": dates[index],
                }
            )
            log_trade(
                {
                    "time_utc": now,
                    "date": dates[index],
                    "action": "模拟买入",
                    "price": round(prices[index], 1),
                    "amount_btc": round(amount, 6),
                    "cash_after": round(state["cash"], 2),
                    "reason": (
                        f"MA{FAST}({fast_ma:,.0f})上穿MA{SLOW}({slow_ma:,.0f}),趋势转多"
                    ),
                }
            )
            print(f"[{dates[index]}] 模拟买入 @ {prices[index]:,.0f}")
        elif previous_difference >= 0 > difference and state["in_pos"]:
            cash = state["pos"] * prices[index] * (1 - FEE)
            trade_return = net_return_pct(state["entry_px"], prices[index])
            log_trade(
                {
                    "time_utc": now,
                    "date": dates[index],
                    "action": "模拟卖出",
                    "price": round(prices[index], 1),
                    "amount_btc": round(state["pos"], 6),
                    "cash_after": round(cash, 2),
                    "reason": (
                        f"MA{FAST}({fast_ma:,.0f})下穿MA{SLOW}({slow_ma:,.0f}),"
                        f"趋势转空,本笔净收益{trade_return:+.2f}%"
                    ),
                }
            )
            state.update(
                {
                    "pos": 0.0,
                    "cash": cash,
                    "in_pos": False,
                    "last_signal_date": dates[index],
                }
            )
            print(
                f"[{dates[index]}] 模拟卖出 @ {prices[index]:,.0f}, "
                f"本笔净收益 {trade_return:+.2f}%"
            )
        else:
            state["last_signal_date"] = dates[index]
            equity = state["cash"] + state["pos"] * prices[index]
            position = "持仓" if state["in_pos"] else "空仓"
            print(f"[{dates[index]}] 无信号。当前权益 {equity:,.0f} USD ({position})")

        # Persist every processed candle so an interruption resumes at the
        # next unprocessed day instead of skipping intermediate crossovers.
        save_state(state)


def seconds_until_next_check(now):
    next_check = now.replace(hour=0, minute=10, second=0, microsecond=0)
    if now >= next_check:
        next_check += timedelta(days=1)
    return (next_check - now).total_seconds()


def run_loop():
    print("纸面交易引擎常驻运行中,每天 00:10 UTC 检查一次。Ctrl+C 退出。")
    while True:
        now = datetime.now(timezone.utc)
        delay = seconds_until_next_check(now)
        next_check = now + timedelta(seconds=delay)
        print(f"下次检查: {next_check.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        time.sleep(delay)
        try:
            run_once()
        except Exception as error:
            failed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{failed_at} UTC] 出错: {error}", file=sys.stderr)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        run_loop()
    else:
        run_once()
