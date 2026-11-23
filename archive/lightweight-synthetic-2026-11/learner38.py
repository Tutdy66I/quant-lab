"""Multivariate packets for the three frozen learners."""
from dataclasses import asdict

from engine_five34 import BASELINES, CONFIG, _score
from features38 import CUTOFF, STOP, WARMUP, check_features
from prequential_synthetic31 import _fit, _mean, _mean_evidence, _version
from quality35 import check_label, check_split, finite, identity


def fit_version(available, dataset, arm, strategy, version, tick):
    subset=available[-CONFIG["train_window"]:] if version==0 else available
    kind,status,value,evidence="unavailable","no_labels",None,{}
    if subset:
        kind,status,value,evidence="mean","mean",_mean(subset),_mean_evidence(subset)
    if strategy!="mean":
        try:
            ridge,evidence=_fit(subset,tick)
            kind,status,value="ridge","ridge",asdict(ridge)
        except (ValueError,ArithmeticError):
            status="fallback_mean" if subset else "no_labels"
    model=_version(f"{dataset}|{arm}",strategy,version,kind,value,tick,tick+1,evidence,status)
    return dict(model,active=kind!="unavailable",train_available=len(subset),fit_status=status)


def run(dataset,arm,columns,events):
    """The learner receives packet and arrival iterators, not target or feature truth tables."""
    if not isinstance(dataset,str) or not dataset.startswith("synthetic38:"):
        raise ValueError("unregistered research dataset")
    arrived,seen,models={}, {}, {}
    available,updates,forecasts,decisions,labels,packet_log=[],[],[],[],[],[]
    events=iter(events)
    for tick in range(CUTOFF+1):
        event=next(events,None)
        if not isinstance(event,tuple) or len(event)!=3 or type(event[0]) is not int or event[0]!=tick:
            raise ValueError("missing, duplicate or out-of-order decision")
        _,packet,batch=event
        if not isinstance(batch,tuple):
            raise ValueError("invalid feedback transport")
        if tick<STOP:
            quality,values=check_features(packet,dataset,tick,columns)
            sid=identity(dataset,tick)
            seen[sid]=(quality,values)
            packet_log.append([asdict(feature) for feature in packet])
            if tick>=WARMUP:
                decisions.append(dict(tick=tick,sample=sid,quality=quality,train_available=len(available)))
                for s in BASELINES:
                    model=models[s]
                    permitted=quality=="valid" and model["active"]
                    if model["model_activation_time"]>tick:
                        raise ValueError("model not yet active")
                    y=_score(model,values) if permitted else None
                    if permitted and not finite(y):
                        raise ValueError("nonfinite forecast")
                    forecasts.append(dict(tick=tick,sample=sid,strategy=s,prediction=y,
                                          model_id=model["model_id"],model_kind=model["kind"],
                                          quality=quality,status=("valid" if permitted else
                                                            "unavailable" if quality=="valid" else quality)))
        elif packet is not None:
            raise ValueError("unexpected feature after evaluation clock")
        for label in batch:
            check_label(label,dataset,tick,seen,arrived)
            quality,values=seen[label.sample]
            arrived[label.sample]=asdict(label)
            labels.append(dict(asdict(label),received=tick,eligible=quality=="valid"))
            if quality=="valid":
                available.append(dict(sample_id=label.sample,decision_time=label.tick,features=values,
                                      target_end_time=label.target_end,label_available_time=tick,target=label.target))
        updating=()
        if tick==WARMUP-1:
            check_split([r["decision_time"] for r in available], range(WARMUP,STOP),WARMUP)
            available.sort(key=lambda r:r["decision_time"])
            updating=BASELINES
        if WARMUP<=tick<STOP and tick%CONFIG["check_interval"]==0:
            updating=("mean","periodic_ridge") if tick%CONFIG["periodic_interval"]==0 else ("mean",)
        for s in updating:
            version=models[s]["version"]+1 if s in models else 0
            models[s]=fit_version(available,dataset,arm,s,version,tick)
            updates.append(models[s])
    end=object()
    if next(events,end) is not end:
        raise ValueError("unexpected trailing packet")
    return dict(dataset=dataset,arm=arm,columns=columns,features=packet_log,
                releases=labels,decisions=decisions,forecasts=forecasts,updates=updates)

