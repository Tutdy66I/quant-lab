"""Paired decision-clock scoring and seed-level uncertainty."""

import math
import statistics


def by_time(run, strategy):
    prediction_times = {p['sample_id']: p['decision_time'] for p in run['predictions']
                        if p['strategy'] == strategy}
    feedback = [f for f in run['feedback'] if f['strategy'] == strategy]
    mapped = {prediction_times[f['sample_id']]: f for f in feedback}
    if len(mapped) != len(feedback):
        raise ValueError('duplicate feedback')
    return mapped


def score(run, strategy, clock, cost):
    observed = by_time(run, strategy)
    if not clock or not clock <= observed.keys():
        raise ValueError('incompatible scoring clocks')
    selected = [observed[t] for t in sorted(clock)]
    mse = math.fsum(f['squared_loss'] for f in selected) / len(selected)
    mae = math.fsum(f['absolute_loss'] for f in selected) / len(selected)
    updates = [e for e in run['events'] if e.get('event_type') == 'activation'
               and e['strategy'] == strategy and e['version'] > 0]
    average_cost = cost * len(updates) / len(selected)
    return {'observed_count': len(observed), 'common_count': len(selected),
            'mae': mae, 'mse': mse, 'rmse': math.sqrt(mse), 'updates': len(updates),
            'average_cost': average_cost, 'objective': mse + average_cost,
            'candidate_fit_attempts': run['metrics'][strategy]['candidate_fit_attempts']}


def recovery(run, strategy, clock, change):
    observed = by_time(run, strategy)
    pre = [observed[t]['squared_loss'] for t in sorted(clock) if t < change]
    if len(pre) < 10:
        return {'delay': None, 'status': 'insufficient_baseline', 'cap': run['horizon'] - change}
    limit = 1.5 * statistics.mean(pre)
    previous = None
    completion_times = []
    for start in range(change, run['horizon'] - 8, 10):
        block = set(range(start, start + 10))
        if not block <= clock or statistics.mean(observed[t]['squared_loss'] for t in block) > limit:
            previous = None
            continue
        latest = max(observed[t]['label_time'] for t in block)
        if previous is not None:
            completion_times.append(max(previous, latest))
        previous = latest
    if completion_times:
        return {'delay': min(completion_times) - change, 'status': 'observed',
                'cap': run['horizon'] - change}
    return {'delay': None, 'status': 'censored', 'cap': run['horizon'] - change}


def interval(values):
    if len(values) != 24:
        raise ValueError('full fixed set of 24 paired report seeds required')
    average = statistics.mean(values)
    half = 2.06865761 * statistics.stdev(values) / math.sqrt(len(values))
    return {'mean': average, 'ci95': [average - half, average + half], 'n': len(values)}


def events_summary(values):
    count = len(values)
    observed = sum(v['delay'] is not None for v in values)
    rate = observed / count
    z = 1.959963984540054
    den = 1 + z*z/count
    center = (rate + z*z/(2*count))/den
    half = z*math.sqrt(rate*(1-rate)/count+z*z/(4*count**2))/den
    usable = [v for v in values if v['status'] != 'insufficient_baseline']
    capped = [v['delay'] if v['delay'] is not None else v['cap'] for v in usable]
    return {'observed': observed, 'total': count, 'not_observed': count-observed,
            'insufficient_baseline': count-len(usable),
            'wilson95': [max(0., center-half), min(1., center+half)],
            'mean_capped_delay': statistics.mean(capped) if capped else None}
