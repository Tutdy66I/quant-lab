"""Reconstruct frozen input evidence and apply the Round 37 release gate."""
from collections import Counter
import statistics

from arrival39 import CUTOFF, STOP, WARMUP, run
from design39 import contract, transport
from engine_five34 import BASELINES, CONFIG
from evaluation35 import losses
from missing36 import sensitivity
from publication37 import canonical_sha, coverage, publication
from quality35 import check_population, identity

CLOCK=range(WARMUP,STOP)


def evaluate(trace,data,source_sha,config_sha,scenario,arm):
    check_population(data["truth"],data["manifest"],data["manifest_sha256"])
    columns,ttl,events=contract(data,scenario,arm)
    committed,stream=transport(data,events)
    if committed!=source_sha or canonical_sha(trace)!=canonical_sha(
            run(data["dataset"],arm,columns,ttl,stream)):
        raise ValueError("source, forecast, fitted preprocessing or model receipt mismatch")
    return _summarize(trace,data,source_sha,config_sha,scenario,events)


def _summarize(trace,data,source_sha,config_sha,scenario,events):
    forecasts={s:{} for s in BASELINES}
    for p in trace["forecasts"]:
        forecasts[p["strategy"]][p["tick"]]=p["prediction"]
    arrived={item["tick"]:item["received"] for item in trace["releases"]}
    truth={r["time"]:r["target"] for r in data["truth"]}
    dataset=data["dataset"]
    decisions=trace["decisions"][WARMUP:]
    availability={e.observation_time:e.available_time for e in events if e.feature_id=="x"}
    rows=[]
    for d in decisions:
        t=d["tick"]
        outcome=("arrived" if t in arrived else "permanent_missing_offline"
                 if data["label_plan"][t] is None else "pending_offline")
        models={s:dict(prediction=forecasts[s][t],verified=forecasts[s][t] is not None,
                       eligible=forecasts[s][t] is not None and t in arrived) for s in BASELINES}
        rows.append(dict(tick=t,sample=d["sample"],quality=d["quality"],label_state=outcome,
                         forecasts=models,common=all(v["eligible"] for v in models.values())))
    legal=[r["tick"] for r in rows if all(v["verified"] for v in r["forecasts"].values())]
    common=[r["tick"] for r in rows if r["common"]]
    cohort=canonical_sha([identity(dataset,t) for t in CLOCK])
    evidence=dict(identity_verified=True,dataset=dataset,config_sha256=config_sha,
                  manifest_sha256=data["manifest_sha256"],source_sha256=source_sha,
                  versions_sha256=canonical_sha(trace["updates"]),
                  cohort_sha256=canonical_sha([r["sample"] for r in rows]),
                  target=coverage(rows,CLOCK,CUTOFF))
    sens=None
    if scenario=="timing_mnar" and legal:
        sens=sensitivity({s:[forecasts[s][t] for t in legal] for s in BASELINES},
                         [truth[t] if t in arrived else None for t in legal])
    status=publication(evidence,config_sha,
        dict(stability="unassessed",probabilities=dict(unknown=len(legal),minimum=None)),{},
        assumption="MNAR" if scenario=="timing_mnar" else "unknown",transport_attested=False,
        mature=True,sensitivity_available=bool(sens and "pairs" in sens),
        expected_planned=len(CLOCK),expected_cohort_sha256=cohort)
    age={d["tick"]:d["ages"][0] for d in decisions if d["tick"] in legal}
    models={}
    for s in BASELINES:
        versions=[m for m in trace["updates"] if m["strategy"]==s]
        updates=[m for m in versions[1:] if m["active"]]
        models[s]=dict(oracle=losses(forecasts[s],truth,legal),
                       observed=losses(forecasts[s],truth,common),
                       age_oracle={key:losses(forecasts[s],truth,
                                    [t for t in legal if lo<=age[t]<=hi])
                                   for key,lo,hi in (("0",0,0),("1_2",1,2),("3_6",3,6))},
                       cost_per_planned=CONFIG["update_penalty"]*len(updates)/len(CLOCK),
                       candidate_fits=sum(m["training"].get("candidate_fits",0) for m in updates),
                       initial_kind=versions[0]["kind"],initial_n=versions[0]["train_available"])
    return dict(evidence=evidence,publication=status,sensitivity=sens,legal=legal,common=common,
                abstained=dict(Counter(d["reason"] for d in decisions if d["quality"]!="valid")),
                known_current_feature_states=dict(Counter(
                  "permanent_offline" if t not in availability else
                  "pending_at_decision_offline" if availability[t]>t else "on_time" for t in CLOCK)),
                expired_arrivals=sum(r["expired_on_arrival"] and r["observation_time"]>=WARMUP
                                     for r in trace["features"]),
                processed_feature_events=len(trace["features"]),
                feature_dimensions=len(trace["columns"]),models=models)


def group_diagnostic(trace):
    if len(trace["columns"])<2:
        return None
    initial=next(m for m in trace["updates"] if m["strategy"]=="fixed_ridge")
    if initial["kind"]!="ridge":
        return None
    ticks=[int(s.rsplit(":",1)[1]) for s in initial["training"]["train_ids"]]
    vectors=[trace["decisions"][t]["features"] for t in ticks]
    columns=list(zip(*vectors))
    pair_corr=statistics.correlation(columns[-2],columns[-1])
    pair_cond=(1+abs(pair_corr))/(1-abs(pair_corr)) if abs(pair_corr)<1 else None
    return dict(last_pair_corr=pair_corr,standardized_pair_condition=pair_cond,
                weights=list(initial["value"]["weights"]),alpha=initial["value"]["alpha"])

