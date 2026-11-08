"""Synthetic-only daily benchmark and provenance primitives.

This deliberately models the repository's idealized previous-close/next-open
convention. Inputs and outputs are memory-only. No research admission, live
observation, model training, external execution proof or trading is implied.
"""
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from research import ai_offline as offline
from strategies import execution, market_data, research, walk_forward
from strategies.market_data import parse_daily_rows

UTC = timezone.utc
PROTOCOL = {
    'schema_version': 1,
    'name': 'synthetic_daily_baselines_v1',
    'decision': 'previous_daily_close',
    'fill': 'next_daily_open_idealized_same_instant',
    'target': 'same_day_close_over_open_return',
    'forecast': ['zero', 'previous_close_to_close_return'],
    'strategies': ['flat', 'previous_return_positive_long', 'ma20_60', 'buy_hold'],
    'previous_return_long_threshold': 0.0,
    'initial_cash': research.INITIAL_CASH,
    'fee_per_side': execution.FEE,
    'slippage_per_side': execution.SLIPPAGE,
    'start_position': 'flat',
    'terminal_liquidation': 'last_close_with_costs',
    'random_seed': None,
    'randomness': 'none',
}
FEATURE_CONFIG = {'schema_version': 1, 'features': ['previous_close_to_close_return'],
                  'signal_lookback_days': 2, 'ma_warmup_days': 61}


def _hash(value):
    return offline.sha(offline.canonical(value))


def _file_hash(module):
    return offline.sha(Path(module.__file__).read_bytes().replace(b'\r\n', b'\n'))


def external_prediction_record(blob):
    """Record bytes without asserting that a model or training produced them."""
    if not isinstance(blob, bytes) or not blob:
        raise ValueError('Nonempty external prediction bytes required')
    return {'sha256': offline.sha(blob), 'lineage_status': 'unverified_external',
            'execution_proven': False, 'eligible_for_formal_research': False}


def predict_synthetic_at(rows, *, dataset_kind, decision_at_utc, max_age_seconds):
    """Single prefix-only prediction; no outcome, strategy or full evaluation."""
    if dataset_kind != 'synthetic' or not isinstance(rows, list):
        raise ValueError('Only declared synthetic rows are supported')
    decision = offline.utc(decision_at_utc)
    visible = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('timestamp')) is not int:
            raise ValueError('Cannot determine candle timestamp')
        try:
            start = datetime.fromtimestamp(row['timestamp'], UTC)
            end = start + timedelta(days=1)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError('Cannot determine candle timestamp') from error
        if start != start.replace(hour=0, minute=0, second=0, microsecond=0):
            raise ValueError('Daily candle must start at UTC midnight')
        if end <= decision:
            try:
                available = offline.utc(row['available_at_utc'])
            except KeyError as error:
                raise ValueError('Cannot determine candle availability') from error
            if available < end:
                raise ValueError('Availability precedes event end')
            if available <= decision:
                visible.append((end, available, row))
    visible.sort(key=lambda item: item[0])
    if len({end for end, _, _ in visible}) != len(visible):
        raise ValueError('Duplicate visible candle')
    selected = []
    for end, available, row in visible[-3:]:
        try:
            prices = {key: offline.number(row[key]) for key in ('open', 'high', 'low', 'close')}
        except KeyError as error:
            raise ValueError('Visible candle price is missing') from error
        if min(prices.values()) <= 0 or prices['high'] < max(prices.values()) or prices['low'] > min(prices.values()):
            raise ValueError('Invalid visible OHLC range')
        selected.append({'end_utc': end.isoformat(), 'available_at_utc': available.isoformat(),
                         'close': prices['close']})
    features = offline.visible_price_features(selected, decision_at_utc,
                                               max_age_seconds=max_age_seconds)
    result = {'scope': 'synthetic_prefix_prediction_only', 'decision_utc': decision.isoformat(),
              'features': features, 'research_admission': 'insufficient_evidence',
              'formal_statistics_enabled': False, 'source_authenticity': 'unverified'}
    if features['status'] == 'fresh':
        result['predictions'] = {'zero': 0.0, 'previous_return': features['return_1d']}
    return result


