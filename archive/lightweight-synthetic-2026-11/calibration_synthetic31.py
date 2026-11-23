"""Predeclared synthetic alarm calibration, not a market or significance claim."""
import math
import random

import drift_synthetic30 as drift

CONFIG = {'version':'synthetic-calibration-v1','development_seeds':list(range(31000,31040)),
          'report_seeds':list(range(41000,41100)), 'reference_sizes':[40,100],
          'window_sizes':[20,40], 'pre_windows':4, 'post_windows':5,
          'candidates':[1.0,1.25,1.5], 'family_alarm_goal':0.10,
          'sensitivity':[0.8,1.0,1.25], 'label_delay':1,
          'scenarios':{'stable':0.0,'mean_weak':0.4,'mean_strong':1.2,
                       'variance_weak':1.35,'variance_strong':2.5,
                       'relation_weak':0.5,'relation_strong':-1.0},
          'resource_cap':'40 dev + 100 report independent trials per cell; 4 reference/window cells'}


def wilson(hits,n):
    if type(hits) is not int or type(n) is not int or n<1 or not 0<=hits<=n: raise ValueError('invalid binomial count')
    z=1.959963984540054; p=hits/n; den=1+z*z/n
    c=(p+z*z/(2*n))/den; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0.0,c-h),min(1.0,c+h)]


def generate(scenario, *, seed, reference_size, window_size):
    if scenario not in CONFIG['scenarios'] or type(seed) is not int or reference_size not in CONFIG['reference_sizes'] or window_size not in CONFIG['window_sizes']:
        raise ValueError('unapproved synthetic scenario or configuration')
    change=reference_size+CONFIG['pre_windows']*window_size
    count=change+CONFIG['post_windows']*window_size
    rng=random.Random(seed); result=[]
    for t in range(count):
        x=rng.gauss(0,1); noise=rng.gauss(0,.5); beta=1.0
        if t>=change:
            if scenario.startswith('mean_'): x+=CONFIG['scenarios'][scenario]
            if scenario.startswith('variance_'): x*=CONFIG['scenarios'][scenario]
            if scenario.startswith('relation_'): beta=CONFIG['scenarios'][scenario]
        result.append({'decision_time':t,'feature_available_time':t,'label_available_time':t+1,
                       'x':x,'y':beta*x+noise})
    return result,change


def _stats(seed,scenario,reference_size,window_size):
    rows,change=generate(scenario,seed=seed,reference_size=reference_size,window_size=window_size)
    reference=drift.fit_reference(rows[:reference_size],as_of_time=reference_size)
    values=[]
    for i in range(CONFIG['pre_windows']+CONFIG['post_windows']):
        start=reference_size+i*window_size; window=rows[start:start+window_size]
        observed=drift.detect_drift(reference,window,as_of_time=window[-1]['label_available_time'])
        values.append(observed)
    return values,change


def _alarm(value, multiplier):
    if not isinstance(multiplier,(int,float)) or isinstance(multiplier,bool) or not math.isfinite(multiplier) or multiplier<=0: raise ValueError('invalid threshold factor')
    v=value['variance_ratio']; rel=value['relationship']
    return {'mean':value['mean_effect']>=drift.THRESHOLDS['mean_standardized']*multiplier,
            'variance':v is None or v>=drift.THRESHOLDS['variance_ratio']**multiplier,
            'relation':rel['status']=='available' and rel['slope_effect']>=drift.THRESHOLDS['slope_standardized']*multiplier}


