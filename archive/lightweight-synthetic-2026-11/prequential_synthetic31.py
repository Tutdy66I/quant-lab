"""Causal synthetic online comparison, not a market model or security boundary.

Predict at integer t, then release all labels with availability <= t, then
observe alarms and schedule model changes for t+1. Input target availability
times are generator declarations; a caller who forges the entire input cannot
be detected without a separately trusted data source.
"""
from dataclasses import asdict
import hashlib
import json
import math
import random

from ridge_synthetic import ALPHAS, Model, fit_ridge, predict_ridge

SCENARIOS=('stable_signal','stable_no_signal','mean_shift','variance_shift',
           'relation_reverse','gradual','transient','high_noise','outliers',
           'long_delay','relation_reverse_long_delay','false_alarm','insufficient_training')
STRATEGIES=('fixed_ridge','mean','periodic_ridge','adaptive')
CONFIG={'warmup':60,'min_train':24,'train_window':45,'check_interval':10,
        'periodic_interval':20,'cooldown':20,'monitor_window':20,
        'mean_threshold':0.9,'variance_threshold':2.5,'residual_threshold':2.5,
        'update_penalty':0.02,'alpha_candidates':list(ALPHAS),
        'activation_delay':1,'version':'prequential31-v1'}


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _observed_digest(rows, horizon):
    """Hash features and only labels available by the end of the simulation.

    The caller may separately commit a full oracle dataset before execution;
    the online system itself must not access never-arriving or future targets.
    """
    visible=[]
    for r in rows[:horizon+1]:
        item={k:r[k] for k in ('synthetic_dataset_id','sample_id','decision_time',
              'feature_available_time','target_start_time','target_end_time',
              'label_available_time','features')}
        at=r['label_available_time']
        if at is not None and at<=horizon: item['target']=r['target']
        visible.append(item)
    return _digest(visible)


def generate(scenario, seed, n=180):
    """Independent synthetic rows; no imported historical data or I/O."""
    if scenario not in SCENARIOS or type(seed) is not int or type(n) is not int or not 120<=n<=2000:
        raise ValueError('invalid scenario, seed or sequence size')
    rng=random.Random(seed); dataset='synthetic31:'+scenario+':'+str(seed)+':'+str(n)
    rows=[]; change=n//2
    for t in range(n):
        x=rng.gauss(0,1); noise=rng.gauss(0,1.7 if scenario=='high_noise' else .3)
        if scenario in ('mean_shift','false_alarm') and t>=change: x+=1.6
        if scenario=='variance_shift' and t>=change: x*=2.2
        coef=.9
        if scenario in ('relation_reverse','relation_reverse_long_delay') and t>=change: coef=-.9
        if scenario=='gradual' and t>=change: coef=.9-1.8*min(1.0,(t-change)/50)
        if scenario=='transient' and change<=t<change+30: coef=-.9
        y=noise if scenario=='stable_no_signal' else coef*x+noise
        if scenario=='outliers' and t in (change+4,change+17,change+33): y+=7.0
        delay=35 if scenario in ('long_delay','relation_reverse_long_delay') else (3 if t%7==0 else 1)
        available=t+1+delay
        if scenario=='insufficient_training' and t%3!=0: available=None
        rows.append({'synthetic_dataset_id':dataset,'sample_id':dataset+':'+str(t),
          'decision_time':t,'feature_available_time':t,'target_start_time':t,
          'target_end_time':t+1,'label_available_time':available,'features':[x], 'target':y})
    return rows


class CausalInputError(ValueError):
    """A bad later input preserves the predictions already made in this process."""
    def __init__(self, message, decision_time, predictions, feedback, events):
        super().__init__(message)
        self.decision_time=decision_time
        self.predictions=json.loads(json.dumps(predictions,allow_nan=False))
        self.feedback=json.loads(json.dumps(feedback,allow_nan=False))
        self.events=json.loads(json.dumps(events,allow_nan=False))


class ModelUpdateInterrupted(RuntimeError):
    """In-process interruption evidence, not a durable crash-recovery system."""
    def __init__(self, decision_time, strategy, predictions, feedback, events):
        super().__init__('synthetic model update interrupted')
        self.decision_time=decision_time
        self.strategy=strategy
        self.predictions=json.loads(json.dumps(predictions,allow_nan=False))
        self.feedback=json.loads(json.dumps(feedback,allow_nan=False))
        self.events=json.loads(json.dumps(events,allow_nan=False))


