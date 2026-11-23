"""Predeclared finite synthetic pressure matrix; no historical or holdout input."""
import hashlib
import json
import math
from pathlib import Path
import sys
import time

if len(sys.argv)!=1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    print('Use python -I -S -B run_pressure31.py',file=sys.stderr);sys.exit(2)
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import prequential_synthetic31 as online

CONFIG_SHA='0ff05dcf35d12cc2ce209ec7ce4705eaac22a5dc5dae9b970da8d1ffa470062a'
STRATEGIES=online.STRATEGIES
RELATION_CHANGES={'relation_reverse','relation_reverse_long_delay','gradual','transient','training_failure'}
FEATURE_CHANGES={'mean_shift':'mean_alarm','variance_shift':'variance_alarm'}


def _sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def _window_metrics(result, start, stop):
    lookup={(p['strategy'],p['sample_id']):p for p in result['predictions']}
    out={}
    for name in STRATEGIES:
        values=[f for f in result['feedback'] if f['strategy']==name and
                start<=lookup[(name,f['sample_id'])]['decision_time']<stop]
        n=len(values)
        out[name]={'n':n,'mae':math.fsum(v['absolute_loss'] for v in values)/n if n else None,
                   'rmse':math.sqrt(math.fsum(v['squared_loss'] for v in values)/n) if n else None}
    eligible=[n for n in STRATEGIES if out[n]['rmse'] is not None]
    return {'start':start,'stop':stop,'metrics':out,
            'ranking':sorted(eligible,key=lambda name:(out[name]['rmse'],name))}


def _recovery(result, strategy, change):
    """First two complete 10-decision blocks within 1.5x own prechange MSE."""
    predictions={(p['strategy'],p['sample_id']):p for p in result['predictions']}
    by_time={predictions[(f['strategy'],f['sample_id'])]['decision_time']:f
             for f in result['feedback'] if f['strategy']==strategy}
    pre=[by_time[t]['squared_loss'] for t in range(result['prediction_start'],change) if t in by_time]
    if len(pre)<10:return {'status':'insufficient_baseline','delay':None,'baseline_count':len(pre)}
    threshold=1.5*math.fsum(pre)/len(pre)
    blocks=[]
    for start in range(change,result['horizon']-8,10):
        items=[by_time.get(t) for t in range(start,start+10)]
        if any(item is None for item in items): blocks.append(None);continue
        blocks.append({'ok':math.fsum(v['squared_loss'] for v in items)/10<=threshold,
                       'observed_at':max(v['label_time'] for v in items)})
    for left,right in zip(blocks,blocks[1:]):
        if left and right and left['ok'] and right['ok']:
            return {'status':'recovered','delay':max(left['observed_at'],right['observed_at'])-change,
                    'threshold':threshold,'baseline_count':len(pre)}
    return {'status':'censored','delay':None,'threshold':threshold,'baseline_count':len(pre)}


def _summary(result, scenario, seed, elapsed):
    horizon=result['horizon']; change=90
    alarm_events=[e for e in result['events'] if e.get('event_type')=='alarm']
    stable={'stable_signal','stable_no_signal','high_noise','long_delay','insufficient_training'}
    false=[a for a in alarm_events if a['alarm'] and (scenario in stable or a['alarm_time']<change)]
    same_relationship=stable | {'mean_shift','variance_shift','false_alarm','outliers'}
    relation_alarms_without_relation_change=sum(a.get('relationship_alarm') is True and
        (scenario in same_relationship or a['alarm_time']<change) for a in alarm_events)
    relevant='relationship_alarm' if scenario in RELATION_CHANGES else FEATURE_CHANGES.get(scenario)
    hits=[a['alarm_time'] for a in alarm_events if relevant and a['alarm_time']>=change and a.get(relevant) is True]
    paired={}
    for comparator in ('fixed_ridge','mean'):
        a={f['sample_id']:f['squared_loss'] for f in result['feedback'] if f['strategy']=='adaptive'}
        b={f['sample_id']:f['squared_loss'] for f in result['feedback'] if f['strategy']==comparator}
        if a.keys()!=b.keys(): raise ValueError('unfair loss pairing')
        paired[comparator]={'n':len(a),'mean_adaptive_minus_comparator':
                            math.fsum(a[k]-b[k] for k in a)/len(a) if a else None}
    switches={}
    for strategy in STRATEGIES:
        activations=[e for e in result['events'] if e.get('event_type')=='activation' and e.get('strategy')==strategy]
        switches[strategy]={'new_model_versions':max(0,len(activations)-1),
                            'kind_switches':sum(a['kind']!=b['kind'] for a,b in zip(activations,activations[1:]))}
    return {'scenario':scenario,'seed':seed,'seconds':round(elapsed,6),
            'metrics':result['metrics'],'censored_prediction_samples':result['censored_prediction_samples'],
            'windows':[_window_metrics(result,a,b) for a,b in ((60,90),(90,120),(120,150),(150,180))],
            'recovery':{s:_recovery(result,s,change) for s in STRATEGIES} if relevant else None,
            'switches':switches,'paired_loss_differences':paired,
            'alarms':{'total':sum(bool(a['alarm']) for a in alarm_events),'false_or_prechange':len(false),
                      'relationship_alarm_without_relation_change':relation_alarms_without_relation_change,
                      'relevant_kind':relevant,'first_detected_delay':min(hits)-change if hits else None,
                      'undetected_within_horizon':bool(relevant and not hits)},
            'audit_sha256':result['audit_sha256'],'horizon':horizon}


