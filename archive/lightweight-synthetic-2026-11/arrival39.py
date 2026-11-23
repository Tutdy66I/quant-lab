"""Append-only point-in-time feature transport and frozen three-model adapter."""
from dataclasses import asdict, dataclass

from engine_five34 import BASELINES, CONFIG, _score
from learner38 import fit_version
from quality35 import check_label, check_split, finite, identity

STOP, WARMUP, CUTOFF = 180, 60, 181
VERSION = "feature39-v1"
SOURCE = "synthetic-exogenous"
COLUMN_GROUP = {"x":"x", "g1":"candidate", "g2":"candidate"}


@dataclass(frozen=True)
class Reading:
    feature_id: str
    feature_version: str
    sample_id: str
    observation_time: int
    available_time: int
    synthetic_dataset_id: str
    source_identity: str
    feature_group_id: str
    revision: int
    value: float
    underlying_observation_time: object = None


def check_arrival(event, dataset, tick, columns, previous):
    if (not isinstance(event,Reading) or event.feature_id not in columns
            or event.feature_version!=VERSION or event.synthetic_dataset_id!=dataset
            or event.source_identity!=SOURCE or event.feature_group_id!=COLUMN_GROUP[event.feature_id]
            or type(event.observation_time) is not int or not 0<=event.observation_time<STOP
            or type(event.available_time) is not int or event.available_time!=tick
            or event.observation_time>tick or event.sample_id!=identity(dataset,event.observation_time)
            or (event.underlying_observation_time is not None and (type(event.underlying_observation_time) is not int or event.feature_id=="x" or event.underlying_observation_time!=event.observation_time-12))
            or type(event.revision) is not int or event.revision<0 or not finite(event.value)):
        raise ValueError("invalid feature identity, version, origin, availability or value")
    if previous is None:
        if event.revision!=0:
            raise ValueError("revision has no original receipt")
    elif event.revision!=previous.revision+1 or tick<=previous.available_time:
        raise ValueError("duplicate, out-of-order or unbound revision")


def snapshot(latest, columns, now, ttl):
    stamps=[latest.get(name) for name in columns]
    ages=[now-r.observation_time if r else None for r in stamps]
    availability=[now-r.available_time if r else None for r in stamps]
    if any(a is not None and (a<0 or b<0) for a,b in zip(ages,availability)):
        raise ValueError("future reading selected for decision")
    if any(a is None for a in ages):
        return "unavailable","not_delivered",None,ages,stamps
    if any(a>ttl for a in ages):
        return "unavailable","past_acceptance_age",None,ages,stamps
    return "valid","accepted",tuple(r.value for r in stamps),ages,stamps


def run(dataset, arm, columns, ttl, events):
    """At t: receive features, issue immutable forecast, receive labels, train for t+1."""
    if (not isinstance(dataset,str) or not dataset.startswith("synthetic39:")
            or not isinstance(columns,tuple) or not columns or len(columns)!=len(set(columns))
            or set(columns)-COLUMN_GROUP.keys() or ttl not in (0,6)):
        raise ValueError("unregistered feature or time contract")
    history,latest,seen,arrived,models={},{},{},{},{}
    eligible,all_labels=[],[]
    releases,features,decisions,forecasts,updates=[],[],[],[],[]
    iterator=iter(events)
    for tick in range(CUTOFF+1):
        event=next(iterator,None)
        if not isinstance(event,tuple) or len(event)!=3 or type(event[0]) is not int or event[0]!=tick:
            raise ValueError("invalid decision clock")
        _,packets,labels=event
        if not isinstance(packets,tuple) or not isinstance(labels,tuple):
            raise ValueError("expected frozen arrival packets")
        for packet in packets:
            if not isinstance(packet,Reading):
                raise ValueError("invalid feature packet")
            key=(packet.feature_id,packet.observation_time)
            check_arrival(packet,dataset,tick,columns,history.get(key))
            history[key]=packet
            head=latest.get(packet.feature_id)
            if head is None or packet.observation_time>=head.observation_time:
                latest[packet.feature_id]=packet
            features.append(dict(asdict(packet),expired_on_arrival=tick-packet.observation_time>ttl))
        if tick<STOP:
            quality,reason,vector,ages,stamps=snapshot(latest,columns,tick,ttl)
            sample=identity(dataset,tick)
            seen[sample]=vector
            decisions.append(dict(tick=tick,sample=sample,quality=quality,reason=reason,ages=ages,
                                  features=vector,feature_sources=[
                                    dict(feature_id=r.feature_id,feature_version=r.feature_version,
                                         sample_id=r.sample_id,observation_time=r.observation_time,
                                         available_time=r.available_time,decision_time=tick,revision=r.revision,
                                         synthetic_dataset_id=r.synthetic_dataset_id,
                                         source_identity=r.source_identity,feature_group_id=r.feature_group_id,
                                         underlying_observation_time=r.underlying_observation_time)
                                    if r else None for r in stamps]))
            if tick>=WARMUP:
                for name in BASELINES:
                    model=models[name]
                    if model["model_activation_time"]>tick:
                        raise ValueError("model is not yet active")
                    prediction=_score(model,vector) if vector is not None and model["active"] else None
                    if prediction is not None and not finite(prediction):
                        raise ValueError("nonfinite forecast")
                    forecasts.append(dict(tick=tick,sample=sample,strategy=name,prediction=prediction,
                                          model_id=model["model_id"],model_kind=model["kind"]))
        for label in labels:
            check_label(label,dataset,tick,seen,arrived)
            arrived[label.sample]=asdict(label)
            vector=seen[label.sample]
            releases.append(dict(asdict(label),received=tick,valid_ridge_row=vector is not None))
            row=dict(sample_id=label.sample,decision_time=label.tick,features=vector,
                     target_end_time=label.target_end,label_available_time=tick,target=label.target)
            all_labels.append(row)
            if vector is not None:
                eligible.append(row)
        updating=()
        if tick==WARMUP-1:
            check_split([r["decision_time"] for r in all_labels],range(WARMUP,STOP),WARMUP)
            for rows in (eligible,all_labels):
                rows.sort(key=lambda r:r["decision_time"])
            updating=BASELINES
        if WARMUP<=tick<STOP and tick%CONFIG["check_interval"]==0:
            updating=("mean","periodic_ridge") if tick%CONFIG["periodic_interval"]==0 else ("mean",)
        for name in updating:
            version=models[name]["version"]+1 if name in models else 0
            rows=all_labels if name=="mean" else eligible
            models[name]=fit_version(rows,dataset,arm,name,version,tick)
            updates.append(models[name])
    end=object()
    if next(iterator,end) is not end:
        raise ValueError("unexpected trailing packet")
    return dict(dataset=dataset,arm=arm,columns=columns,ttl=ttl,decisions=decisions,
                features=features,releases=releases,forecasts=forecasts,updates=updates)

