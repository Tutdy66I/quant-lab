"""Preregister, execute and summarize the Round 36 artificial study."""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent
if __name__ == "__main__":
    sys.path.insert(0,str(ROOT))

from analyses36 import CAP, RECOVERY_THRESHOLD, WINDOW, mse, recovery_metrics, selection
from design36 import MODES, RECOVERY, RECOVERY_ARMS, SCENARIOS, execute, fixture, make_plan
from engine_five34 import BASELINES, CONFIG
from missing36 import (CAL_CUTOFF, CAL_END, CAL_START, EVAL_START, MAX_WEIGHT, MIN_ESS,
                       MIN_OBSERVED, MIN_P, MIN_STRATUM, RATIOS)
from qualification36 import MIN_FRACTION, MIN_RANKED, WINDOWS, qualify, strata
from study34 import historical_seeds

FILES = ("protocol36.json","study36.py","qualification36.py","design36.py","missing36.py",
         "analyses36.py","test_feedback36.py","run_feedback36.py","run_synthetic.py",
         "engine_five34.py","engine_cross33.py","baselines35.py","quality35.py","evaluation35.py",
         "feedback35.py","prequential_synthetic31.py","ridge_synthetic.py","synthetic_manifest27.py",
         "sample_lineage.py","study34.py","round35-lock.json","round35-evidence-manifest.json",
         "round34-lock.json","protocol34.json","protocol35.json","round31-calibration-preconfig.json",
         "round31-pressure-preconfig.json","pressure_protocol32.json","round32-cluster-lock.json",
         "round32-alarm-dev-results.json","protocol33.json","round36-prior-runner35.txt")


def sha(file):
    return hashlib.sha256((ROOT/file).read_bytes()).hexdigest()


def read(file):
    return json.loads((ROOT/file).read_bytes())


def dump(path, obj):
    with path.open("x",encoding="utf8") as out:
        json.dump(obj,out,allow_nan=False,sort_keys=True,separators=(",",":"))
        out.write("\n")


def freeze(protocol):
    seeds = protocol["report_seeds"]
    prior = [read(name) for name in ("protocol34.json","protocol35.json","protocol33.json")]
    forbidden = historical_seeds() | set(protocol["development_seeds"])
    forbidden |= set(read("round31-calibration-preconfig.json")["config"]["development_seeds"])
    forbidden |= set(read("round32-alarm-dev-results.json")["development_seeds"])
    for cfg in prior:
        forbidden.update(cfg.get("development_seeds",[]))
        forbidden.update(cfg.get("report_seeds",[]))
    engine = json.loads(json.dumps(CONFIG))
    p = protocol["propensity"]
    rank = protocol["ranking_gate"]
    recovery = protocol["recovery"]
    arms = tuple(product(protocol["initial"],protocol["missing"],protocol["delay"],protocol["quality"]))
    arms += tuple(tuple(a) for a in protocol["no_return_arms"])
    prior_hashes = read("round35-evidence-manifest.json")["files"]
    if (set(seeds)&forbidden or len(seeds)!=24 or len(set(seeds))!=24
        or tuple(protocol["scenarios"])!=SCENARIOS or tuple(protocol["modes"])!=MODES or arms!=RECOVERY_ARMS
        or len(seeds)*len(SCENARIOS)*(len(MODES)+len(arms)) != protocol["budget"]["max_traces"]
        or engine!=read("round34-lock.json")["engine_config"]
        or any(sha(f)!=prior_hashes[f] for f in ("engine_five34.py","baselines35.py","evaluation35.py"))
        or sha("round36-prior-runner35.txt")!=prior_hashes["run_synthetic.py"]
        or (p["calibration"],p["calibration_cutoff"],p["evaluation"],p["evaluation_cutoff"]) !=
           ([CAL_START,CAL_END],CAL_CUTOFF,[EVAL_START,180],181)
        or (p["min_stratum"],p["min_probability"],p["max_weight"],p["min_ess"],p["min_observed"]) !=
           (MIN_STRATUM,MIN_P,MAX_WEIGHT,MIN_ESS,MIN_OBSERVED)
        or (rank["min_common"],rank["min_fraction"])!=(MIN_RANKED,MIN_FRACTION)
        or tuple(protocol["loss_mean_ratio_grid"])!=RATIOS
        or tuple(map(tuple,protocol["quality_windows"]))!=WINDOWS
        or (recovery["reference_tick"],recovery["block"],recovery["mse_max"],recovery["restricted_delay"]) !=
           (RECOVERY,WINDOW,RECOVERY_THRESHOLD,CAP)):
        raise ValueError("seed, parameter, design or previously frozen bytes changed")
    return dict(protocol=protocol,baseline=engine,sources={x:sha(x) for x in FILES})


