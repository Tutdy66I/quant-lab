"""Preregistered synthetic holdout: preparation only unless explicitly consumed.

Local exclusive-create ledger guards accidental repeats, not hostile rollback.
The externally approved seal must come from a genuinely independent custodian;
matching hashes alone cannot establish that trust. No network or market data.
"""
from pathlib import Path
import hashlib
import json
import math
import os
import random
import ridge_synthetic

APPROVAL = 'AUTHORIZE_SYNTHETIC_HOLDOUT_ONCE'
ALPHAS = (0.01, 0.1, 1.0, 10.0)
FINAL_PLAN = {'version':'synthetic-holdout-v1', 'dataset_id':'synthetic-holdout-v1-20261114',
    'seed':20261114, 'total':360, 'train':[0,120], 'tune':[120,180],
    'development_validation':[180,300], 'holdout':[300,360], 'gap_ticks':1,
    'generation':'independent-gaussian-linear-v1', 'noise_std':0.5,
    'features':'(x_t,previous_y)', 'target':'0.7*x_t+epsilon_t',
    'feature_available':'2*t', 'target_interval':'[2*t,2*t+2)',
    'alpha_selection':'minimum tune MSE, lower alpha on tie; refit none',
    'alphas':list(ALPHAS), 'metrics':['MAE','RMSE','paired squared loss difference'],
    'interpretation':'descriptive synthetic only; no market inference',
    'success':'valid execution, exact identities and all declared metrics finite',
    'failure':'integrity or predeclared execution contract violation',
    'inconclusive':'missing trust anchor, incomplete evidence or unavailable run'}


