"""Generate synthetic truth and a separate, time-gated packet iterator."""
from collections import defaultdict
import random

from prequential_synthetic31 import generate
from quality35 import Feature, Label, identity
from synthetic_manifest27 import build_manifest, manifest_sha256

WARMUP = 60
STOP = 180
CUTOFF = 181
RECOVERY = 135
FEATURE_RATES = {"clean": (0., 0.), "medium": (.15, .10), "high": (.30, .20)}
MCAR_RATES = {"mcar0": 0., "mcar25": .25, "mcar55": .55}


def truth_path(scenario, seed, config_hash):
    source = generate(scenario, seed)
    dataset = f"synthetic35:{scenario}:{seed}"
    rows = [dict(time=r["decision_time"], features=r["features"],
                 previous=source[max(0, i-1)]["target"], target=r["target"])
            for i, r in enumerate(source)]
    population = build_manifest(rows, dataset_id=dataset, dataset_fingerprint=config_hash,
                                config_hash=config_hash)
    return dataset, rows, population, manifest_sha256(population)


def mechanism(target, feature, mode):
    """Return a probability of never being delivered, conditional on the generator inputs."""
    if mode in MCAR_RATES:
        return MCAR_RATES[mode]
    if mode == "mar":
        return .70 if feature > 0. else .15
    if mode == "mnar":
        return .85 if abs(target) > .8 else .15
    raise ValueError("unknown missingness")


def release_plan(rows, seed, missingness, latency):
    """Offline plan, including labels never returned and arrivals after cutoff."""
    if latency not in {"short", "long", "batch"}:
        raise ValueError("unknown delay level")
    missing = random.Random(f"35-missing:{seed}")
    delays = random.Random(f"35-delay:{seed}")
    plan = []
    for tick, row in enumerate(rows):
        missing_draw = missing.random()
        random_lag = delays.randint(8, 32)
        drop = mechanism(row["target"], row["features"][0], missingness)
        if tick >= WARMUP and missing_draw < drop:
            plan.append(None)
        elif latency == "batch" and 80 <= tick < 108:
            plan.append(112)
        else:
            plan.append(tick + (random_lag if tick >= WARMUP and latency == "long" else 2))
    return plan


def packets(dataset, rows, seed, feature_level, missingness, latency):
    """Yield a feature and only the labels actually arriving on each clock tick."""
    if feature_level not in FEATURE_RATES or latency not in {"short", "long", "batch"}:
        raise ValueError("unknown quality or delay level")
    if missingness == "mar" and feature_level != "clean":
        raise ValueError("MAR comparison requires its conditioning feature to be observed")
    if len(rows) != STOP:
        raise ValueError("incomplete latent path")

    missing_rate, stale_rate = FEATURE_RATES[feature_level]
    features = random.Random(f"35-feature:{seed}")
    plan = release_plan(rows, seed, missingness, latency)
    queue = defaultdict(list)
    fresh_tick, fresh_value = None, None
    for tick in range(CUTOFF + 1):
        packet = None
        if tick < STOP:
            row = rows[tick]
            feature_draw = features.random()
            x, target = row["features"][0], row["target"]
            sample = identity(dataset, tick)
            impaired = WARMUP <= tick < RECOVERY
            if impaired and feature_draw < missing_rate:
                packet = Feature(dataset, sample, tick, None, None, None)
            elif impaired and feature_draw < missing_rate + stale_rate:
                packet = Feature(dataset, sample, tick, (fresh_value,), fresh_tick, fresh_tick, True)
            else:
                packet = Feature(dataset, sample, tick, (x,), tick, tick)
                fresh_tick, fresh_value = tick, x

            release = plan[tick]
            if release is not None:
                queue[release].append(Label(dataset, sample, tick, tick+1, release, target))
        yield tick, packet, tuple(queue.pop(tick, ()))

