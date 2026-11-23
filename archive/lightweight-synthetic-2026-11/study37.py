"""Lock and run paired artificial publication research."""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parent
if __name__=="__main__":
    sys.path.insert(0,str(ROOT))
from engine_five34 import BASELINES, CONFIG
from missing36 import MAX_WEIGHT, MIN_ESS, MIN_P, MIN_STRATUM, RATIOS
from publication37 import (MIN_COUNT, MIN_COVERAGE, MIN_ESS_RATIO, STABILITY_BIN,
                            STABILITY_DRIFT, STABILITY_ERROR, THRESHOLDS, choose_winner)
from study34 import historical_seeds
from study36 import stats
from transfer37 import CENSUS, MODES, SCENARIOS, cases

SOURCES=("protocol37.json","publication37.py","transfer37.py","study37.py","test_publication37.py",
         "run_publication37.py","run_synthetic.py","round37-prior-runner36.txt","round36-evidence-manifest.json",
         "round36-lock.json","protocol36.json","study36.py","missing36.py","design36.py","qualification36.py",
         "baselines35.py","feedback35.py","quality35.py","evaluation35.py","engine_five34.py","engine_cross33.py",
         "prequential_synthetic31.py","ridge_synthetic.py","synthetic_manifest27.py","sample_lineage.py",
         "round34-lock.json","protocol34.json","protocol35.json","protocol33.json","study34.py",
         "round31-calibration-preconfig.json","round31-pressure-preconfig.json","pressure_protocol32.json",
         "round32-alarm-dev-results.json","round32-cluster-lock.json")
METHODS=("observed","ipw","oracle","truth")


def read(name):
    return json.loads((ROOT/name).read_bytes())


def sha(name):
    return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()


def dump(path,value):
    with path.open("x",encoding="utf8") as out:
        json.dump(value,out,allow_nan=False,sort_keys=True,separators=(",",":"))
        out.write("\n")


def freeze(config):
    prior=read("round36-evidence-manifest.json")["files"]
    protected=("round36-lock.json","engine_five34.py","missing36.py","qualification36.py","baselines35.py")
    forbidden=historical_seeds()|set(config["development_seeds"])
    for name in ("protocol33.json","protocol34.json","protocol35.json","protocol36.json"):
        previous=read(name)
        forbidden.update(previous.get("report_seeds",[]))
        forbidden.update(previous.get("development_seeds",[]))
    forbidden.update(read("round31-calibration-preconfig.json")["config"]["development_seeds"])
    forbidden.update(read("round32-alarm-dev-results.json")["development_seeds"])
    seeds=config["report_seeds"]
    params=json.loads(json.dumps(CONFIG))
    operational=config["operating"]
    ipw=config["ipw"]
    if (len(seeds)!=24 or len(set(seeds))!=24 or set(seeds)&forbidden
        or tuple(config["scenarios"])!=SCENARIOS or tuple(config["modes"])!=MODES
        or len(seeds)*len(SCENARIOS)*len(MODES)!=config["max_sequences"]
        or any(sha(name)!=prior[name] for name in protected)
        or sha("round37-prior-runner36.txt")!=prior["run_synthetic.py"]
        or params!=read("round34-lock.json")["engine_config"]
        or tuple(map(tuple,config["qualifying_thresholds"]))!=THRESHOLDS
        or (operational["min"],operational["coverage"],operational["ess_fraction"],
            operational["calibration_error"],operational["time_residual_shift"],operational["min_diagnostic_bin"])
           !=(MIN_COUNT,MIN_COVERAGE,MIN_ESS_RATIO,STABILITY_ERROR,STABILITY_DRIFT,STABILITY_BIN)
        or (ipw["minimum_p"],ipw["ess"],ipw["clipping"],ipw["min_train_bin"])!=(MIN_P,MIN_ESS,MAX_WEIGHT,MIN_STRATUM)
        or tuple(config["sensitivity"]["ratio"])!=RATIOS):
        raise ValueError("inconsistent configuration, baseline, workload or report seeds")
    return dict(config=config,baseline=params,sources={f:sha(f) for f in SOURCES})


def measurement(arm,strategy,method):
    value=arm["models"][strategy][method]
    if method in ("ipw","oracle"):
        return value["estimate"] if value else None
    return value


def ordered(values):
    if any(v is None for v in values.values()):
        return None
    order=sorted(values,key=values.get)
    if any(abs(values[a]-values[b])<1e-12 for a,b in zip(order,order[1:])):
        return None
    return order


