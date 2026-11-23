"""Replay the frozen synthetic matrix and retain every decision record."""

import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
import prequential_synthetic32 as engine
from pressure_metrics32 import by_time, events_summary, interval, recovery, score
from synthetic_paths32 import CASES, common_times, make_case

SOURCES = ('pressure_protocol32.json', 'synthetic_paths32.py', 'pressure_metrics32.py',
           'run_pressure32.py', 'drift_robust32.py', 'prequential_synthetic32.py',
           'prequential_synthetic31.py', 'ridge_synthetic.py', 'round32-alarm-dev-config.json',
           'round32-alarm-dev-results.json')
FACTORIAL = ('reverse_full_short', 'reverse_full_long', 'reverse_small_short', 'reverse_small_long')
RELATION = {*FACTORIAL, 'reverse_native_long', 'transient', 'gradual',
            'backlog', 'missing', 'training_failure'}


def read(name):
    return json.loads((ROOT / name).read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_sources():
    return {name: sha(ROOT / name) for name in SOURCES}


def save(path, payload):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, sort_keys=True, allow_nan=False, separators=(',', ':'))
        stream.write('\n')


def alarms(run, case, change):
    # The original online alarm records are retained verbatim. Additional
    # detectors have independent records and no access to latent truth.
    streams = {'original': [(e['alarm_time'], e['mean_alarm'], e['variance_alarm'],
                             e['relationship_alarm'])
                            for e in run['events'] if e['event_type'] == 'alarm']}
    for kind in ('robust', 'confirmed'):
        key = kind + '_axes'
        streams[kind] = [(e['alarm_time'], e[key]['mean'], e[key]['variance'], e[key]['relation'])
                         for e in run['events'] if e['event_type'] == 'alarm']
        if not streams[kind]:
            raise ValueError('missing detector evidence: ' + kind)
    result = {}
    for kind, ticks in streams.items():
        relevant = 3 if case in RELATION else 1 if case == 'mean_shift' else None
        hit = next((tick[0] - change for tick in ticks
                    if relevant is not None and tick[0] >= change and tick[relevant]), None)
        result[kind] = {
            'feature_alarms': sum(bool(m or v) for _, m, v, _ in ticks),
            'relation_alarms': sum(r is True for _, _, _, r in ticks),
            'unavailable_relation_checks': sum(r is None for _, _, _, r in ticks),
            'false_or_prechange': sum(bool(m or v or r) for t, m, v, r in ticks
                                      if relevant is None or t < change),
            'detection': {'delay': hit, 'status': 'observed' if hit is not None else 'censored',
                          'cap': run['horizon'] - change} if relevant is not None else None,
        }
    return result


def summarize(run, case, seed, clock, protocol):
    change = protocol['change']
    if any(set(by_time(run, s)) != set(by_time(run, engine.STRATEGIES[0])) for s in engine.STRATEGIES):
        raise ValueError('strategies received unequal scores')
    comparisons = {}
    for strategy in engine.STRATEGIES:
        metrics = score(run, strategy, clock, protocol['update_cost_units'])
        events = [e for e in run['events'] if e['event_type'] == 'activation'
                  and e['strategy'] == strategy]
        first = next((e for e in events if e['version'] > 0 and e['retrain_time'] >= change
                      and e['kind'] == 'ridge'), None)
        metrics.update(initial_model_kind=events[0]['kind'],
                       initial_available_labels=run['initial_available_labels'],
                       initial_samples_used=len(events[0]['training'].get('train_ids', ())) +
                       len(events[0]['training'].get('tune_ids', ())) or
                       events[0]['training'].get('sample_count', 0),
                       first_postchange_fit=first['retrain_time'] if first else None,
                       first_postchange_activation=first['model_activation_time'] if first else None,
                       first_postchange_fit_samples=len(first['training']['train_ids']) +
                       len(first['training']['tune_ids']) if first else None,
                       first_postchange_fit_event={
                           'delay': first['retrain_time']-change if first else None,
                           'status': 'observed' if first else 'censored',
                           'cap': run['horizon']-change} if case in RELATION else None,
                       first_postchange_activation_event={
                           'delay': first['model_activation_time']-change if first else None,
                           'status': 'observed' if first else 'censored',
                           'cap': run['horizon']-change} if case in RELATION else None,
                       recovery=recovery(run, strategy, clock, change) if case in RELATION else None)
        comparisons[strategy] = metrics
    return {'case': case, 'seed': seed, 'clock': sorted(clock), 'models': comparisons,
            'alarms': alarms(run, case, change)}


