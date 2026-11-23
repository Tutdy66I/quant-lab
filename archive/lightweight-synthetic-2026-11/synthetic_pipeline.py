"""Small in-memory synthetic example; not a trading system or source attestation."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Bar:
    day: int
    close: float


def two_lag_returns(bars, cutoff):
    """Compute two returns using only rows at or before an integer cutoff.

    Rows must be increasing, finite and positive. Any future row is rejected,
    rather than silently accepted as a valid dependency response.
    """
    if isinstance(cutoff, bool) or not isinstance(cutoff, int):
        raise ValueError("cutoff must be an integer")
    prior = None
    for bar in bars:
        if not isinstance(bar, Bar) or isinstance(bar.day, bool) or not isinstance(bar.day, int):
            raise ValueError("invalid bar")
        if prior is not None and bar.day <= prior:
            raise ValueError("days must be strictly increasing")
        if bar.day > cutoff:
            raise ValueError("future row in dependency response")
        if isinstance(bar.close, bool) or not isinstance(bar.close, (int, float)):
            raise ValueError("invalid close")
        if not math.isfinite(bar.close) or bar.close <= 0:
            raise ValueError("close must be finite and positive")
        prior = bar.day
    if len(bars) < 3:
        raise ValueError("three rows required")
    a, b, c = (bar.close for bar in bars[-3:])
    result = c / b - 1.0, b / a - 1.0
    if not all(math.isfinite(value) for value in result):
        raise ValueError("nonfinite feature")
    return result


class SyntheticMemorySource:
    """In-memory fake provider, with explicit access trace."""
    def __init__(self, bars):
        self._bars = tuple(bars)
        self.calls = []

    def fetch_until(self, cutoff):
        self.calls.append(cutoff)
        return tuple(bar for bar in self._bars if bar.day <= cutoff)


def synthetic_predict(source, cutoff):
    """Fixed arithmetic example to test source/feature integration only."""
    rows = source.fetch_until(cutoff)
    latest, preceding = two_lag_returns(rows, cutoff)
    prediction = 0.25 * latest + 0.5 * preceding
    if not math.isfinite(prediction):
        raise ValueError("nonfinite prediction")
    return prediction
