"""Exploratory multi-window evaluation of the existing synthetic generator.

This module never loads market data. Each toy step t has decision at 2*t,
feature availability at 2*t, and label interval [2*t, 2*t+2). A one-tick
embargo purges samples whose target ends too near the next decision boundary.
These timestamps are synthetic DGP claims, not authenticated information
availability. Earlier evaluation windows can enter later training periods;
none of these repeatedly inspected windows is a pristine final holdout.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math

from ridge_synthetic import ALPHAS, SEED, TOTAL, fit_ridge, generate, predict_ridge
from sample_lineage import (Prediction, Sample, make_losses, prediction_id_for,
                            sample_id_for, validate_samples)

MODEL_IDS = ('mean', 'persistence', 'ridge')


@dataclass(frozen=True)
class Bounds:
    window_id: str
    train_start: int
    train_stop: int
    tune_start: int
    tune_stop: int
    validation_start: int
    validation_stop: int


# Stops are exclusive. OOS windows are disjoint, but later expanding training
# reuses observations from earlier OOS windows: they are not independent runs.
DEFAULT_WINDOWS = (Bounds('w0', 0, 60, 60, 90, 90, 120),
                   Bounds('w1', 0, 120, 120, 150, 150, 180),
                   Bounds('w2', 0, 180, 180, 210, 210, 240))


def _canonical_hash(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('invalid synthetic dataset or configuration') from exc
    return hashlib.sha256(raw).hexdigest()


def _window_plan(windows, gap):
    if type(gap) is not int or gap < 0:
        raise ValueError('gap must be a nonnegative integer tick count')
    if not isinstance(windows, (tuple, list)) or not windows:
        raise ValueError('nonempty window plan required')
    seen = set()
    previous_validation_stop = None
    for w in windows:
        if not isinstance(w, Bounds) or not isinstance(w.window_id, str) or not w.window_id or w.window_id in seen:
            raise ValueError('unique window IDs required')
        seen.add(w.window_id)
        nums = (w.train_start, w.train_stop, w.tune_start, w.tune_stop,
                w.validation_start, w.validation_stop)
        if any(type(x) is not int for x in nums) or not (0 <= w.train_start < w.train_stop <= w.tune_start <
                w.tune_stop <= w.validation_start < w.validation_stop <= TOTAL):
            raise ValueError('invalid chronological split')
        if previous_validation_stop is not None and w.validation_start < previous_validation_stop:
            raise ValueError('validation windows must be ordered and nonoverlapping')
        previous_validation_stop = w.validation_stop
    return tuple(windows)


def _sample(row, dataset_id, fingerprint, config_hash, split, window):
    t = row['time']
    sample_id = sample_id_for(dataset_id, fingerprint, t)
    return Sample(sample_id, dataset_id, fingerprint, config_hash, 2*t, 2*t, 2*t, 2*t+2,
                  row['target'], split, window, t)


def _purge(samples, next_decision, gap):
    """Apply half-open label cutoff; record discarded sample IDs."""
    kept, discarded = [], []
    for sample in samples:
        (kept if sample.target_end_time <= next_decision - gap else discarded).append(sample)
    return tuple(kept), tuple(s.sample_id for s in discarded)


def _select_alpha(train_samples, tune_samples, features, *, context):
    """Select only from records explicitly tagged training and tuning."""
    dataset_id, fingerprint, config_hash, window_id, tune_cutoff = context
    for samples, split, cutoff in ((train_samples, 'train', None), (tune_samples, 'tune', tune_cutoff)):
        validate_samples(samples, expected_dataset_id=dataset_id, expected_dataset_fingerprint=fingerprint,
                         expected_config_hash=config_hash, split_id=split, window_id=window_id,
                         label_cutoff=cutoff)
    if max(s.target_end_time for s in train_samples) > min(s.decision_time for s in tune_samples):
        raise ValueError('training label crosses tuning boundary')
    xtrain = [features[s.sample_id] for s in train_samples]
    ytrain = [s.target_value for s in train_samples]
    xtune = [features[s.sample_id] for s in tune_samples]
    ytune = [s.target_value for s in tune_samples]
    models = [(alpha, fit_ridge(xtrain, ytrain, alpha)) for alpha in ALPHAS]
    scores = []
    for alpha, model in models:
        errors = [(predict_ridge(model, x)-y)**2 for x,y in zip(xtune, ytune)]
        score = math.fsum(errors) / len(errors)
        if not math.isfinite(score): raise ValueError('nonfinite tuning loss')
        scores.append((alpha, score))
    selected = min(scores, key=lambda item:(item[1],item[0]))[0]
    return selected, next(model for alpha,model in models if alpha == selected), tuple(scores)


def _metrics(records):
    count = len(records)
    mae = math.fsum(abs(item.prediction.prediction_value-item.sample.target_value) for item in records) / count
    rmse = math.sqrt(math.fsum(item.loss_value for item in records) / count)
    if not math.isfinite(mae) or not math.isfinite(rmse): raise ValueError('nonfinite validation metric')
    return {'mae': mae, 'rmse': rmse}


def run_rolling(scenario, *, seed=SEED, windows=DEFAULT_WINDOWS, gap=1):
    """Return same-order synthetic validation samples, predictions, losses.

    One generator invocation fixes the actual dataset fingerprint. Different
    seeds, scenarios, sample values, or configuration change the corresponding
    digest. Models and preprocessing are fitted from purged training rows only;
    alpha uses purged tuning rows only, with no final fit on tuning data.
    """
    windows = _window_plan(windows, gap)
    if type(seed) is not int: raise ValueError('seed must be integer')
    rows = generate(scenario, seed)
    if len(rows) != TOTAL or any(row['time'] != index for index, row in enumerate(rows)):
        raise ValueError('synthetic generated sequence must be complete and chronological')
    dataset_id = 'synthetic-ridge-v1:' + scenario + ':' + str(seed)
    config = {'schema':'rolling-synthetic-v1', 'generator':'ridge_synthetic.generate-v1',
              'scenario':scenario,'seed':seed,'windows':[asdict(w) for w in windows],
              'gap_ticks':gap,'alphas':list(ALPHAS),'refit':'training_only',
              'target_interval':'[2*t,2*t+2)','feature_available':'2*t'}
    config_hash = _canonical_hash(config)
    fingerprint = _canonical_hash({'schema':'synthetic-generated-rows-v1','scenario':scenario,
                                   'seed':seed,'rows':rows})
    output = []
    prior_validation_ids = set()
    previous_evaluation_used_for_training = False
    for w in windows:
        train = tuple(_sample(row,dataset_id,fingerprint,config_hash,'train',w.window_id)
                      for row in rows[w.train_start:w.train_stop])
        tune = tuple(_sample(row,dataset_id,fingerprint,config_hash,'tune',w.window_id)
                     for row in rows[w.tune_start:w.tune_stop])
        validation = tuple(_sample(row,dataset_id,fingerprint,config_hash,'validation',w.window_id)
                           for row in rows[w.validation_start:w.validation_stop])
        train, train_purged = _purge(train, tune[0].decision_time, gap)
        tune, tune_purged = _purge(tune, validation[0].decision_time, gap)
        if len(train) < 3 or not tune:
            raise ValueError('purging left too few training or tuning samples')
        context = dict(expected_dataset_id=dataset_id, expected_dataset_fingerprint=fingerprint,
                       expected_config_hash=config_hash, window_id=w.window_id)
        validate_samples(train, split_id='train', label_cutoff=2*w.tune_start-gap, **context)
        validate_samples(tune, split_id='tune', label_cutoff=2*w.validation_start-gap, **context)
        validate_samples(validation, split_id='validation', **context)
        if prior_validation_ids.intersection(s.sample_id for s in train):
            previous_evaluation_used_for_training = True
        prior_validation_ids.update(s.sample_id for s in validation)
        if train[-1].target_end_time > tune[0].decision_time-gap or tune[-1].target_end_time > validation[0].decision_time-gap:
            raise ValueError('target crosses boundary or embargo')
        features = {_sample(row,dataset_id,fingerprint,config_hash,'feature',w.window_id).sample_id:
                    tuple(row['features']) for row in rows}
        selected, model, scores = _select_alpha(train,tune,features,
                         context=(dataset_id,fingerprint,config_hash,w.window_id,2*w.validation_start-gap))
        mean_value = math.fsum(row.target_value for row in train) / len(train)
        raw_validation = rows[w.validation_start:w.validation_stop]
        pred_values = {'mean':[mean_value]*len(validation),
                       'persistence':[row['previous'] for row in raw_validation],
                       'ridge':[predict_ridge(model,row['features']) for row in raw_validation]}
        predictions, losses = {}, {}
        for name in MODEL_IDS:
            predictions[name] = tuple(Prediction(prediction_id_for(s.sample_id,w.window_id,name,config_hash),
                          s.sample_id,dataset_id,w.window_id,name,value)
                          for s,value in zip(validation,pred_values[name]))
            losses[name] = make_losses(validation,predictions[name],split_id='validation',model_id=name,**context)
        metrics={name:_metrics(losses[name]) for name in MODEL_IDS}
        diffs={name:math.fsum(a.loss_value-b.loss_value for a,b in zip(losses['ridge'],losses[name]))/len(validation)
               for name in ('mean','persistence')}
        output.append({'window_id':w.window_id,'bounds':asdict(w),
                       'train_samples':train,'tune_samples':tune,'validation_samples':validation,
                       'purged_train_ids':train_purged,'purged_tune_ids':tune_purged,
                       'train_time_range':(train[0].decision_time,train[-1].decision_time),
                       'tune_time_range':(tune[0].decision_time,tune[-1].decision_time),
                       'validation_time_range':(validation[0].decision_time,validation[-1].decision_time),
                       'model':asdict(model),'mean_prediction':mean_value,'selected_alpha':selected,
                       'alpha_tune_mse':scores,'predictions':predictions,'losses':losses,
                       'metrics':metrics,'ridge_minus_mse':diffs,
                       'ranking':tuple(sorted(MODEL_IDS,key=lambda name:(metrics[name]['rmse'],name)))})
    return {'scope':'synthetic_exploratory_not_market_inference','dataset_id':dataset_id,
            'dataset_fingerprint':fingerprint,'config_hash':config_hash,'config':config,
            'validation_windows_nonoverlapping':True,
            'earlier_validation_reused_for_later_training':previous_evaluation_used_for_training,
            'interpretation':'Repeatedly inspected, dependent windows; not untouched independent final test.',
            'windows':tuple(output)}
