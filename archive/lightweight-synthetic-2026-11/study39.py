"""Run the preregistered common-path artificial feature study."""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
if __name__=="__main__":
    sys.path.insert(0,str(ROOT))
from arrival39 import run
from design39 import FEATURE_SEARCH, GROUPS, PERTURB, SCENARIOS, TIMING, arms, contract, fixture, transport
from engine_five34 import BASELINES, CONFIG
from evaluation35 import losses
from evaluation39 import CLOCK, evaluate, group_diagnostic
from study34 import historical_seeds
from study36 import stats

FILES=("protocol39.json","arrival39.py","design39.py","evaluation39.py","study39.py","run_study39.py",
       "test_arrival39.py","run_synthetic.py","round39-prior-runner38.txt","round39-development.json",
       "round38-lock.json","round38-evidence-manifest.json","round38-capability-inventory.json",
       "publication37.py","missing36.py","quality35.py",
       "synthetic_manifest27.py","sample_lineage.py","ridge_synthetic.py","learner38.py",
       "evaluation35.py","prequential_synthetic31.py","engine_five34.py","engine_cross33.py",
       "study34.py","study36.py","round34-lock.json","round31-calibration-preconfig.json",
       "round31-pressure-preconfig.json","pressure_protocol32.json","round32-cluster-lock.json",
       "round32-alarm-dev-results.json","protocol33.json","protocol34.json","protocol35.json",
       "protocol36.json","protocol37.json","protocol38.json")


def read(name):
    return json.loads((ROOT/name).read_bytes())


def sha(name):
    return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()


def dump(path,value):
    with path.open("x",encoding="utf8") as out:
        json.dump(value,out,allow_nan=False,sort_keys=True,separators=(",",":"))
        out.write("\n")


def allowed(config):
    seeds=config["report_seeds"]
    forbid=historical_seeds()|set(config["development_seeds"])
    forbid.update(read("round31-calibration-preconfig.json")["config"]["development_seeds"])
    forbid.update(read("round32-alarm-dev-results.json")["development_seeds"])
    for n in range(33,39):
        prior=read(f"protocol{n}.json")
        forbid.update(prior.get("report_seeds",[]))
        forbid.update(prior.get("development_seeds",[]))
    if len(seeds)!=24 or len(set(seeds))!=24 or set(seeds)&forbid:
        raise ValueError("report cohort not fresh")


def freeze(config):
    allowed(config)
    p=read("protocol39.json")
    archive=read("round38-evidence-manifest.json")["files"]
    frozen_config=json.loads(json.dumps(CONFIG))
    dev=read("round39-development.json")
    if (config!=p or tuple(config["scenarios"])!=SCENARIOS or tuple(config["timing_arms"])!=TIMING
            or tuple(config["group_search"])!=FEATURE_SEARCH or tuple(config["lag_group_scenarios"])!=PERTURB
            or tuple(config["mnar_arms"])!=arms("timing_mnar")
            or len(config["report_seeds"])*sum(len(arms(s)) for s in SCENARIOS)!=config["max_triplets"]
            or frozen_config!=read("round34-lock.json")["engine_config"]
            or any(sha(f)!=archive[f] for f in ("publication37.py","engine_five34.py","ridge_synthetic.py"))
            or sha("round39-prior-runner38.txt")!=archive["run_synthetic.py"]
            or dev["seeds"]!=config["development_seeds"] or dev["protocol_sha256"]!=sha("protocol39.json")
            or dev["attempts"]!=len(config["development_seeds"])*sum(len(arms(s)) for s in SCENARIOS)):
        raise ValueError("configuration, baseline or development search does not match freeze")
    return dict(config=config,baseline=frozen_config,sources={f:sha(f) for f in FILES})


def specs(case):
    if case.startswith("timing"):
        rows=[(a,"instant",125 if "fade" in a else 60) for a in arms(case)[1:]]
        if case=="timing":
            rows.extend([("short","short_abstain",60),("delay_fade","short",125)])
        return rows
    rows=[("full","base",90),("single","base",90),("full","single",90)]
    if case in PERTURB:
        rows.append(("group_lag","full",90))
    return rows


def paired(a,b,truth,clock):
    first=losses(a,truth,clock)
    second=losses(b,truth,clock)
    return dict(n=len(clock),candidate=first,reference=second,
                delta=first["mse"]-second["mse"] if clock else None)