def named(arm):
    i,m,l,q=arm
    return f"i{i}/m{float(m):g}/{l}/{q}"


def seed_case(scenario,seed,config_hash):
    dataset,truth,population,anchor=fixture(scenario,seed,config_hash)
    raw = dict(dataset=dataset,truth=truth,population=population,anchor=anchor,selection={},recovery={})
    record = dict(seed=seed,selection={},recovery={})
    for mode in MODES:
        plan=make_plan(truth,scenario,seed,mode=mode)
        tr=execute(dataset,truth,seed,mode,"clean",plan)
        q=qualify(tr,truth,population,anchor,181,offline_plan=plan,
                  context=dict(scenario=scenario,missingness=mode,latency="short",initial=45,feature="clean"))
        raw["selection"][mode]=dict(plan=plan,trace=tr)
        record["selection"][mode]=dict(eligibility=strata(q),**selection(q,truth,mode))
    forecasts, cohorts = {}, {}
    for arm in RECOVERY_ARMS:
        i,m,l,quality=arm
        name=named(arm)
        plan=make_plan(truth,scenario,seed,initial=i,missing=m,delay=l)
        tr=execute(dataset,truth,seed,name,quality,plan)
        q=qualify(tr,truth,population,anchor,181,offline_plan=plan,
                  context=dict(scenario=scenario,missingness=m,latency=l,initial=i,feature=quality))
        raw["recovery"][name]=dict(plan=plan,trace=tr)
        record["recovery"][name]=dict(context=q["context"],eligibility=strata(q),
                                       models=recovery_metrics(q,tr,truth))
        forecasts[name] = {s:{r["tick"]:r["forecasts"][s]["prediction"] for r in q["records"]} for s in BASELINES}
        cohorts[name] = {r["tick"] for r in q["records"] if r["tick"]>=RECOVERY and
                         all(r["forecasts"][s]["verified"] for s in BASELINES)}
    names = [named((i,0,l,"clean")) for i,l in product((8,45),("short","batch"))]
    common = sorted(set.intersection(*(cohorts[k] for k in names)))
    if len(common)<MIN_RANKED:
        raise ValueError("paired recovery cohort is not comparable")
    post = [truth[t]["target"] for t in common]
    record["factor_clock"]=common
    for key in names:
        for s in BASELINES:
            record["recovery"][key]["models"][s]["paired_post_mse"]=mse([forecasts[key][s][t] for t in common],post)
    return raw,record


def stats(values,critical,min_n=18):
    retained = [v for v in values if v is not None]
    if not retained:
        return dict(status="unknown",n=0,attempted=len(values))
    average=statistics.mean(retained)
    sd=statistics.stdev(retained) if len(retained)>1 else None
    if len(retained)<min_n:
        return dict(status="insufficient_evidence",n=len(retained),attempted=len(values),mean=average,sd=sd)
    half=critical*sd/math.sqrt(len(retained))
    return dict(status="estimated",n=len(retained),attempted=len(values),mean=average,sd=sd,ci=[average-half,average+half])


