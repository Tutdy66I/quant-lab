"""Bind feature research to Round 37 qualification and publication."""
import statistics

from engine_five34 import BASELINES, CONFIG
from evaluation35 import losses
from features38 import CUTOFF, STOP, WARMUP, feature_digest, stream
from learner38 import run
from missing36 import sensitivity
from publication37 import canonical_sha, coverage, publication
from qualification36 import strata
from quality35 import check_population, identity


def evaluate(trace, truth, features, plan, manifest, anchor, feature_anchor, plan_anchor, config_sha, scenario, *, expected_arm, expected_columns):
    """Replay from the precommitted synthetic fixture, including fit-time preprocessing."""
    check_population(truth,manifest,anchor)
    dataset,arm,columns=trace["dataset"],trace["arm"],tuple(trace["columns"])
    if (dataset!=manifest["dataset_id"] or arm!=expected_arm or columns!=expected_columns or feature_digest(features,columns)!=feature_anchor or canonical_sha(plan)!=plan_anchor
            or canonical_sha(trace)!=canonical_sha(run(dataset,arm,columns,
                                                     stream(dataset,truth,features,plan,columns)))):
        raise ValueError("feature, model version, preprocessing or receipt consistency failure")
    return _summarize(trace,truth,plan,anchor,feature_anchor,plan_anchor,config_sha,scenario)


def _summarize(trace,truth,plan,anchor,feature_anchor,plan_anchor,config_sha,scenario):
    dataset=trace["dataset"]
    predictions={s:{} for s in BASELINES}
    for p in trace["forecasts"]:
        predictions[p["strategy"]][p["tick"]]=p["prediction"]
    arrived={r["tick"]:r["received"] for r in trace["releases"]}
    q=[]
    for event in trace["decisions"]:
        t=event["tick"]
        state="arrived" if t in arrived else "permanent_missing_offline" if plan[t] is None else "pending_offline"
        per_model={s:dict(prediction=predictions[s][t],verified=predictions[s][t] is not None,
                          eligible=predictions[s][t] is not None and t in arrived) for s in BASELINES}
        bounds=next((a,b) for a,b in ((60,90),(90,112),(112,135),(135,180)) if a<=t<b)
        q.append(dict(tick=t,sample=event["sample"],quality=event["quality"],window=f"{bounds[0]}-{bounds[1]-1}",
                      label_state=state,label=truth[t]["target"] if t in arrived else None,forecasts=per_model,
                      common=all(v["eligible"] for v in per_model.values())))
    clock=range(WARMUP,STOP)
    report=dict(identity_verified=True,dataset=dataset,config_sha256=config_sha,manifest_sha256=anchor,
                feature_sha256=feature_anchor,delivery_sha256=plan_anchor,cohort_sha256=canonical_sha([r["sample"] for r in q]),
                versions_sha256=canonical_sha(trace["updates"]),target=coverage(q,clock,CUTOFF),
                strata=strata(dict(records=q,planned=len(clock),cutoff=CUTOFF)))
    common=[r["tick"] for r in q if r["common"]]
    legal=[r["tick"] for r in q if all(m["verified"] for m in r["forecasts"].values())]
    actual={r["time"]:r["target"] for r in truth}
    sens=None
    if scenario=="mnar_outage" and legal:
        sens=sensitivity({s:[predictions[s][t] for t in legal] for s in BASELINES},
                         [actual[t] if t in arrived else None for t in legal])
    status=publication(report,config_sha,
                       dict(stability="unassessed",probabilities=dict(unknown=len(legal),minimum=None)),{},
                       assumption="MNAR" if scenario=="mnar_outage" else "unknown",transport_attested=False,
                       mature=True,sensitivity_available=sens is not None and "pairs" in sens,
                       expected_planned=len(clock),
                       expected_cohort_sha256=canonical_sha([identity(dataset,t) for t in clock]))
    models={}
    for s,pred in predictions.items():
        versions=[u for u in trace["updates"] if u["strategy"]==s]
        updates=[u for u in versions if u["version"]>0 and u["active"]]
        models[s]=dict(observed=losses(pred,actual,common),oracle=losses(pred,actual,legal),
                       late_oracle=losses(pred,actual,{t for t in legal if t>=125}),
                       initial_kind=versions[0]["kind"],initial_train=versions[0]["train_available"],
                       fallback_updates=sum(u["fit_status"]=="fallback_mean" for u in versions),
                       updated=len(updates),cost_per_planned=CONFIG["update_penalty"]*len(updates)/120,
                       candidate_fits=sum(u["training"].get("candidate_fits",0) for u in updates))
    return dict(qualification=report,publication=status,sensitivity=sens,legal=legal,common=common,models=models)


def correlation_diagnostics(trace, features, selected, truth):
    early=range(60,90)
    late=range(135,180)
    def corr(left,right,ticks):
        return statistics.correlation([features[t][left].value for t in ticks],
                                      [features[t][right].value for t in ticks])
    rho=corr("stable","redundant",early)
    initial=next(u for u in trace["updates"] if u["strategy"]=="fixed_ridge" and u["version"]==0)
    alpha=initial["value"]["alpha"] if initial["kind"]=="ridge" else None
    weights=list(initial["value"]["weights"]) if initial["kind"]=="ridge" else None
    fit_rho=None
    if alpha is not None and trace["arm"]=="redundant":
        fit_ticks=[int(i.rsplit(":",1)[1]) for i in initial["training"]["train_ids"]]
        fit_rho=abs(corr("stable","redundant",fit_ticks))
    return dict(initial_alpha=alpha,initial_weights=weights,
                redundant_regularized_condition=((1+fit_rho+alpha)/(1-fit_rho+alpha)
                                                 if fit_rho is not None else None),
                redundant_corr=rho,redundant_gram_condition=(1+abs(rho))/(1-abs(rho)),
                fading_corr_early=corr("stable","fading",early),fading_corr_late=corr("stable","fading",late),
                selected_noise_report_abs_corr=abs(statistics.correlation(
                    [features[t][selected].value for t in range(60,180)],
                    [truth[t]["target"] for t in range(60,180)])),
                feature_dim=len(trace["columns"]))