def _validate(rows, *, limit=None):
    if not isinstance(rows,(tuple,list)) or len(rows)<100: raise ValueError('at least 100 synthetic records required')
    if limit is None: limit=len(rows)
    if type(limit) is not int or not 1<=limit<=len(rows): raise ValueError('invalid validation prefix')
    seen=set(); dataset=None
    for i,r in enumerate(rows[:limit]):
        if not isinstance(r,dict) or type(r.get('decision_time')) is not int or r['decision_time']!=i:
            raise ValueError('invalid chronology')
        if dataset is None: dataset=r.get('synthetic_dataset_id')
        if not isinstance(dataset,str) or not dataset.startswith('synthetic31:') or r.get('synthetic_dataset_id')!=dataset:
            raise ValueError('synthetic dataset identity mismatch')
        sid=r.get('sample_id')
        if sid!=dataset+':'+str(i) or sid in seen: raise ValueError('sample ID mismatch or duplicate')
        seen.add(sid)
        if (type(r.get('feature_available_time')) is not int or r['feature_available_time']>i or
            r.get('target_start_time')!=i or r.get('target_end_time')!=i+1):
            raise ValueError('invalid feature or target timing')
        at=r.get('label_available_time')
        if at is not None and (type(at) is not int or at<r['target_end_time']):
            raise ValueError('label released before target end')
        x=r.get('features')
        if not isinstance(x,(tuple,list)) or len(x)!=1 or isinstance(x[0],bool) or not isinstance(x[0],(int,float)) or not math.isfinite(x[0]):
            raise ValueError('invalid feature')
    return dataset


def _fit(available, now):
    """Train on prefix, tune on subsequent available labels, never validation."""
    eligible=[v for v in available if v['label_available_time']<=now and v['decision_time']<now]
    eligible.sort(key=lambda v:v['decision_time'])
    if len(eligible)<CONFIG['min_train']: raise ValueError('insufficient available labels')
    eligible=eligible[-CONFIG['train_window']:]
    split=max(3,int(len(eligible)*.75)); train=eligible[:split]; tune=eligible[split:]
    if len(tune)<3 or train[-1]['target_end_time']>tune[0]['decision_time']:
        raise ValueError('invalid chronological fit or tune')
    models=[(a,fit_ridge([r['features'] for r in train],[r['target'] for r in train],a)) for a in ALPHAS]
    scores=[(a,math.fsum((predict_ridge(m,r['features'])-r['target'])**2 for r in tune)/len(tune)) for a,m in models]
    alpha=min(scores,key=lambda z:(z[1],z[0]))[0]; model=next(m for a,m in models if a==alpha)
    return model,{'train_ids':[r['sample_id'] for r in train], 'tune_ids':[r['sample_id'] for r in tune],
      'max_label_available':max(r['label_available_time'] for r in eligible), 'alpha':alpha,
      'train_last_decision':train[-1]['decision_time'],'tune_first_decision':tune[0]['decision_time'],
      'tune_last_decision':tune[-1]['decision_time'], 'training_rows':len(train),
      'tuning_rows':len(tune),'candidate_fits':len(models)}


def _mean(available):
    vals=[r['target'] for r in available[-CONFIG['train_window']:]]
    return math.fsum(vals)/len(vals) if vals else 0.0


def _mean_evidence(available):
    used=available[-CONFIG['train_window']:]
    return {'available_sample_count':len(available), 'sample_count':len(used),
            'mean_ids':[r['sample_id'] for r in used],
            'max_label_available':max((r['label_available_time'] for r in used),default=None)}


def _version(dataset, strategy, version, kind, value, train_time, active_time, evidence, reason, alarm_time=None):
    return {'model_id':dataset+':'+strategy+':v'+str(version),'strategy':strategy,'version':version,
            'kind':kind,'value':value,'retrain_time':train_time,'model_activation_time':active_time,
            'training':evidence,'reason':reason,'alarm_time':alarm_time}


