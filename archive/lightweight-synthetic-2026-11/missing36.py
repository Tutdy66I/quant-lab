"""Chronological response-probability calibration and loss sensitivity."""
from collections import defaultdict
from itertools import combinations
import math
import statistics

from quality35 import finite

CAL_START, CAL_END, CAL_CUTOFF, EVAL_START = 60, 120, 121, 122
MIN_OBSERVED, MIN_ESS, MIN_STRATUM, MIN_P, MAX_WEIGHT = 12, 12, 10, .10, 8.
RATIOS = (.5, 1., 2., 4.)


def fit_response(rows, *, cutoff, include_sign=True):
    """Inputs are event indicators and a point-in-time feature, never targets."""
    if cutoff != CAL_CUTOFF or [r.get("tick") for r in rows] != list(range(CAL_START, CAL_END)):
        raise ValueError("propensity calibration requires its complete, mature chronological cohort")
    counts = defaultdict(lambda: [0, 0])
    for row in rows:
        if (set(row) != {"tick", "positive", "returned"} or type(row["tick"]) is not int
                or type(row["positive"]) is not bool or type(row["returned"]) is not bool):
            raise ValueError("propensity inputs must contain only visible covariate and response indicator")
        counter = counts[row["positive"] if include_sign else False]
        counter[0] += 1
        counter[1] += row["returned"]
    return {key: (hit + .5)/(n + 1) for key, (n, hit) in counts.items() if n >= MIN_STRATUM}


def estimated_probabilities(rows, fit, include_sign=True):
    if (any(set(row) != {"tick", "positive"} or type(row["tick"]) is not int or row["tick"] < EVAL_START
            or type(row["positive"]) is not bool for row in rows)
            or any(a["tick"] >= b["tick"] for a, b in zip(rows, rows[1:]))):
        raise ValueError("estimation rows violate evaluation time or covariate contract")
    return [fit.get(row["positive"] if include_sign else False) for row in rows]


def weighted(losses, probabilities, *, trustworthy=True):
    """Hajek estimate of mean loss; null loss represents nonresponse."""
    if not losses or len(losses) != len(probabilities):
        raise ValueError("probability-to-cohort mismatch")
    if any(x is not None and (not finite(x) or x < 0) for x in losses):
        raise ValueError("invalid observed loss")
    if any(p is not None and (not finite(p) or not 0 <= p <= 1) for p in probabilities):
        raise ValueError("invalid observation probability")
    observed = [(x,p) for x,p in zip(losses,probabilities) if x is not None]
    result = dict(planned=len(losses), observed=len(observed), estimate=None, ess=None,
                  max_raw_weight=None, clipped=0, status="insufficient_evidence")
    if any(p is None or p < MIN_P for p in probabilities):
        return dict(result,status="overlap_failed")
    raw = [1/p for _,p in observed]
    if not raw:
        return result
    weights = [min(w, MAX_WEIGHT) for w in raw]
    total = math.fsum(weights)
    ess = total**2 / math.fsum(w*w for w in weights)
    result.update(ess=ess,max_raw_weight=max(raw),clipped=sum(w>MAX_WEIGHT for w in raw))
    if len(observed) >= MIN_OBSERVED and (ess >= MIN_ESS or math.isclose(ess,MIN_ESS,rel_tol=1e-12)):
        # Store the diagnostic even for a deliberately misspecified method.
        status = ("assumptions_failed_diagnostic" if not trustworthy else
                  "clipped_diagnostic" if result["clipped"] else "identified_under_assumptions")
        result.update(estimate=math.fsum(w/total*x for w,(x,_) in zip(weights,observed)),
                      status=status)
    return result


def sensitivity(predictions, labels, *, ratios=RATIOS):
    """Sensitivity of fixed forecasts to common, missing outcomes.

    Report marginal loss-mean assumptions and outer pairwise intervals. A
    finite interval is only a sensitivity scenario, never a population bound.
    """
    if (not predictions or any(not values or len(values) != len(labels) for values in predictions.values())
            or any(not finite(x) for v in predictions.values() for x in v)
            or any(y is not None and not finite(y) for y in labels)
            or not ratios or any(not finite(r) or r < 0 for r in ratios)):
        raise ValueError("invalid or unpaired sensitivity inputs")
    observed = [i for i,y in enumerate(labels) if y is not None]
    missing = [i for i,y in enumerate(labels) if y is None]
    if len(observed) < MIN_OBSERVED or len(observed)/len(labels) < .30:
        return dict(status="insufficient_evidence", observed=len(observed), planned=len(labels))
    fraction = len(observed)/len(labels)
    mean = {s:statistics.mean((v[i]-labels[i])**2 for i in observed) for s,v in predictions.items()}
    grid = {s:[m*(fraction+(1.-fraction)*r) for r in ratios] for s,m in mean.items()}
    pairs = []
    for a,b in combinations(predictions,2):
        low, high = min(grid[a])-max(grid[b]), max(grid[a])-min(grid[b])
        changes = any(predictions[a][i] != predictions[b][i] for i in missing)
        diff = mean[a]-mean[b]
        pairs.append(dict(a=a,b=b,conditional_outer=[low,high],
                          conditional_order=a if high < 0 else b if low > 0 else "undetermined",
                          unrestricted_order="undetermined" if changes else
                          (a if diff < 0 else b if diff > 0 else "tie")))
    return dict(status="partially_identified" if missing else "identified_under_assumptions",
                unrestricted_status="not_identifiable" if missing else "identified_under_assumptions",
                observed=len(observed),planned=len(labels),observed_mse=mean,grid=grid,
                conditional_ratios=list(ratios),pairs=pairs,
                unrestricted_loss={s:[m*fraction,None if missing else m] for s,m in mean.items()})