def error_stats(values,truth,critical):
    errors=[v-y if v is not None else None for v,y in zip(values,truth)]
    return dict(values=stats(values,critical),error=stats(errors,critical),
                rmse=math.sqrt(statistics.mean(e*e for e in errors if e is not None))
                if any(e is not None for e in errors) else None)


def interval_coverage(values,truth,params):
    """Small descriptive diagnostic on disjoint groups of independent seeds."""
    n=params["seeds_per_block"]
    critical=params["block_t3_critical"]
    if len(values)!=n*params["coverage_blocks"]:
        raise ValueError("coverage block count changed")
    attempted,hit=0,0
    for i in range(0,len(values),n):
        estimates=values[i:i+n]
        if any(v is None for v in estimates):
            continue
        attempted+=1
        mean=statistics.mean(estimates)
        half=critical*statistics.stdev(estimates)/math.sqrt(n)
        target=statistics.mean(truth[i:i+n])
        hit+=mean-half<=target<=mean+half
    return dict(covered=hit,assessed=attempted,planned=params["coverage_blocks"])


def aggregate(records,config):
    results={}
    primary=[]
    main=config["main"]
    sec=config["secondary"]
    for scenario,group in records.items():
        if len(group)!=24 or [r["seed"] for r in group]!=config["report_seeds"]:
            raise ValueError("incomplete or reordered seed cohort")
        cells={}
        for mode in MODES:
            arms=[r["modes"][mode] for r in group]
            status=[a["publication"] for a in arms]
            states=dict(Counter(s["status"] for s in status))
            rank_ticks="observed" if mode in CENSUS else "ipw"
            values={s:{m:[measurement(arm,s,m) for arm in arms] for m in METHODS}
                    for s in BASELINES}
            common=[a["report"]["target"]["common"]["ratio"] for a in arms]
            counts=[a["report"]["target"]["common"]["numerator"] for a in arms]
            rows=stats(counts,sec["critical"])
            scope={"states":states,"reasons":dict(Counter(s["reason"] for s in status)),
                   "common_counts":rows,"common_fraction":stats(common,sec["critical"]),
                   "threshold_states":[dict(Counter(a["thresholds"][i]["status"] for a in arms))
                                       for i in range(len(THRESHOLDS))],
                   "stability":dict(Counter(a["diagnostics"]["stability"] for a in arms))}
            scope["models"]={}
            wins_by_method={m:Counter() for m in METHODS}
            disagreement=Counter()
            for idx,a in enumerate(arms):
                rankings={m:ordered({s:values[s][m][idx] for s in BASELINES}) for m in METHODS}
                for m,order in rankings.items():
                    wins_by_method[m][order[0] if order else "none"]+=1
                    if order and rankings["truth"]:
                        disagreement[m]+=order[0]!=rankings["truth"][0]
            scope["seed_winner_counts"]={m:dict(c) for m,c in wins_by_method.items()}
            scope["top_rank_disagreements_with_truth"]=dict(disagreement)
            scope["weight_diag"]={}
            for field in ("raw_max","above_five","above_eight","count"):
                scope["weight_diag"][field]=stats([x["diagnostics"]["weights"][field] for x in arms],sec["critical"])
            for field in ("minimum","maximum","below_point_one","known_offline_minimum"):
                scope["weight_diag"][field]=stats([x["diagnostics"]["probabilities"][field] for x in arms],sec["critical"])
            scope["weight_diag"]["brier"]=stats([x["diagnostics"]["brier"] for x in arms],sec["critical"])
            scope["weight_diag"]["ess"]=stats([x["models"][BASELINES[0]]["ipw"]["ess"] for x in arms],sec["critical"])
            scope["weight_diag"]["clipped"]=sum(x["models"][BASELINES[0]]["ipw"]["clipped"] for x in arms)
            scope["weight_diag"]["estimator_status"]=dict(Counter(x["models"][BASELINES[0]]["ipw"]["status"] for x in arms))
            for s,ms in values.items():
                y=ms["truth"]
                scope["models"][s]={m:dict(error_stats(seq,y,sec["critical"]),
                                          interval_coverage=interval_coverage(seq,y,sec))
                                     for m,seq in ms.items()}
                stable=[r["modes"]["stable"] for r in group]
                actual=[a-b["models"][s]["truth"] if v["target_ticks"]==b["target_ticks"] else None
                        for a,v,b in zip(y,arms,stable)]
                scope["models"][s]["paired_true_loss_vs_stable_exploratory"]=stats(actual,sec["critical"])
                delta=[abs(w-t)-abs(o-t) if w is not None and o is not None else None
                       for w,o,t in zip(ms["ipw"],ms["observed"],y)]
                primary.append(dict(scenario=scenario,mode=mode,kind="ipw_minus_observed_absolute_error",
                                    strategy=s,**stats(delta,main["critical"],main["minimum_pairs"])))
            paired=[]
            for a,b in combinations(BASELINES,2):
                diffs=[x-y if x is not None and y is not None else None
                       for x,y in zip(values[a][rank_ticks],values[b][rank_ticks])]
                estimate=stats(diffs,main["critical"],main["minimum_pairs"])
                item=dict(scenario=scenario,mode=mode,kind="paired_model_difference",a=a,b=b,
                          method=rank_ticks,**estimate)
                primary.append(item)
                paired.append(item)
            compatible=("observed","truth") if mode in CENSUS else METHODS
            summary_rankings=[]
            for method in compatible:
                ms={s:statistics.mean(seq) if all(v is not None for v in seq) else None
                    for s in BASELINES for seq in (values[s][method],)}
                summary_rankings.append(ordered(ms))
            scope["aggregate_rankings"]={m:r for m,r in zip(compatible,summary_rankings)}
            scope["decision"]=choose_winner(status,paired,summary_rankings,len(group))
            if mode=="mnar":
                sens=[v["sensitivity"] for v in arms if "pairs" in v["sensitivity"]]
                scope["sensitivity"]=dict(evaluated=len(sens),unrestricted={a+"/"+b:sum(
                    v["unrestricted_order"]=="undetermined" for row in sens for v in row["pairs"]
                    if v["a"]==a and v["b"]==b) for a,b in combinations(BASELINES,2)},
                    conditional={a+"/"+b:dict(Counter(v["conditional_order"] for row in sens
                        for v in row["pairs"] if v["a"]==a and v["b"]==b))
                        for a,b in combinations(BASELINES,2)})
            cells[mode]=scope
        results[scenario]=cells
    if len(primary)!=main["family_size"]:
        raise ValueError("predefined family size mismatch")
    return results,primary


