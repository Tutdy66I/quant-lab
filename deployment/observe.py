"""Archive read-only paper evidence; never start bots or place orders."""
import json
import math
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
HK = timezone(timedelta(hours=8))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(path, query):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query)]


def write_public_report(evidence):
    """Publish only selected simulation metrics, never raw local runtime data."""
    ma = evidence.get("ma", {})
    health = ma.get("health", {})
    daily = ma.get("daily", [])
    final_days = sum(bool(row["final"]) for row in daily)
    lines = [
        "# 纸面观察进度", "",
        f"报告采集时间：{evidence['observed_at']}（UTC）。这是定时快照，不能用来证明此刻进程仍在线。", "",
        "当前仅持续观察原生 Python MA20/60；Docker 和 Hummingbot 已按用户要求暂停。", "",
        "| 指标 | 最近记录 |", "|---|---|",
        f"| 最近账本检查 | {health.get('observed_at', '未知')} |",
        f"| 最近账本检查结果 | {health.get('status', '未知')} |",
        f"| 最近已收盘行情日 | {health.get('latest_closed_date', '未知')} |",
        f"| 模拟权益（USD，当日可能暂定） | {health.get('equity', '未知')} |",
        f"| BTC 持仓 | {health.get('pos', '未知')} |",
        f"| 每日权益记录 / 已最终确认 | {len(daily)} / {final_days} |",
        f"| 模型成交记录数（含可能的补跑） | {len(ma.get('trades', []))} |",
        f"| 下次计划检查 | {health.get('next_check_at', '未知')} |", "",
        "最近采集有异常，需要核对本机记录。" if evidence["errors"] else "本次证据采集无错误。", "",
        "模型成交数不是自然完成交易数；历史补跑和自然新增观察须在复盘时分别统计。", "",
        "日线检查为香港时间每天 08:10，定时跟进为 08:25。四周复盘计划于 2026-11-17 或之后首次检查生成；停机、睡眠及断网缺口如实记录。", "",
        "回测基线净收益 +2.29%、最大回撤 36.36%，属于历史样本，不代表上述纸面账户表现。当前观察样本不足以判断盈利能力。", "",
        "仅同步这份汇总；原始账本、日志、快照和本机配置保留在本地。", "",
    ]
    (ROOT / "docs/纸面观察进度.md").write_text("\n".join(lines), encoding="utf-8")


def collect():
    now = datetime.now(timezone.utc)
    evidence = {"observed_at": now.isoformat(), "local_date": now.astimezone(HK).date().isoformat()}
    errors = []
    native = ROOT / "runtime/paper_v2/paper.sqlite3"
    try:
        # One transaction keeps health, daily valuations and trades consistent.
        with sqlite3.connect(native.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            health = json.loads(db.execute("SELECT payload FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0])
            evidence["ma"] = {
                "health": health,
                "daily": [dict(row) for row in db.execute("SELECT * FROM daily ORDER BY date")],
                "trades": [json.loads(row[0]) for row in db.execute("SELECT payload FROM trades ORDER BY execution_date, action")],
                "runs": [dict(row) for row in db.execute("SELECT id, observed_at, status FROM runs ORDER BY id")],
            }
        if health["status"] not in ("ok", "recovered") or now > datetime.fromisoformat(health["next_check_at"]) + timedelta(minutes=5):
            errors.append("MA health is failed or overdue; check the background process.")
    except Exception as error:
        errors.append(f"MA evidence: {error}")
    if (ROOT / "runtime/hummingbot/AUTOSTART_DISABLED").exists():
        evidence["hummingbot"] = {"status": "paused_by_user", "equity_usd": None}
    else:
        try:
            directory = ROOT / "runtime/hummingbot"
            status = read_json(directory / "data/bot/status.json")
            meta = read_json(directory / "data/bot/meta.json")
            fresh = 0 <= now.timestamp() - status["updated_at"] <= 120
            evidence["hummingbot"] = {
                "status": status,
                "session_started_at": meta["started_at"],
                "snapshot_fresh": fresh,
                "trade_fills_raw": rows(directory / "data/paper_btc.sqlite", 'SELECT * FROM "TradeFill" ORDER BY timestamp'),
                "equity_usd": None,
                "valuation": "Public bid/ask midpoint; before liquidation costs. Compare only within one session; paper balances reset on restart.",
            }
            if not fresh or not status.get("running"):
                errors.append("Hummingbot is stopped or its snapshot is stale; equity left unknown.")
            else:
                source = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
                with urlopen(source, timeout=20) as response:
                    quote = json.load(response)
                if quote.get("error"):
                    raise ValueError(quote["error"])
                ticker = next(iter(quote["result"].values()))
                bid, ask = float(ticker["b"][0]), float(ticker["a"][0])
                if not all(math.isfinite(value) and value > 0 for value in (bid, ask)) or bid > ask:
                    raise ValueError("Invalid public bid/ask")
                balance = status["balances"]["kraken_paper_trade"]
                evidence["hummingbot"].update({
                    "quote": {"source": source, "fetched_at": datetime.now(timezone.utc).isoformat(), "raw": quote},
                    "mark_usd": (bid + ask) / 2,
                    "equity_usd": float(balance["USD"]) + float(balance["BTC"]) * (bid + ask) / 2,
                })
        except Exception as error:
            errors.append(f"Hummingbot evidence: {error}")
    evidence["errors"] = errors
    directory = ROOT / "runtime/observation/snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (now.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_public_report(evidence)
    print(json.dumps({"snapshot": str(path), "errors": errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(collect())
