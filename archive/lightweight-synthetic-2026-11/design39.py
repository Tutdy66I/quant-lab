"""Frozen artificial timing policies and correlated group mechanisms."""
import math
import random

from arrival39 import CUTOFF, COLUMN_GROUP, SOURCE, STOP, VERSION, Reading
from publication37 import canonical_sha
from quality35 import Label, identity
from synthetic_manifest27 import build_manifest, manifest_sha256

TIMING=("instant","short","long","delay_fade","missing","expired","timely_fade","short_abstain")
GROUPS=("stable","weak","reverse","gone","none","redundant","partial","fade","interaction","thin_partial")
SCENARIOS=("timing","timing_mnar",*GROUPS)
PERTURB=("stable","partial","interaction")
DELAY={"instant":0,"short":2,"long":5,"delay_fade":2,"missing":None,"expired":9,"timely_fade":0,"short_abstain":2}
FEATURE_SEARCH=("base","full","single","group_lag")


def arms(scenario):
    if scenario=="timing":
        return TIMING
    if scenario=="timing_mnar":
        return ("instant","short","short_abstain")
    if scenario not in GROUPS:
        raise ValueError("unknown mechanism")
    return FEATURE_SEARCH if scenario in PERTURB else FEATURE_SEARCH[:-1]


def fixture(scenario,seed,config_sha):
    if scenario not in SCENARIOS:
        raise ValueError("unknown artificial mechanism")
    rng=random.Random(f"39-structure:{seed}")
    dropout=random.Random(f"39-transport:{seed}")
    response=random.Random(f"39-response:{seed}")
    dataset=f"synthetic39:{scenario}:{seed}"
    timing=scenario.startswith("timing")
    x,z,w=0.,0.,0.
    previous=0.
    truth,columns,drop,release=[],[],[],[]
    group_history=[]
    for tick in range(-12,STOP):
        ex,ez,ew,error,proxy=(rng.gauss(0,1) for _ in range(5))
        phi=.85 if timing else .35
        x=phi*x+math.sqrt(1-phi**2)*ex
        z=.4*z+math.sqrt(.84)*ez
        w=.4*w+math.sqrt(.84)*ew
        if scenario=="redundant":
            g1,g2=x+.003*z,x+.003*w
        elif scenario in ("partial","thin_partial"):
            g1,g2=z,.8*z+.6*w
        else:
            g1,g2=z,.98*z+math.sqrt(1-.98**2)*w
        group_history.append((g1,g2))
        if tick<0:
            continue
        coef=.75
        if scenario in ("none","redundant","interaction") or timing:
            coef=0.
        elif tick>=90:
            if scenario=="weak":
                coef=.25
            elif scenario=="reverse":
                coef=-.75
            elif scenario=="gone":
                coef=0.
            elif scenario=="fade":
                coef=.75*max(0.,(125-tick)/35)
        y=1.1*x+coef*z+.35*error
        if scenario in ("partial","thin_partial"):
            y+=.55*w
        if scenario=="interaction":
            y+=.95*z*w
        r=max(0.,min(1.,(125-tick)/35))
        late_proxy=r*x+math.sqrt(1-r*r)*proxy
        delay=tick>=60 and dropout.random()<.65
        draw=response.random()
        at=tick+2
        if scenario=="thin_partial" and tick<60 and not 38<=tick<=57:
            at=None
        if scenario=="timing_mnar" and tick>=60 and draw >= (
                (.17 if abs(y)>1.0 else .86)*(.75 if delay else 1.)):
            at=None
        truth.append(dict(time=tick,features=[x,z,w],previous=previous,target=y))
        previous=y
        group_lag=group_history[tick]
        columns.append(dict(x=x,g1=g1,g2=g2,proxy=late_proxy,lag1=group_lag[0],lag2=group_lag[1]))
        drop.append(delay)
        release.append(at)
    population=build_manifest(truth,dataset_id=dataset,dataset_fingerprint=config_sha,config_hash=config_sha)
    return dict(dataset=dataset,truth=truth,columns=columns,delay_mask=drop,label_plan=release,
                manifest=population,manifest_sha256=manifest_sha256(population))


def contract(data,scenario,arm):
    if arm not in arms(scenario):
        raise ValueError("unregistered arm")
    cols=("x",) if scenario.startswith("timing") or arm=="base" else (
         ("x","g1") if arm=="single" else ("x","g1","g2"))
    ttl=0 if arm=="short_abstain" else 6
    events=[]
    d=data["dataset"]
    for t,row in enumerate(data["columns"]):
        lag=DELAY[arm] if scenario.startswith("timing") and data["delay_mask"][t] else 0
        if lag is None:
            continue
        for f in cols:
            obs=t-12 if arm=="group_lag" and f!="x" else t
            # Burn-in observations are not presented as in-scope samples. The
            # shifted candidate is a registered derived feature observed at t.
            derived=arm=="group_lag" and f!="x"
            value=(row["proxy"] if arm in ("delay_fade","timely_fade") else
                   row["lag1" if f=="g1" else "lag2"] if derived else row[f])
            stamp=t if derived else obs
            events.append(Reading(f,VERSION,identity(d,stamp),stamp,t+lag,d,SOURCE,COLUMN_GROUP[f],0,value,obs if derived else None))
    return cols,ttl,events


def transport(data,events):
    from dataclasses import asdict
    feature_schedule,feedback_schedule={},{}
    for event in events:
        feature_schedule.setdefault(event.available_time,[]).append(event)
    d=data["dataset"]
    for t,at in enumerate(data["label_plan"]):
        if at is not None:
            feedback_schedule.setdefault(at,[]).append(
                Label(d,identity(d,t),t,t+1,at,data["truth"][t]["target"]))
    digest=canonical_sha(dict(features=[asdict(e) for e in events],labels=data["label_plan"]))
    return digest, ((t,tuple(feature_schedule.get(t,())),tuple(feedback_schedule.get(t,())))
                    for t in range(CUTOFF+1))