def _matched_delay(short, long):
    """Compare adaptive errors only where both paired synthetic labels arrived."""
    def rows(run):
        pred={(p['strategy'],p['sample_id']):p for p in run['predictions']}
        return {pred[(f['strategy'],f['sample_id'])]['decision_time']:(f,pred[(f['strategy'],f['sample_id'])])
                for f in run['feedback'] if f['strategy']=='adaptive'}
    a,b=rows(short),rows(long); keys=sorted(a.keys() & b.keys())
    if any(a[t][0]['target']!=b[t][0]['target'] or a[t][1]['features']!=b[t][1]['features'] for t in keys):
        raise ValueError('paired delay DGP does not contain matching observed samples')
    return {'matched_count':len(keys),'decision_times':keys,
            'mean_squared_loss_long_minus_short':math.fsum(b[t][0]['squared_loss']-a[t][0]['squared_loss']
                                                         for t in keys)/len(keys) if keys else None,
            'note':'same synthetic seed and identical feature/target on matched observed decisions'}


def main():
    cfg_path=ROOT/'round31-pressure-preconfig.json'
    if _sha(cfg_path)!=CONFIG_SHA: raise ValueError('pressure preconfiguration changed')
    cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
    lock=json.loads((ROOT/'round31-pressure-source-lock-v2.json').read_text(encoding='utf-8'))
    if lock.get('online_config')!=online.CONFIG:
        raise ValueError('online parameters changed since pre-run lock')
    for file,digest in lock['source_sha256'].items():
        if _sha(ROOT/file)!=digest: raise ValueError('source changed since pre-run lock')
    if len(cfg['scenarios'])*len(cfg['report_seeds'])!=cfg['max_runs']:
        raise ValueError('predeclared run count mismatch')
    if set(cfg['development_seeds']) & set(cfg['report_seeds']):
        raise ValueError('development and report seeds must be disjoint')
    started=time.perf_counter(); run_dir=ROOT/'round31-pressure-runs-v2';run_dir.mkdir(exist_ok=False)
    records=[]
    for scenario in cfg['scenarios']:
        for seed in cfg['report_seeds']:
            if time.perf_counter()-started>cfg['resource_wall_seconds']:
                raise TimeoutError('predeclared wall resource cap exceeded; partial evidence retained')
            base=cfg['failure_scenario_base'] if scenario=='training_failure' else scenario
            rows=online.generate(base,seed,n=cfg['rows_per_run'])
            begin=time.perf_counter()
            result=online.run_online(rows,injected_failure_times=cfg['failure_times'] if scenario=='training_failure' else ())
            online.verify_run(result,result['audit_sha256'],rows=rows)
            elapsed=time.perf_counter()-begin
            file=run_dir/(scenario+'-'+str(seed)+'.json')
            with file.open('x',encoding='utf-8') as f: json.dump(result,f,sort_keys=True,allow_nan=False,separators=(',',':'))
            record=_summary(result,scenario,seed,elapsed); record['raw_file']=str(file.relative_to(ROOT))
            record['raw_sha256']=_sha(file);records.append(record)
    paired_delay=[]
    for seed in cfg['report_seeds']:
        left=json.loads((run_dir/('relation_reverse-'+str(seed)+'.json')).read_text(encoding='utf-8'))
        right=json.loads((run_dir/('relation_reverse_long_delay-'+str(seed)+'.json')).read_text(encoding='utf-8'))
        paired_delay.append(dict(seed=seed,**_matched_delay(left,right)))
    output={'version':'synthetic-pressure-results-v1','config_sha256':CONFIG_SHA,
            'source_lock':lock,'python':sys.version,'elapsed_seconds':round(time.perf_counter()-started,6),
            'records':records,'paired_label_delay':paired_delay,'limitations':['exploratory synthetic development; not an untouched final test',
             'paired scenarios use common seed; 12 repeats have substantial Monte Carlo error',
             'label censoring varies between delay scenarios; evaluate matched samples for comparisons',
             'recovery is a fixed descriptive definition, not guaranteed permanent recovery']}
    with (ROOT/'round31-pressure-results-v2.json').open('x',encoding='utf-8') as f:
        json.dump(output,f,sort_keys=True,indent=2,allow_nan=False)
    print(json.dumps({'run_count':len(records),'elapsed_seconds':output['elapsed_seconds'],
                      'scope':'synthetic only','status':'complete'}))


if __name__=='__main__':main()
