"""Run a locally preregistered synthetic-only comparison of five policies."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent
if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
import engine_five34 as engine
from paths33 import make_case
from pressure_metrics32 import by_time, events_summary, recovery, score
from synthetic_paths32 import common_times

SOURCE_NAMES = ('protocol34.json', 'candidates34.json', 'study34.py', 'engine_five34.py', 'engine_cross33.py',
                'paths33.py', 'synthetic_paths32.py', 'pressure_metrics32.py', 'drift_robust32.py',
                'prequential_synthetic31.py', 'prequential_synthetic32.py', 'ridge_synthetic.py', 'round34-engine-development-parity.json',
                'test_study34.py', 'test_engine_five34.py', 'run_synthetic.py', 'freeze34.py',
                'round32-alarm-dev-results.json', 'round33-evidence-manifest.json',
                'round31-calibration-preconfig.json', 'round31-pressure-preconfig.json',
                'pressure_protocol32.json', 'round32-cluster-lock.json', 'protocol33.json')
ORIGINAL = 'original__original'
ROBUST = 'robust__original'


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open('x', encoding='utf8') as stream:
        json.dump(value, stream, separators=(',', ':'), sort_keys=True, allow_nan=False)
        stream.write('\n')


def source_hashes():
    return {name: sha(ROOT / name) for name in SOURCE_NAMES}


def historical_seeds():
    def prior(name):
        return read(ROOT / name)
    r32 = prior('pressure_protocol32.json')
    return set.union(set(prior('round31-calibration-preconfig.json')['config']['report_seeds']),
                     set(prior('round31-pressure-preconfig.json')['report_seeds']),
                     set(range(r32['report_seed_start'], r32['report_seed_stop_exclusive'])),
                     set(prior('round32-cluster-lock.json')['seeds']),
                     set(prior('protocol33.json')['report_seeds']))


def verify_lock(protocol, lock):
    if lock['source_sha256'] != source_hashes() or lock['engine_config'] != json.loads(json.dumps(engine.CONFIG)):
        raise ValueError('frozen code, parameters or protocol changed')
    if lock['protocol'] != protocol:
        raise ValueError('frozen comparison changed')
    if lock['alarm_thresholds'] != read(ROOT / 'round32-alarm-dev-results.json')['thresholds']:
        raise ValueError('frozen alarm thresholds changed')
    if tuple(protocol['candidates']) != engine.STRATEGIES:
        raise ValueError('candidate inventory mismatch')
    seeds = protocol['report_seeds']
    forbidden = historical_seeds() | set(protocol['development_seeds'])
    forbidden |= set(read(ROOT / 'round32-alarm-dev-results.json')['development_seeds'])
    if len(seeds) != 24 or len(set(seeds)) != 24 or set(seeds) & forbidden:
        raise ValueError('report seed duplicate or overlap')
    if len(seeds) * len(set(protocol['scenarios'])) != protocol['max_report_sequences']:
        raise ValueError('invalid workload')


def reserve_seeds(directory, seeds, lock_sha):
    directory.mkdir(exist_ok=True)
    for seed in seeds:
        write(directory / f'{seed}.json', {'lock_sha256': lock_sha, 'seed': seed})


def timed(at, onset, horizon):
    return {'delay': None if at is None else at - onset, 'cap': horizon - onset,
            'status': 'censored' if at is None else 'observed'}


def summarize(case, seed, run, clock, protocol):
    onset, horizon = protocol['onset'], protocol['horizon']
    if run['horizon'] != horizon or set(run['metrics']) != set(engine.STRATEGIES):
        raise ValueError('execution scope mismatch')
    reference = by_time(run, engine.STRATEGIES[0])
    population = set(reference)
    if not clock or not clock <= population or any(set(by_time(run, s)) != population for s in engine.STRATEGIES):
        raise ValueError('cannot match scoring clocks')
    models = {}
    for strategy in engine.STRATEGIES:
        events = [e for e in run['events'] if e.get('strategy') == strategy]
        activations = [e for e in events if e['event_type'] == 'activation']
        if not activations or activations[0]['version'] != 0:
            raise ValueError('missing initial model')
        updates = [a for a in activations if a['version'] > 0]
        alarms = [e for e in events if e['event_type'] == 'candidate_update' and e['raw_alarm']]
        observed = by_time(run, strategy)
        if any((f['sample_id'], f['label_time'], f['target']) !=
               (reference[t]['sample_id'], reference[t]['label_time'], reference[t]['target'])
               for t, f in observed.items()):
            raise ValueError('paired feedback identity mismatch')
        metrics = score(run, strategy, clock, protocol['cost_per_activation'])
        if not math.isclose(metrics['updates'] * protocol['cost_per_activation'],
                            run['metrics'][strategy]['penalty'], rel_tol=0., abs_tol=1e-12):
            raise ValueError('engine and study activation cost mismatch')
        metrics.update(initial_kind=activations[0]['kind'], alarms=len(alarms),
                       qualified=sum(a['qualified'] for a in alarms),
                       denials=dict(Counter(reason for a in alarms for reason in a['blocked'])))
        if case in protocol['reversal'] or case == 'gradual':
            hit = next((a['decision_time'] for a in alarms if a['decision_time'] >= onset
                        and a['axes']['relation'] is True), None)
            changed = next((a['model_activation_time'] for a in updates if a['retrain_time'] >= onset), None)
            metrics.update(detection=timed(hit, onset, horizon) if '__' in strategy else None,
                           activation=timed(changed, onset, horizon),
                           recovery=recovery(run, strategy, clock, onset))
        models[strategy] = metrics
    return {'case': case, 'seed': seed, 'clock': sorted(clock), 'models': models}


def estimate(values, critical):
    if len(values) != 24:
        raise ValueError('complete set of 24 seed-level pairs required')
    mean = statistics.mean(values)
    half = critical * statistics.stdev(values) / math.sqrt(len(values))
    return {'mean': mean, 'interval': [mean-half, mean+half], 'n': len(values)}


def classify(effect, margin):
    low, high = effect['interval']
    if high < -margin:
        return 'improve'
    if low > margin:
        return 'degrade'
    if low >= -margin and high <= margin:
        return 'within_margin'
    return 'inconclusive'


def result_summary(protocol, records):
    expected = {(case, seed) for case in protocol['scenarios'] for seed in protocol['report_seeds']}
    pairs = {(r['case'], r['seed']) for r in records}
    if pairs != expected or len(records) != len(expected):
        raise ValueError('missing or duplicate run evidence')
    by_case = {case: [r for r in records if r['case'] == case] for case in protocol['scenarios']}
    family = [(c, ROBUST, ORIGINAL) for c in protocol['anomalies'] + protocol['reversal']]
    family += [(c, ORIGINAL, b) for c in protocol['original_comparison_cases'] for b in protocol['baselines']]
    if len(family) != 17 or len(set(family)) != 17:
        raise ValueError('invalid primary family')
    comparisons = []
    for case, candidate, reference in family:
        delta = estimate([r['models'][candidate]['objective']-r['models'][reference]['objective']
                          for r in by_case[case]], 4.)
        pure = estimate([r['models'][candidate]['mse']-r['models'][reference]['mse']
                         for r in by_case[case]], 2.06865761)
        comparisons.append(dict(case=case, candidate=candidate, reference=reference, estimate=delta,
                                exploratory_pure_mse=pure, classification=classify(delta, protocol['practical_margin'])))
    aggregate = {}
    fields = ('mse', 'mae', 'rmse', 'objective', 'updates', 'average_cost', 'candidate_fit_attempts',
              'observed_count', 'common_count', 'alarms', 'qualified')
    for case, group in by_case.items():
        cells = {}
        for strategy in engine.STRATEGIES:
            values = [r['models'][strategy] for r in group]
            out = {f: estimate([r[f] for r in values], 2.06865761) for f in fields}
            for event in ('detection', 'activation', 'recovery'):
                if values[0].get(event) is not None:
                    out[event] = events_summary([v[event] for v in values])
            cells[strategy] = out
        aggregate[case] = cells
    decision = {s:'retain_as_baseline' for s in protocol['baselines']}
    robust = [c for c in comparisons if c['candidate'] == ROBUST]
    original = [c for c in comparisons if c['candidate'] == ORIGINAL]
    margin = protocol['practical_margin']
    improved = sum(c['classification'] == 'improve' for c in robust if c['case'] in protocol['anomalies'])
    harm_guard = all(c['estimate']['interval'][1] <= margin for c in robust if c['case'] in protocol['reversal'])
    decision[ROBUST] = ('retain_as_research_candidate' if improved >= 2 and harm_guard else
                        'diagnostic_only' if improved >= 2 else
                        'deprioritize' if improved == 0 and any(c['classification']=='degrade'
                            for c in robust if c['case'] in protocol['reversal']) else 'inconclusive')
    reversal_wins = all(c['classification']=='improve' for c in original if c['case'] in protocol['reversal'])
    safety = all(c['estimate']['interval'][1] <= margin for c in original
                 if c['case'] in ('stable_signal','cluster') and c['reference']=='fixed_ridge')
    decision[ORIGINAL] = ('retain_as_research_candidate' if reversal_wins and safety else
                          'deprioritize' if any(c['classification']=='degrade' for c in original
                               if c['case'] in ['cluster', *protocol['reversal']]) else 'inconclusive')
    return comparisons, aggregate, decision


def main():
    lock_path = ROOT/'round34-lock.json'
    lock_sha = sha(lock_path)
    receipt = read(ROOT/'round34-independent-freeze.json')
    if receipt['lock_sha256'] != lock_sha or receipt['status'] != 'verified_before_report':
        raise ValueError('independent freeze receipt does not match')
    lock = read(lock_path)
    protocol = read(ROOT/'protocol34.json')
    verify_lock(protocol, lock)
    folder = ROOT/'round34-runs'
    folder.mkdir()
    reserve_seeds(ROOT/'round34-seed-reservations', protocol['report_seeds'], lock_sha)
    start = time.monotonic()
    records = []
    for seed in protocol['report_seeds']:
        if time.monotonic() - start > protocol['wall_timeout_seconds']:
            raise TimeoutError('report time limit exceeded')
        paths = {case: make_case(case, seed) for case in protocol['scenarios']}
        common = common_times(paths['full_short'][0], paths['full_long'][0])
        snapshots = []
        for case, (rows, args) in paths.items():
            run = engine.run_online(rows, alarm_config=lock['alarm_thresholds'], **args)
            if case in protocol['reversal']:
                row = next(e for e in run['events'] if e['event_type']=='activation'
                           and e['strategy']=='fixed_ridge' and e['version']==0)
                if row['kind']!='ridge':
                    raise ValueError('unexpected initial fallback')
                snapshots.append((row['value'], [i.rsplit(':',1)[1] for i in run['initial_label_ids']]))
            clock = common if case in protocol['reversal'] else set(by_time(run, 'fixed_ridge'))
            path = folder/f'{case}-{seed}.json'
            write(path, run)
            records.append(dict(summarize(case,seed,run,clock,protocol),
                                raw=path.relative_to(ROOT).as_posix(),sha256=sha(path)))
        if len(snapshots)!=2 or snapshots[0]!=snapshots[1]:
            raise ValueError('reversal initial snapshot mismatch')
    verify_lock(protocol, lock)
    if sha(lock_path) != lock_sha:
        raise ValueError('lock changed during execution')
    if any(sha(ROOT / r['raw']) != r['sha256'] for r in records):
        raise ValueError('raw evidence changed before aggregation')
    primary, aggregate, decision = result_summary(protocol, records)
    write(ROOT/'round34-results.json',dict(records=records,primary=primary,aggregate=aggregate,
          decision=decision,lock_sha256=lock_sha,seconds=time.monotonic()-start))
    print(f'{len(records)} runs completed')


if __name__ == '__main__':
    if len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit('Use Python -I -S -B study34.py')
    main()