def cases(case,seed,digest):
    data=fixture(case,seed,digest)
    vals,arms_raw,preds={}, {}, {}
    truth={r["time"]:r["target"] for r in data["truth"]}
    for name in arms(case):
        columns,ttl,events=contract(data,case,name)
        source,sequence=transport(data,events)
        trace=run(data["dataset"],name,columns,ttl,sequence)
        item=evaluate(trace,data,source,digest,case,name)
        item["group_diagnostic"]=group_diagnostic(trace)
        vals[name],arms_raw[name]=item,dict(source_sha256=source,trace=trace)
        preds[name]={s:{p["tick"]:p["prediction"] for p in trace["forecasts"]
                        if p["strategy"]==s} for s in BASELINES}
    reference=arms(case)[0]
    for name,p in preds.items():
        for t in set(vals[name]["legal"])&set(vals[reference]["legal"]):
            if p["mean"][t]!=preds[reference]["mean"][t]:
                raise ValueError("label-only mean prediction changed with feature arm")
    comparisons={}
    for a,b,start in specs(case):
        legal=sorted(t for t in set(vals[a]["legal"])&set(vals[b]["legal"]) if t>=start)
        observed=sorted(t for t in set(vals[a]["common"])&set(vals[b]["common"]) if t>=start)
        if len(observed)<12 or len(observed)/(180-start)<.30:
            status="insufficient_evidence"
        elif case=="timing_mnar":
            status="sensitivity_only"
        elif all(vals[k]["publication"]["status"]=="population_supported_under_assumptions" for k in (a,b)):
            status="shared_verified_cohort"
        else:
            status="valid_observed_only"
        comparisons[a+"-"+b]=dict(start=start,legal=legal,observed=observed,status=status,
             models={s:dict(oracle=paired(preds[a][s],preds[b][s],truth,legal),
                            observed=paired(preds[a][s],preds[b][s],truth,observed)) for s in BASELINES})
    rescued=None
    if case.startswith("timing"):
        gain=sorted(set(vals["short"]["common"])-set(vals["short_abstain"]["common"]))
        if case=="timing":
            rescued=dict(clock=gain,models={
                s:paired(preds["short"][s],preds["instant"][s],truth,gain) for s in BASELINES},
                ridge_vs_label_mean=paired(preds["short"]["fixed_ridge"],preds["short"]["mean"],truth,gain))
        else:
            rescued=dict(clock=gain,scope="observed_only_MNAR_diagnostic")
    return dict(case=case,seed=seed,fixture=data,arms=arms_raw),dict(
                seed=seed,arms=vals,comparisons=comparisons,rescued=rescued)


def aggregate(records,config):
    result,primary={},[]
    p=config["primary"]
    critical=config["secondary"]["critical"]
    for case,group in records.items():
        if [r["seed"] for r in group]!=config["report_seeds"]:
            raise ValueError("nonregistered seed cohort")
        summaries={}
        for name in arms(case):
            items=[r["arms"][name] for r in group]
            summaries[name]=dict(
                qualification=dict(Counter(a["publication"]["status"] for a in items)),
                legal=stats([len(a["legal"]) for a in items],critical),common=stats([len(a["common"]) for a in items],critical),
                expired=stats([a["expired_arrivals"] for a in items],critical),
                packets=stats([a["processed_feature_events"] for a in items],critical),
                dimensions=items[0]["feature_dimensions"],models={})
            for s in BASELINES:
                models=[a["models"][s] for a in items]
                summaries[name]["models"][s]=dict(
                    oracle={k:stats([m["oracle"][k] for m in models],critical) for k in ("mae","mse","rmse")},
                    observed={k:stats([m["observed"][k] for m in models],critical) for k in ("mae","mse","rmse")},
                    age={k:{v:stats([m["age_oracle"][k][v] for m in models],critical) for v in ("n","mse")}
                         for k in ("0","1_2","3_6")},
                    fits=stats([m["candidate_fits"] for m in models],critical),
                    cost=stats([m["cost_per_planned"] for m in models],critical),
                    initial=dict(Counter(m["initial_kind"] for m in models)))
            diagnostics=[a["group_diagnostic"] for a in items]
            if all(d is not None for d in diagnostics):
                summaries[name]["diagnostic"]=dict(
                    rho=stats([d["last_pair_corr"] for d in diagnostics],critical),
                    condition=stats([d["standardized_pair_condition"] for d in diagnostics],critical),
                    weights=[stats([d["weights"][i] for d in diagnostics],critical)
                             for i in range(len(diagnostics[0]["weights"]))])
        result[case]=summaries
        for a,b,start in specs(case):
            selected=[r["comparisons"][a+"-"+b] for r in group]
            for s in BASELINES:
                primary.append(dict(case=case,candidate=a,reference=b,start=start,strategy=s,
                    estimate=stats([r["models"][s]["oracle"]["delta"] for r in selected],
                                   p["critical"],p["minimum_seed_pairs"]),
                    observed=stats([r["models"][s]["observed"]["delta"] for r in selected],critical),
                    statuses=dict(Counter(r["status"] for r in selected)),
                    offline_only=case=="timing_mnar"))
    if len(primary)!=p["family_size"]:
        raise ValueError("primary family size mismatch")
    return result,primary


