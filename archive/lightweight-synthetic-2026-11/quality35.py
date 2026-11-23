"""Point-in-time packet contracts for trusted synthetic research."""
from dataclasses import dataclass
import math

from synthetic_manifest27 import verify_manifest


@dataclass(frozen=True)
class Feature:
    dataset: str
    sample: str
    tick: int
    values: object
    available: object
    source: object
    stale_marked: bool = False


@dataclass(frozen=True)
class Label:
    dataset: str
    sample: str
    tick: int
    target_end: int
    available: int
    target: float


def finite(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def identity(dataset, tick):
    return f"{dataset}:{tick}"


def feature_status(packet, dataset, tick):
    """Unknown provenance can neither authorize a forecast nor feed training."""
    if (not isinstance(packet, Feature) or packet.dataset != dataset or
            type(packet.tick) is not int or packet.tick != tick or packet.sample != identity(dataset, tick)
            or type(packet.stale_marked) is not bool):
        return "invalid"
    if packet.values is None:
        return ("unavailable" if packet.available is None and packet.source is None
                and not packet.stale_marked else "invalid")
    if (type(packet.values) is not tuple or len(packet.values) != 1
            or not all(map(finite, packet.values))):
        return "invalid"
    if (type(packet.available) is not int or not 0 <= packet.available <= tick):
        return "invalid"
    if packet.source is None:
        return "invalid" if packet.stale_marked else "unknown"
    if type(packet.source) is not int or not 0 <= packet.source <= packet.available:
        return "invalid"
    if packet.source != tick:
        return "degraded" if packet.stale_marked else "invalid"
    return "invalid" if packet.stale_marked else "valid"


def check_label(packet, dataset, now, seen, arrived):
    """Check actual processing clock against the source-declared availability."""
    if (not isinstance(packet, Label) or packet.dataset != dataset or
            type(packet.tick) is not int or packet.sample != identity(dataset, packet.tick)
            or packet.sample not in seen or type(packet.target_end) is not int
            or packet.target_end != packet.tick + 1 or type(packet.available) is not int
            or not packet.target_end < packet.available <= now or not finite(packet.target)):
        raise ValueError("invalid label identity, value or release timing")
    if packet.sample in arrived:
        kind = "duplicate" if packet.target == arrived[packet.sample]["target"] else "conflict"
        raise ValueError(f"{kind} label")


def check_population(rows, population, expected_digest):
    return verify_manifest(rows, population, expected_manifest_sha256=expected_digest,
                           anchor_source="synthetic fixture commitment before models or losses")


def check_split(train_ticks, evaluation_ticks, first_decision):
    train_ticks, evaluation_ticks = tuple(train_ticks), tuple(evaluation_ticks)
    if (any(type(t) is not int or t < 0 for t in (*train_ticks, *evaluation_ticks))
            or len(set(train_ticks)) != len(train_ticks)
            or len(set(evaluation_ticks)) != len(evaluation_ticks)
            or set(train_ticks) & set(evaluation_ticks) or
            any(t + 1 > first_decision for t in train_ticks) or
            any(t < first_decision for t in evaluation_ticks)):
        raise ValueError("initial training and evaluation clocks overlap")

