"""Daily open model observer: previous close signal, next open with fees/slippage.

Public data only. Historical modeled fills are labeled with observation delay.
SQLite is authoritative; JSON/CSV are regenerable views. Python 3.10+.
"""
import argparse
import contextlib
import csv
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from strategies.execution import FEE, SLIPPAGE, buy_fill, crossover, sell_fill
from strategies.market_data import fetch_snapshot, parse_daily_rows, snapshot_from_rows

DEFAULT_DATA = ROOT / "runtime/paper_v2"
MODEL = {"version": 2, "fast": 20, "slow": 60, "fee": FEE, "slippage": SLIPPAGE, "execution": "daily_open_model"}
INITIAL = {"cash": 10000.0, "pos": 0.0, "entry_capital": 0.0, "entry_price": 0.0, "entry_date": "", "last_execution_date": ""}


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + f".{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def database(data_dir):
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "paper.sqlite3", timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades (
                execution_date TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (execution_date, action));
            CREATE TABLE IF NOT EXISTS daily (
                date TEXT PRIMARY KEY, signal_date TEXT NOT NULL, observed_at TEXT NOT NULL,
                action TEXT NOT NULL, cash REAL NOT NULL, pos REAL NOT NULL,
                mark REAL NOT NULL, equity REAL NOT NULL, final INTEGER NOT NULL,
                data_sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL,
                status TEXT NOT NULL, payload TEXT NOT NULL);
        """)
        yield db
    finally:
        db.close()


def get_meta(db, key, default):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default.copy()


def set_meta(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))


def export_views(data_dir):
    """Hold the database writer lock while exporting a consistent latest view."""
    directory = Path(data_dir)
    with database(directory) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            state = get_meta(db, "state", INITIAL)
            latest = db.execute("SELECT payload FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            health = json.loads(latest[0]) if latest else {"status": "never_run"}
            atomic_json(directory / "state.json", {"model": get_meta(db, "model", MODEL), **state})
            atomic_json(directory / "health.json", health)
            trades = [json.loads(row[0]) for row in db.execute("SELECT payload FROM trades ORDER BY execution_date, action")]
            daily = [dict(row) for row in db.execute("SELECT * FROM daily ORDER BY date")]
            fields = ["execution_date", "signal_date", "observed_at", "action", "raw_open", "fill_price", "quantity", "fee", "cash_after", "pos_after", "return_pct", "reason", "mode", "delay_seconds", "data_sha256"]
            for name, rows, columns in (("trades.csv", trades, fields), ("equity.csv", daily, ["date", "signal_date", "observed_at", "action", "cash", "pos", "mark", "equity", "final", "data_sha256"])):
                temp = directory / f"{name}.{uuid4().hex}.tmp"
                try:
                    with temp.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
                        writer.writeheader()
                        writer.writerows(rows)
                    os.replace(temp, directory / name)
                finally:
                    temp.unlink(missing_ok=True)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise


def record_error(data_dir, now, error):
    payload = {"status": "error", "observed_at": now.isoformat(), "error": str(error), "retry_seconds": 60, "mode": MODEL["execution"]}
    with database(data_dir) as db:
        previous = db.execute("SELECT observed_at FROM runs WHERE status != 'error' ORDER BY id DESC LIMIT 1").fetchone()
        payload["last_success_at"] = previous[0] if previous else None
        db.execute("INSERT INTO runs (observed_at,status,payload) VALUES (?,?,?)", (payload["observed_at"], "error", json.dumps(payload)))
    export_views(data_dir)


def run_once(data_dir=DEFAULT_DATA, now=None, snapshot=None):
    explicit_time = now is not None
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    directory = Path(data_dir)
    try:
        snapshot = fetch_snapshot() if snapshot is None else snapshot
        if not explicit_time:
            now = datetime.now(timezone.utc)
        validated = snapshot_from_rows(snapshot["rows"], now, snapshot["metadata"]["source"])
        if validated["metadata"]["sha256"] != snapshot["metadata"]["sha256"]:
            raise ValueError("Snapshot fingerprint mismatch")
        fetched_at = datetime.fromisoformat(snapshot["metadata"]["fetched_at"])
        if fetched_at.tzinfo is None or not 0 <= (now - fetched_at).total_seconds() <= 3600:
            raise ValueError("Snapshot capture time is stale or in the future")
        candles = parse_daily_rows(snapshot["rows"])
        dates, prices = [c.date for c in candles], [c.close for c in candles]
        snapshots = directory / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        name = now.strftime("%Y%m%dT%H%M%S%fZ") + "_" + snapshot["metadata"]["sha256"][:12] + ".json"
        atomic_json(snapshots / name, snapshot)
        with database(directory) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if get_meta(db, "model", MODEL) != MODEL:
                    raise ValueError("Account model has changed; use a new --data-dir")
                state = get_meta(db, "state", INITIAL)
                last = state["last_execution_date"]
                if last:
                    if last not in dates:
                        raise ValueError(f"Checkpoint {last} outside available history; cannot safely catch up")
                    start = dates.index(last) + 1
                    if start < MODEL["slow"] + 1:
                        raise ValueError("Insufficient warm-up for checkpoint")
                else:
                    start = len(candles) - 1
                previous_run = db.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
                count = 0
                for index in range(start, len(candles)):
                    signal = crossover(prices, MODEL["fast"], MODEL["slow"], index - 1)
                    candle = candles[index]
                    action, trade = "hold", None
                    common = {"execution_date": candle.date, "signal_date": dates[index - 1], "observed_at": now.isoformat(), "raw_open": candle.open, "mode": MODEL["execution"], "delay_seconds": (now.timestamp() - candle.timestamp), "data_sha256": snapshot["metadata"]["sha256"]}
                    if signal == "buy" and state["pos"] == 0:
                        capital = state["cash"]
                        fill, quantity, fee = buy_fill(capital, candle.open)
                        state.update(cash=0.0, pos=quantity, entry_capital=capital, entry_price=fill, entry_date=candle.date)
                        action = "buy"
                        trade = {**common, "action": action, "fill_price": fill, "quantity": quantity, "fee": fee, "return_pct": None, "reason": "MA20 crosses above MA60 at previous close"}
                    elif signal == "sell" and state["pos"] > 0:
                        quantity = state["pos"]
                        fill, cash, fee = sell_fill(quantity, candle.open)
                        net_return = (cash / state["entry_capital"] - 1) * 100
                        state.update(cash=cash, pos=0.0, entry_capital=0.0, entry_price=0.0, entry_date="")
                        action = "sell"
                        trade = {**common, "action": action, "fill_price": fill, "quantity": quantity, "fee": fee, "return_pct": net_return, "reason": "MA20 crosses below MA60 at previous close"}
                    if trade:
                        trade.update(cash_after=state["cash"], pos_after=state["pos"])
                        db.execute("INSERT INTO trades VALUES (?,?,?)", (candle.date, action, json.dumps(trade, allow_nan=False)))
                    final = index < len(candles) - 1
                    db.execute("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?)", (candle.date, dates[index - 1], now.isoformat(), action, state["cash"], state["pos"], candle.close, state["cash"] + state["pos"] * candle.close, int(final), snapshot["metadata"]["sha256"]))
                    state["last_execution_date"] = candle.date
                    count += 1
                for candle in candles:
                    db.execute("UPDATE daily SET mark=?,equity=cash+pos*?,final=?,data_sha256=?,observed_at=? WHERE date=?", (candle.close, candle.close, int(candle.date < dates[-1]), snapshot["metadata"]["sha256"], now.isoformat(), candle.date))
                set_meta(db, "model", MODEL)
                set_meta(db, "state", state)
                health = {
                    "status": "recovered" if previous_run and previous_run[0] == "error" else "ok",
                    "observed_at": now.isoformat(), "fetched_at": snapshot["metadata"]["fetched_at"],
                    "source": snapshot["metadata"]["source"], "latest_closed_date": dates[-2],
                    "unconfirmed_date": dates[-1], "processed_days": count,
                    "last_execution_date": state["last_execution_date"],
                    "cash": state["cash"], "pos": state["pos"],
                    "equity": state["cash"] + state["pos"] * candles[-1].close,
                    "mark_is_final": False, "mode": MODEL["execution"],
                    "data_sha256": snapshot["metadata"]["sha256"], "snapshot": f"snapshots/{name}",
                    "next_check_at": (now + timedelta(seconds=seconds_until_next_check(now))).isoformat(),
                }
                db.execute("INSERT INTO runs (observed_at,status,payload) VALUES (?,?,?)", (now.isoformat(), health["status"], json.dumps(health, allow_nan=False)))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        export_views(directory)
        print(json.dumps(health, ensure_ascii=False), flush=True)
        return health
    except Exception as error:
        record_error(directory, now, error)
        raise


def seconds_until_next_check(now):
    now = now.astimezone(timezone.utc)
    target = now.replace(hour=0, minute=10, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def read_status(data_dir, now=None):
    path = Path(data_dir) / "paper.sqlite3"
    if not path.exists():
        return {"status": "never_run"}
    with database(data_dir) as db:
        latest = db.execute("SELECT payload FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if latest is None:
        return {"status": "never_run"}
    health = json.loads(latest[0])
    now = now or datetime.now(timezone.utc)
    if health["status"] in ("ok", "recovered"):
        due = datetime.fromisoformat(health["next_check_at"]) + timedelta(minutes=5)
        if now > due:
            health["last_status"], health["status"] = health["status"], "overdue"
    return health


def run_loop(data_dir):
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stop = directory / "STOP"
    stop.unlink(missing_ok=True)
    atomic_json(directory / "runner.json", {"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(), "mode": MODEL["execution"]})
    while not stop.exists():
        try:
            run_once(directory)
            delay = seconds_until_next_check(datetime.now(timezone.utc))
        except Exception as error:
            print(f"Paper observer error; retrying in 60s: {error}", file=sys.stderr, flush=True)
            delay = 60
        deadline = time.monotonic() + delay
        while not stop.exists() and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--loop", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.status:
        health = read_status(args.data_dir)
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return 0 if health["status"] in ("ok", "recovered") else 1
    elif args.stop:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        (args.data_dir / "STOP").touch()
    elif args.loop:
        run_loop(args.data_dir)
    else:
        run_once(args.data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
