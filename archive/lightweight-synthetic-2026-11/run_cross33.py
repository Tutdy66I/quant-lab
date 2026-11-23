"""Execute the frozen crossed policies on identical synthetic streams."""
import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
import engine_cross33 as engine
import prequential_synthetic31 as prior
from paths33 import FACTORIAL, factorial_clock, make_case
from pressure_metrics32 import by_time, events_summary, interval, recovery, score
from ridge_synthetic import predict_ridge

CHANGE_CASES = {*FACTORIAL, 'transient', 'gradual', 'backlog', 'missing', 'training_failure'}
SOURCE_NAMES = ('protocol33.json', 'run_cross33.py', 'paths33.py', 'engine_cross33.py',
                'synthetic_paths32.py', 'prequential_synthetic32.py', 'prequential_synthetic31.py',
                'ridge_synthetic.py', 'pressure_metrics32.py', 'drift_robust32.py',
                'round32-alarm-dev-config.json', 'round32-alarm-dev-results.json')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_bytes())


def write(path, obj):
    with path.open('x', encoding='utf8') as file:
        json.dump(obj, file, sort_keys=True, allow_nan=False, separators=(',', ':'))
        file.write('\n')


def frozen_sources():
    return {name: sha(ROOT / name) for name in SOURCE_NAMES}


def timed_event(at, onset, horizon):
    return {'delay': at - onset if at is not None else None, 'cap': horizon - onset,
            'status': 'observed' if at is not None else 'censored'}


def shadow_count(denials, clock, predictions, rows, models, strategy):
    """Offline local contrast against a frozen incumbent, never a policy input."""
    gains = []
    rejected = 0
    for event in denials:
        tick = event['decision_time']
        next_times = sorted(clock & set(range(tick + 1, tick + 11)))
        if len(next_times) < 5:
            rejected += 1
            continue
        prefix = [r for r in rows[:tick] if r['label_available_time'] is not None
                  and r['label_available_time'] <= tick]
        try:
            shadow, _ = prior._fit(prefix, tick)
        except (ArithmeticError, ValueError):
            rejected += 1
            continue
        current = models[predictions[strategy, tick]['active_model_id']]
        difference = statistics.mean(
            (predict_ridge(shadow, rows[t]['features']) - rows[t]['target'])**2 -
            (engine._score(current, rows[t]['features']) - rows[t]['target'])**2
            for t in next_times)
        gains.append({'tick': tick, 'score_n': len(next_times), 'mse_difference': difference})
    return {'evaluated': len(gains), 'unavailable': rejected,
            'beneficial': sum(g['mse_difference'] < 0 for g in gains), 'windows': gains}


