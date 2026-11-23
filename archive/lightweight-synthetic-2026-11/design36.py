"""Round 36 paired response schedules, using unchanged Round 35 learners."""
from itertools import product
import random

from baselines35 import run_online
from feedback35 import packets, truth_path
from quality35 import Label, identity

MODES = ("mcar","mar","mar_omitted","mar_overlap","mnar_large","mnar_small","mnar_latent")
SCENARIOS = ("stable_signal","relation_reverse")
RECOVERY = 112
RECOVERY_ARMS = tuple(product((8,45),(0,.55),("short","long","batch"),("clean","high"))) + (
    (8,1.,"batch","clean"),(45,1.,"batch","clean"))


def observable_p(x, mode):
    if mode == "mcar":
        return .56
    if mode in ("mar","mar_omitted"):
        return .25 if x > 0 else .87
    if mode == "mar_overlap":
        return .025 if x > 0 else .87
    raise ValueError("no observed-covariate oracle for this mechanism")


def probability(row, scenario, mode):
    x, y, t = row["features"][0], row["target"], row["time"]
    if mode == "mnar_large":
        return .18 if abs(y) > .8 else .87
    if mode == "mnar_small":
        return .87 if abs(y) > .8 else .18
    if mode == "mnar_latent":
        coef = -.9 if scenario == "relation_reverse" and t >= 90 else .9
        return .20 if abs(y-coef*x) > .30 else .88
    return observable_p(x,mode)


def make_plan(rows, scenario, seed, *, mode=None, initial=45, missing=0., delay="short"):
    rng = random.Random(f"36-responses:{seed}")
    jitter = random.Random(f"36-lags:{seed}")
    plan = []
    for t,row in enumerate(rows):
        draw = rng.random()
        lag = jitter.randint(16,35)
        if t < 60:
            # Exactly the most recent 8 or 45 labels legally available by tick 59.
            at = t+2 if 58-initial <= t <= 57 else None
        else:
            offered = probability(row, scenario, mode) if mode else 1.-missing
            at = None if draw >= offered else t+2
            if at is not None and delay == "batch" and t < RECOVERY-2:
                at = RECOVERY
            elif at is not None and delay == "long" and t < RECOVERY-2:
                at = t+lag
        plan.append(at)
    return plan


def stream(dataset, rows, seed, quality, plan):
    """Strip the R35 feedback, retaining only its point-in-time feature adapter."""
    pending = {}
    for t,packet,_ in packets(dataset, rows, seed, quality, "mcar0", "short"):
        if t < len(rows) and plan[t] is not None:
            due = plan[t]
            pending.setdefault(due,[]).append(Label(dataset,identity(dataset,t),t,t+1,due,rows[t]["target"]))
        yield t,packet,tuple(pending.pop(t,()))


def fixture(scenario, seed, config_hash):
    return truth_path(scenario,seed,config_hash)


def execute(dataset, rows, seed, arm, quality, plan):
    return run_online(dataset,arm,stream(dataset,rows,seed,quality,plan))