def _canon(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')

def _sha(x):
    return hashlib.sha256(_canon(x)).hexdigest()

def _sources():
    paths = [Path(__file__), Path(ridge_synthetic.__file__)]
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

def validate_plan(plan):
    if not isinstance(plan,dict) or plan.get('version') != 'synthetic-holdout-v1':
        raise ValueError('invalid plan')
    varying = {'dataset_id', 'seed', 'total', 'train', 'tune', 'development_validation', 'holdout'}
    if set(plan) != set(FINAL_PLAN) or any(plan[k] != v for k,v in FINAL_PLAN.items() if k not in varying):
        raise ValueError('predeclared methods, metrics or criteria differ from implementation')
    if plan.get('generation') != 'independent-gaussian-linear-v1' or plan.get('alphas') != list(ALPHAS):
        raise ValueError('unapproved generation or alpha selection')
    if type(plan.get('seed')) is not int or type(plan.get('total')) is not int:
        raise ValueError('integer seed and total required')
    if plan.get('gap_ticks') != 1 or plan.get('noise_std') != .5:
        raise ValueError('unexpected gap or noise')
    names=('train','tune','development_validation','holdout')
    intervals=[plan.get(n) for n in names]
    if any(not isinstance(x,list) or len(x)!=2 or any(type(v) is not int for v in x) for x in intervals):
        raise ValueError('valid intervals required')
    if not (intervals[0][0] == 0 and all(a[0]<a[1] for a in intervals) and
            all(intervals[i][1]==intervals[i+1][0] for i in range(3)) and
            intervals[-1][1]==plan['total'] and intervals[0][1]-intervals[0][0]>=5 and
            intervals[1][1]-intervals[1][0]>=3 and intervals[3][1]-intervals[3][0]>=3):
        raise ValueError('split order/size invalid')
    if not isinstance(plan.get('dataset_id'),str) or not plan['dataset_id']:
        raise ValueError('dataset_id required')

def generate(plan):
    """Generate fixed synthetic data; no prediction, loss or metrics are computed."""
    validate_plan(plan)
    rng=random.Random(plan['seed']); previous=0.0; rows=[]
    for t in range(plan['total']):
        x=rng.gauss(0,1); y=.7*x+rng.gauss(0,.5)
        sid=hashlib.sha256((plan['dataset_id']+'\0'+str(t)).encode()).hexdigest()
        rows.append({'sample_id':sid,'ordinal':t,'decision_time':2*t,
                     'feature_available_time':2*t,'target_start_time':2*t,
                     'target_end_time':2*t+2,'features':[x,previous],
                     'target':y})
        previous=y
    return rows

def make_seal(plan,rows):
    """Only hashes and descriptions: never calculate holdout metrics."""
    validate_plan(plan)
    if rows != generate(plan): raise ValueError('dataset does not match frozen generator')
    start,stop=plan['holdout']; holdout=rows[start:stop]
    manifest={'version':'synthetic-holdout-manifest-v1','dataset_id':plan['dataset_id'],
       'count':len(holdout),'samples':[{k:r[k] for k in ('sample_id','ordinal','decision_time',
           'feature_available_time','target_start_time','target_end_time')} for r in holdout]}
    return {'seal_version':'synthetic-holdout-seal-v1','plan':plan,
            'plan_sha256':_sha(plan),'dataset_sha256':_sha(rows),
            'holdout_data_sha256':_sha(holdout),'holdout_manifest':manifest,
            'holdout_manifest_sha256':_sha(manifest),'source_sha256':_sources(),
            'status':'unopened','metrics_computed':False,
            'trust':'external approval required; local files are not an independent trust root'}

def evaluate_once(plan,rows,seal,expected_seal_sha256,receipt_path,approval):
    """Consume before scoring. Receipt must be in an externally protected store.

    Calling this function is the explicit opening action. A same-user writable
    receipt may be removed or replaced; this provides no tamper-proof ledger.
    If scoring fails after consumption it remains consumed, not silently retried.
    """
    if approval != APPROVAL or not isinstance(expected_seal_sha256,str) or len(expected_seal_sha256)!=64:
        raise ValueError('explicit approval and external seal anchor required')
    if _sha(seal)!=expected_seal_sha256 or make_seal(plan,rows)!=seal:
        raise ValueError('seal, source or dataset mismatch')
    # Exclusive creation is the irreversible logical opening point. A durable,
    # independent store is required for an actual one-shot trust guarantee.
    fd=os.open(str(receipt_path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as fp:
        json.dump({'status':'consumed','seal_sha256':expected_seal_sha256,
                   'meaning':'opening attempted; result may be incomplete'},fp,sort_keys=True)
        fp.flush(); os.fsync(fp.fileno())
    tr=rows[plan['train'][0]:plan['train'][1]-1]
    tu=rows[plan['tune'][0]:plan['tune'][1]-1]
    ho=rows[plan['holdout'][0]:plan['holdout'][1]]
    if tr[-1]['target_end_time']>tu[0]['decision_time']-1 or tu[-1]['target_end_time']>rows[plan['development_validation'][0]]['decision_time']-1:
        raise ValueError('training or tuning target crosses boundary')
    # Purge final development row to maintain the predeclared one-tick gap.
    if rows[plan['holdout'][0]-2]['target_end_time']>ho[0]['decision_time']-1:
        raise ValueError('development label crosses holdout boundary')
    x=[r['features'] for r in tr]; y=[r['target'] for r in tr]
    candidates=[(a,ridge_synthetic.fit_ridge(x,y,a)) for a in ALPHAS]
    errors=[(math.fsum((ridge_synthetic.predict_ridge(m,r['features'])-r['target'])**2 for r in tu)/len(tu),a,m) for a,m in candidates]
    _,alpha,model=min(errors,key=lambda q:(q[0],q[1]))
    mean=math.fsum(y)/len(y); by_model={'mean':[], 'persistence':[], 'ridge':[]}
    for row in ho:
        for name,pred in (('mean',mean),('persistence',row['features'][1]),('ridge',ridge_synthetic.predict_ridge(model,row['features']))):
            by_model[name].append((pred-row['target'])**2)
    out={name:{'rmse':math.sqrt(math.fsum(v)/len(v)),'mae':math.fsum(math.sqrt(z) for z in v)/len(v)} for name,v in by_model.items()}
    return {'alpha':alpha,'metrics':out,'paired_ridge_minus_mean_mse':math.fsum(a-b for a,b in zip(by_model['ridge'],by_model['mean']))/len(ho),
            'count':len(ho),'seal_sha256':expected_seal_sha256,'status':'consumed'}