def summarize(case, seed, run, rows, clock, protocol):
    onset, horizon = protocol['onset'], run['horizon']
    events = run['events']
    predictions = {(p['strategy'], p['decision_time']): p for p in run['predictions']}
    activations = {e['model_id']: e for e in events if e['event_type'] == 'activation'}
    observations = [set(by_time(run, s)) for s in engine.STRATEGIES]
    if any(times != observations[0] for times in observations) or not clock <= observations[0]:
        raise ValueError('scoring population mismatch')
    metrics = {}
    for strategy in engine.STRATEGIES:
        m = score(run, strategy, clock, protocol['cost_per_activation'])
        updates = [e for e in activations.values() if e['strategy'] == strategy and e['version'] > 0]
        initial = next(e for e in activations.values() if e['strategy'] == strategy and e['version'] == 0)
        m['initial_kind'] = initial['kind']
        m['initial_fit_ids'] = [int(x.rsplit(':', 1)[1]) for x in run['initial_label_ids']]
        if case in FACTORIAL and strategy != 'mean' and initial['kind'] != 'ridge':
            raise ValueError('factorial initial Ridge identity changed')
        if case in CHANGE_CASES:
            m['recovery'] = recovery(run, strategy, clock, onset)
            m['response'] = timed_event(next((a['model_activation_time'] for a in updates
                                               if a['retrain_time'] >= onset), None), onset, horizon)
        candidates = [e for e in events if e['event_type'] == 'candidate_update' and e['strategy'] == strategy]
        if candidates:
            alarms = [e for e in candidates if e['raw_alarm']]
            relevant = [e for e in alarms if e['decision_time'] >= onset and e['axes']['relation'] is True
                        and (case != 'transient' or e['decision_time'] < onset + 30)]
            denials = [e for e in alarms if e['blocked'] and
                       not any('insufficient_labels' == reason for reason in e['blocked'])]
            m.update(alarms=len(alarms), qualified=sum(e['qualified'] for e in alarms),
                     first_alarm=min((e['decision_time'] for e in alarms), default=None),
                     first_train=min((e['retrain_time'] for e in updates), default=None),
                     first_activation=min((e['model_activation_time'] for e in updates), default=None),
                     denials=dict(Counter(reason for e in alarms for reason in e['blocked'])),
                     alarm_without_persistent_change=sum(case not in CHANGE_CASES or e['decision_time'] < onset
                                                         for e in alarms),
                     update_without_persistent_change=sum(case not in CHANGE_CASES or e['retrain_time'] < onset
                                                          for e in updates),
                     shadow=shadow_count(denials, clock, predictions, rows, activations, strategy))
            if case in CHANGE_CASES:
                m['detection'] = timed_event(relevant[0]['decision_time'] if relevant else None, onset, horizon)
                if case == 'transient':
                    m['detection']['cap'] = 30
        metrics[strategy] = m
    return {'case': case, 'seed': seed, 'clock': sorted(clock), 'models': metrics}


def complexity(strategy):
    if '__' not in strategy:
        return 1
    alarm, gate = strategy.split('__')
    return 2 + {'original': 0, 'robust': 1, 'confirmed': 2}[alarm] + (gate != 'original')


def aggregate(records, protocol):
    by_case = {case: [r for r in records if r['case'] == case] for case in protocol['scenarios']}
    summary = {}
    for case, runs in by_case.items():
        cells = {}
        for strategy in engine.STRATEGIES:
            policies = [r['models'][strategy] for r in runs]
            fields = ('mse', 'mae', 'rmse', 'objective', 'updates', 'average_cost', 'candidate_fit_attempts',
                      'observed_count', 'common_count', 'alarms', 'qualified', 'alarm_without_persistent_change',
                      'update_without_persistent_change')
            stats = {field: interval([p[field] for p in policies]) for field in fields if field in policies[0]}
            for field in ('detection', 'response', 'recovery'):
                if field in policies[0]:
                    stats[field] = events_summary([p[field] for p in policies])
            stats['versus'] = {ref: {measure: interval([r['models'][strategy][measure] - r['models'][ref][measure]
                                                       for r in runs]) for measure in ('mse', 'objective')}
                               for ref in protocol['primary_references'] if ref != strategy}
            if 'shadow' in policies[0]:
                stats['shadow'] = {key: sum(p['shadow'][key] for p in policies)
                                   for key in ('evaluated', 'beneficial', 'unavailable')}
            cells[strategy] = stats
        points = {s: (v['mse']['mean'], v['average_cost']['mean'], complexity(s)) +
                  ((v['recovery']['mean_capped_delay'],) if 'recovery' in v and
                   v['recovery']['mean_capped_delay'] is not None else ()) for s, v in cells.items()}
        pareto = {}
        for s, point in points.items():
            pareto[s] = [other for other, candidate in points.items()
                         if len(candidate) == len(point) and all(a <= b for a, b in zip(candidate, point))
                         and any(a < b for a, b in zip(candidate, point))]
        # Balanced marginal effects are seed-paired across all levels of the other factor.
        effects = {}
        for dimension, changes in (
                ('alarm', [('robust', 'original'), ('confirmed', 'original')]),
                ('gate', [('delay_aware', 'original'), ('conservative', 'original')])):
            effects[dimension] = {}
            for new, old in changes:
                pairs = ([(new+'__'+g, old+'__'+g) for g in ('original', 'delay_aware', 'conservative')]
                         if dimension == 'alarm' else
                         [(a+'__'+new, a+'__'+old) for a in ('original', 'robust', 'confirmed')])
                effects[dimension][new] = interval([statistics.mean(r['models'][a]['objective'] -
                    r['models'][b]['objective'] for a,b in pairs) for r in runs])
        summary[case] = {'strategies': cells, 'pareto_dominators': pareto,
                         'pareto_points': points, 'marginal_objective': effects,
                         'alarm_gate_interactions': {
                            a+'__'+g: interval([
                                r['models'][a+'__'+g]['objective'] - r['models'][a+'__original']['objective'] -
                                r['models']['original__'+g]['objective'] + r['models']['original__original']['objective']
                                for r in runs])
                            for a in ('robust', 'confirmed') for g in ('conservative', 'delay_aware')}}
    index = {(r['case'], r['seed']): r for r in records}
    factorial = {}
    for strategy in engine.STRATEGIES:
        values = []
        for seed in protocol['report_seeds']:
            a,b,c,d = [index[case,seed]['models'][strategy]['mse'] for case in FACTORIAL]
            values.append({'delay_at_27': b-a, 'delay_at_45': d-c, 'small_at_short': a-c,
                           'small_at_long': b-d, 'delay_marginal': ((b-a)+(d-c))/2,
                           'smaller_marginal': ((a-c)+(b-d))/2, 'interaction': (b-a)-(d-c)})
        factorial[strategy] = {key: interval([r[key] for r in values]) for key in values[0]}
    return {'cases': summary, 'factorial': factorial}