def aggregate(records, protocol):
    groups = {case: [r for r in records if r['case'] == case] for case in protocol['cases']}
    out = {}
    fields = ('mae', 'mse', 'rmse', 'updates', 'average_cost', 'objective', 'candidate_fit_attempts',
              'observed_count', 'common_count', 'initial_available_labels', 'initial_samples_used')
    for case, group in groups.items():
        models = {}
        for strategy in engine.STRATEGIES:
            runs = [row['models'][strategy] for row in group]
            models[strategy] = {field: interval([r[field] for r in runs]) for field in fields}
            models[strategy]['paired_vs'] = {
                other: {field: interval([r['models'][strategy][field] - r['models'][other][field]
                                        for r in group]) for field in ('mse', 'objective')}
                for other in protocol['primary_comparators'] if other != strategy}
            if runs[0]['recovery'] is not None:
                for name in ('recovery', 'first_postchange_fit_event', 'first_postchange_activation_event'):
                    models[strategy][name] = events_summary([r[name] for r in runs])
        detectors = {}
        for kind in ('original', 'robust', 'confirmed'):
            runs = [row['alarms'][kind] for row in group]
            detectors[kind] = {f: interval([r[f] for r in runs])
                               for f in ('false_or_prechange', 'feature_alarms', 'relation_alarms',
                                         'unavailable_relation_checks')}
            if runs[0]['detection'] is not None:
                detectors[kind]['detection'] = events_summary([r['detection'] for r in runs])
        out[case] = {'models': models, 'detectors': detectors}
    by_case_seed = {(r['case'], r['seed']): r for r in records}
    effects = {}
    for strategy in engine.STRATEGIES:
        def breakdown(seed):
            a, b, c, d = [by_case_seed[case, seed]['models'][strategy]['mse'] for case in FACTORIAL]
            return {'delay_at_full': b-a, 'delay_at_small': d-c,
                    'small_at_short': c-a, 'small_at_long': d-b,
                    'joint': d-a, 'interaction': (d-c)-(b-a),
                    'symmetric_delay': ((b-a)+(d-c))/2,
                    'symmetric_initial': ((c-a)+(d-b))/2}
        seed_effects = [breakdown(r['seed']) for r in groups[FACTORIAL[0]]]
        effects[strategy] = {f: interval([row[f] for row in seed_effects]) for f in seed_effects[0]}
    return {'cases': out, 'factorial_mse': effects}


def main():
    protocol = read('pressure_protocol32.json')
    lock = read('round32-lock.json')
    if digest_sources() != lock['source_sha256'] or set(protocol['cases']) != set(CASES):
        raise ValueError('protocol or source differs from frozen inventory')
    seeds = range(protocol['report_seed_start'], protocol['report_seed_stop_exclusive'])
    if set(seeds) & (set(protocol['development_seeds']) | set(lock['alarm_calibration']['development_seeds'])):
        raise ValueError('development and report seeds overlap')
    if len(seeds)*len(CASES) != protocol['max_report_runs']:
        raise ValueError('report workload differs from protocol')
    start = time.monotonic()
    output = ROOT / 'round32-runs'
    output.mkdir(exist_ok=False)
    records = []
    for seed in seeds:
        paths = {case: make_case(case, seed) for case in protocol['cases']}
        # All attribution contrasts, including native long, share one clock.
        cohort = FACTORIAL + ('reverse_native_long',)
        clock = set.intersection(*(common_times(paths[cohort[0]][0], paths[case][0]) for case in cohort))
        for case, (rows, limit, failures) in paths.items():
            if time.monotonic()-start > protocol['timeout_seconds']:
                raise TimeoutError('start boundary exceeded; completed raw files retained')
            run = engine.run_online(rows, initial_limit=limit,
                                    alarm_config=lock['alarm_calibration']['thresholds'],
                                    failure_times=failures)
            engine.verify_run(run, run['audit_sha256'], rows=rows, initial_limit=limit,
                              alarm_config=lock['alarm_calibration']['thresholds'],
                              failure_times=failures)
            raw = output / f'{case}-{seed}.json'
            save(raw, run)
            scoring_clock = clock if case in cohort else set(by_time(run, engine.STRATEGIES[0]))
            records.append(dict(summarize(run, case, seed, scoring_clock, protocol),
                                raw=raw.relative_to(ROOT).as_posix(), sha256=sha(raw)))
    if digest_sources() != lock['source_sha256']:
        raise ValueError('source changed during report run')
    result = {'lock_sha256': sha(ROOT / 'round32-lock.json'), 'seconds': time.monotonic()-start,
              'records': records, **aggregate(records, protocol)}
    save(ROOT / 'round32-results.json', result)
    print(f"Completed {len(records)} sequences in {result['seconds']:.2f}s")


if __name__ == '__main__':
    if len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit('Use Python -I -S -B run_pressure32.py with no arguments')
    main()