def _summarize(records,axis, window_size):
    n=len(records); pre=CONFIG['pre_windows']; post=CONFIG['post_windows']
    def estimate(h): return {'count':h,'n':n,'rate':h/n,'wilson95':wilson(h,n)}
    first=sum(any(x.values()) for x in (r['pre'][0] for r in records))
    multi=sum(any(any(x.values()) for x in r['pre']) for r in records)
    if axis is None:
        return {'single_window_any_alarm':estimate(first),'four_window_any_alarm':estimate(multi),
                'post_windows_at_least_two_any_alarms':estimate(sum(sum(any(x.values()) for x in r['post'])>=2 for r in records)),
                'post_consecutive_any_alarms':estimate(sum(any(any(a.values()) and any(b.values()) for a,b in zip(r['post'],r['post'][1:])) for r in records))}
    detected=[]; censored=0; repeated=0; consecutive=0
    for row in records:
        indices=[i for i,x in enumerate(row['post']) if x[axis]]
        if indices: detected.append((indices[0]+1)*window_size)
        else: censored+=1
        repeated+=len(indices)>=2
        consecutive+=any(b==a+1 for a,b in zip(indices,indices[1:]))
    return {'single_window_any_alarm':estimate(first),'four_window_any_alarm':estimate(multi),
            'post_relevant_detection':estimate(len(detected)), 'undetected':estimate(censored),
            'post_repeated_relevant_alarm':estimate(repeated),
            'post_consecutive_relevant_alarm':estimate(consecutive),
            'detected_delay_ticks':detected,'delay_mean_detected_only':sum(detected)/len(detected) if detected else None,
            'censor_at_ticks':post*window_size,
            'delay_note':'includes the completed observation window and label availability; censored trials are separately counted'}


def run_calibration():
    """Freeze a multiplier on development seeds; evaluate untouched report seeds.

    Common random numbers across scenario variants give paired exploratory
    comparisons, not independent scenario experiments. The independent unit
    for the Wilson interval is one random-seed trial, not each time window.
    """
    if set(CONFIG['development_seeds']) & set(CONFIG['report_seeds']): raise ValueError('seed sets overlap')
    cells=[]
    for ref in CONFIG['reference_sizes']:
      for size in CONFIG['window_sizes']:
        dev=[_stats(seed,'stable',ref,size)[0] for seed in CONFIG['development_seeds']]
        choices=[]
        for mult in CONFIG['candidates']:
            count=sum(any(any(_alarm(v,mult).values()) for v in trial[:CONFIG['pre_windows']]) for trial in dev)
            choices.append({'multiplier':mult,'family_alarms':count,'n':len(dev),'wilson95':wilson(count,len(dev))})
        acceptable=[c for c in choices if c['family_alarms']/c['n']<=CONFIG['family_alarm_goal']]
        chosen=acceptable[0]['multiplier'] if acceptable else choices[-1]['multiplier']
        for scenario in CONFIG['scenarios']:
          stats=[_stats(seed,scenario,ref,size)[0] for seed in CONFIG['report_seeds']]
          axis=None if scenario=='stable' else scenario.split('_')[0]
          for sensitivity in CONFIG['sensitivity']:
            factor=chosen*sensitivity
            records=[]
            for seed,trial in zip(CONFIG['report_seeds'],stats):
                alarms=[_alarm(x,factor) for x in trial]
                records.append({'seed':seed,'pre':alarms[:CONFIG['pre_windows']],
                                'post':alarms[CONFIG['pre_windows']:]})
            cells.append({'reference_size':ref,'window_size':size,'scenario':scenario,'axis':axis,
                          'dev_choices':choices,'dev_goal_met':bool(acceptable),'selected_multiplier':chosen,
                          'sensitivity_multiplier':sensitivity,'effective_multiplier':factor,
                          'summary':_summarize(records,axis,size),'trials':records})
    return {'config':CONFIG,'cells':cells,'limitations':[
      'Effect-size alarms are not formal calibrated p-values.',
      'Fixed data-generating mechanism only; scenarios share random seeds.',
      'Wilson intervals reflect Monte Carlo trials, not uncertainty about real markets.',
      'Delayed labels and cross-window dependence affect operational latency.',
      'Chosen threshold only targets development empirical four-window family rate; no test guarantee.',
      'Detection delay among successes is conditional; censored trials retained explicitly.']}
