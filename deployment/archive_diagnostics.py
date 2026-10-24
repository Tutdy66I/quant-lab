"""Offline operational projections. Never changes collection or admission policy."""
import sys
import ssl
from urllib.request import getproxies
from pathlib import Path
from urllib.parse import urlsplit

from research import archive_normalizer as n


def execution_context():
    """Local process facts, without proxy addresses, credentials or network I/O."""
    result = {'python_version': sys.version.split()[0], 'openssl_version': ssl.OPENSSL_VERSION,
              'proxy_schemes': sorted(getproxies()), 'scope': 'Current service process only'}
    try:
        result['certificate_store_counts'] = ssl.create_default_context().cert_store_stats()
    except (OSError, ssl.SSLError) as error:
        result['certificate_store_error_type'] = type(error).__name__
    return result


def code_fingerprints():
    folder = Path(__file__).parent
    return {name: n.sha((folder / name).read_bytes().replace(b'\r\n', b'\n'))
            for name in ('archive_service.py', 'archive_acceptance.py',
                         'archive_diagnostics.py', 'archive_task_history.py')}


def successful_intervals(runs, start, instant):
    """Include the open interval; failed runs never move the success clock."""
    if instant < start:
        raise ValueError('Report clock precedes collection interval')
    cursor = start
    result = []
    for run in runs:
        end = n.utc(run['finished_at_utc'])
        if run['stop_reason'] or end < start or end > instant:
            continue
        if end < cursor:
            raise ValueError('Successful collection clock moved backwards')
        result.append({'start_utc': n.iso(cursor), 'end_utc': n.iso(end),
                       'seconds': (end - cursor).total_seconds()})
        cursor = end
    result.append({'start_utc': n.iso(cursor), 'end_utc': n.iso(instant),
                   'seconds': (instant - cursor).total_seconds(), 'open_interval': True})
    return result


def failure(root, run):
    """Classify saved evidence only; no request, certificate change or resume."""
    if not run or not run['stop_reason']:
        return None
    event = run['events'][-1] if run['events'] else None
    result = {'category': 'unknown', 'stop_reason': run['stop_reason'],
              'status': event['status'] if event else None,
              'host': urlsplit(event['url']).hostname if event else None,
              'evidence_sha256': event['body_sha256'] if event else None,
              'diagnostic_python_version': sys.version.split()[0],
              'interpreter_scope': 'Reporter interpreter; historical collector interpreter is unknown.'}
    if not event:
        return result
    status = event['status']
    if status is None:
        # Do not expose arbitrary exception bodies (which may contain private paths).
        text = (root / 'objects' / (event['body_sha256'] + '.bin')).read_bytes().decode('utf-8', errors='replace').lower()
        if 'certificate_verify_failed' in text or 'certificate verify failed' in text:
            result['category'] = 'tls_certificate_validation'
        elif 'getaddrinfo' in text or 'name resolution' in text:
            result['category'] = 'dns_resolution'
        elif 'timed out' in text or 'timeout' in text:
            result['category'] = 'network_timeout'
        else:
            result['category'] = 'network_other'
    elif event['final_url'] != event['url']:
        result['category'] = 'redirect_stopped'
    elif status in (401, 403, 429, 451):
        result['category'] = 'access_restriction'
    elif status in (500, 502, 503, 504):
        result['category'] = 'server_transient'
    else:
        result['category'] = 'source_or_integrity_stop'
    return result


def criteria(report):
    """Conservative review gates, never an automatic long-run acceptance."""
    checks = {
        'observation_period': 'pass' if report['stage'] == 'due_for_manual_review' else 'not_due',
        'integrity': 'pass' if report['integrity'] == 'verified' else 'fail',
        'successful_collection_gaps': 'fail' if report['collection_gaps_over_3_hours'] else 'pass',
        'latest_collection': 'fail' if (report['latest_collection_stop_reason']
                                        or not report['successful_collection_runs']) else 'pass',
        'overdue_archive_gaps': 'fail' if report['overdue_gaps'] else 'pass',
        'scheduler_denominator': 'not_assessable',
        'real_logon_reboot_recovery': 'unverified',
        'source_revision_handling_in_real_operation': 'unverified',
        'statistics_disabled': 'pass' if report['statistics_module_enabled'] is False else 'fail',
    }
    if report['stage_days'] == 28:
        checks['capacity_trend'] = 'not_assessable'
    return {'version': 1, 'checks': checks,
            'decision': 'blocked' if 'fail' in checks.values() else 'evidence_incomplete',
            'policy': 'Any failure blocks acceptance. Unknown evidence stays unresolved; manual review is required.'}