def run_online(rows, *, injected_failure_times=(), injected_interrupt_times=()):
    """Evaluate all strategies fairly on identical observed targets.

    injectable failure times are for synthetic adversarial tests only; no
    model is updated until the next decision. Baseline alarms are measured
    independently of the adaptive model, preventing self-referential labels.
    """
    warmup=CONFIG['warmup']; dataset=_validate(rows,limit=warmup)
    if not isinstance(injected_failure_times,(tuple,list)) or any(type(t) is not int for t in injected_failure_times):
        raise ValueError('failure times must be integer sequence')
    if not isinstance(injected_interrupt_times,(tuple,list)) or any(type(t) is not int for t in injected_interrupt_times):
        raise ValueError('interrupt times must be integer sequence')
    available=[]; arrived=set(); predictions=[]; feedback=[]; events=[]; models={}; versions={s:0 for s in STRATEGIES}
    # Initial fit runs before the first decision. Labels arriving exactly at
    # warmup are processed only after that decision's prediction.
    for r in rows[:warmup]:
        at=r['label_available_time']
        if at is not None and at<warmup:
            y=r.get('target')
            if isinstance(y,bool) or not isinstance(y,(int,float)) or not math.isfinite(y):raise ValueError('invalid arrived label')
            available.append(r);arrived.add(r['sample_id'])
    try:
        base_model,base_meta=_fit(available,warmup-1)
        predict_ridge(base_model,available[-1]['features'])
    except Exception: base_model,base_meta=None,{'failure':'insufficient or invalid initial training'}
    initmean=_mean(available)
    for s in STRATEGIES:
        kind='mean' if s=='mean' or base_model is None else 'ridge'
        evidence=_mean_evidence(available) if kind=='mean' else base_meta
        model=_version(dataset,s,0,kind,initmean if kind=='mean' else asdict(base_model),warmup-1,warmup,evidence,'initial')
        models[s]=model;events.append(dict(model,event_type='activation'))
    # Calibration reference is the pre-decision features and observed labels.
    xs=[r['features'][0] for r in rows[:warmup]]
    reference_mean=math.fsum(xs)/len(xs); reference_var=math.fsum((x-reference_mean)**2 for x in xs)/len(xs)
    if reference_var<=1e-12: raise ValueError('degenerate reference feature variance')
    residual_reference=math.fsum((r['target']-(predict_ridge(base_model,r['features']) if base_model else initmean))**2 for r in available)/len(available) if available else 1.0
    residual_reference=max(residual_reference,1e-9)
    initial_count=len(available); processed_features=[]; scored_fixed={}; last_adaptive=-10**9
    per_sample={}
    for t in range(warmup,len(rows)):
        try: _validate(rows,limit=t+1)
        except ValueError as exc:
            raise CausalInputError(str(exc),t,predictions,feedback,events) from exc
        r=rows[t]; processed_features.append(r['features'][0]); entries={}
        for s in STRATEGIES:
            m=models[s]
            if m['model_activation_time']>t: raise ValueError('future model used for decision')
            if m['kind']=='ridge':
                val=predict_ridge(Model(**m['value']),r['features'])
            else: val=m['value']
            if not math.isfinite(val): raise ValueError('invalid prediction')
            record={'sample_id':r['sample_id'],'synthetic_dataset_id':dataset,'strategy':s,
                    'decision_time':t,'feature_available_time':r['feature_available_time'],
                    'target_start_time':r['target_start_time'],'target_end_time':r['target_end_time'],
                    'label_available_time':r['label_available_time'],'features':list(r['features']),
                    'alarm_time':m['alarm_time'],'retrain_time':m['retrain_time'],
                    'model_activation_time':m['model_activation_time'],'active_model_id':m['model_id'],
                    'prediction':val}
            predictions.append(record);entries[s]=record
        if r['sample_id'] in per_sample: raise ValueError('prediction already recorded')
        per_sample[r['sample_id']]=entries
        # This loop reads target values only after their declared availability.
        for old in rows[:t+1]:
            at=old['label_available_time']; sid=old['sample_id']
            if sid in arrived or at is None or at>t: continue
            y=old.get('target')
            if isinstance(y,bool) or not isinstance(y,(int,float)) or not math.isfinite(y):
                raise CausalInputError('invalid arrived label',t,predictions,feedback,events)
            available.append(old);arrived.add(sid)
            if sid in per_sample:
                for s in STRATEGIES:
                    pred=per_sample[sid][s]['prediction']; err=(pred-y)**2
                    feedback.append({'sample_id':sid,'strategy':s,'label_time':t,'target':y,'squared_loss':err,
                                     'absolute_loss':abs(pred-y),'active_model_id':per_sample[sid][s]['active_model_id']})
                    if s=='fixed_ridge':scored_fixed[sid]=err
        if t%CONFIG['check_interval']!=0: continue
        w=processed_features[-CONFIG['monitor_window']:]
        mean_alarm=variance_alarm=False
        if len(w)>=CONFIG['monitor_window']:
            m=math.fsum(w)/len(w); var=math.fsum((x-m)**2 for x in w)/len(w)
            mean_alarm=abs(m-reference_mean)/math.sqrt(reference_var)>=CONFIG['mean_threshold']
            variance_alarm=var<=1e-12 or max(var/reference_var,reference_var/var)>=CONFIG['variance_threshold']
        labeled=[v for v in available if v['sample_id'] in scored_fixed][-CONFIG['monitor_window']:]
        relationship_available=len(labeled)>=CONFIG['monitor_window']
        relation_alarm=relationship_available and math.fsum(scored_fixed[v['sample_id']] for v in labeled)/len(labeled)>=CONFIG['residual_threshold']*residual_reference
        alarm=bool(mean_alarm or variance_alarm or relation_alarm)
        events.append({'event_type':'alarm','decision_time':t,'alarm_time':t,
                       'mean_alarm':mean_alarm,'variance_alarm':variance_alarm,
                       'relationship_status':'available' if relationship_available else 'unavailable',
                       'relationship_alarm':bool(relation_alarm) if relationship_available else None,
                       'alarm':alarm,'available_labels':len(available),
                       'max_label_time':max((v['label_available_time'] for v in labeled),default=None)})
        should={'mean':True,'periodic_ridge':t%CONFIG['periodic_interval']==0,
                'adaptive':alarm and t-last_adaptive>=CONFIG['cooldown']}
        for s,update in should.items():
            if not update:continue
            if s!='mean' and t in injected_interrupt_times:
                raise ModelUpdateInterrupted(t,s,predictions,feedback,events)
            versions[s]+=1; new_kind='mean'; new_value=_mean(available); evidence=_mean_evidence(available)
            reason='periodic_mean' if s=='mean' else ('periodic_ridge' if s=='periodic_ridge' else 'drift_triggered')
            if s!='mean':
                try:
                    if t in injected_failure_times:raise ValueError('injected training failure')
                    fitted,evidence=_fit(available,t)
                    # Reject malformed/nonfinite models before they can be activated.
                    predict_ridge(fitted,available[-1]['features'])
                    new_kind='ridge';new_value=asdict(fitted)
                except Exception:
                    reason+=':fallback_mean'
                    evidence=_mean_evidence(available)
            if s=='adaptive':last_adaptive=t
            new=_version(dataset,s,versions[s],new_kind,new_value,t,t+1,evidence,reason,t if s=='adaptive' else None)
            models[s]=new;events.append(dict(new,event_type='activation'))
    metrics={}; evaluation_start=warmup; censored=len(rows)-warmup-len({f['sample_id'] for f in feedback})
    for s in STRATEGIES:
        f=[q for q in feedback if q['strategy']==s]; n=len(f)
        updates=[q for q in events if q.get('event_type')=='activation' and q.get('strategy')==s and q.get('version',0)>0]
        metrics[s]={'n_scored':n,'mae':math.fsum(q['absolute_loss'] for q in f)/n if n else None,
                    'rmse':math.sqrt(math.fsum(q['squared_loss'] for q in f)/n) if n else None,
                    'updates':len(updates),'penalty':len(updates)*CONFIG['update_penalty'],
                    'completed_candidate_fit_count':sum(q['training'].get('candidate_fits',0) for q in updates),
                    'failed_fit_attempts':sum('fallback_mean' in q['reason'] for q in updates),
                    'penalized_mean_squared_loss':(math.fsum(q['squared_loss'] for q in f)+len(updates)*CONFIG['update_penalty'])/n if n else None}
    result={'scope':'synthetic development only; not independent final evaluation', 'synthetic_dataset_id':dataset,
            'config':dict(CONFIG),'prediction_start':evaluation_start,'horizon':len(rows)-1,
            'initial_available_labels':initial_count,'predictions':predictions,'feedback':feedback,
            'events':events,'metrics':metrics,'censored_prediction_samples':censored,
            'dataset_sha256':_observed_digest(rows,len(rows)-1),
            'dataset_sha256_scope':'observed features and labels arrived by horizon only',
            'limitations':['caller-declared timing not externally authenticated',
              'finite horizon censors missing or late labels','alarms are fixed heuristic rules, not calibrated significance',
              'repeated development inspection is not an untouched final test']}
    result['audit_sha256']=_digest(result)
    return result