def run(frozen,lock_sha):
    cfg=frozen["config"]
    folder=ROOT/"round37-runs"
    folder.mkdir()
    for seed in cfg["report_seeds"]:
        dump(folder/f"{seed}-reserved.json",dict(seed=seed,lock_sha256=lock_sha))
    traces,records={},{s:[] for s in SCENARIOS}
    start=time.monotonic()
    for seed in cfg["report_seeds"]:
        for scenario in SCENARIOS:
            if time.monotonic()-start>cfg["wall_seconds"]:
                raise TimeoutError("registered report wall limit exceeded")
            raw,result=cases(scenario,seed,frozen["sources"]["protocol37.json"])
            path=folder/f"{scenario}-{seed}.json.gz"
            with path.open("xb") as out:
                data=json.dumps(raw,allow_nan=False,sort_keys=True,separators=(",",":")).encode()
                out.write(gzip.compress(data,compresslevel=4,mtime=0))
            name=path.relative_to(ROOT).as_posix()
            traces[name]=sha(name)
            records[scenario].append(dict(seed=seed,modes=result))
    if freeze(cfg)!=frozen or sha("round37-lock.json")!=lock_sha:
        raise ValueError("frozen inputs changed during report")
    summary,primary=aggregate(records,cfg)
    dump(ROOT/"round37-results.json",dict(lock_sha256=lock_sha,records=records,summary=summary,
                                          primary=primary,raw_sha256=traces,seconds=time.monotonic()-start))
    print(f"{cfg['max_sequences']} triplets complete")


def main():
    if len(sys.argv)!=2 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit("Use Python -I -S -B study37.py freeze|report")
    frozen=freeze(read("protocol37.json"))
    lock=ROOT/"round37-lock.json"
    if sys.argv[1]=="freeze":
        if (ROOT/"round37-runs").exists() or (ROOT/"round37-results.json").exists():
            raise ValueError("report already started")
        dump(lock,dict(frozen=frozen,utc=datetime.now(timezone.utc).isoformat()))
        print(sha(lock.name))
    elif sys.argv[1]=="report":
        receipt=read("round37-independent-freeze.json")
        digest=sha(lock.name)
        if (read(lock.name)["frozen"]!=frozen or receipt["lock_sha256"]!=digest
                or receipt["status"]!="verified_before_report"):
            raise ValueError("pre-report witness mismatch")
        run(frozen,digest)
    else:
        raise SystemExit("Unknown operation")


if __name__=="__main__":
    main()

