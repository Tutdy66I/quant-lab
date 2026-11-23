"""Scoring coverage and conservative release decisions for artificial research."""
from collections import Counter
import hashlib
import json
import math

from engine_five34 import BASELINES
from qualification36 import qualify, strata
from quality35 import finite

MIN_COUNT, MIN_COVERAGE, MIN_ESS_RATIO = 12, .30, .50
STABILITY_ERROR, STABILITY_DRIFT, STABILITY_BIN = .20, .30, 5
THRESHOLDS = ((8,.20),(12,.30),(20,.50),(30,.70))
LABEL_STATES = ("arrived","pending_offline","permanent_missing_offline","unknown")


def canonical_sha(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(",",":"),
                                    allow_nan=False).encode()).hexdigest()


def fraction(n,d):
    return dict(numerator=n,denominator=d,ratio=n/d if d else None)


def coverage(rows, clock, cutoff):
    if not clock or [r["tick"] for r in rows] != list(clock) or len(set(clock)) != len(clock) or len({r["sample"] for r in rows})!=len(rows):
        raise ValueError("coverage denominator or ordered sample cohort changed")
    n=len(clock)
    if any(r["label_state"] not in LABEL_STATES or
           r["common"] != all(v["eligible"] for v in r["forecasts"].values())
           for r in rows):
        raise ValueError("label classification or joint eligibility inconsistent")
    return dict(cutoff=cutoff,planned=n,
                legal_features=fraction(sum(r["quality"]=="valid" for r in rows),n),
                labels={k:fraction(sum(r["label_state"]==k for r in rows),n) for k in LABEL_STATES},
                common=fraction(sum(r["common"] for r in rows),n),
                quality={k:fraction(v,n) for k,v in Counter(r["quality"] for r in rows).items()},
                models={s:{k:fraction(sum(r["forecasts"][s][field] is not None if field=="prediction"
                                          else r["forecasts"][s][field] for r in rows),n)
                           for k,field in (("predicted","prediction"),("verified","verified"),
                                           ("scored","eligible"))} for s in BASELINES})


def build_report(trace, truth, population, anchor, plan, context, config_sha, start=122):
    """Bind a full 120-slot census, explicit primary target and model receipts."""
    if start not in (122,160):
        raise ValueError("unregistered primary cohort")
    qualified=qualify(trace,truth,population,anchor,181,offline_plan=plan,context=context)
    rows=qualified["records"]
    selected=[r for r in rows if r["tick"]>=start]
    report=dict(identity_verified=True,dataset=qualified["dataset"],context=context,config_sha256=config_sha,
                manifest_sha256=anchor,versions_sha256=canonical_sha(trace["updates"]),
                cohort_sha256=canonical_sha([r["sample"] for r in selected]),
                full=coverage(rows,range(60,180),181),target=coverage(selected,range(start,180),181),
                strata=strata(qualified),
                models={s:{"train_sample_min":min(r["train_available"] for r in rows),
                           "train_sample_max":max(r["train_available"] for r in rows),
                           "initial_available":next(u["train_available"] for u in trace["updates"]
                                                    if u["strategy"]==s and u["version"]==0),
                           "activated_updates":sum(u["active"] and u["version"]>0 for u in trace["updates"]
                                                   if u["strategy"]==s),
                           "ridge_update_fits":sum(u["kind"]=="ridge" and u["version"]>0
                                                   for u in trace["updates"] if u["strategy"]==s)}
                        for s in BASELINES})
    return rows,selected,report


