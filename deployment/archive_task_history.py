"""Read-only parser for exported TaskScheduler Operational event XML.

Exports are partial evidence, never proof of a complete scheduling denominator.
"""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from research import archive_normalizer as n

NS = {'e': 'http://schemas.microsoft.com/win/2004/08/events/event'}
PROVIDER = 'Microsoft-Windows-TaskScheduler'
TASK = '\\quant-lab-archive-observer'
TRIGGERS = {107: 'time', 110: 'user', 119: 'logon'}


def read(path, start, end):
    if start > end:
        raise ValueError('Invalid scheduler event interval')
    with Path(path).open('rb') as stream:
        blob = stream.read(8 * 1024 * 1024 + 1)
    if len(blob) > 8 * 1024 * 1024:
        raise ValueError('Unsafe or oversized event export')
    text = blob.decode('utf-8-sig')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('Unsafe event export')
    try:
        document = ET.fromstring(text)
    except ET.ParseError as error:
        raise ValueError('Invalid scheduler event XML') from error
    if document.tag not in ('Events', '{' + NS['e'] + '}Events', '{' + NS['e'] + '}Event'):
        raise ValueError('Expected an Events export')
    nodes = [document] if document.tag == '{' + NS['e'] + '}Event' else list(document)
    events = {}
    for node in nodes:
        provider = node.find('e:System/e:Provider', NS)
        if provider is None or provider.get('Name') != PROVIDER:
            continue
        fields = node.findall('e:EventData/e:Data', NS)
        data = {item.get('Name'): item.text for item in fields}
        if len(data) != len(fields):
            raise ValueError('Duplicate scheduler event field')
        if data.get('TaskName') != TASK:
            continue
        event_id = int(node.findtext('e:System/e:EventID', namespaces=NS))
        record = int(node.findtext('e:System/e:EventRecordID', namespaces=NS))
        created = node.find('e:System/e:TimeCreated', NS)
        if created is None or not created.get('SystemTime'):
            raise ValueError('Missing scheduler event timestamp')
        stamp = n.utc(created.get('SystemTime'))
        if not start <= stamp <= end:
            continue
        item = {'record_id': record, 'event_id': event_id, 'at_utc': n.iso(stamp),
                'instance_id': data.get('InstanceId') or data.get('TaskInstanceId'),
                'trigger_kind': TRIGGERS.get(event_id, 'unknown'),
                'action_result_code': data.get('ResultCode') if event_id == 201 else None}
        if record in events and events[record] != item:
            raise ValueError('Conflicting scheduler event record')
        events[record] = item
    return {'source_sha256': n.sha(blob), 'events': sorted(events.values(), key=lambda e: (e['at_utc'], e['record_id'])),
            'coverage': 'partial_or_unknown', 'task_execution_success_rate': None,
            'schedule_punctuality_rate': None,
            'limitations': ['Export does not establish logging uptime, retention completeness or missed triggers.',
                            'Scheduler completion and action exit codes do not prove collection success.',
                            'Receipt trigger_kind is unchanged; clock proximity does not prove association.']}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    print(json.dumps(read(args.path, n.utc(args.start), n.utc(args.end)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
