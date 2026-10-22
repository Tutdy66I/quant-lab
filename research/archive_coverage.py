"""Frozen-protocol taker coverage audit. No models, accounts or network access."""
import argparse
import csv
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import json
import io
import math
from pathlib import Path
import statistics
import zipfile

from research import archive_normalizer as n

DEFAULT_SOURCE = n.ROOT/'data/archive_coverage/binance_um_btcusdt_v1'


def read_reference(name,records):
    try:
        rows,info=n.read_archive(name,records,n.KLINES)
    except ValueError as error:
        # The common reader has already verified receipts, publisher checksum,
        # ZIP member and size. Only the observed 2021 headerless variant is allowed.
        if str(error)!='Unexpected archive schema: '+name or not name.startswith('BTCUSDT-5m-2021-'):
            raise
        receipt,blob=records['raw/'+name]
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            csv_blob=archive.read(name[:-4]+'.csv')
        raw=list(csv.reader(io.StringIO(csv_blob.decode('utf-8-sig'))))
        if not raw or any(len(row)!=12 for row in raw) or not raw[0][0].isdigit():
            raise ValueError('Unrecognized headerless reference schema')
        rows=[dict(zip(n.KLINES,row)) for row in raw]
        check,checksum=records['raw/'+name+'.CHECKSUM']
        info={'file':name,'url':receipt['url'],'zip_sha256':n.sha(blob),'csv_sha256':n.sha(csv_blob),
            'checksum_sha256':n.sha(checksum),'collected_at_utc':receipt['collected_at_utc'],
            'checksum_collected_at_utc':check['collected_at_utc'],'published_at_utc':None,
            'schema_variant':'2021 headerless official 12-column futures klines'}
    day=datetime.strptime(name[-14:-4],'%Y-%m-%d').replace(tzinfo=n.UTC)
    expected={int((day+timedelta(minutes=5*i)).timestamp()*1000) for i in range(288)}
    if len(rows)!=288 or {int(r['open_time']) for r in rows}!=expected:
        raise ValueError('Reference day grid incomplete')
    return rows,info


def correlation(pairs):
    if len(pairs)<2:
        return None
    a,b=zip(*pairs)
    ma,mb=math.fsum(a)/len(a),math.fsum(b)/len(b)
    denominator=(math.fsum((x-ma)**2 for x in a)*math.fsum((y-mb)**2 for y in b))**.5
    return round(math.fsum((x-ma)*(y-mb) for x,y in pairs)/denominator,12) if denominator else None


def score(pairs):
    return {'pairs':len(pairs),'r':correlation(pairs),
            'mae':round(math.fsum(abs(a-b) for a,b in pairs)/len(pairs),12) if pairs else None,
            'median_relative_error':round(statistics.median(abs(a-b)/abs(b) for a,b in pairs),12) if pairs else None}