def diagnostic(covariates, response, estimated_probabilities, nominal_probabilities):
    """Observed response calibration does not test dependence on missing outcomes."""
    size=len(response)
    if not size or any(len(v)!=size for v in (covariates,estimated_probabilities,nominal_probabilities)):
        raise ValueError("diagnostic cohort mismatch")
    n=sum(response)
    probs=[p for p in estimated_probabilities if p is not None]
    raw=[1/p for y,p in zip(response,estimated_probabilities) if y and p is not None and p>0]
    def group(indices):
        use=[i for i in indices if estimated_probabilities[i] is not None]
        if len(use)<STABILITY_BIN:
            return None
        declared=math.fsum(estimated_probabilities[i] for i in use)/len(use)
        rate=sum(response[i] for i in use)/len(use)
        return dict(n=len(use),returned=sum(response[i] for i in use),observed_rate=rate,
                    predicted_rate=declared,residual=rate-declared)
    groups={}
    for sign in (False,True):
        inds=[i for i,x in enumerate(covariates) if x==sign]
        groups[str(sign).lower()]=dict(full=group(inds),early=group([i for i in inds if i<size//2]),
                                      late=group([i for i in inds if i>=size//2]))
    residuals=[abs(g["full"]["residual"]) for g in groups.values() if g["full"]]
    shifts=[abs(g["late"]["residual"]-g["early"]["residual"]) for g in groups.values()
            if g["late"] and g["early"]]
    complete=len(residuals)==2 and len(shifts)==2 and len(probs)==size
    stability="unassessed"
    if complete:
        stability="pass" if max(residuals)<=STABILITY_ERROR and max(shifts)<=STABILITY_DRIFT else "fail"
    return dict(returned=n,planned=size,
                probabilities=dict(minimum=min(probs,default=None),maximum=max(probs,default=None),
                                   below_point_one=sum(p<.10 for p in probs),unknown=size-len(probs),
                                   known_offline_minimum=min((p for p in nominal_probabilities if p is not None),
                                                             default=None)),
                weights=dict(raw_max=max(raw,default=None),above_five=sum(w>5 for w in raw),
                             above_eight=sum(w>8 for w in raw),count=len(raw)),
                brier=math.fsum((p-y)**2 for p,y in zip(estimated_probabilities,response))/size
                      if len(probs)==size else None,
                groups=groups,stability=stability,
                max_calibration_error=max(residuals,default=None),max_time_shift=max(shifts,default=None))


def publication(report, expected_config, diagnostics, weights, *, assumption,
                transport_attested, mature, sensitivity_available, expected_planned, expected_cohort_sha256, threshold=(MIN_COUNT,MIN_COVERAGE)):
    """Decide release scope, leaving any winner to a separate paired-interval gate."""
    if not isinstance(report,dict) or report.get("identity_verified") is not True:
        return dict(status="invalid",target=None,reason="unverified_identity")
    if any(not isinstance(report.get(k),str) or len(report[k])!=64 or
           any(c not in "0123456789abcdef" for c in report[k])
           for k in ("manifest_sha256","cohort_sha256","versions_sha256","config_sha256")):
        return dict(status="invalid",target=None,reason="unbound_evidence")
    target=report["target"]
    n=target["planned"]
    common=target["common"]["numerator"]
    valid=target["legal_features"]["numerator"]
    arrived=target["labels"]["arrived"]["numerator"]
    same_scope=all(m["verified"]["numerator"]==valid and m["scored"]["numerator"]==common
                   for m in target["models"].values())
    fields=[target["common"],target["legal_features"],*target["labels"].values(),
            *target["quality"].values(),*(f for model in target["models"].values() for f in model.values())]
    fractions_match=all(type(f["numerator"]) is int and 0<=f["numerator"]<=n and
                        f["denominator"]==n and f["ratio"]==(f["numerator"]/n if n else None) for f in fields)
    verified_counts=all(m["predicted"]["numerator"]==m["verified"]["numerator"]
                        for m in target["models"].values())
    minimum,ratio=threshold
    if (report["config_sha256"]!=expected_config or n!=expected_planned
            or report["cohort_sha256"]!=expected_cohort_sha256 or not same_scope or set(target["models"])!=set(BASELINES)
            or target["common"]["denominator"]!=n or min(n,valid,common,arrived)<0
            or common>min(valid,arrived) or max(valid,arrived)>n
            or not fractions_match or not verified_counts
            or set(target["labels"])!=set(LABEL_STATES)
            or sum(f["numerator"] for f in target["labels"].values())!=n
            or sum(f["numerator"] for f in target["quality"].values())!=n
            or target["quality"].get("valid",fraction(0,n))["numerator"]!=valid
            or "unknown" in target["quality"] or "invalid" in target["quality"]):
        return dict(status="invalid",target=None,reason="identity_quality_or_configuration")
    if n==0 or common<minimum or common/n<ratio:
        return dict(status="insufficient_evidence",target=None,reason="below_registered_coverage")
    if common==valid:
        return dict(status="population_supported_under_assumptions",target="planned" if valid==n else
                    "verified_forecast_cohort",reason="census_of_target")
    if assumption=="MNAR" and sensitivity_available:
        return dict(status="sensitivity_only",target="verified_forecast_cohort",
                    reason="MNAR_population_not_identified")
    failures=[]
    if not mature:
        failures.append("immature_labels")
    if assumption not in {"MCAR","MAR"} or not transport_attested:
        failures.append("assumptions_not_supported")
    if diagnostics["stability"]!="pass":
        failures.append("calibration_or_time_stability")
    p=diagnostics["probabilities"]
    if (p["unknown"] or p["minimum"] is None or p["minimum"]<.10):
        failures.append("overlap")
    if set(weights)!=set(BASELINES) or any(
            w["status"]!="identified_under_assumptions" or w["planned"]!=valid or w["observed"]!=common or not finite(w["ess"])
            or w["ess"]<12-1e-10 or w["ess"]<MIN_ESS_RATIO*common or w["clipped"]
            for w in weights.values()):
        failures.append("weight_or_ESS")
    if failures:
        return dict(status="valid_observed_only",target="common_respondents",reason=";".join(failures))
    return dict(status="population_supported_under_assumptions",target="planned" if valid==n else
                "verified_forecast_cohort",reason="registered_MAR_and_diagnostics")


def choose_winner(states, contrasts, rankings, expected_seeds):
    """Require the complete paired cohort, one supported target and method agreement."""
    if len(states)!=expected_seeds or not all(s["status"]=="population_supported_under_assumptions"
                                             for s in states):
        return dict(winner=None,reason="population_release_incomplete")
    if len({s["target"] for s in states})!=1:
        return dict(winner=None,reason="target_instability")
    if len(rankings)<2 or any(r is None or len(r)!=len(BASELINES) or set(r)!=set(BASELINES) for r in rankings) or len({r[0] for r in rankings})!=1:
        return dict(winner=None,reason="method_disagreement_or_tie")
    candidate=rankings[0][0]
    votes={}
    for c in contrasts:
        if candidate not in (c["a"],c["b"]):
            continue
        lo,hi=c.get("ci",(None,None))
        rival=c["b"] if candidate==c["a"] else c["a"]
        if rival in votes:
            return dict(winner=None,reason="duplicate_or_missing_rival")
        votes[rival]=(c["n"]==expected_seeds and lo is not None and hi is not None
                      and lo<=hi and (hi<0 if candidate==c["a"] else lo>0))
    if set(votes)!=set(BASELINES)-{candidate} or not all(votes.values()):
        return dict(winner=None,reason="paired_intervals_do_not_separate")
    return dict(winner=candidate,reason="conditional_population_rank")