def aggregate(records,protocol):
    secondary=protocol["stats"]["secondary_critical"]
    primary=protocol["stats"]["primary_critical"]
    min_n=protocol["stats"]["minimum_seed_pairs"]
    output, contrasts={},[]
    for scenario,group in records.items():
        if {r["seed"] for r in group}!=set(protocol["report_seeds"]) or len(group)!=24:
            raise ValueError("incomplete or duplicated report cohort")
        selection_cells={}
        for mode in MODES:
            studies=[r["selection"][mode] for r in group]
            n_return=[r["count_returned"] for r in studies]
            arm=dict(label_count=stats(n_return,secondary),models={})
            sensitive=[r["sensitivity"] for r in studies if r["sensitivity"] is not None]
            if sensitive:
                evaluated=[r for r in sensitive if "pairs" in r]
                arm["sensitivity"] = dict(attempted=len(sensitive),evaluated=len(evaluated),
                    identification=dict(Counter(r["status"] for r in sensitive)),
                    pairwise={a+"/"+b:dict(undetermined=sum(v["conditional_order"]=="undetermined" for v in results),
                                             unrestricted=sum(v["unrestricted_order"]=="undetermined" for v in results),
                                             conditional_orders=dict(Counter(v["conditional_order"] for v in results)))
                              for a,b in [(BASELINES[0],BASELINES[1]),(BASELINES[0],BASELINES[2]),(BASELINES[1],BASELINES[2])]
                              for results in [[v for r in evaluated for v in r["pairs"] if (v["a"],v["b"])==(a,b)]]},
                    grids={s:[stats([r["grid"][s][j] for r in evaluated],secondary)
                              for j in range(len(RATIOS))] for s in BASELINES})
            for s in BASELINES:
                series=[r["models"][s] for r in studies]
                actual=[r["complete_truth"] for r in series]
                dest = {"complete_truth":stats(actual,secondary)}
                for label in ("complete_case","estimated","oracle"):
                    estimates = [r[label] if label=="complete_case" else
                                 r[label]["estimate"] if r[label] is not None else None for r in series]
                    err = [e-a if e is not None else None for e,a in zip(estimates,actual)]
                    est=dict(values=stats(estimates,secondary),bias=stats(err,secondary),
                             error_rmse=math.sqrt(statistics.mean(e*e for e in err if e is not None))
                             if any(e is not None for e in err) else None)
                    if label != "complete_case":
                        est.update(statuses=dict(Counter(r[label]["status"] for r in series if r[label] is not None)),
                                   ess=stats([r[label]["ess"] if r[label] else None for r in series],secondary),
                                   clipped=sum(r[label]["clipped"] for r in series if r[label]))
                    dest[label]=est
                arm["models"][s]=dest
                if mode=="mar":
                    effects=[abs(r["estimated"]["estimate"]-a)-abs(r["complete_case"]-a)
                             if r["estimated"]["estimate"] is not None and r["complete_case"] is not None
                             else None for r,a in zip(series,actual)]
                    contrasts.append(dict(scenario=scenario,strategy=s,contrast="mar_abs_error_IPW_minus_CC",
                                          estimate=stats(effects,primary,min_n)))
            selection_cells[mode]=arm
        rec={}
        for arm in RECOVERY_ARMS:
            key=named(arm)
            samples=[r["recovery"][key] for r in group]
            totals=[v["eligibility"]["overall"] for v in samples]
            rec[key]=dict(
                coverage={name:stats([fn(v) for v in totals],secondary) for name,fn in
                    (("legal",lambda x:x["legal_features"]),("common",lambda x:x["common"]),
                     ("arrived",lambda x:x["labels"]["arrived"]))},
                ranking_qualified=sum(r["ranking"]=="descriptive_respondents_only" for r in totals),models={})
            for s in BASELINES:
                ms=[v["models"][s] for v in samples]
                rec[key]["models"][s]=dict(
                    oracle_recovered=sum(not v["oracle_censored"] for v in ms),
                    label_confirmed=sum(not v["confirm_censored"] for v in ms),
                    initial_kinds=dict(Counter(v["initial_kind"] for v in ms)),
                    metrics={f:stats([v[f] for v in ms],secondary) for f in
                             ("ore_pre","ore_post","restricted_delay","first_ridge_fit","first_update",
                              "first_using_new_feedback","cost_per_planned","candidate_fits","fallback_count")})
        for s in BASELINES:
            def value(r, i, d, metric):
                return r["recovery"][named((i,0,d,"clean"))]["models"][s][metric]
            for name,terms,metric in (
                ("batch_effect_full",((1,45,"batch"),(-1,45,"short")),"paired_post_mse"),
                ("thin_effect_short",((1,8,"short"),(-1,45,"short")),"paired_post_mse"),
                ("thin_x_batch",((1,8,"batch"),(-1,8,"short"),(-1,45,"batch"),(1,45,"short")),"paired_post_mse"),
                ("restricted_delay_batch_effect_full",((1,45,"batch"),(-1,45,"short")),"restricted_delay")):
                x=[math.fsum(sign*value(r,i,d,metric) for sign,i,d in terms) for r in group]
                contrasts.append(dict(scenario=scenario,strategy=s,contrast=name,estimate=stats(x,primary,min_n)))
        output[scenario]=dict(selection=selection_cells,recovery=rec)
    if len(contrasts)!=protocol["main_comparisons"]["family_size"]:
        raise ValueError("primary family changed")
    return output,contrasts


