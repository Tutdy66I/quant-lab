"""Frozen development selection and paired feature reliability report."""
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
from engine_five34 import BASELINES, CONFIG
from evaluation35 import losses
from evaluation38 import correlation_diagnostics, evaluate
from features38 import ARMS, SCENARIOS, feature_digest, fixture, select_noise, selector, stream
from learner38 import run
from publication37 import canonical_sha
from study34 import historical_seeds
from study36 import stats

FILES=("protocol38.json","features38.py","learner38.py","evaluation38.py","study38.py","inventory38.py",
       "test_features38.py","run_features38.py","run_synthetic.py","round38-capability-inventory.json",
       "round38-prior-runner37.txt","round38-development-selection.json","round37-evidence-manifest.json",
       "round37-lock.json","engine_five34.py","engine_cross33.py","ridge_synthetic.py",
       "prequential_synthetic31.py","quality35.py","evaluation35.py","qualification36.py","publication37.py",
       "missing36.py","synthetic_manifest27.py","sample_lineage.py","round34-lock.json","study34.py",
       "study36.py","round31-calibration-preconfig.json","round31-pressure-preconfig.json",
       "pressure_protocol32.json","round32-cluster-lock.json","round32-alarm-dev-results.json",
       "protocol33.json","protocol34.json","protocol35.json","protocol36.json","protocol37.json")


def read(name):
    return json.loads((ROOT/name).read_bytes())


def sha(name):
    return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()


def dump(path,value):
    with path.open("x",encoding="utf8") as out:
        json.dump(value,out,sort_keys=True,separators=(",",":"),allow_nan=False)
        out.write("\n")


def assert_unused(config):
    p={"round38-runs","round38-results.json"}
    if any((ROOT/file).exists() for file in p):
        raise ValueError("report cohort already reserved or executed")
    reported=config["report_seeds"]
    forbid=historical_seeds()|set(config["development_seeds"])
    forbid.update(read("round31-calibration-preconfig.json")["config"]["development_seeds"])
    forbid.update(read("round32-alarm-dev-results.json")["development_seeds"])
    for n in range(33,38):
        prior=read(f"protocol{n}.json")
        forbid.update(prior.get("development_seeds",[]))
        forbid.update(prior.get("report_seeds",[]))
    if len(reported)!=24 or len(set(reported))!=24 or set(reported)&forbid:
        raise ValueError("report seeds overlap or are missing")


def freeze(config):
    archived=read("round37-evidence-manifest.json")["files"]
    params=json.loads(json.dumps(CONFIG))
    if (any(sha(f)!=archived[f] for f in ("engine_five34.py","ridge_synthetic.py","publication37.py"))
        or params!=read("round34-lock.json")["engine_config"]
        or sha("round38-prior-runner37.txt")!=archived["run_synthetic.py"]
        or tuple(config["scenarios"])!=SCENARIOS or tuple(config["arms"])!=ARMS
        or len(config["report_seeds"])*len(SCENARIOS)*len(ARMS)!=config["max_triplets"]):
        raise ValueError("frozen strategy, publication gate or workload differs")
    selection=select_noise(config["development_seeds"],read("protocol38.json")["development_seeds"],
                           sha("protocol38.json"))
    if read("round38-development-selection.json")!=selection:
        raise ValueError("feature selection did not come from the frozen development record")
    return dict(config=config,baseline=params,selection=selection,sources={name:sha(name) for name in FILES})


def cases(scenario,seed,configuration,selected):
    dataset,truth,features,plan,manifest,anchor=fixture(scenario,seed,configuration)
    plan_anchor=canonical_sha(plan)
    feature_anchors={arm:feature_digest(features,selector(arm,selected)) for arm in ARMS}
    evidence=dict(scenario=scenario,seed=seed,dataset=dataset,truth=truth,manifest=manifest,
                  manifest_sha256=anchor,plan=plan,plan_sha256=plan_anchor,
                  feature_sha256=feature_anchors,selected=selected,arms={})
    result={}
    targets={row["time"]:row["target"] for row in truth}
    preds={}
    for arm in ARMS:
        cols=selector(arm,selected)
        trace=run(dataset,arm,cols,stream(dataset,truth,features,plan,cols))
        evidence["arms"][arm]=trace
        result[arm]=evaluate(trace,truth,features,plan,manifest,anchor,feature_anchors[arm],plan_anchor,
                             configuration,scenario,expected_arm=arm,expected_columns=cols)
        result[arm]["diagnostics"]=correlation_diagnostics(trace,features,selected,truth)
        preds[arm]={s:{p["tick"]:p["prediction"] for p in trace["forecasts"] if p["strategy"]==s}
                    for s in BASELINES}
    base=result["stable"]
    for arm,item in result.items():
        select={t for t in item["legal"] if arm!="fading" or t>=125}
        legal=sorted(select&set(base["legal"]))
        observed=sorted(set(legal)&set(item["common"])&set(base["common"]))
        comparisons={}
        for s in BASELINES:
            def comparison(clock):
                a=losses(preds[arm][s],targets,clock)
                b=losses(preds["stable"][s],targets,clock)
                return dict(n=len(clock),candidate=a["mse"],reference=b["mse"],
                            delta=a["mse"]-b["mse"] if clock else None)
            comparisons[s]=dict(oracle=comparison(legal),observed=comparison(observed))
            if arm=="stable":
                item["models"][s]["post100_oracle"]=losses(preds[arm][s],targets,
                                                         {t for t in legal if t>=100})
        item["paired"]=dict(legal_clock=legal,observed_clock=observed,models=comparisons,
                            scope="offline artifact; observed-based release subject to qualification")
    return evidence,dict(seed=seed,arms=result)