def verify_run(result, expected_sha256, *, rows):
    if not isinstance(result,dict) or not isinstance(expected_sha256,str) or len(expected_sha256)!=64:
        raise ValueError('expected prior digest required')
    body=dict(result); saved=body.pop('audit_sha256',None)
    if saved!=expected_sha256 or _digest(body)!=expected_sha256:raise ValueError('run receipt mismatch')
    dataset=_validate(rows)
    if (body['config']!=CONFIG or body['prediction_start']!=CONFIG['warmup'] or
        body['horizon']!=len(rows)-1): raise ValueError('unapproved configuration or evaluation horizon')
    if (body['synthetic_dataset_id']!=dataset or
        body['dataset_sha256_scope']!='observed features and labels arrived by horizon only' or
        body['dataset_sha256']!=_observed_digest(rows,body['horizon'])):
        raise ValueError('source rows differ from result')
    source={r['sample_id']:r for r in rows}
    activations=[e for e in body['events'] if e.get('event_type')=='activation']
    models={e['model_id']:e for e in activations}
    if len(models)!=len(activations):raise ValueError('duplicate model ID')
    warmup=CONFIG['warmup']
    initial=[r for r in rows[:warmup] if r['label_available_time'] is not None and r['label_available_time']<warmup]
    def arrived(now):
        later=[r for r in rows[:now+1] if r['label_available_time'] is not None and
               warmup<=r['label_available_time']<=now]
        later.sort(key=lambda r:(r['label_available_time'],r['decision_time']))
        return initial+later
    fit_cache={}
    for m in activations:
        if (m['model_id']!=dataset+':'+m['strategy']+':v'+str(m['version']) or
            m['strategy'] not in STRATEGIES or m['model_activation_time']<=m['retrain_time']):
            raise ValueError('model activation identity/timing invalid')
        if m['alarm_time'] is not None and (m['alarm_time']!=m['retrain_time'] or m['model_activation_time']<=m['alarm_time']):
            raise ValueError('alarm precedes activation incorrectly')
        if m['model_activation_time']!=m['retrain_time']+1:
            raise ValueError('unexpected activation delay')
        available=arrived(m['retrain_time'])
        if m['kind']=='ridge':
            if m['retrain_time'] not in fit_cache:
                fit_cache[m['retrain_time']]=_fit(available,m['retrain_time'])
            fitted,expected_training=fit_cache[m['retrain_time']]
            if _digest(m['value'])!=_digest(asdict(fitted)) or m['training']!=expected_training:
                raise ValueError('model or training lineage differs from available inputs')
        elif m['kind']=='mean':
            if m['value']!=_mean(available) or m['training']!=_mean_evidence(available):
                raise ValueError('mean uses unavailable or incorrect labels or lineage')
        else: raise ValueError('unknown model kind')
        tr=m['training']
        if 'train_ids' in tr:
            train=tr['train_ids']; tune=tr['tune_ids']; ids=train+tune
            if len(ids)!=len(set(ids)) or any(sid not in source for sid in ids):raise ValueError('invalid training IDs')
            rr=[source[sid] for sid in ids]
            if (any(r['label_available_time'] is None or r['label_available_time']>m['retrain_time'] or
                    r['decision_time']>=m['retrain_time'] for r in rr) or
                any(a['decision_time']>=b['decision_time'] for a,b in zip(rr,rr[1:])) or
                source[train[-1]]['target_end_time']>source[tune[0]]['decision_time']):
                raise ValueError('future label or training/tuning overlap')
    seen={}
    for p in body['predictions']:
        key=(p['strategy'],p['sample_id'])
        if key in seen or p['sample_id'] not in source or p['active_model_id'] not in models: raise ValueError('duplicate prediction or missing model')
        seen[key]=p;m=models[p['active_model_id']]; r=source[p['sample_id']]
        if (m['strategy']!=p['strategy'] or m['model_activation_time']>p['decision_time'] or
            p['feature_available_time']>p['decision_time'] or p['target_end_time']<=p['decision_time'] or
            any(p[k]!=r[k] for k in ('synthetic_dataset_id','decision_time','feature_available_time',
                'target_start_time','target_end_time','label_available_time','features')) or
            p['retrain_time']!=m['retrain_time'] or p['model_activation_time']!=m['model_activation_time'] or
            p['alarm_time']!=m['alarm_time']):
            raise ValueError('causal identity violation')
        applicable=[q for q in activations if q['strategy']==p['strategy'] and q['model_activation_time']<=p['decision_time']]
        if not applicable or max(applicable,key=lambda q:q['model_activation_time'])['model_id']!=m['model_id']:
            raise ValueError('prediction does not use active version')
        value=predict_ridge(Model(**m['value']),r['features']) if m['kind']=='ridge' else m['value']
        if value!=p['prediction']:raise ValueError('prediction does not match model')
    expected={(s,r['sample_id']) for r in rows[body['prediction_start']:] for s in STRATEGIES}
    if set(seen)!=expected:raise ValueError('missing strategy or prediction')
    expected_order=[(s,r['sample_id']) for r in rows[body['prediction_start']:] for s in STRATEGIES]
    if [(v['strategy'],v['sample_id']) for v in body['predictions']]!=expected_order:
        raise ValueError('prediction order differs from decision sequence')
    feedback_keys=set()
    for f in body['feedback']:
        key=(f['strategy'],f['sample_id'])
        if key not in seen or key in feedback_keys:raise ValueError('feedback without unique prediction')
        feedback_keys.add(key);p=seen[key];r=source[f['sample_id']]
        if (r['label_available_time'] is None or f['label_time']!=r['label_available_time'] or
            f['label_time']<=p['decision_time'] or f['label_time']>body['horizon'] or
            f['target']!=r['target'] or f['active_model_id']!=p['active_model_id'] or
            f['squared_loss']!=(p['prediction']-f['target'])**2 or
            f['absolute_loss']!=abs(p['prediction']-f['target'])):
            raise ValueError('feedback violates label availability or loss pairing')
    expected_feedback={(s,r['sample_id']) for r in rows[body['prediction_start']:]
        if r['label_available_time'] is not None and r['label_available_time']<=body['horizon'] for s in STRATEGIES}
    if feedback_keys!=expected_feedback:raise ValueError('incomplete or unfair feedback pairing')
    rank={s:i for i,s in enumerate(STRATEGIES)}
    if body['feedback']!=sorted(body['feedback'],key=lambda f:(f['label_time'],
              source[f['sample_id']]['decision_time'],rank[f['strategy']])):
        raise ValueError('feedback order differs from arrival sequence')
    # Reconstruct the fixed-monitor alarms and ordered update schedule from
    # information available at each step, rather than trusting a self-hash.
    events=body['events']; cursor=0; versions={s:0 for s in STRATEGIES}
    for s in STRATEGIES:
        if cursor>=len(events):raise ValueError('missing initial activation')
        m=events[cursor]; cursor+=1
        if (m.get('event_type')!='activation' or m.get('strategy')!=s or m.get('version')!=0 or
            m.get('retrain_time')!=warmup-1 or m.get('model_activation_time')!=warmup or
            m.get('reason')!='initial' or m.get('alarm_time') is not None):
            raise ValueError('initial model event mismatch')
    initial_fixed=models[dataset+':fixed_ridge:v0']
    rx=[r['features'][0] for r in rows[:warmup]]
    refmean=math.fsum(rx)/len(rx); refvar=math.fsum((x-refmean)**2 for x in rx)/len(rx)
    if refvar<=1e-12:raise ValueError('invalid reference variance')
    def initial_prediction(r):
        if initial_fixed['kind']=='ridge':return predict_ridge(Model(**initial_fixed['value']),r['features'])
        return initial_fixed['value']
    residual=(math.fsum((r['target']-initial_prediction(r))**2 for r in initial)/len(initial)) if initial else 1.0
    residual=max(residual,1e-9)
    fixed_feedback=sorted((f for f in body['feedback'] if f['strategy']=='fixed_ridge'),
                          key=lambda f:(f['label_time'],source[f['sample_id']]['decision_time']))
    last_adaptive=-10**9
    for t in range(warmup,body['horizon']+1):
        if t%CONFIG['check_interval']!=0:continue
        w=[r['features'][0] for r in rows[warmup:t+1]][-CONFIG['monitor_window']:]
        mean_alarm=variance_alarm=False
        if len(w)>=CONFIG['monitor_window']:
            avg=math.fsum(w)/len(w); var=math.fsum((x-avg)**2 for x in w)/len(w)
            mean_alarm=abs(avg-refmean)/math.sqrt(refvar)>=CONFIG['mean_threshold']
            variance_alarm=var<=1e-12 or max(var/refvar,refvar/var)>=CONFIG['variance_threshold']
        labeled=[f for f in fixed_feedback if f['label_time']<=t][-CONFIG['monitor_window']:]
        ready=len(labeled)>=CONFIG['monitor_window']
        relation=ready and math.fsum(f['squared_loss'] for f in labeled)/len(labeled)>=CONFIG['residual_threshold']*residual
        alarm=bool(mean_alarm or variance_alarm or relation)
        expected_alarm={'event_type':'alarm','decision_time':t,'alarm_time':t,
            'mean_alarm':mean_alarm,'variance_alarm':variance_alarm,
            'relationship_status':'available' if ready else 'unavailable',
            'relationship_alarm':bool(relation) if ready else None,'alarm':alarm,
            'available_labels':len(arrived(t)),
            'max_label_time':max((source[f['sample_id']]['label_available_time'] for f in labeled),default=None)}
        if cursor>=len(events) or events[cursor]!=expected_alarm:raise ValueError('alarm evidence differs from observed history')
        cursor+=1
        wanted={'mean':True,'periodic_ridge':t%CONFIG['periodic_interval']==0,
                'adaptive':alarm and t-last_adaptive>=CONFIG['cooldown']}
        for s in ('mean','periodic_ridge','adaptive'):
            if not wanted[s]:continue
            if cursor>=len(events):raise ValueError('missing scheduled update')
            m=events[cursor];cursor+=1;versions[s]+=1
            expected_reason={'mean':'periodic_mean','periodic_ridge':'periodic_ridge','adaptive':'drift_triggered'}[s]
            if (m.get('event_type')!='activation' or m.get('strategy')!=s or m.get('version')!=versions[s] or
                m.get('retrain_time')!=t or m.get('model_activation_time')!=t+1 or
                m.get('alarm_time')!=(t if s=='adaptive' else None) or
                m.get('reason') not in (expected_reason,expected_reason+':fallback_mean') or
                (s=='mean' and m.get('reason')!=expected_reason) or
                (m.get('kind')=='mean' and s!='mean' and m.get('reason')!=expected_reason+':fallback_mean')):
                raise ValueError('model update order, trigger or cooldown mismatch')
            if s=='adaptive':last_adaptive=t
    if cursor!=len(events):raise ValueError('unexpected or out-of-order event')
    if body['censored_prediction_samples']!=len(rows)-body['prediction_start']-len(expected_feedback)//len(STRATEGIES):
        raise ValueError('censor count differs from actual arrived labels')
    for s in STRATEGIES:
        f=[v for v in body['feedback'] if v['strategy']==s]; n=len(f); metric=body['metrics'][s]
        updates=[m for m in activations if m['strategy']==s and m['version']>0]
        cost=len(updates)*CONFIG['update_penalty']
        if (metric['n_scored']!=n or metric['mae']!=(math.fsum(v['absolute_loss'] for v in f)/n if n else None) or
            metric['rmse']!=(math.sqrt(math.fsum(v['squared_loss'] for v in f)/n) if n else None) or
            metric['updates']!=len(updates) or metric['penalty']!=cost or
            metric['completed_candidate_fit_count']!=sum(m['training'].get('candidate_fits',0) for m in updates) or
            metric['failed_fit_attempts']!=sum('fallback_mean' in m['reason'] for m in updates) or
            metric['penalized_mean_squared_loss']!=((math.fsum(v['squared_loss'] for v in f)+cost)/n if n else None)):
            raise ValueError('metric does not match recorded feedback')
    return True
