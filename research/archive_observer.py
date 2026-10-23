"""Quant Lab 2.3: isolated, append-only public archive observation evidence."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import time
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener
from uuid import uuid4

from research import archive_normalizer as n
from research import archive_coverage as coverage

DEFAULT_ROOT=n.ROOT/'runtime/archive_observer_v1'
TERMS='https://raw.githubusercontent.com/binance/binance-public-data/master/TERMS_AND_CONDITIONS.md'
APPROVED_TERMS=n.ROOT/'data/archive_coverage/binance_um_btcusdt_v1/raw/current_terms.md'
CONTRACT_ROOT=n.ROOT/'data/archive_normalized/binance_um_btcusdt_coverage_v1'


def now():
    return datetime.now(n.UTC)


def day(value):
    return datetime.strptime(value,'%Y-%m-%d').date()


def names(start,end):
    dates=[start+timedelta(days=i) for i in range((end-start).days+1)]
    result=[]
    for date in dates:
        for name in (f'BTCUSDT-metrics-{date}.zip',f'BTCUSDT-5m-{date-timedelta(days=1)}.zip',f'BTCUSDT-5m-{date}.zip'):
            for key in (name,name+'.CHECKSUM'):
                if key not in result:
                    result.append(key)
    return result


def url_for(name):
    if name=='terms':
        return TERMS
    match=re.fullmatch(r'BTCUSDT-(metrics|5m)-(\d{4}-\d{2}-\d{2})\.zip(?:\.CHECKSUM)?',name)
    if not match:
        raise ValueError('Unexpected source object')
    day(match[2])
    folder='metrics/BTCUSDT/' if match[1]=='metrics' else 'klines/BTCUSDT/5m/'
    return 'https://data.binance.vision/data/futures/um/daily/'+folder+name


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def fetch(url,headers):
    try:
        with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'quant-lab-observer/1',**headers}),timeout=30) as r:
            return r.status,r.read(),dict(r.headers),r.url
    except HTTPError as error:
        return error.code,error.read(),dict(error.headers),error.url
    except (URLError,OSError,TimeoutError) as error:
        return None,str(error).encode('utf-8'),{},url


def publish(path,blob):
    """Single writer; publish complete bytes without overwriting an existing fact."""
    if path.exists():
        raise FileExistsError(path)
    temporary=path.with_name('.'+uuid4().hex+'.tmp')
    with temporary.open('xb') as output:
        output.write(blob)
        output.flush()
        os.fsync(output.fileno())
    if path.exists():
        raise FileExistsError(path)
    temporary.rename(path)


@contextmanager
def writer(root):
    root.mkdir(parents=True,exist_ok=True)
    lock=root/'collector.lock'
    descriptor=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(descriptor,str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def object_bytes(root,digest):
    if not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('Unsafe object digest')
    path=root/'objects'/(digest+'.bin')
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Unsafe object path')
    blob=path.read_bytes()
    if n.sha(blob)!=digest:
        raise ValueError('Object SHA-256 mismatch')
    return blob


def save_object(root,blob):
    digest=n.sha(blob)
    path=root/'objects'/(digest+'.bin')
    if path.exists():
        object_bytes(root,digest)
    else:
        publish(path,blob)
    return digest


def pair(name,current,root):
    if name not in current or name+'.CHECKSUM' not in current:
        return None,'missing_archive_or_checksum'
    records={}
    for key in (name,name+'.CHECKSUM'):
        event=current[key]
        records['raw/'+key]=({'status':200,'url':event['url'],'final_url':event['url'],
            'file':'raw/'+key,'collected_at_utc':event['received_at_utc'],'headers':event['headers']},
            object_bytes(root,event['body_sha256']))
    try:
        if '-metrics-' in name:
            rows,info=n.read_archive(name,records,n.METRICS)
            quality=n.grid_quality(name,rows)
        else:
            rows,info=coverage.read_reference(name,records)
            coverage.reference_values(rows)
            quality={'rows':len(rows)}
        return {'rows':rows,'source':info,'quality':quality},None
    except (ValueError,KeyError,ArithmeticError,UnicodeError,zipfile.BadZipFile) as error:
        return None,str(error)


def replay(root,as_of=None):
    root=Path(root)
    if (root/'config.json').is_symlink():
        raise ValueError('Unsafe configuration path')
    config_blob=(root/'config.json').read_bytes()
    config=json.loads(config_blob)
    if config['version']!=1 or config['allowed_terms_sha256']!=n.sha(APPROVED_TERMS.read_bytes()):
        raise ValueError('Unsupported observer configuration or terms policy')
    day(config['start_date'])
    cutoff=n.utc(as_of) if as_of else None
    current,versions,attempts,verified,revisions={},{},{},{},[]
    head=None
    visible_head=None
    runs=[]
    referenced=set()
    previous_time=n.utc(config['created_at_utc'])
    for index,path in enumerate(sorted((root/'runs').glob('*.json')),1):
        blob=path.read_bytes()
        digest=n.sha(blob)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.name!=f'{index:06d}-{digest}.json':
            raise ValueError('Run sequence or SHA-256 mismatch')
        run=json.loads(blob)
        if run['previous_sha256']!=head or run['config_sha256']!=n.sha(config_blob):
            raise ValueError('Run chain/configuration mismatch')
        started,finished=n.utc(run['started_at_utc']),n.utc(run['finished_at_utc'])
        if started<previous_time or finished<started:
            raise ValueError('Run clock order invalid')
        event_time=started
        visible=[]
        for event in run['events']:
            request,received=n.utc(event['requested_at_utc']),n.utc(event['received_at_utc'])
            if request<event_time or received<request or received>finished or event['url']!=url_for(event['name']):
                raise ValueError('Event time/source invalid')
            event_time=received
            body=object_bytes(root,event['body_sha256'])
            referenced.add(event['body_sha256'])
            if len(body)!=event['bytes']:
                raise ValueError('Object size mismatch')
            if cutoff and finished>cutoff:
                continue
            key=event['name']
            visible.append(event)
            attempts[key]=event
            if event['status']==200 and event['final_url']==event['url']:
                digest=event['body_sha256']
                old=current.get(key)
                if old and old['body_sha256']!=digest:
                    revisions.append({'name':key,'old_sha256':old['body_sha256'],'new_sha256':digest,
                                      'observed_at_utc':event['received_at_utc']})
                version=versions.setdefault(key,{}).setdefault(digest,{'first_observed_at_utc':event['received_at_utc'],
                    'body_downloads':0,'confirmations':0,'first_publisher_verified_at_utc':None})
                version['body_downloads']+=1
                version['confirmations']+=1
                version['last_confirmed_at_utc']=event['received_at_utc']
                current[key]=event
            elif event['status']==304:
                invalid=(bool(body) or key not in current or not event['request_headers']
                    or event['revalidated_sha256']!=current[key]['body_sha256'] or event['final_url']!=event['url'])
                if invalid and not event['conditional_invalid']:
                    raise ValueError('304 without matching prior bytes')
                if not invalid:
                    versions[key][current[key]['body_sha256']]['last_confirmed_at_utc']=event['received_at_utc']
                    versions[key][current[key]['body_sha256']]['confirmations']+=1
            if key!='terms':
                archive=key.removesuffix('.CHECKSUM')
                result,error=pair(archive,current,root)
                if result:
                    verified[archive]=result
                    digest=current[archive]['body_sha256']
                    version=versions[archive][digest]
                    if version['first_publisher_verified_at_utc'] is None:
                        version['first_publisher_verified_at_utc']=run['finished_at_utc']
                else:
                    verified.pop(archive,None)
        if not cutoff or finished<=cutoff:
            runs.append(run)
            visible_head=n.sha(blob)
        head=n.sha(blob)
        previous_time=finished
    all_objects=set()
    for path in (root/'objects').glob('*.bin'):
        object_bytes(root,path.stem)
        all_objects.add(path.stem)
    return {'config':config,'head':visible_head,'runs':runs,'current':current,'versions':versions,
            'attempts':attempts,'verified':verified,'revisions':revisions,
            'orphan_objects':sorted(all_objects-referenced),'all_runs_end':n.iso(previous_time)}


def collect(root,start_date,end_date,*,refresh=False,resume=False,recheck_hours=24,max_requests=25,fetcher=fetch,clock=now):
    root=Path(root).resolve()
    start,end=day(start_date),day(end_date)
    if start>end or (end-start).days>3650 or end>=clock().date() or recheck_hours<0 or not 2<=max_requests<=100:
        raise ValueError('Use completed UTC dates, valid interval and 2..100 request budget')
    with writer(root):
        if not (root/'config.json').exists():
            if any(p.name!='collector.lock' for p in root.iterdir()):
                raise ValueError('Observer requires an empty independent directory')
            (root/'objects').mkdir()
            (root/'runs').mkdir()
            publish(root/'config.json',n.json_bytes({'version':1,'start_date':start_date,'created_at_utc':n.iso(clock()),
                'allowed_terms_sha256':n.sha(APPROVED_TERMS.read_bytes()),'published_at_utc':None}))
        state=replay(root)
        if start_date!=state['config']['start_date']:
            raise ValueError('Start date is immutable; use the same observer or a new root')
        if state['runs'] and state['runs'][-1]['stop_reason'] and not resume:
            return {'stop_reason':'manual_review_required','previous_reason':state['runs'][-1]['stop_reason'],'network_requests':0}
        events=[]
        run_start=n.iso(clock())
        if n.utc(run_start)<n.utc(state['all_runs_end']):
            raise ValueError('Observer clock moved backwards')
        stop=None
        keys=names(start,end)
        recent=set(names(max(start,end-timedelta(days=1)),end))
        candidates=[key for key in keys if key not in state['attempts']]
        candidates += sorted((key for key in keys if key in state['attempts']
            and (refresh or key in recent or state['attempts'][key]['status']==404)
            and (refresh or clock()-n.utc(state['attempts'][key]['received_at_utc'])>=timedelta(
                hours=min(1,recheck_hours) if state['attempts'][key]['status']==404 else recheck_hours))),
            key=lambda key:state['attempts'][key]['received_at_utc'])
        for name in ['terms',*candidates]:
            if len(events)>=max_requests:
                break
            old=state['current'].get(name)
            attempt=state['attempts'].get(name)
            headers={}
            if old:
                validator=next((v for k,v in old['headers'].items() if k.lower()=='etag'),None)
                if validator:
                    headers['If-None-Match']=validator
            requested=n.iso(clock())
            try:
                status,body,response_headers,final=fetcher(url_for(name),headers)
            except Exception as error:
                status,body,response_headers,final=None,str(error).encode('utf-8'),{},url_for(name)
            received=n.iso(clock())
            if n.utc(received)<n.utc(requested) or n.utc(requested)<n.utc(events[-1]['received_at_utc'] if events else run_start):
                save_object(root,body)
                raise ValueError('Clock anomaly; response quarantined as an uncommitted object')
            event={'name':name,'url':url_for(name),'final_url':final,'status':status,'headers':response_headers,
                'requested_at_utc':requested,'received_at_utc':received,'body_sha256':save_object(root,body),
                'bytes':len(body),'published_at_utc':None,'request_headers':headers,'conditional_invalid':False,
                'revalidated_sha256':old['body_sha256'] if old else None}
            events.append(event)
            if status==200 and final==event['url']:
                state['current'][name]=event
            elif status==304:
                if not old or body or not headers or final!=event['url']:
                    stop='invalid_conditional_response'
                    event['conditional_invalid']=True
            elif status!=404:
                stop='access_or_network_failure'
            if final!=event['url']:
                stop='unexpected_redirect'
            if name=='terms':
                content=state['current'].get('terms')
                if status not in (200,304) or not content or content['body_sha256']!=state['config']['allowed_terms_sha256']:
                    stop=stop or 'terms_not_approved'
            if name.endswith('.CHECKSUM') and status in (200,304) and not stop:
                result,error=pair(name.removesuffix('.CHECKSUM'),state['current'],root)
                if not result and error!='missing_archive_or_checksum':
                    stop='publisher_integrity_failure'
            if stop:
                break
        run={'previous_sha256':state['head'],'config_sha256':n.sha((root/'config.json').read_bytes()),
            'started_at_utc':run_start,'finished_at_utc':n.iso(clock()),'requested_end_date':end_date,
            'stop_reason':stop,'events':events}
        blob=n.json_bytes(run)
        publish(root/'runs'/f"{len(state['runs'])+1:06d}-{n.sha(blob)}.json",blob)
        replay(root)
        return {'network_requests':len(events),'stop_reason':stop,'run_sha256':n.sha(blob)}


def contracts():
    blob=(CONTRACT_ROOT/'contracts.json').read_bytes()
    manifest=json.loads((CONTRACT_ROOT/'manifest.json').read_bytes())
    if n.sha(blob)!=manifest['files']['contracts.json']:
        raise ValueError('Trusted semantic registry integrity failure')
    return json.loads(blob)


def snapshot(root,end_date,as_of=None):
    state=replay(root,as_of)
    end=day(end_date)
    start=day(state['config']['start_date'])
    dates=[str(start+timedelta(days=i)) for i in range((end-start).days+1)]
    registry={x['date']:x for x in contracts()}
    gaps,files,accepted,isolation=[],[],[],[]
    for date in dates:
        name=f'BTCUSDT-metrics-{date}.zip'
        result=state['verified'].get(name)
        if not result:
            gaps.append({'date':date,'reason':'no_complete_publisher_verified_archive'})
            continue
        event=state['current'][name]
        version=state['versions'][name][event['body_sha256']]
        files.append({'date':date,'sha256':event['body_sha256'],**version,'rows':len(result['rows']),
                      'quality':result['quality'],'published_at_utc':None})
        contract=registry.get(date)
        matched=bool(contract and all(source['file'] in state['verified']
            and state['current'][source['file']]['body_sha256']==source['zip_sha256'] for source in contract['sources']))
        unknown=[k for k in n.METRICS[2:] if k!=n.TAKER or not matched]
        isolation.append({'date':date,'fields':unknown,'rows':len(result['rows']),
            'reason':'unverified metric timing, new file/version or missing exact support fingerprints'})
        if not matched:
            continue
        support_time=max(n.utc(state['versions'][s['file']][s['zip_sha256']]['first_publisher_verified_at_utc']) for s in contract['sources'])
        for line,row in enumerate(result['rows'],2):
            window_start=n.label_time(row['create_time'])+timedelta(minutes=contract['offset_minutes'])
            window_end=window_start+timedelta(minutes=5)
            accepted.append({'symbol':'BTCUSDT','field':n.TAKER,'value':row[n.TAKER],
                'source_label':row['create_time'],'window_start_utc':n.iso(window_start),'event_time_utc':n.iso(window_end),
                'published_at_utc':None,'collected_at_utc':version['first_observed_at_utc'],
                'last_confirmed_at_utc':version['last_confirmed_at_utc'],
                'first_observed_at_utc':version['first_observed_at_utc'],
                'research_available_at_utc':n.iso(max(window_end,support_time,n.utc(contract['verified_at_utc']))),
                'source_version_sha256':event['body_sha256'],'semantic_contract_id':contract['id'],'source_line':line})
    accepted.sort(key=lambda r:r['window_start_utc'])
    failures={key:pair(key,state['current'],Path(root))[1] for key in state['current'] if key.endswith('.zip') and key not in state['verified']}
    return {'schema_version':1,'observer_started_at_utc':state['config']['created_at_utc'],
        'as_of_utc':as_of,'ledger_head_sha256':state['head'],'runs_visible':len(state['runs']),
        'successful_metric_archives':len(files),'metric_rows':sum(f['rows'] for f in files),
        'accepted_taker_windows':len(accepted),'coverage_start':dates[0] if dates else None,'coverage_end':end_date,
        'gaps':gaps,'archives':files,'revisions':state['revisions'],'isolated':isolation,
        'unavailable_objects':[k for k,v in state['attempts'].items() if v['status']==404],
        'source_versions':state['versions'],'last_attempt_status':{k:v['status'] for k,v in state['attempts'].items()},
        'local_integrity':'verified','orphan_objects':state['orphan_objects'],
        'publisher_verification_failures':failures,
        'official_publication_evidence':None,'independent_oi_long_short_evidence':'not available; remain isolated',
        'first_observed_evidence_scope':'earliest committed HTTP-200 receipt for these exact bytes in this observer',
        'prospective_prediction_samples':0,'statistics_module_enabled':False,'accepted':accepted}


def export_snapshot(root,end_date,output,as_of=None):
    if as_of is None:
        state=replay(root)
        as_of=state['runs'][-1]['finished_at_utc'] if state['runs'] else state['config']['created_at_utc']
    result=snapshot(root,end_date,as_of)
    output=Path(output)
    if output.exists() or output.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Use new snapshot directory outside observer')
    output.mkdir(parents=True)
    rows=result.pop('accepted')
    publish(output/'summary.json',n.json_bytes(result))
    publish(output/'accepted.jsonl',b''.join((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode('utf-8') for r in rows))
    publish(output/'manifest.json',n.json_bytes({'ledger_head_sha256':result['ledger_head_sha256'],
        'observer_code_sha256':n.sha(Path(__file__).read_bytes().replace(b'\r\n',b'\n')),
        'semantic_registry_sha256':n.sha((CONTRACT_ROOT/'contracts.json').read_bytes()),
        'files':{name:n.sha((output/name).read_bytes()) for name in ('summary.json','accepted.jsonl')}}))
    return result


def verify_snapshot(root,output):
    output=Path(output)
    summary=json.loads((output/'summary.json').read_bytes())
    result=snapshot(root,summary['coverage_end'],summary['as_of_utc'])
    rows=result.pop('accepted')
    expected={'summary.json':n.json_bytes(result),
        'accepted.jsonl':b''.join((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode('utf-8') for r in rows)}
    expected['manifest.json']=n.json_bytes({'ledger_head_sha256':result['ledger_head_sha256'],
        'observer_code_sha256':n.sha(Path(__file__).read_bytes().replace(b'\r\n',b'\n')),
        'semantic_registry_sha256':n.sha((CONTRACT_ROOT/'contracts.json').read_bytes()),
        'files':{name:n.sha(blob) for name,blob in expected.items()}})
    if {p.name for p in output.iterdir()}!=set(expected) or any((output/k).read_bytes()!=v for k,v in expected.items()):
        raise ValueError('Snapshot replay mismatch')
    return {'verified_snapshot_files':len(expected),'ledger_head_sha256':summary['ledger_head_sha256']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('collect','watch','verify','snapshot'))
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--start')
    parser.add_argument('--end',default=str(now().date()-timedelta(days=1)))
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--resume-after-review',action='store_true')
    parser.add_argument('--interval-seconds',type=int,default=3600)
    parser.add_argument('--max-requests',type=int,default=25)
    parser.add_argument('--recheck-hours',type=float,default=24)
    parser.add_argument('--as-of')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    config_path=args.root/'config.json'
    start=args.start or (json.loads(config_path.read_bytes())['start_date'] if config_path.exists()
                        else str(now().date()-timedelta(days=3)))
    if args.command in ('collect','watch'):
        if args.command=='watch' and args.interval_seconds<60:
            parser.error('Watch interval must be at least 60 seconds')
        while True:
            end=args.end if args.command=='collect' else str(now().date()-timedelta(days=1))
            result=collect(args.root,start,end,refresh=args.refresh,resume=args.resume_after_review,
                max_requests=args.max_requests,recheck_hours=args.recheck_hours)
            print(json.dumps(result),flush=True)
            if result['stop_reason']:
                raise SystemExit(2)
            if args.command=='collect':
                break
            time.sleep(args.interval_seconds)
    elif args.command=='verify':
        if args.output:
            print(json.dumps(verify_snapshot(args.root,args.output)))
        else:
            state=replay(args.root)
            print(json.dumps({'verified_runs':len(state['runs']),'head_sha256':state['head'],'orphan_objects':state['orphan_objects']}))
    else:
        result=export_snapshot(args.root,args.end,args.output,args.as_of) if args.output else snapshot(args.root,args.end,args.as_of)
        print(json.dumps({k:v for k,v in result.items() if k not in ('accepted','source_versions')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
