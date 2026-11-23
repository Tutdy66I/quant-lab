"""Explicit identities for synthetic samples, predictions, and paired losses.

Caller controls all metadata; equality is checked but origin, actual feature
availability and claims are not independently authenticated. This is not a
historical-data admission or process security boundary. Time uses integer
ticks in one declared timeline, and target windows are half-open.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re


@dataclass(frozen=True)
class Sample:
    sample_id: str
    synthetic_dataset_id: str
    dataset_fingerprint: str
    config_hash: str
    decision_time: int
    feature_available_time: int
    target_start_time: int
    target_end_time: int
    target_value: float
    split_id: str
    window_id: str
    source_ordinal: int


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    sample_id: str
    synthetic_dataset_id: str
    window_id: str
    model_id: str
    prediction_value: float


@dataclass(frozen=True)
class LossRecord:
    sample: Sample
    prediction: Prediction
    loss_value: float
    loss_definition: str = 'squared_error'


def _text(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(label + ' must be a nonempty canonical string')
    return value


def _digest(value, label):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError(label + ' must be a lowercase SHA-256 string')
    return value


def _record_id(value, prefix):
    if not isinstance(value, str) or re.fullmatch(prefix + r':[0-9a-f]{64}', value) is None:
        raise ValueError(prefix + '_id must be namespaced SHA-256, not a timestamp')
    return value


def _make_id(prefix, values):
    payload = json.dumps([prefix, *values], ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return prefix + ':' + hashlib.sha256(payload).hexdigest()


def sample_id_for(dataset_id, dataset_fingerprint, source_ordinal):
    """Stable toy dataset identity from dataset fingerprint and source index.

    The integer ordinal is an input row identity, not its timestamp; caller
    is responsible for deriving dataset_fingerprint from actual generation.
    """
    _text(dataset_id, 'dataset_id'); _digest(dataset_fingerprint, 'dataset_fingerprint')
    if type(source_ordinal) is not int or source_ordinal < 0:
        raise ValueError('source_ordinal must be a nonnegative integer')
    return _make_id('sample', (dataset_id, dataset_fingerprint, source_ordinal))


def prediction_id_for(sample_id, window_id, model_id, config_hash):
    """Stable identity bound to sample, window, model, and configuration."""
    _record_id(sample_id, 'sample'); _text(window_id, 'window_id'); _text(model_id, 'model_id')
    _digest(config_hash, 'config_hash')
    return _make_id('pred', (sample_id, window_id, model_id, config_hash))


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + ' must be finite numeric')
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(label + ' must be finite numeric') from exc
    if not math.isfinite(value):
        raise ValueError(label + ' must be finite numeric')
    return value


def _time(value, label):
    if type(value) is not int:
        raise ValueError(label + ' must be integer ticks')
    return value


def validate_samples(samples, *, expected_dataset_id, expected_dataset_fingerprint,
                     expected_config_hash, split_id, window_id, label_cutoff=None):
    """Validate input order/context; never silently reorder or fill records.

    label_cutoff, if set, is the inclusive latest permissible target-end tick;
    upstream split construction must supply it for each training/tuning set.
    """
    _text(expected_dataset_id, 'expected_dataset_id')
    _digest(expected_dataset_fingerprint, 'expected_dataset_fingerprint')
    _digest(expected_config_hash, 'expected_config_hash')
    _text(split_id, 'split_id'); _text(window_id, 'window_id')
    if label_cutoff is not None: _time(label_cutoff, 'label_cutoff')
    if not isinstance(samples, (list, tuple)) or not samples:
        raise ValueError('nonempty sample sequence required')
    seen = set(); previous = None
    for row in samples:
        if not isinstance(row, Sample): raise ValueError('Sample records required')
        _record_id(row.sample_id, 'sample')
        if row.sample_id in seen: raise ValueError('duplicate sample_id')
        seen.add(row.sample_id)
        if (row.synthetic_dataset_id != expected_dataset_id or
            row.dataset_fingerprint != expected_dataset_fingerprint or row.config_hash != expected_config_hash or
            row.split_id != split_id or row.window_id != window_id):
            raise ValueError('dataset, configuration, split or window mismatch')
        if row.sample_id != sample_id_for(expected_dataset_id, expected_dataset_fingerprint, row.source_ordinal):
            raise ValueError('sample ID does not match source ordinal and dataset')
        for name in ('decision_time','feature_available_time','target_start_time','target_end_time'):
            _time(getattr(row, name), name)
        _number(row.target_value, 'target_value')
        if row.feature_available_time > row.decision_time:
            raise ValueError('feature not available at decision')
        if row.target_start_time < row.decision_time or row.target_end_time <= row.target_start_time:
            raise ValueError('invalid future target interval')
        if label_cutoff is not None and row.target_end_time > label_cutoff:
            raise ValueError('target crosses split cutoff')
        if previous is not None and row.decision_time <= previous:
            raise ValueError('samples are not in strict decision order')
        previous = row.decision_time
    return tuple(samples)


def make_losses(samples, predictions, *, expected_dataset_id, expected_dataset_fingerprint,
                expected_config_hash, split_id, window_id, model_id, label_cutoff=None):
    """Pair supplied predictions in input order, rejecting duplicates/misalignment."""
    samples = validate_samples(samples, expected_dataset_id=expected_dataset_id,
             expected_dataset_fingerprint=expected_dataset_fingerprint, expected_config_hash=expected_config_hash,
             split_id=split_id, window_id=window_id, label_cutoff=label_cutoff)
    _text(model_id, 'model_id')
    if not isinstance(predictions, (list, tuple)) or len(predictions) != len(samples):
        raise ValueError('exactly one prediction per sample required')
    ids = set(); out = []
    for row, pred in zip(samples, predictions):
        if not isinstance(pred, Prediction): raise ValueError('Prediction records required')
        _record_id(pred.prediction_id, 'pred')
        if pred.prediction_id in ids: raise ValueError('duplicate prediction_id')
        ids.add(pred.prediction_id)
        if (pred.sample_id != row.sample_id or pred.synthetic_dataset_id != expected_dataset_id or
            pred.window_id != window_id or pred.model_id != model_id):
            raise ValueError('prediction identity/order mismatch')
        if pred.prediction_id != prediction_id_for(row.sample_id, window_id, model_id, expected_config_hash):
            raise ValueError('prediction ID does not match sample, model, window and configuration')
        difference = _number(pred.prediction_value, 'prediction') - _number(row.target_value, 'target')
        loss = _number(difference * difference, 'squared_error')
        out.append(LossRecord(row, pred, loss))
    return tuple(out)


def pair_losses(samples, losses_a, losses_b, *, expected_dataset_id, expected_dataset_fingerprint,
                expected_config_hash, split_id, window_id, model_a, model_b):
    """Validate exact same ordered sample records and return two loss tuples.

    A record is re-derived from its bound prediction/target, preventing an
    incorrect loss field being silently treated as trustworthy. Both models
    and IDs must differ. Sample identity metadata is a caller declaration.
    """
    _text(model_a, 'model_a'); _text(model_b, 'model_b')
    if model_a == model_b: raise ValueError('model IDs must differ')
    rows = validate_samples(samples, expected_dataset_id=expected_dataset_id,
           expected_dataset_fingerprint=expected_dataset_fingerprint, expected_config_hash=expected_config_hash,
           split_id=split_id, window_id=window_id)
    result=[]; all_prediction_ids=set()
    for model, records in ((model_a,losses_a),(model_b,losses_b)):
        if not isinstance(records, (list,tuple)) or len(records)!=len(rows):
            raise ValueError('loss sequences must cover identical samples')
        if any(not isinstance(item,LossRecord) for item in records):
            raise ValueError('LossRecord records required')
        predicted=make_losses(rows, [item.prediction for item in records],
                  expected_dataset_id=expected_dataset_id, expected_dataset_fingerprint=expected_dataset_fingerprint,
                  expected_config_hash=expected_config_hash, split_id=split_id, window_id=window_id, model_id=model)
        for supplied, expected in zip(records, predicted):
            if supplied.sample != expected.sample or supplied.loss_definition != 'squared_error':
                raise ValueError('loss sample or definition mismatch')
            if _number(supplied.loss_value,'loss_value') != expected.loss_value:
                raise ValueError('loss does not match prediction and target')
            if supplied.prediction.prediction_id in all_prediction_ids:
                raise ValueError('prediction ID must be globally distinct across models')
            all_prediction_ids.add(supplied.prediction.prediction_id)
        result.append(tuple(item.loss_value for item in predicted))
    return tuple(result)