def compare_synthetic(source_bytes, *, expected_sha256, dataset_kind, start_index, as_of_utc):
    """Compare simple forecasts and trading rules on one declared synthetic set.

    Source bytes must be canonical JSON: an array of objects with timestamp,
    open/high/low/close and available_at_utc. Synthetic provenance is declared,
    not independently authenticated. Daily fills are a counterfactual model.
    """
    if dataset_kind != 'synthetic' or not isinstance(source_bytes, bytes):
        raise ValueError('Only declared synthetic input is supported')
    if offline.sha(source_bytes) != expected_sha256:
        raise ValueError('Synthetic source fingerprint mismatch')
    try:
        rows = json.loads(source_bytes.decode('utf-8'))
    except (UnicodeError, ValueError) as error:
        raise ValueError('Invalid JSON input') from error
    if not isinstance(rows, list) or not rows or offline.canonical(rows) != source_bytes:
        raise ValueError('Require nonempty canonical JSON array')
    expected = {'timestamp', 'open', 'high', 'low', 'close', 'available_at_utc'}
    if any(not isinstance(r, dict) or set(r) != expected or type(r['timestamp']) is not int for r in rows):
        raise ValueError('Invalid synthetic candle schema')
    try:
        for row in rows:
            for key in ('open', 'high', 'low', 'close'):
                offline.number(row[key])
        raw = [[r['timestamp'], r['open'], r['high'], r['low'], r['close']] for r in rows]
        candles = parse_daily_rows(raw)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Invalid synthetic candle') from error
    if type(start_index) is not int or not 61 <= start_index < len(candles):
        raise ValueError('Require MA60 warmup and a nonempty comparison')
    availability = [offline.utc(r['available_at_utc']) for r in rows]
    ends = [datetime.fromtimestamp(c.timestamp, UTC) + timedelta(days=1) for c in candles]
    if any(a < e for a, e in zip(availability, ends)):
        raise ValueError('A candle cannot be available before its daily close')
    as_of = offline.utc(as_of_utc)
    if any(a > as_of for a in availability[start_index:]):
        raise ValueError('Outcomes are not yet available at evaluation time')
    # All signal history used by MA60 and the return forecast must have been
    # available by each idealized next-open decision, or the run is rejected.
    features, forecasts, labels = [], [], []
    cash, quantity = research.INITIAL_CASH, 0.0
    lag_curve = []
    for index in range(start_index, len(candles)):
        decision = datetime.fromtimestamp(candles[index].timestamp, UTC)
        if any(a > decision for a in availability[index-61:index]):
            raise ValueError('Signal data was not available by decision')
        previous_return = offline.finite_return(candles[index-1].close, candles[index-2].close)
        target = offline.finite_return(candles[index].close, candles[index].open)
        if not math.isfinite(previous_return) or not math.isfinite(target):
            raise ValueError('Nonfinite forecast or target')
        features.append({'decision_utc': decision.isoformat(), 'previous_return': previous_return,
                         'feature_available_at_utc': max(availability[index-61:index]).isoformat()})
        forecasts.append({'decision_utc': decision.isoformat(), 'zero': 0.0,
                          'previous_return': previous_return})
        labels.append({'decision_utc': decision.isoformat(), 'target': target,
                       'target_available_at_utc': availability[index].isoformat()})
        should_hold = previous_return > 0
        if should_hold and not quantity:
            _, quantity, _ = execution.buy_fill(cash, candles[index].open)
            quantity = offline.number(quantity)
            cash = 0.0
        elif not should_hold and quantity:
            _, cash, _ = execution.sell_fill(quantity, candles[index].open)
            cash = offline.number(cash)
            quantity = 0.0
        lag_curve.append(offline.number(cash + offline.number(quantity * candles[index].close)))
    if quantity:
        _, cash, _ = execution.sell_fill(quantity, candles[-1].close)
        lag_curve[-1] = offline.number(cash)
    ma = research.run_backtest(candles, research.Config(), start_index)
    hold_curve = walk_forward.hold_curve(candles, start_index, len(candles))
    offline.require_finite_tree(ma)
    offline.require_finite_tree(hold_curve)
    count = len(forecasts)
    metrics = {'sample_count': count,
        'forecast_mse': {
            'zero': offline.mse((0.0, label['target']) for label in labels),
            'previous_return': offline.mse((prediction['previous_return'], label['target'])
                                           for prediction, label in zip(forecasts, labels))},
        'strategy_final_equity': {'flat': research.INITIAL_CASH,
                                  'previous_return_positive_long': lag_curve[-1],
                                  'ma20_60': ma['equity'][-1], 'buy_hold': hold_curve[-1]}}
    protocol, feature_config = deepcopy(PROTOCOL), deepcopy(FEATURE_CONFIG)
    model = {'kind': 'built_in_deterministic_baselines', 'training': 'not_applicable',
             'parameters': deepcopy(protocol), 'source_sha256': _file_hash(sys.modules[__name__]),
             'execution_sha256': _file_hash(execution), 'research_engine_sha256': _file_hash(research),
             'walk_forward_sha256': _file_hash(walk_forward),
             'offline_sha256': _file_hash(offline), 'market_data_sha256': _file_hash(market_data)}
    provenance = {'source_sha256': expected_sha256, 'feature_config_sha256': _hash(feature_config),
                  'features_sha256': _hash(features),
                  'training_sample_sha256': None, 'model_sha256': _hash(model),
                  'predictions_sha256': _hash(forecasts), 'labels_sha256': _hash(labels),
                  'metrics_sha256': _hash(metrics),
                  'python_version': sys.version.split()[0], 'execution_proven': False,
                  'lineage_status': 'locally_recomputed_from_caller_bytes'}
    payload = {'schema_version': 2, 'scope': 'synthetic_engineering_only',
               'research_admission': 'insufficient_evidence', 'formal_statistics_enabled': False,
               'execution_model': 'idealized_daily_open_not_real_fill',
               'protocol': protocol, 'feature_config': feature_config, 'start_index': start_index,
               'as_of_utc': as_of.isoformat(), 'features': features, 'predictions': forecasts,
               'labels': labels, 'metrics': metrics,
               'model': model, 'provenance': provenance,
               'limitations': ['Synthetic provenance and timestamps are caller claims.',
                               'No model was trained and no AI edge is established.',
                               'Equal-boundary daily close/open is an idealization, not a verified executable fill.']}
    offline.require_finite_tree(payload)
    payload['report_payload_sha256'] = _hash(payload)
    return payload


def verify_synthetic_report(source_bytes, report):
    """Recompute this implementation's full report, without asserting source truth."""
    try:
        if report['schema_version'] != 2:
            raise ValueError('Legacy report requires its original evaluator for replay')
        if report['scope'] != 'synthetic_engineering_only':
            raise ValueError('Unexpected report scope')
        recomputed = compare_synthetic(source_bytes,
            expected_sha256=report['provenance']['source_sha256'], dataset_kind='synthetic',
            start_index=report['start_index'], as_of_utc=report['as_of_utc'])
        if offline.canonical(recomputed) != offline.canonical(report):
            raise ValueError('Synthetic report does not match recomputation')
    except (KeyError, TypeError) as error:
        raise ValueError('Invalid synthetic report') from error
    return {'status': 'internally_recomputed', 'execution_proven': False,
            'source_authenticity': 'unverified', 'research_admission': 'insufficient_evidence'}
