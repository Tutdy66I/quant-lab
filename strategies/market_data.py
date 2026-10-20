"""Validated daily OHLC snapshots from Kraken's public, unauthenticated API."""
import hashlib
import json
import math
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

SOURCE = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440"


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float

    @property
    def date(self):
        return datetime.fromtimestamp(self.timestamp, timezone.utc).date().isoformat()


def parse_daily_rows(raw):
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError("Expected at least three Kraken OHLC rows")
    candles = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 5:
            raise ValueError("Invalid OHLC row")
        try:
            candle = Candle(int(row[0]), *(float(value) for value in row[1:5]))
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Invalid OHLC values") from error
        if candle.timestamp != row[0] or candle.timestamp % 86400:
            raise ValueError("Expected integer UTC daily timestamps")
        if not all(math.isfinite(p) and p > 0 for p in (candle.open, candle.high, candle.low, candle.close)):
            raise ValueError("OHLC prices must be finite and positive")
        if not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
            raise ValueError("Invalid OHLC price range")
        candles.append(candle)
    if any(b.timestamp - a.timestamp != 86400 for a, b in zip(candles, candles[1:])):
        raise ValueError("Daily candles must be ordered, unique, and contiguous")
    return candles


def snapshot_from_rows(rows, now, source=SOURCE):
    if now.tzinfo is None:
        raise ValueError("Observation time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    candles = parse_daily_rows(rows)
    today = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if candles[-1].timestamp != today or candles[-2].timestamp != today - 86400:
        raise ValueError("Stale or future market data: need today's open and yesterday's closed candle")
    if len(candles) < 62:
        raise ValueError("Need at least 61 closed candles and today's open")
    canonical = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return {
        "metadata": {
            "source": source, "pair": "XBTUSD", "interval_minutes": 1440,
            "fetched_at": now.isoformat(), "latest_closed_date": candles[-2].date,
            "unconfirmed_date": candles[-1].date, "rows": len(rows),
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "hash_normalization": "json_compact_ascii",
        },
        "rows": rows,
    }


def fetch_snapshot(now=None):
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "quant-lab paper observer/2"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    if payload.get("error"):
        raise RuntimeError(f"Kraken API error: {payload['error']}")
    result = payload.get("result", {})
    pairs = [key for key in result if key != "last"]
    if len(pairs) != 1:
        raise ValueError("Expected exactly one Kraken OHLC pair")
    return snapshot_from_rows(result[pairs[0]], now or datetime.now(timezone.utc))
