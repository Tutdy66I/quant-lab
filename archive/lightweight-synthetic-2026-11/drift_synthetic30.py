"""Synthetic rolling drift alarms with explicit feature and label availability.

Thresholds are predeclared effect-size rules, not p-values or a calibrated
false-alarm guarantee.  The relationship alarm compares univariate slopes; it
requires labels available by the monitoring timestamp and cannot detect every
kind of concept drift.  No market data or production interfaces are used.
"""
import math
import random

SEED = 20261113
THRESHOLDS = {'mean_standardized': 0.8, 'variance_ratio': 2.5,
              'slope_standardized': 0.65}


def _number(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError('finite numeric value required')
    try: result=float(v)
    except (OverflowError, ValueError) as exc: raise ValueError('finite numeric value required') from exc
    if not math.isfinite(result): raise ValueError('finite numeric value required')
    return result


def _rows(rows, as_of, *, min_count):
    if type(as_of) is not int or not isinstance(rows, (list, tuple)) or len(rows) < min_count:
        raise ValueError('integer observation time and sufficient samples required')
    previous = None
    values = []
    for row in rows:
        if not isinstance(row, dict): raise ValueError('record must be a dictionary')
        time = row.get('decision_time'); feature_time = row.get('feature_available_time')
        label_time = row.get('label_available_time')
        if (type(time) is not int or type(feature_time) is not int or type(label_time) is not int or
            (previous is not None and time <= previous) or feature_time > time or
            label_time < time or time > as_of or feature_time > as_of):
            raise ValueError('invalid ordering or feature/label time')
        values.append(_number(row.get('x'))); previous = time
    return tuple(values)


def _moments(values):
    try:
        mean = math.fsum(values) / len(values)
        var = math.fsum((v-mean)**2 for v in values) / len(values)
    except OverflowError as exc: raise ValueError('nonfinite moments') from exc
    if not math.isfinite(mean) or not math.isfinite(var): raise ValueError('nonfinite moments')
    return mean, var


def _slope(xs, ys):
    mx, var = _moments(xs); my, _ = _moments(ys)
    if var <= 1e-12: raise ValueError('feature variance too small for relationship diagnostic')
    value = math.fsum((x-mx)*(y-my) for x,y in zip(xs,ys)) / len(xs) / var
    if not math.isfinite(value): raise ValueError('nonfinite slope')
    return value


def fit_reference(rows, *, as_of_time):
    """Fit baseline with >=40 earlier records and fully available labels."""
    xs = _rows(rows, as_of_time, min_count=40)
    if any(row['label_available_time'] > as_of_time or row.get('y') is None for row in rows):
        raise ValueError('reference labels not yet available')
    ys = tuple(_number(row['y']) for row in rows)
    mean, var = _moments(xs); _, yvar = _moments(ys)
    if var <= 1e-12 or yvar <= 1e-12: raise ValueError('degenerate reference variance')
    return {'count':len(xs), 'mean':mean, 'variance':var, 'target_variance':yvar,
            'slope':_slope(xs, ys), 'reference_end':rows[-1]['decision_time'],
            'as_of_time':as_of_time}


def detect_drift(reference, rows, *, as_of_time):
    """Evaluate one chronological window with unmodified fixed thresholds.

    Relationship status is 'unavailable' if any window label is not yet
    available. In that case no window target values are inspected.
    """
    if not isinstance(reference, dict) or set(reference) != {'count','mean','variance','target_variance','slope','reference_end','as_of_time'}:
        raise ValueError('invalid fitted reference')
    if type(reference['count']) is not int or reference['count'] < 40 or type(reference['reference_end']) is not int or type(reference['as_of_time']) is not int:
        raise ValueError('invalid reference metadata')
    mu = _number(reference['mean']); rv = _number(reference['variance'])
    yv = _number(reference['target_variance']); slope = _number(reference['slope'])
    if rv <= 1e-12 or yv <= 1e-12: raise ValueError('degenerate fitted reference')
    xs = _rows(rows, as_of_time, min_count=20)
    if (rows[0]['decision_time'] <= reference['reference_end'] or
        reference['as_of_time'] > rows[0]['decision_time'] or as_of_time < reference['as_of_time']):
        raise ValueError('reference and monitoring windows overlap or run backwards')
    mean, var = _moments(xs)
    mean_effect = abs(mean-mu)/math.sqrt(rv)
    # A collapsed window has an undefined/infinite ratio. Represent it with
    # None and a variance alarm, never nonstandard JSON Infinity.
    ratio = max(var/rv, rv/var) if var > 1e-12 else None
    if not math.isfinite(mean_effect) or (ratio is not None and not math.isfinite(ratio)):
        raise ValueError('nonfinite drift statistic')
    mean_alarm = mean_effect >= THRESHOLDS['mean_standardized']
    variance_alarm = ratio is None or ratio >= THRESHOLDS['variance_ratio']
    labels_ready = all(r['label_available_time'] <= as_of_time and r.get('y') is not None for r in rows)
    if not labels_ready:
        relation = {'status':'unavailable','slope_effect':None,'alarm':None}
    elif ratio is None:
        relation = {'status':'undefined_constant_feature','slope_effect':None,'alarm':None}
    else:
        ys = tuple(_number(r['y']) for r in rows)
        change = abs(_slope(xs,ys)-slope)*math.sqrt(rv/yv)
        if not math.isfinite(change): raise ValueError('nonfinite relationship statistic')
        relation = {'status':'available','slope_effect':change,
                    'alarm':change >= THRESHOLDS['slope_standardized']}
    return {'as_of_time':as_of_time,'first_decision_time':rows[0]['decision_time'],
            'last_decision_time':rows[-1]['decision_time'], 'n':len(rows),
            'mean_effect':mean_effect,'variance_ratio':ratio,
            'mean_alarm':mean_alarm,'variance_alarm':variance_alarm,'relationship':relation,
            'thresholds':dict(THRESHOLDS),
            'scope':'synthetic effect rules; not calibrated significance or market inference'}


def generate(scenario, *, seed=SEED, count=300, change_at=160, label_delay=1):
    """Gaussian toy x/y with optional post-change mean, variance or slope shift."""
    if scenario not in ('stable','mean_shift','variance_shift','relation_shift'):
        raise ValueError('unknown scenario')
    if any(type(v) is not int for v in (seed,count,change_at,label_delay)) or count < 200 or not 120 <= change_at < count or label_delay < 0:
        raise ValueError('invalid synthetic configuration')
    rng=random.Random(seed); rows=[]
    for t in range(count):
        x=rng.gauss(0,1); noise=rng.gauss(0,.25)
        if t>=change_at and scenario=='mean_shift': x+=2.0
        if t>=change_at and scenario=='variance_shift': x*=2.5
        coefficient=-1.0 if t>=change_at and scenario=='relation_shift' else 1.0
        rows.append({'decision_time':t,'feature_available_time':t,
                     'label_available_time':t+label_delay,'x':x,'y':coefficient*x+noise})
    return rows


def experiment(*, trials=20, seed=SEED):
    """Fixed finite toy evaluation of alarm rate and observed detection delay.

    Common per-trial seeds across scenarios form paired comparisons. A trial is
    a false alarm if any of three stable pre-change nonoverlapping windows
    alerts. Delay is elapsed ticks from change to the *label-available* alarm.
    This is an empirical diagnostic, not a calibration or statistical guarantee.
    """
    if type(trials) is not int or not 2 <= trials <= 100 or type(seed) is not int:
        raise ValueError('invalid finite experiment configuration')
    out={}
    for scenario in ('stable','mean_shift','variance_shift','relation_shift'):
        records=[]
        for i in range(trials):
            rows=generate(scenario,seed=seed+i)
            ref=fit_reference(rows[:100],as_of_time=100)
            def check(start):
                window=rows[start:start+20]; return detect_drift(ref,window,as_of_time=window[-1]['label_available_time'])
            def any_alarm(d):return d['mean_alarm'] or d['variance_alarm'] or d['relationship']['alarm']
            pre=[check(s) for s in (100,120,140)]
            post=[check(s) for s in (160,180,200,220,240,260,280)]
            delays=[d['as_of_time']-160 for d in post if any_alarm(d)]
            records.append({'seed':seed+i,'pre_any_alarm':any(any_alarm(d) for d in pre),
                            'post_alarm':bool(delays),'first_alarm_delay_ticks':min(delays) if delays else None,
                            'post_first':post[0]})
        out[scenario]={'trials':trials,'pre_any_alarm_count':sum(x['pre_any_alarm'] for x in records),
                       'post_alarm_count':sum(x['post_alarm'] for x in records), 'records':records}
    return {'seed':seed,'trials':trials,'window_size':20,'reference_size':100,'change_at':160,
            'thresholds':dict(THRESHOLDS),'results':out,
            'limitations':'common seeds across scenarios; correlated windows and multiple alarms; no universal false-alarm guarantee'}
