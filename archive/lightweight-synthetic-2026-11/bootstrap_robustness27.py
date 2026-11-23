"""Predeclared bounded synthetic AR(1) coverage matrix; no holdout inputs.

Each cell has an independent, hash-derived random stream including effect;
methods within one cell deliberately share the same underlying observations.
Identity/order checks occur before every resampling call. This module cannot
independently authenticate its same-process generated reference samples.
"""
import hashlib
import json
import math
import random
from coverage_synthetic import paired_window_bootstrap, _wilson
from sample_lineage import Sample, Prediction, make_losses, sample_id_for, prediction_id_for


def _digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def derive_seed(*labels):
    return int(_digest(list(labels))[:16],16)


def make_synthetic_case(*, n, phi, effect, noise, seed):
    if type(n) is not int or n < 20 or type(seed) is not int:
        raise ValueError('n and seed invalid')
    if not (0 <= phi < .95 and abs(effect) <= 1 and .01 <= noise <= .4):
        raise ValueError('synthetic DGP outside fixed bounds')
    cfg={'version':'ar1-bounded-v2','n':n,'phi':phi,'effect':effect,'noise':noise,'seed':seed,'burnin':200,'base_loss':8.0}
    cfg_hash=_digest(cfg)
    rng=random.Random(seed); x=0.0; differences=[]
    for t in range(n+200):
        x=phi*x+rng.uniform(-noise,noise)
        if t>=200: differences.append(effect+x)
    dataset_id='synthetic-ar1-v2:'+cfg_hash
    fingerprint=_digest({'config':cfg,'differences':differences})
    samples=[]; pa=[]; pb=[]
    for t,diff in enumerate(differences):
        sid=sample_id_for(dataset_id,fingerprint,t)
        s=Sample(sid,dataset_id,fingerprint,cfg_hash,10*t,10*t,10*t,10*t+1,0.0,'validation','v2-window',t)
        samples.append(s)
        pa.append(Prediction(prediction_id_for(sid,'v2-window','a',cfg_hash),sid,dataset_id,'v2-window','a',math.sqrt(8+diff)))
        pb.append(Prediction(prediction_id_for(sid,'v2-window','b',cfg_hash),sid,dataset_id,'v2-window','b',math.sqrt(8)))
    common={'expected_dataset_id':dataset_id,'expected_dataset_fingerprint':fingerprint,'expected_config_hash':cfg_hash,'split_id':'validation','window_id':'v2-window'}
    la=make_losses(samples,pa,model_id='a',**common); lb=make_losses(samples,pb,model_id='b',**common)
    return samples,la,lb,{**common,'model_a':'a','model_b':'b'},cfg


def evaluate_cell(*,n,phi,effect,noise,seed,trials=20,replicates=100,block_lengths=(3,5,8),confidence=.95):
    if type(trials) is not int or trials < 20 or type(replicates) is not int or replicates < 100:
        raise ValueError('minimum trial or bootstrap count not met')
    if len(set(block_lengths)) != len(block_lengths) or not block_lengths or any(type(k) is not int or k < 1 or n//k < 5 for k in block_lengths):
        raise ValueError('invalid predeclared blocks')
    config={'n':n,'phi':phi,'effect':effect,'noise':noise,'seed':seed,'trials':trials,'replicates':replicates,'block_lengths':list(block_lengths),'confidence':confidence}
    stats={key:{'covered':0,'widths':[],'biases':[],'lag1':[]} for key in ['iid']+[f'block_{x}' for x in block_lengths]}
    records=[]
    for trial in range(trials):
        trial_seed=derive_seed('dgp',seed,n,phi,effect,noise,trial)
        samples,la,lb,ctx,_=make_synthetic_case(n=n,phi=phi,effect=effect,noise=noise,seed=trial_seed)
        record={'dgp_seed':trial_seed,'dataset_id':ctx['expected_dataset_id'],'dataset_fingerprint':ctx['expected_dataset_fingerprint'],'sample_ids':[s.sample_id for s in samples],'methods':{}}
        for method,block in [('iid',None)]+[('moving_block',k) for k in block_lengths]:
            key='iid' if method=='iid' else f'block_{block}'
            bootstrap_seed=derive_seed('bootstrap',seed,n,phi,effect,noise,trial,key)
            out=paired_window_bootstrap(samples,la,lb,**ctx,spacing=10,method=method,block_length=block,replicates=replicates,seed=bootstrap_seed,confidence=confidence)
            lo,hi=out['interval']; st=stats[key]
            st['covered']+=lo<=effect<=hi; st['widths'].append(hi-lo); st['biases'].append(out['mean_difference']-effect); st['lag1'].append(out['lag1_correlation'])
            record['methods'][key]={'seed':bootstrap_seed,'interval':[lo,hi],'mean_difference':out['mean_difference'],'lag1':out['lag1_correlation']}
        records.append(record)
    summary={}
    for key,s in stats.items():
        summary[key]={'covered':s['covered'],'total':trials,'coverage':s['covered']/trials,'wilson_95':_wilson(s['covered'],trials),'mean_width':math.fsum(s['widths'])/trials,'mean_bias':math.fsum(s['biases'])/trials,'mean_lag1':math.fsum(s['lag1'])/trials}
    return {'configuration':config,'config_sha256':_digest(config),'nominal_confidence':confidence,'methods':summary,'trials':records,'window_overlap':False,'scope':'synthetic_not_market_inference'}


def execute_matrix(config):
    expected={'version':'coverage-v2','sample_lengths':[40,100],'ar1':[0.0,0.5,0.8],'effects':[0.0,0.2],'noise_scales':[0.1,0.3],'master_seeds':[20261113,20261114],'trials':20,'replicates':100,'block_lengths':[3,5,8],'confidence':0.95}
    if any(config.get(k)!=v for k,v in expected.items()):
        raise ValueError('only predeclared bounded matrix supported')
    results=[]
    for n in config['sample_lengths']:
        for phi in config['ar1']:
            for effect in config['effects']:
                for noise in config['noise_scales']:
                    for seed in config['master_seeds']:
                        results.append(evaluate_cell(n=n,phi=phi,effect=effect,noise=noise,seed=seed,trials=config['trials'],replicates=config['replicates'],block_lengths=tuple(config['block_lengths']),confidence=config['confidence']))
    return {'config':config,'config_sha256':_digest(config),'cells':results,'limitations':'Independent pseudo-random streams are hash-derived; methods share each trial. Pointwise Wilson intervals not adjusted for 48 comparisons. Synthetic bounded stationary AR(1) only; no general or market guarantee.'}
