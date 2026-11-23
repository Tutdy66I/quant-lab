"""Stratified scoring eligibility, using the fixed Round 35 evidence validator."""
import math

from engine_five34 import BASELINES
from evaluation35 import verify_run

WINDOWS = ((60,90),(90,112),(112,135),(135,180))
MIN_RANKED, MIN_FRACTION = 12, .30


def qualify(trace, truth, population, anchor, cutoff, *, offline_plan=None, context=None):
    """Keep exactly one row per planned tick and disclose the label cutoff."""
    if (type(cutoff) is not int or not 60 <= cutoff <= 181 or
            (offline_plan is not None and (len(offline_plan) != 180
             or any(t is not None and (type(t) is not int or t < i+2)
                    for i,t in enumerate(offline_plan))))):
        raise ValueError("invalid cutoff or offline label plan")
    targets, releases, forecasts, _ = verify_run(trace, truth, population, anchor)
    if offline_plan is not None and releases != {t:at for t,at in enumerate(offline_plan)
                                                 if at is not None and at <= 181}:
        raise ValueError("recorded labels contradict frozen delivery plan")
    records = []
    for decision in trace["decisions"]:
        t = decision["tick"]
        elapsed = t <= cutoff
        quality = decision["quality"] if elapsed else "not_yet_scheduled"
        observed = t in releases and releases[t] <= cutoff
        if observed:
            label_state = "arrived"
        elif offline_plan is None:
            label_state = "unknown"
        elif offline_plan[t] is None:
            label_state = "permanent_missing_offline"
        else:
            label_state = "pending_offline"
        entries = {s:dict(prediction=forecasts[s].get(t) if elapsed else None, verified=elapsed and t in forecasts[s],
                          eligible=t in forecasts[s] and observed) for s in BASELINES}
        window = next(f"{a}-{b-1}" for a,b in WINDOWS if a <= t < b)
        records.append(dict(tick=t,sample=decision["sample"],quality=quality,
                            window=window,label_state=label_state,label=targets[t] if observed else None,
                            feature_value=decision["features"][0] if quality == "valid" else None,
                            label_received=releases[t] if observed else None,
                            forecasts=entries,common=all(v["eligible"] for v in entries.values()),
                            train_available=decision["train_available"] if elapsed else None))
    return dict(cutoff=cutoff,dataset=trace["dataset"],context=dict(context or {}),
                records=records,planned=120,manifest_sha256=anchor)


def strata(result):
    """Emit explicit numerators, denominators, and non-ranking gates."""
    rows = result["records"]
    if len(rows) != result["planned"] or [r["tick"] for r in rows] != list(range(60,180)):
        raise ValueError("scoring denominator, clock or order drift")
    groups = {"overall":rows}
    for dimension in ("window","quality"):
        for level in sorted({r[dimension] for r in rows}):
            groups[f"{dimension}:{level}"] = [r for r in rows if r[dimension] == level]
    output = {}
    for key, group in groups.items():
        n = len(group)
        score_clock = [r for r in group if r["common"]]
        common = len(score_clock)
        qualified = common >= MIN_RANKED and common/n >= MIN_FRACTION
        models = {}
        for s in BASELINES:
            models[s] = dict(
                predicted=sum(r["forecasts"][s]["prediction"] is not None for r in group),
                verified=sum(r["forecasts"][s]["verified"] for r in group),
                eligible=sum(r["forecasts"][s]["eligible"] for r in group),
                common_mse=math.fsum((r["forecasts"][s]["prediction"]-r["label"])**2
                                     for r in score_clock)/common if qualified else None)
        output[key] = dict(cutoff=result["cutoff"],denominator=n,
                           legal_features=sum(r["quality"]=="valid" for r in group),
                           labels={state:sum(r["label_state"]==state for r in group) for state in
                                   ("arrived","pending_offline","permanent_missing_offline","unknown")},
                           common=common,ranking="descriptive_respondents_only" if qualified else
                           "insufficient_evidence",models=models)
    return output