def run_report(frozen,lock_sha):
    cfg=frozen["config"]
    folder=ROOT/"round39-runs"
    folder.mkdir()
    for seed in cfg["report_seeds"]:
        dump(folder/f"{seed}-reserved.json",dict(seed=seed,lock_sha256=lock_sha))
    groups={s:[] for s in SCENARIOS}
    hashes={}
    start=time.monotonic()
    for seed in cfg["report_seeds"]:
        for case in SCENARIOS:
            if time.monotonic()-start>cfg["wall_seconds"]:
                raise TimeoutError("registered resource limit")
            raw,result=cases(case,seed,frozen["sources"]["protocol39.json"])
            path=folder/f"{case}-{seed}.json.gz"
            with path.open("xb") as out:
                out.write(gzip.compress(json.dumps(raw,allow_nan=False,sort_keys=True,
                                                   separators=(",",":")).encode(),compresslevel=4,mtime=0))
            name=path.relative_to(ROOT).as_posix()
            hashes[name]=sha(name)
            groups[case].append(result)
    if frozen!=freeze(cfg) or sha("round39-lock.json")!=lock_sha:
        raise ValueError("freeze changed during report")
    summary,primary=aggregate(groups,cfg)
    dump(ROOT/"round39-results.json",dict(lock_sha256=lock_sha,records=groups,summary=summary,
                                          primary=primary,raw_sha256=hashes,seconds=time.monotonic()-start))
    print(f"{cfg['max_triplets']} triplets complete")


def main():
    if len(sys.argv)!=2 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit("Use Python -I -S -B study39.py development|freeze|report")
    operation=sys.argv[1]
    cfg=read("protocol39.json")
    allowed(cfg)
    folder=ROOT/"round39-runs"
    lock=ROOT/"round39-lock.json"
    if folder.exists() or (ROOT/"round39-results.json").exists():
        raise ValueError("report cohort already reserved")
    if operation=="development":
        if lock.exists():
            raise ValueError("development closed by freeze")
        results=[]
        for seed in cfg["development_seeds"]:
            for case in SCENARIOS:
                _,record=cases(case,seed,sha("protocol39.json"))
                for name,arm in record["arms"].items():
                    results.append(dict(seed=seed,case=case,arm=name,
                         cover=len(arm["common"]),state=arm["publication"]["status"],
                         models={s:m["oracle"]["mse"] for s,m in arm["models"].items()}))
        dump(ROOT/"round39-development.json",dict(seeds=cfg["development_seeds"],
              attempts=len(results),protocol_sha256=sha("protocol39.json"),rows=results,
              selection="all registered arms retained; no post-hoc feature choice"))
    elif operation=="freeze":
        dump(lock,dict(frozen=freeze(cfg),utc=datetime.now(timezone.utc).isoformat()))
        print(sha(lock.name))
    elif operation=="report":
        context=read(lock.name)["frozen"]
        digest=sha(lock.name)
        witness=read("round39-independent-freeze.json")
        if witness["status"]!="verified_before_report" or witness["lock_sha256"]!=digest or context!=freeze(cfg):
            raise ValueError("freeze or independent witness mismatch")
        run_report(context,digest)
    else:
        raise SystemExit("Unknown operation")


if __name__=="__main__":
    main()

