"""Point-in-time artificial features and label transport."""
from dataclasses import asdict, dataclass
import math
import random
import statistics

from publication37 import canonical_sha
from quality35 import Label, finite, identity
from synthetic_manifest27 import build_manifest, manifest_sha256

VERSION="feature38-v1"
SCENARIOS=("context_on","context_off","covariate_shift","thin_start","mnar_outage")
NOISE=tuple(f"noise{i}" for i in range(6))
FEATURES=("stable","redundant","context","fading","perturbed","delayed","missing",*NOISE)
ARMS=("stable","noise","stable_noise","redundant","context","fading","perturb","delayed","missing","wide")
STOP, WARMUP, CUTOFF = 180, 60, 181


@dataclass(frozen=True)
class Feature:
    feature_id: str
    sample_id: str
    observation_time: int
    available_time: object
    decision_time: int
    version: str
    source: str
    value: object


def selector(arm, selected):
    if selected not in NOISE:
        raise ValueError("unregistered noise feature")
    return {"stable":("stable",),"noise":(selected,),"stable_noise":("stable",selected),
            "redundant":("stable","redundant"),"context":("stable","context"),
            "fading":("fading",),"perturb":("perturbed",),"delayed":("delayed",),
            "missing":("missing",),"wide":("stable",*NOISE)}[arm]


def check_features(records, dataset, tick, columns):
    """Make only completely legal vectors available to the learners."""
    if not isinstance(records,tuple) or any(not isinstance(r,Feature) for r in records) or tuple(r.feature_id for r in records)!=columns:
        raise ValueError("feature set, order or cardinality differs from registry")
    states=[]
    for item in records:
        if (item.feature_id not in FEATURES or item.sample_id!=identity(dataset,tick)
                or item.version!=VERSION or type(item.decision_time) is not int or item.decision_time!=tick
                or type(item.observation_time) is not int or item.observation_time!=tick
                or item.source not in ("exogenous","unknown")):
            raise ValueError("invalid feature identity, version, source or observation time")
        at=item.available_time
        if at is not None and (type(at) is not int or at<item.observation_time):
            raise ValueError("invalid feature availability")
        if item.value is not None and (not finite(item.value) or at is None or at>tick):
            raise ValueError("nonfinite or future feature delivered")
        if item.source=="unknown":
            states.append("unknown")
        elif item.value is None:
            if at is not None and at<=tick:
                raise ValueError("promised feature was omitted")
            states.append("unavailable")
        else:
            states.append("valid")
    if all(s=="valid" for s in states):
        return "valid",tuple(item.value for item in records)
    return ("unknown" if "unknown" in states else "unavailable"),None


def fixture(scenario,seed,config_sha):
    if scenario not in SCENARIOS or type(seed) is not int:
        raise ValueError("invalid synthetic scenario")
    base=random.Random(f"38-path:{seed}")
    extras=random.Random(f"38-extras:{seed}")
    fault=random.Random(f"38-availability:{seed}")
    response=random.Random(f"38-response:{seed}")
    dataset=f"synthetic38:{scenario}:{seed}"
    truth,features,plan=[],[],[]
    previous=0.
    for tick in range(STOP):
        x,z,e=(base.gauss(0,1) for _ in range(3))
        if scenario=="covariate_shift" and tick>=100:
            x=1.25+1.25*x
        redundant,substitute,perturb=(extras.gauss(0,1) for _ in range(3))
        r=max(0.,min(1.,(125-tick)/35))
        y=1.1*x+(.75*z if scenario!="context_off" else 0.)+.35*e
        noise={f:extras.gauss(0,1) for f in NOISE}
        missing_draw=fault.random()
        response_draw=response.random()
        missing=75<=tick<135 and missing_draw<.40
        delayed=missing
        values=dict(stable=x,redundant=x+.003*redundant,context=z,
                    fading=r*x+math.sqrt(1.-r*r)*substitute,
                    perturbed=x+.85*perturb if tick>=60 else x,
                    delayed=None if delayed else x,missing=None if missing else x,**noise)
        at={f:tick for f in FEATURES}
        if delayed:
            at["delayed"]=tick+6
        if missing:
            at["missing"]=None
        features.append({f:Feature(f,identity(dataset,tick),tick,at[f],tick,VERSION,"exogenous",values[f])
                         for f in FEATURES})
        truth.append(dict(time=tick,features=[x,z],previous=previous,target=y))
        previous=y
        release=tick+2
        if scenario=="thin_start" and tick<60 and not 38<=tick<=57:
            release=None
        if scenario=="mnar_outage" and tick>=60:
            # Common external outage and outcome mechanism, independent of chosen arm.
            chance=(.23 if abs(y)>1.1 else .85) * (.60 if missing else 1.)
            if response_draw>=chance:
                release=None
        plan.append(release)
    manifest=build_manifest(truth,dataset_id=dataset,dataset_fingerprint=config_sha,config_hash=config_sha)
    return dataset,truth,features,plan,manifest,manifest_sha256(manifest)


def stream(dataset,truth,rows,plan,columns):
    pending={}
    for tick in range(CUTOFF+1):
        packet=None
        if tick<STOP:
            packet=tuple(rows[tick][f] for f in columns)
            due=plan[tick]
            if due is not None:
                pending.setdefault(due,[]).append(Label(dataset,identity(dataset,tick),
                                                       tick,tick+1,due,truth[tick]["target"]))
        yield tick,packet,tuple(pending.pop(tick,()))


def feature_digest(rows,columns):
    return canonical_sha([[asdict(row[f]) for f in columns] for row in rows])


def select_noise(development_seeds,expected_development,protocol_sha):
    """Choose from six noise columns on released development-only labels."""
    if (tuple(development_seeds)!=tuple(expected_development) or not development_seeds
            or len(development_seeds)!=len(set(development_seeds))):
        raise ValueError("selection must use the registered development seeds only")
    scores={f:[] for f in NOISE}
    for seed in development_seeds:
        _,truth,rows,plan,_,_=fixture("context_on",seed,protocol_sha)
        ticks=[t for t,at in enumerate(plan) if at is not None and at<=59]
        for f in NOISE:
            scores[f].append(abs(statistics.correlation([rows[t][f].value for t in ticks],
                                                        [truth[t]["target"] for t in ticks])))
    mean={f:statistics.mean(v) for f,v in scores.items()}
    selected=max(NOISE,key=lambda f:(mean[f],-NOISE.index(f)))
    return dict(selected=selected,mean_abs_development_correlation=mean,
                seeds=list(development_seeds),label_cutoff=59,
                rule="largest mean absolute correlation on legally released warmup labels")

