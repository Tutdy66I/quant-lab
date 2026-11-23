"""Evaluate response estimators and policy recovery without altering the learners."""
import math
import statistics

from design36 import RECOVERY, observable_p
from engine_five34 import BASELINES, CONFIG
from missing36 import (CAL_CUTOFF, CAL_END, CAL_START, EVAL_START, estimated_probabilities,
                       fit_response, sensitivity, weighted)


def mse(pred, outcomes):
    values = [(p-y)**2 for p,y in zip(pred,outcomes) if p is not None and y is not None]
    return statistics.mean(values) if values else None


def selection(qualified, truth, mode):
    """Generate estimator inputs from received indicators; truth is for offline scoring only."""
    records = qualified["records"]
    covariates = {r["tick"]:r["feature_value"] for r in records}
    if any(r["quality"] != "valid" for r in records):
        raise ValueError("propensity experiment requires completely observed features")
    calibration = [dict(tick=r["tick"],positive=covariates[r["tick"]]>0.,
                        returned=r["label_received"] is not None and r["label_received"] <= CAL_CUTOFF)
                   for r in records if CAL_START <= r["tick"] < CAL_END]
    use_sign = mode != "mar_omitted"
    calibrated = fit_response(calibration, cutoff=CAL_CUTOFF, include_sign=use_sign)
    evaluation = [r for r in records if r["tick"] >= EVAL_START]
    chosen = [dict(tick=r["tick"],positive=covariates[r["tick"]]>0.) for r in evaluation]
    p = estimated_probabilities(chosen, calibrated, include_sign=use_sign)
    honest = mode in {"mcar","mar"}
    oracle_p = [observable_p(covariates[r["tick"]],mode) for r in evaluation] if not mode.startswith("mnar") else None

    observed = [r["label"] for r in evaluation]
    forecasts = {s:[r["forecasts"][s]["prediction"] for r in evaluation] for s in BASELINES}
    offline = [truth[r["tick"]]["target"] for r in evaluation]
    if any(p is None for v in forecasts.values() for p in v):
        raise ValueError("selection experiment requires verified forecasts on the planned cohort")
    methods = {}
    for s, pred in forecasts.items():
        observed_loss = [(p-y)**2 if y is not None else None for p,y in zip(pred,observed)]
        methods[s] = dict(complete_truth=mse(pred,offline), complete_case=mse(pred,observed),
                          estimated=weighted(observed_loss,p,trustworthy=honest),
                          oracle=weighted(observed_loss,oracle_p) if oracle_p is not None else None)
    return dict(calibration=calibration, probabilities=p, oracle_probabilities=oracle_p,
                evaluation_ticks=[r["tick"] for r in evaluation], known_design_overlap_failure=mode == "mar_overlap", count_returned=sum(y is not None for y in observed),
                models=methods,
                sensitivity=sensitivity(forecasts,observed) if mode.startswith("mnar") else None)


WINDOW, RECOVERY_THRESHOLD, CAP = 10, .4, 68


def recovery_observation(rows, targets, strategy):
    """First pair of adjacent qualifying fixed blocks; censor at horizon."""
    previous = None
    oracle_tick = None
    confirmations = []
    for offset in range(RECOVERY,180-WINDOW+1,WINDOW):
        block = [rows[t-60] for t in range(offset,offset+WINDOW)]
        pred = [r["forecasts"][strategy]["prediction"] for r in block]
        if any(v is None for v in pred) or mse(pred,[targets[r["tick"]] for r in block]) > RECOVERY_THRESHOLD:
            previous = None
            continue
        if previous is not None:
            if oracle_tick is None:
                oracle_tick = block[-1]["tick"]
            arrivals = [r["label_received"] for r in previous+block]
            if all(a is not None for a in arrivals):
                confirmations.append(max(arrivals))
        previous = block
    confirm = min(confirmations) if confirmations else None
    return dict(oracle_tick=oracle_tick, confirmed_tick=confirm,
                restricted_delay=oracle_tick-RECOVERY if oracle_tick is not None else CAP,
                oracle_censored=oracle_tick is None, confirm_censored=confirm is None)


def recovery_metrics(qualified, trace, truth):
    rows = qualified["records"]
    targets = {row["time"]:row["target"] for row in truth}
    result = {}
    for strategy in BASELINES:
        updates = [u for u in trace["updates"] if u["strategy"] == strategy]
        activated = [v for v in updates[1:] if v["active"]]
        first_ridge = next((v for v in updates if v["kind"] == "ridge"),None)
        assimilated = []
        for v in activated:
            evidence = v["training"]
            ids = evidence.get("mean_ids",[]) + evidence.get("train_ids",[]) + evidence.get("tune_ids",[])
            if any(int(i.rsplit(":",1)[1]) >= 60 for i in ids):
                assimilated.append(v)
        charge = len(activated)*CONFIG["update_penalty"]
        cohort = [r for r in rows if r["tick"] >= RECOVERY]
        pre = [r for r in rows if 90 <= r["tick"] < RECOVERY]
        oracle = lambda period: mse([r["forecasts"][strategy]["prediction"] for r in period],
                                    [targets[r["tick"]] for r in period])
        result[strategy] = dict(initial_kind=updates[0]["kind"],
                                initial_labels=updates[0]["train_available"],
                                first_ridge_fit=first_ridge["retrain_time"] if first_ridge else None,
                                first_ridge_activation=first_ridge["model_activation_time"] if first_ridge else None,
                                first_update=activated[0]["model_activation_time"] if activated else None,
                                first_using_new_feedback=assimilated[0]["model_activation_time"] if assimilated else None,
                                fallback_count=sum(v["fit_status"] in {"fallback_mean","no_labels"} for v in updates),
                                activated_updates=len(activated), refits=sum(v["kind"]=="ridge" for v in activated),
                                candidate_fits=sum(v["training"].get("candidate_fits",0) for v in activated),
                                cost_per_planned=charge/len(rows), ore_pre=oracle(pre), ore_post=oracle(cohort),
                                train_available={str(t):rows[t-60]["train_available"] for t in (60,90,112,113,135,179)},
                                **recovery_observation(rows,targets,strategy))
    return result

