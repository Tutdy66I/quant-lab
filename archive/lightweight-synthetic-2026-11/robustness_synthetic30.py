"""Exploratory, chronological model comparison on *only* new synthetic data.

These repeatedly inspected development windows are not an untouched test set.
No result proves market forecasting or trading performance.
"""
from dataclasses import asdict
import hashlib
import json
import math
import random

from ridge_synthetic import ALPHAS, fit_ridge, predict_ridge

SEED = 20261115
SCENARIOS = ('signal', 'no_signal', 'high_noise', 'outliers', 'feature_shift', 'relation_shift')
STARTS = (140, 190)
VALID_SIZE = 30
TUNE_SIZE = 30
TRAIN_SIZES = (35, 80)
GAP_TICKS = 1
MODELS = ('mean', 'persistence', 'ridge')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                       allow_nan=False).encode('utf-8')).hexdigest()


def generate(scenario, seed=SEED):
    """240 artificial observations. Feature x[t] and previous y are available at decision.

    Feature/relationship changes occur only at t>=140. Outlier impulses occur
    at fixed indices 149, 165, 199, 215 and do not depend on model outcomes.
    """
    if scenario not in SCENARIOS or type(seed) is not int:
        raise ValueError('unknown scenario or invalid seed')
    rng = random.Random(seed); previous = 0.0; rows = []
    for t in range(240):
        x = rng.gauss(0.0, 1.0)
        noise = rng.gauss(0.0, 1.5 if scenario == 'high_noise' else 0.3)
        if scenario == 'feature_shift' and t >= 140:
            x = 2.5*x + 2.0
        coefficient = -0.8 if scenario == 'relation_shift' and t >= 140 else 0.8
        target = noise if scenario == 'no_signal' else 0.35*previous + coefficient*x + noise
        if scenario == 'outliers' and t in (149, 165, 199, 215):
            target += 8.0
        rows.append({'ordinal':t,'decision_time':2*t,'feature_available_time':2*t,
                     'target_start_time':2*t,'target_end_time':2*t+2,
                     'features':(previous,x),'target':target})
        previous = target
    return rows


def _validate(rows):
    if not isinstance(rows,(list,tuple)) or len(rows)!=240:
        raise ValueError('complete 240-row generated sequence required')
    for i,r in enumerate(rows):
        if not isinstance(r,dict) or r.get('ordinal')!=i:
            raise ValueError('missing, duplicated, reordered or invalid ordinal')
        if any(type(r.get(k)) is not int for k in ('decision_time','feature_available_time','target_start_time','target_end_time')):
            raise ValueError('integer timestamps required')
        if (r['decision_time']!=2*i or r['feature_available_time']>r['decision_time'] or
            r['target_start_time']!=2*i or r['target_end_time']!=2*i+2):
            raise ValueError('future feature or inconsistent label window')
        if not isinstance(r.get('features'),(tuple,list)) or len(r['features'])!=2:
            raise ValueError('two features required')
        for v in (*r['features'],r.get('target')):
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):
                raise ValueError('nonfinite or invalid numeric observation')


def _ids(rows, dataset_hash):
    return tuple('synthetic30:'+_hash({'dataset':dataset_hash,'ordinal':r['ordinal']}) for r in rows)


def evaluate_window(rows, *, scenario, seed, validation_start, train_size):
    """Fit training-only, pick alpha by earlier tuning MSE, then evaluate.

    All splits are half-open; one tick gap removes the row immediately before
    both tune and validation. Later windows may reuse earlier validation rows.
    """
    _validate(rows)
    if (scenario not in SCENARIOS or type(seed) is not int or validation_start not in STARTS or
            train_size not in TRAIN_SIZES):
        raise ValueError('unapproved scenario, seed, window or training size')
    tune_start = validation_start - TUNE_SIZE
    train_start = tune_start - train_size
    if train_start < 0: raise ValueError('insufficient history')
    train = rows[train_start:tune_start-1]
    tune = rows[tune_start:validation_start-1]
    valid = rows[validation_start:validation_start+VALID_SIZE]
    if (len(train)<3 or not tune or len(valid)!=VALID_SIZE or
        train[-1]['target_end_time'] > tune[0]['decision_time']-GAP_TICKS or
        tune[-1]['target_end_time'] > valid[0]['decision_time']-GAP_TICKS):
        raise ValueError('invalid split or label crosses embargo')
    candidates = [(a,fit_ridge([r['features'] for r in train],[r['target'] for r in train],a)) for a in ALPHAS]
    tune_mse = [(a,math.fsum((predict_ridge(m,r['features'])-r['target'])**2 for r in tune)/len(tune))
                for a,m in candidates]
    alpha = min(tune_mse,key=lambda pair:(pair[1],pair[0]))[0]
    model = next(m for a,m in candidates if a==alpha)
    mean = math.fsum(r['target'] for r in train)/len(train)
    predictions = {'mean':[mean for _ in valid], 'persistence':[r['features'][0] for r in valid],
                   'ridge':[predict_ridge(model,r['features']) for r in valid]}
    losses={name:[(p-r['target'])**2 for p,r in zip(pred,valid)] for name,pred in predictions.items()}
    metrics={name:{'rmse':math.sqrt(math.fsum(ls)/len(ls)),
                   'mae':math.fsum(abs(p-r['target']) for p,r in zip(predictions[name],valid))/len(valid)}
             for name,ls in losses.items()}
    fingerprint=_hash({'scenario':scenario,'seed':seed,'rows':rows})
    return {'scenario':scenario,'seed':seed,'dataset_sha256':fingerprint,
      'window_id':'start-'+str(validation_start)+'-train-'+str(train_size),
      'ranges':{'train':[train_start,tune_start-1], 'tune':[tune_start,validation_start-1],
                'validation':[validation_start,validation_start+VALID_SIZE], 'stops_exclusive':True},
      'purged_ordinals':[tune_start-1,validation_start-1], 'gap_ticks':GAP_TICKS,
      'valid_sample_ids':_ids(valid,fingerprint), 'selected_alpha':alpha,'tune_mse':tune_mse,
      'fitted_model':asdict(model),'training_target_mean':mean,'metrics':metrics,
      'ranking':tuple(sorted(MODELS,key=lambda name:(metrics[name]['rmse'],name))),
      'loss_differences':{name:tuple(a-b for a,b in zip(losses['ridge'],losses[name]))
                          for name in ('mean','persistence')},
      'limitations':['synthetic exploratory development, not market inference',
                     'previous development windows may be reused in later training',
                     'entire-series fingerprint is retrospective identity, not a predictor',
                     'repeated inspection introduces selection bias']}


def run_matrix(seed=SEED):
    if type(seed) is not int: raise ValueError('integer seed required')
    results=[]
    for scenario in SCENARIOS:
        rows=generate(scenario,seed)
        for size in TRAIN_SIZES:
            for start in STARTS:
                results.append(evaluate_window(rows,scenario=scenario,seed=seed,
                                      validation_start=start,train_size=size))
    return {'seed':seed,'predeclared_scenarios':SCENARIOS,'window_starts':STARTS,
            'train_sizes':TRAIN_SIZES,'models':MODELS,'results':results,
            'scope':'exploratory synthetic development; not held-out final evaluation'}
