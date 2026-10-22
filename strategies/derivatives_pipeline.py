"""One isolated daily collection/report/study cycle; failures remain auditable."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from strategies.derivatives_data import ROOT, collect, encode, iso, utc_ms
from strategies.derivatives_study import run as study
from strategies.market_structure import run as structure

DEFAULT_RUNTIME = ROOT / 'runtime' / 'derivatives'


def run(runtime=DEFAULT_RUNTIME, *, collector=collect, clock=utc_ms):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    lock = runtime / 'RUNNING'
    # Do not race another automated/manual run. A crashed process leaves an explicit lock.
    with lock.open('x', encoding='utf-8') as file:
        file.write(str(os.getpid()))
    try:
        started = clock()
        name = iso(started).replace(':', '').replace('+', '_') + '-' + uuid4().hex[:8]
        reports = runtime / 'reports' / name
        snapshots = runtime / 'snapshots'
        snapshots.mkdir(exist_ok=True)
        status = {'started_at_ms': started, 'collection': 'failed', 'study': 'not_run'}
        try:
            snapshot = collector(snapshots)
            status['snapshot'] = str(snapshot.relative_to(runtime))
            structure(snapshot, reports / 'structure')
            status['collection'] = 'complete'
        except (ValueError, OSError, KeyError, TypeError) as error:
            status['collection_error'] = str(error)
        try:
            result = study(snapshots, reports / 'study', as_of_ms=clock())
            status.update(study=result['status'], samples=len(result['samples']), forecasts=len(result['forecasts']))
        except (ValueError, OSError, KeyError, TypeError) as error:
            status.update(study='failed', study_error=str(error))
        http_statuses = set()
        for path in snapshots.glob('*/manifest.json'):
            manifest = json.loads(path.read_bytes())
            if manifest.get('started_at_ms', 0) >= started:
                http_statuses.update(attempt['status'] for source in manifest['sources'].values()
                                     for attempt in source['attempts'])
        status['http_statuses'] = sorted(http_statuses, key=lambda value: -1 if value is None else value)
        status.update(completed_at_ms=clock(), report_directory=str(reports.relative_to(runtime)))
        previous = json.loads((runtime / 'latest.json').read_bytes()) if (runtime / 'latest.json').exists() else {}
        signature = lambda item: (item.get('collection'), item.get('study'), item.get('http_statuses'))
        status['meaningful_change'] = signature(status) != signature(previous)
        reports.mkdir(parents=True, exist_ok=True)
        (reports / 'status.json').write_bytes(encode(status))
        temporary = runtime / ('latest-' + uuid4().hex + '.tmp')
        temporary.write_bytes(encode(status))
        temporary.replace(runtime / 'latest.json')
        return status
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    result = run(args.runtime)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['collection'] == 'complete' and result['study'] != 'failed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