def aggregate(records,config):
    main=config["main"]
    primary=[]
    summary={}
    descriptive=config["secondary"]["critical"]
    for scenario,paths in records.items():
        if [r["seed"] for r in paths]!=config["report_seeds"]:
            raise ValueError("wrong paired seed order")
        summaries={}
        for arm in ARMS:
            cells=[r["arms"][arm] for r in paths]
            out=dict(publication=dict(Counter(r["publication"]["status"] for r in cells)),
                     targets=dict(Counter(r["publication"]["target"] or "none" for r in cells)),
                     legal=stats([len(r["legal"]) for r in cells],descriptive),
                     observed=stats([len(r["common"]) for r in cells],descriptive),models={})
            for field in ("redundant_corr","redundant_gram_condition","redundant_regularized_condition",
                          "selected_noise_report_abs_corr","fading_corr_early","fading_corr_late"):
                out[field]=stats([r["diagnostics"][field] for r in cells],descriptive)
            for s in BASELINES:
                m=[r["models"][s] for r in cells]
                out["models"][s]=dict(
                    observed=stats([v["observed"]["mse"] for v in m],descriptive),
                    oracle=stats([v["oracle"]["mse"] for v in m],descriptive),
                    late_oracle=stats([v["late_oracle"]["mse"] for v in m],descriptive),
                    initial_kind=dict(Counter(v["initial_kind"] for v in m)),
                    candidate_fits=stats([v["candidate_fits"] for v in m],descriptive),
                    cost_per_planned=stats([v["cost_per_planned"] for v in m],descriptive))
                if arm=="stable":
                    continue
                pair=[r["paired"]["models"][s] for r in cells]
                primary.append(dict(scenario=scenario,arm=arm,strategy=s,metric="oracle_common_mse_delta",
                                    estimate=stats([p["oracle"]["delta"] for p in pair],
                                                   main["critical"],main["minimum_pairs"]),
                                    observed_exploratory=stats([p["observed"]["delta"] for p in pair],descriptive),
                                    offline_only=scenario=="mnar_outage"))
            summaries[arm]=out
        summary[scenario]=summaries
    for s in BASELINES:
        shifts=records["covariate_shift"]
        control=records["context_on"]
        values=[a["arms"]["stable"]["models"][s]["post100_oracle"]["mse"]-
                b["arms"]["stable"]["models"][s]["post100_oracle"]["mse"] for a,b in zip(shifts,control)]
        primary.append(dict(scenario="covariate_shift",arm="stable",strategy=s,
                            metric="post100_minus_context_on_mse",
                            estimate=stats(values,main["critical"],main["minimum_pairs"])))
    if len(primary)!=main["family_size"]:
        raise ValueError("pre-registered family size differs")
    return summary,primary


def report(frozen,lock_sha):
    cfg=frozen["config"]
    folder=ROOT/"round38-runs"
    folder.mkdir()
    for seed in cfg["report_seeds"]:
        dump(folder/f"{seed}-reserved.json",dict(seed=seed,lock_sha256=lock_sha))
    start=time.monotonic()
    records={s:[] for s in SCENARIOS}
    hashes={}
    for seed in cfg["report_seeds"]:
        for s in SCENARIOS:
            if time.monotonic()-start>cfg["wall_seconds"]:
                raise TimeoutError("registered research wall limit exceeded")
            raw,result=cases(s,seed,frozen["sources"]["protocol38.json"],frozen["selection"]["selected"])
            dest=folder/f"{s}-{seed}.json.gz"
            with dest.open("xb") as out:
                out.write(gzip.compress(json.dumps(raw,allow_nan=False,sort_keys=True,
                                                   separators=(",",":")).encode(),compresslevel=4,mtime=0))
            name=dest.relative_to(ROOT).as_posix()
            hashes[name]=sha(name)
            records[s].append(result)
    if freeze(cfg)!=frozen or sha("round38-lock.json")!=lock_sha:
        raise ValueError("research freeze changed during report")
    summary,primary=aggregate(records,cfg)
    dump(ROOT/"round38-results.json",dict(lock_sha256=lock_sha,summary=summary,records=records,
         primary=primary,raw_sha256=hashes,seconds=time.monotonic()-start))
    print(f"{cfg['max_triplets']} triplets complete")


def main():
    if len(sys.argv)!=2 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit("Use Python -I -S -B study38.py development|freeze|report")
    cfg=read("protocol38.json")
    if sys.argv[1]=="development":
        assert_unused(cfg)
        if (ROOT/"round38-lock.json").exists():
            raise ValueError("feature choices are already frozen")
        selected=select_noise(cfg["development_seeds"],cfg["development_seeds"],sha("protocol38.json"))
        dump(ROOT/"round38-development-selection.json",selected)
    elif sys.argv[1]=="freeze":
        assert_unused(cfg)
        dump(ROOT/"round38-lock.json",dict(frozen=freeze(cfg),utc=datetime.now(timezone.utc).isoformat()))
        print(sha("round38-lock.json"))
    elif sys.argv[1]=="report":
        assert_unused(cfg)
        lock_sha=sha("round38-lock.json")
        frozen=read("round38-lock.json")["frozen"]
        witness=read("round38-independent-freeze.json")
        if (witness["status"]!="verified_before_report" or witness["lock_sha256"]!=lock_sha
                or frozen!=freeze(cfg)):
            raise ValueError("independent freeze witness mismatch")
        report(frozen,lock_sha)
    else:
        raise SystemExit("Unknown operation")


if __name__=="__main__":
    main()