def classify(rows, reference, protocol):
    """Select only a clearly separated full-day AND four-subwindow interpretation."""
    pairs={offset:[] for offset in (-5,0)}
    blocks={offset:[[] for _ in range(4)] for offset in (-5,0)}
    stamps=[]
    for row in rows:
        stamp=n.label_time(row['create_time'])
        if row['symbol']!='BTCUSDT' or stamp.second or stamp.minute%5:
            return {'qualified':False,'reason':'invalid_symbol_or_label'}
        value=float(n.decimal(row[n.TAKER]))
        stamps.append(stamp)
        for offset in pairs:
            key=int((stamp+timedelta(minutes=offset)).timestamp()*1000)
            if key in reference:
                pair=(value,reference[key])
                pairs[offset].append(pair)
                blocks[offset][stamp.hour//6].append(pair)
    ordered=sorted(stamps)
    if len(ordered)!=288 or len(set(ordered))!=288 or any(b-a!=timedelta(minutes=5) for a,b in zip(ordered,ordered[1:])):
        return {'qualified':False,'reason':'incomplete_or_duplicate_grid'}
    scores={str(k):score(v) for k,v in pairs.items()}
    valid=[offset for offset in pairs if scores[str(offset)]['r'] is not None]
    if len(valid)!=2:
        return {'qualified':False,'reason':'insufficient_reference_variation','scores':scores}
    best=max(valid,key=lambda k:scores[str(k)]['r'])
    other=next(k for k in valid if k!=best)
    selected,alternative=scores[str(best)],scores[str(other)]
    block_scores=[]
    for chosen,alternate in zip(blocks[best],blocks[other]):
        a,b=score(chosen),score(alternate)
        block_scores.append({'chosen':a,'alternative':b})
    passed=(selected['pairs']==protocol['minimum_pairs'] and alternative['pairs']==protocol['minimum_pairs']
        and selected['r']>=protocol['minimum_pearson_r']
        and selected['r']-alternative['r']>=protocol['minimum_correlation_margin']
        and selected['median_relative_error']<=protocol['maximum_median_relative_error']
        and all(x['chosen']['pairs']==72 and x['alternative']['pairs']==72
            and x['chosen']['r'] is not None and x['alternative']['r'] is not None
            and x['chosen']['r']>=protocol['minimum_subwindow_pearson_r']
            and x['chosen']['r']-x['alternative']['r']>=protocol['minimum_subwindow_margin'] for x in block_scores))
    return {'qualified':passed,'reason':'empirical_window_verified' if passed else 'alignment_gate_failed',
            'offset_minutes':best if passed else None,'scores':scores,'six_hour_checks':block_scores}


def reference_values(rows):
    result={}
    for row in rows:
        start=int(row['open_time'])
        if int(row['close_time'])!=start+299999 or start in result:
            raise ValueError('Invalid reference window')
        buy,volume=n.decimal(row['taker_buy_volume']),n.decimal(row['volume'])
        if volume<=buy:
            raise ValueError('Nonpositive reference sell volume')
        with localcontext() as context:
            context.prec=50
            result[start]=float(buy/(volume-buy))
    return result


def continuous_runs(rows):
    starts=sorted({n.utc(row['window_start_utc']) for row in rows})
    runs=[]
    for stamp in starts:
        if not runs or stamp!=runs[-1][-1]+timedelta(minutes=5):
            runs.append([])
        runs[-1].append(stamp)
    return [{'start_utc':n.iso(run[0]),'end_utc':n.iso(run[-1]+timedelta(minutes=5)),
             'windows':len(run),'duration_days':round(len(run)/288,8)} for run in runs]


def derive(source,verified_at):
    source=Path(source)
    when=n.utc(verified_at)
    protocol_blob=(source/'protocol.json').read_bytes()
    protocol=json.loads(protocol_blob)
    if protocol['candidate_offsets_minutes']!=[-5,0] or protocol['field']!=n.TAKER or protocol['prediction_enabled']:
        raise ValueError('Unsupported coverage protocol')
    if (protocol['minimum_pairs']!=288 or protocol['minimum_pearson_r']<.99
            or protocol['minimum_correlation_margin']<.5 or protocol['maximum_median_relative_error']>.02
            or protocol['minimum_subwindow_pearson_r']<.98 or protocol['minimum_subwindow_margin']<.3
            or protocol['subwindow_hours']!=6 or len(protocol['dates'])!=len(set(protocol['dates']))):
        raise ValueError('Coverage gates cannot be weakened')
    records,input_sha=n.load_evidence(source)
    if any(n.utc(r['collected_at_utc'])>when for r,_ in records.values()):
        raise ValueError('Audit cannot precede evidence collection')
    tables,sources={},{}
    for path,(receipt,_) in sorted(records.items()):
        name=path[4:]
        if name.endswith('.zip') and receipt['status']==200:
            if '-metrics-' in name:
                tables[name],sources[name]=n.read_archive(name,records,n.METRICS)
            else:
                tables[name],sources[name]=read_reference(name,records)
    candidates,contracts,diagnostics,isolation=[],[],[],[]
    for day in protocol['dates']:
        name=f'BTCUSDT-metrics-{day}.zip'
        prev=(datetime.fromisoformat(day)-timedelta(days=1)).date().isoformat()
        ref_names=[f'BTCUSDT-5m-{d}.zip' for d in (prev,day)]
        unknown={'date':day,'fields':protocol['independent_fields'],
                 'status':'quarantined','reason':'no admissible independently collected timing reference',
                 'event_time_utc':None,'research_available_at_utc':None}
        isolation.append(unknown)
        if any(x not in tables for x in [name,*ref_names]):
            diagnostics.append({'date':day,'qualified':False,'reason':'missing_archive_or_reference'})
            continue
        rows=tables[name]
        day_time=datetime.fromisoformat(day).replace(tzinfo=n.UTC)
        labels=[n.label_time(row['create_time']) for row in rows]
        if any(stamp<day_time or stamp>day_time+timedelta(days=1) for stamp in labels):
            raise ValueError('Metric label outside filename day boundary')
        reference={}
        for ref_name in ref_names:
            values=reference_values(tables[ref_name])
            if set(values)&set(reference):
                raise ValueError('Reference file boundary duplicate')
            reference.update(values)
        finding=classify(rows,reference,protocol)
        finding.update(date=day,source=n.grid_quality(name,rows),zip_sha256=sources[name]['zip_sha256'])
        diagnostics.append(finding)
        if not finding['qualified']:
            isolation.append({'date':day,'fields':[n.TAKER],'status':'quarantined','reason':finding['reason']})
            continue
        support=[sources[x] for x in [name,*ref_names]]
        evidence_at=max(n.utc(r[k]) for r in support for k in ('collected_at_utc','checksum_collected_at_utc'))
        contract={'date':day,'field':n.TAKER,'offset_minutes':finding['offset_minutes'],
                  'verified_at_utc':verified_at,'protocol_sha256':n.sha(protocol_blob),'sources':support,
                  'scope':'exact fingerprints, empirical window interpretation; no publication-time claim'}
        contract_id=n.sha(n.json_bytes(contract))
        contracts.append(dict(contract,id=contract_id))
        for line,row in enumerate(rows,2):
            start=n.label_time(row['create_time'])+timedelta(minutes=finding['offset_minutes'])
            end=start+timedelta(minutes=5)
            candidates.append({'symbol':'BTCUSDT','field':n.TAKER,'value':row[n.TAKER],
                'source_label':row['create_time'],'window_start_utc':n.iso(start),'window_end_utc':n.iso(end),
                'event_time_utc':n.iso(end),'published_at_utc':None,'collected_at_utc':sources[name]['collected_at_utc'],
                'evidence_collected_at_utc':n.iso(evidence_at),'normalized_at_utc':verified_at,
                'semantic_verified_at_utc':verified_at,'research_available_at_utc':n.iso(max(when,evidence_at,end)),
                'semantic_contract':contract_id,'provenance':[dict(sources[name],line=line)]})
    # Different per-file contract IDs describe the same window semantics. Compare
    # economic values and actual window bounds, then retain all contributing IDs.
    for row in candidates:
        row['provenance'][0]['contract_id']=row['semantic_contract']
        row['semantic_contract']='coverage-v1-empirical-five-minute-flow'
    accepted,conflicts,identical=n.deduplicate(candidates)
    runs=continuous_runs(accepted)
    stats=n.describe(accepted)
    summary={'dates_requested':len(protocol['dates']),'dates_qualified':len(contracts),'accepted_windows':len(accepted),
        'longest_continuous_days':max((r['duration_days'] for r in runs),default=0),'continuous_runs':runs,
        'identical_duplicates_removed':identical,'conflicting_duplicate_groups':len(conflicts),
        'independent_oi_long_short_status':'not_verified; access/permission evidence unavailable',
        'historical_publication_proven':False,'statistics_module_enabled':False,
        'reason':'limited continuity and no historical availability/independent OI or ratio timing evidence',
        'descriptive_statistics':stats,'daily_diagnostics':diagnostics}
    payloads={'validated.jsonl':b''.join((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode() for r in accepted),
        'coverage.json':n.json_bytes(summary),'contracts.json':n.json_bytes(contracts),
        'isolation.json':n.json_bytes(isolation),'conflicts.json':n.json_bytes(conflicts)}
    manifest={'version':1,'verified_at_utc':verified_at,'source_receipts_sha256':input_sha,
        'protocol_sha256':n.sha(protocol_blob),'adapter_sha256':n.sha(Path(__file__).read_bytes().replace(b'\r\n',b'\n')),
        'normalizer_sha256':n.sha(Path(n.__file__).read_bytes().replace(b'\r\n',b'\n')),
        'attribution':'Binance Vision https://data.binance.vision/','license':'CC BY-NC-SA 4.0',
        'files':{k:n.sha(v) for k,v in payloads.items()}}
    payloads['manifest.json']=n.json_bytes(manifest)
    return payloads


def build(source,output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Use new output outside raw evidence')
    payloads=derive(source,n.iso(datetime.now(n.UTC)))
    output.mkdir(parents=True,exist_ok=False)
    for name,blob in payloads.items():
        (output/name).write_bytes(blob)
    result=json.loads(payloads['coverage.json'])
    return {k:v for k,v in result.items() if k!='daily_diagnostics'}


def verify(source,output):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_bytes())
    payloads=derive(source,manifest['verified_at_utc'])
    if {x.name for x in output.iterdir()}!=set(payloads) or any((output/k).read_bytes()!=v for k,v in payloads.items()):
        raise ValueError('Coverage replay mismatch')
    return {'verified_files':len(payloads)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('build','verify'))
    parser.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps({'build':build,'verify':verify}[args.command](args.source,args.output),ensure_ascii=False,indent=2))
