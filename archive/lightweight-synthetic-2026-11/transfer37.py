"""Prefixed synthetic mechanisms and frozen Round 36 propensity estimators."""
import random
import statistics

from design36 import SCENARIOS, execute, fixture
from engine_five34 import BASELINES
from missing36 import CAL_CUTOFF, estimated_probabilities, fit_response, sensitivity, weighted
from publication37 import THRESHOLDS, build_report, canonical_sha, diagnostic, publication
from quality35 import identity
from synthetic_manifest27 import build_manifest, manifest_sha256

MODES=("stable","gradual","sudden","feature_shift","joint_shift","omitted","low_overlap",
       "extreme","sample_small","mnar","immature","census","census_partial")
SHIFTS=("feature_shift","joint_shift")
CENSUS=("census","census_partial")
EVAL_START=122
CUTOFF=181


def path(scenario,seed,sha,shift):
    dataset,truth,manifest,anchor=fixture(scenario,seed,sha)
    if not shift:
        return dataset,truth,manifest,anchor
    dataset+=":feature_shift"
    for t in range(EVAL_START,len(truth)):
        row=truth[t]
        coefficient=-.9 if scenario=="relation_reverse" else .9
        row["features"]=[row["features"][0]+1.35]
        row["target"]+=1.35*coefficient
        if t+1<len(truth):
            truth[t+1]["previous"]=row["target"]
    manifest=build_manifest(truth,dataset_id=dataset,dataset_fingerprint=sha,config_hash=sha)
    return dataset,truth,manifest,manifest_sha256(manifest)


def response(row,mode):
    t=row["time"]
    pos=row["features"][0]>0
    ref=.25 if pos else .87
    lag=2
    if mode in CENSUS:
        return 1.,lag
    if t>=EVAL_START:
        if mode in ("sudden","joint_shift"):
            ref=.85 if pos else .28
        elif mode=="gradual":
            fraction=(t-EVAL_START)/57
            ref+=( (.85 if pos else .28)-ref)*fraction
        elif mode=="mnar":
            ref=.17 if abs(row["target"])>.8 else .88
        elif mode=="immature" and pos:
            lag=32
    if mode in ("low_overlap","extreme") and pos:
        ref=.025 if mode=="low_overlap" else .115
    return ref,lag


def schedule(truth,seed,mode):
    rng=random.Random(f"37-responses:{seed}")
    result=[]
    nominal=[]
    for row in truth:
        t=row["time"]
        draw=rng.random()
        probability,lag=response(row,mode)
        result.append(t+2 if t<60 else t+lag if draw<probability else None)
        # The probability of an actually observed outcome at the scoring cutoff.
        nominal.append((probability if t+lag<=CUTOFF else 0.) if mode!="mnar" else None)
    return result,nominal


def estimate(qualified,target,trace_report,truth,nominal,mode,config_sha):
    """Use only verified feature and arrived indicators in the probability model."""
    calibration=[dict(tick=r["tick"],positive=(r["feature_value"]>0
                                                     if r["quality"]=="valid" else None),
                      returned=r["label_received"] is not None and r["label_received"]<=CAL_CUTOFF)
                 for r in qualified if 60<=r["tick"]<120]
    covariates=[dict(tick=r["tick"],positive=r["feature_value"]>0) for r in target
                if r["quality"]=="valid"]
    probabilities=[None]*len(covariates)
    if all(r["positive"] is not None for r in calibration):
        fit=fit_response(calibration,cutoff=CAL_CUTOFF,include_sign=mode!="omitted")
        probabilities=estimated_probabilities(covariates,fit,include_sign=mode!="omitted")
    measurable=[r for r in target if r["quality"]=="valid"]
    observed=[r["label"] for r in measurable]
    oracle=[nominal[r["tick"]] for r in measurable]
    diag=diagnostic([v["positive"] for v in covariates],[y is not None for y in observed],probabilities,oracle)
    conditional=mode in {"stable","feature_shift","sample_small"}
    sensitivity_summary=None
    estimates={}
    frozen_forecasts={s:[r["forecasts"][s]["prediction"] for r in measurable] for s in BASELINES}
    if any(not seq or any(p is None for p in seq) for seq in frozen_forecasts.values()):
        raise ValueError("unable to verify all measurable predictions")
    for s,pred in frozen_forecasts.items():
        losses=[(p-y)**2 if y is not None else None for p,y in zip(pred,observed)]
        observed_losses=[v for v in losses if v is not None]
        mse=statistics.mean(observed_losses) if observed_losses else None
        true=statistics.mean((p-truth[r["tick"]]["target"])**2 for p,r in zip(pred,measurable))
        estimates[s]=dict(observed=mse,ipw=weighted(losses,probabilities,trustworthy=conditional),
                          oracle=weighted(losses,oracle) if mode!="mnar" else None,truth=true)
    if mode=="mnar":
        sensitivity_summary=sensitivity(frozen_forecasts,observed)
    weights={s:v["ipw"] for s,v in estimates.items()}
    registered_start=160 if mode=="sample_small" else EVAL_START
    inputs=dict(expected_planned=180-registered_start,
                expected_cohort_sha256=canonical_sha([identity(trace_report["dataset"],t)
                                                      for t in range(registered_start,180)]),
                assumption="MNAR" if mode=="mnar" else "MAR",
                transport_attested=conditional,
                mature=mode!="immature",
                sensitivity_available=sensitivity_summary is not None and "pairs" in sensitivity_summary)
    releases=[publication(trace_report,config_sha,diag,weights,threshold=t,**inputs) for t in THRESHOLDS]
    return dict(calibration=calibration,target_ticks=[r["tick"] for r in measurable],
                diagnostics=diag,weights_p=probabilities,oracle_p=oracle,models=estimates,
                sensitivity=sensitivity_summary,publication=releases[1],thresholds=releases,
                assumptions=inputs)


def cases(scenario,seed,config_sha):
    fixtures={k:path(scenario,seed,config_sha,k=="shifted") for k in ("base","shifted")}
    raw={"fixtures":{k:dict(dataset=d,truth=t,manifest=m,anchor=a) for k,(d,t,m,a) in fixtures.items()},
         "arms":{}}
    summary={}
    for mode in MODES:
        kind="shifted" if mode in SHIFTS else "base"
        dataset,truth,manifest,anchor=fixtures[kind]
        plan,oracle=schedule(truth,seed,mode)
        quality="high" if mode=="census_partial" else "clean"
        trace=execute(dataset,truth,seed,mode,quality,plan)
        context=dict(scenario=scenario,mode=mode,feature_quality=quality,initial=45,
                     latency="2 or 32; ascertained at 181" if mode=="immature" else "2")
        all_rows,target,report=build_report(trace,truth,manifest,anchor,plan,context,config_sha,
                                             start=160 if mode=="sample_small" else EVAL_START)
        result=estimate(all_rows,target,report,truth,oracle,mode,config_sha)
        summary[mode]=dict(report=report,**result)
        raw["arms"][mode]=dict(fixture=kind,plan=plan,trace=trace)
    return raw,summary