def main():
    protocol = read('protocol33.json')
    lock = read('round33-lock.json')
    if (lock['source_sha256'] != frozen_sources() or lock['engine_config'] != engine.CONFIG or
        lock['thresholds'] != read('round32-alarm-dev-results.json')['thresholds']):
        raise ValueError('frozen experiment drift')
    if set(protocol['development_seeds']) & set(protocol['report_seeds']):
        raise ValueError('development and report overlap')
    if len(protocol['scenarios']) != protocol['cases_per_seed'] or len(engine.STRATEGIES) != 12:
        raise ValueError('incomplete experiment design')
    start = time.monotonic()
    folder = ROOT / 'round33-runs'
    folder.mkdir()
    records = []
    for seed in protocol['report_seeds']:
        paths = {case: make_case(case, seed) for case in protocol['scenarios']}
        common = factorial_clock(paths)
        snapshots = {}
        for case, (rows, args) in paths.items():
            if time.monotonic() - start >= protocol['wall_timeout_seconds']:
                raise TimeoutError('workload deadline reached')
            run = engine.run_online(rows, alarm_config=lock['thresholds'], **args)
            clock = common if case in FACTORIAL else set(by_time(run, 'fixed_ridge'))
            if case in FACTORIAL:
                initial = next(e for e in run['events'] if e['event_type'] == 'activation'
                               and e['strategy'] == 'fixed_ridge' and e['version'] == 0)
                snapshot = (initial['value'], [int(x.rsplit(':',1)[1]) for x in run['initial_label_ids']])
                size = args['initial_limit']
                if size in snapshots and snapshot != snapshots[size]:
                    raise ValueError('initial Ridge snapshots differ within delay contrast')
                snapshots[size] = snapshot
            path = folder / f'{case}-{seed}.json'
            write(path, run)
            records.append(dict(summarize(case, seed, run, rows, clock, protocol),
                                raw=path.relative_to(ROOT).as_posix(), sha256=sha(path)))
    if lock['source_sha256'] != frozen_sources():
        raise ValueError('source changed during execution')
    write(ROOT/'round33-results.json', {'records': records, **aggregate(records, protocol),
                                   'lock_sha256': sha(ROOT/'round33-lock.json'),
                                   'seconds': time.monotonic()-start})
    print(f"{len(records)} runs completed")


if __name__ == '__main__':
    if len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit('Use Python -I -S -B run_cross33.py')
    main()