def run(frozen,lock_sha):
    protocol=frozen["protocol"]
    root=ROOT/"round36-runs"
    root.mkdir()
    for seed in protocol["report_seeds"]:
        dump(root/f"{seed}-reserved.json",dict(seed=seed,lock_sha256=lock_sha))
    records={s:[] for s in SCENARIOS}
    files={}
    start=time.monotonic()
    for seed in protocol["report_seeds"]:
        for scenario in SCENARIOS:
            if time.monotonic()-start>protocol["budget"]["wall_seconds"]:
                raise TimeoutError("report duration exceeded")
            raw,result=seed_case(scenario,seed,frozen["sources"]["protocol36.json"])
            path=root/f"{scenario}-{seed}.json.gz"
            with path.open("xb") as out:
                out.write(gzip.compress(json.dumps(raw,allow_nan=False,separators=(",",":"),
                                                   sort_keys=True).encode(),compresslevel=4,mtime=0))
            name=path.relative_to(ROOT).as_posix()
            files[name]=sha(name)
            records[scenario].append(result)
    if freeze(protocol)!=frozen or sha("round36-lock.json")!=lock_sha:
        raise ValueError("frozen bytes changed during report")
    summary,contrasts=aggregate(records,protocol)
    dump(ROOT/"round36-results.json",dict(records=records,summary=summary,contrasts=contrasts,raw_sha256=files,
                                           seconds=time.monotonic()-start,lock_sha256=lock_sha))
    print(f"{protocol['budget']['max_traces']} triplets complete")


def main():
    if len(sys.argv)!=2 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit("Use Python -I -S -B study36.py freeze|report")
    frozen=freeze(read("protocol36.json"))
    lock=ROOT/"round36-lock.json"
    if sys.argv[1]=="freeze":
        if (ROOT/"round36-runs").exists() or (ROOT/"round36-results.json").exists():
            raise ValueError("report already started")
        dump(lock,dict(frozen=frozen,utc=datetime.now(timezone.utc).isoformat()))
        print(sha(lock.name))
    elif sys.argv[1]=="report":
        approval=read("round36-independent-freeze.json")
        lock_sha=sha(lock.name)
        if (read(lock.name)["frozen"]!=frozen or approval["status"]!="verified_before_report"
                or approval["lock_sha256"]!=lock_sha):
            raise ValueError("freeze evidence mismatch")
        run(frozen,lock_sha)
    else:
        raise SystemExit("Unknown operation")


if __name__=="__main__":
    main()

