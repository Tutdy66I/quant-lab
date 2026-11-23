"""Frozen Round 35 experiment runner and paired seed-level summaries."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
from baselines35 import run_online
from engine_five34 import BASELINES, CONFIG
from evaluation35 import describe, losses
from feedback35 import CUTOFF, RECOVERY, STOP, WARMUP, packets, release_plan, truth_path
from study34 import historical_seeds

SOURCES = ("protocol35.json", "quality35.py", "feedback35.py", "baselines35.py",
           "evaluation35.py", "study35.py", "test_quality35.py", "run_synthetic.py",
           "run_quality35.py", "engine_five34.py", "engine_cross33.py",
           "prequential_synthetic31.py", "ridge_synthetic.py",
           "synthetic_manifest27.py", "sample_lineage.py", "study34.py",
           "round31-pressure-preconfig.json", "round31-calibration-preconfig.json",
           "pressure_protocol32.json", "round32-cluster-lock.json", "round32-alarm-dev-results.json", "protocol33.json",
           "protocol34.json", "round34-lock.json", "round34-evidence-manifest.json")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_bytes())


def freeze(protocol):
    config = json.loads(json.dumps(CONFIG))
    old = read("round34-lock.json")
    previous = read("protocol34.json")
    seeds = protocol["report_seeds"]
    forbidden = historical_seeds() | set(previous["report_seeds"]) | set(previous["development_seeds"])
    forbidden |= set(protocol["development_seeds"]) | set(read("round32-alarm-dev-results.json")["development_seeds"])
    identities = [cell_name(a) for a in arms(protocol)]
    if (config != old["engine_config"] or tuple(protocol["strategies"]) != BASELINES
            or sha(ROOT / "engine_five34.py") != old["source_sha256"]["engine_five34.py"]
            or len(seeds) != 24 or len(set(seeds)) != 24 or set(seeds) & forbidden
            or len(identities) != len(set(identities))
            or len(seeds) * len(identities) * len(protocol["scenarios"]) != protocol["max_sequences"]
            or [WARMUP, STOP, RECOVERY, CUTOFF] != [protocol[k] for k in
                ("warmup", "score_stop_exclusive", "recovery_start", "label_cutoff")]):
        raise ValueError("baseline, seed, workload or clock freeze mismatch")
    return dict(source_sha256={name: sha(ROOT/name) for name in SOURCES},
                protocol=protocol, baseline_config=config)


def dump(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.write("\n")


def arms(protocol):
    return [(quality, missing, delay) for quality in protocol["feature_quality"]
            for missing in protocol["mcar_levels"] for delay in protocol["latency"]] + [
                tuple(a) for a in protocol["diagnostics"]]


def cell_name(cell):
    return "/".join(cell)


def cases_for(scenario, seed, protocol, config_hash):
    dataset, truth, population, anchor = truth_path(scenario, seed, config_hash)
    targets = {r["time"]: r["target"] for r in truth}
    raw, metrics, prediction_maps, plans = {}, {}, {}, {}
    for cell in arms(protocol):
        name = cell_name(cell)
        trace = run_online(dataset, name, packets(dataset, truth, seed, *cell))
        plan = release_plan(truth, seed, cell[1], cell[2])
        expected = {i: at for i, at in enumerate(plan) if at is not None and at <= CUTOFF}
        if {e["tick"]: e["received"] for e in trace["releases"]} != expected:
            raise ValueError("lost, extra or moved release")
        result = describe(trace, truth, population, anchor)
        result["never_returned"] = sum(at is None for at in plan[WARMUP:])
        result["after_cutoff"] = sum(at is not None and at > CUTOFF for at in plan[WARMUP:])
        if result["label_count"]+result["never_returned"]+result["after_cutoff"] != result["planned"]:
            raise ValueError("label coverage census mismatch")
        raw[name], metrics[name], plans[name] = trace, result, plan
        prediction_maps[name] = {s:{f["tick"]: f["prediction"] for f in trace["forecasts"]
                               if f["strategy"] == s and f["status"] == "valid"} for s in BASELINES}
    common = set.intersection(*(set(r["prediction_clock"]) for r in metrics.values()))
    for name, result in metrics.items():
        for strategy in BASELINES:
            result["metrics"][strategy]["factor_oracle"] = losses(prediction_maps[name][strategy], targets, common)
    return dict(dataset=dataset, truth=truth, population=population, anchor=anchor, plans=plans,
                traces=raw, factor_clock=sorted(common)), metrics


def interval(values, critical):
    if len(values) != 24 or any(value is None for value in values):
        return dict(status="unknown", n=sum(value is not None for value in values))
    mean = statistics.mean(values)
    half = critical * statistics.stdev(values) / math.sqrt(len(values))
    return dict(status="estimated", n=len(values), mean=mean, ci=[mean-half, mean+half])


def ranking_flips(metrics):
    """Compare within-arm pairs using fixed forecasts, omitting ties and empty masks."""
    flips, pairs = 0, 0
    for i, a in enumerate(BASELINES):
        for b in BASELINES[i+1:]:
            left, right = metrics[a], metrics[b]
            if left["observed"]["n"] and right["observed"]["n"]:
                observed = left["observed"]["mse"] - right["observed"]["mse"]
                oracle = left["oracle"]["mse"] - right["oracle"]["mse"]
                if abs(observed) > 1e-12 and abs(oracle) > 1e-12:
                    pairs += 1
                    flips += observed * oracle < 0
    return flips, pairs


def aggregate(records, protocol):
    contrasts, aggregates = [], {}
    cells = tuple(map(cell_name, arms(protocol)))
    critical = protocol["primary_critical"]
    for scenario in protocol["scenarios"]:
        group = records[scenario]
        if len(group) != 24 or len({r["seed"] for r in group}) != 24 or any(
                set(row["cells"]) != set(cells) for row in group):
            raise ValueError("incomplete factorial evidence")
        by_cell = {}
        for cell in cells:
            rows = [r["cells"][cell] for r in group]
            counts = ("planned", "forecast_count", "label_count", "score_count",
                      "never_returned", "after_cutoff", "no_forecast_count", "no_score_count")
            secondary = protocol["secondary_critical"]
            flips = [ranking_flips(r["metrics"]) for r in rows]
            by_cell[cell] = dict(counts={k:interval([r[k] for r in rows],secondary) for k in counts},
                                 runs_with_rank_flip=sum(f > 0 for f, _ in flips),
                                 runs_with_comparable_pairs=sum(n > 0 for _, n in flips),
                                 flipped_pair_count=sum(f for f, _ in flips),
                                 comparable_pair_count=sum(n for _, n in flips), strategies={})
            for strategy in BASELINES:
                ms = [r["metrics"][strategy] for r in rows]
                summary = {view:{metric:interval([m[view][metric] for m in ms],secondary)
                           for metric in ("mse","mae","rmse")}
                           for view in ("oracle","observed","recovery","factor_oracle")}
                summary.update({metric:interval([m[metric] for m in ms],secondary) for metric in
                                ("updates","update_attempts","intended_updates","update_success_rate","fallback_count","large_error_count",
                                 "train_available_mean","cost_per_scored","cost_per_planned","selection_bias")})
                by_cell[cell]["strategies"][strategy] = summary
        aggregates[scenario] = by_cell

        for strategy in BASELINES:
            def contrast(terms):
                values = []
                for row in group:
                    terms_values = [(sign, row["cells"][cell]["metrics"][strategy]["factor_oracle"]["mse"])
                                    for sign, cell in terms]
                    values.append(None if any(v is None for _, v in terms_values) else
                                  math.fsum(sign*v for sign, v in terms_values))
                return interval(values, critical)

            ref = "clean/mcar0/short"
            conditions = {"input": "high/mcar0/short", "missing": "clean/mcar55/short",
                          "latency": "clean/mcar0/long"}
            for factor, cell in conditions.items():
                contrasts.append(dict(scenario=scenario,strategy=strategy,contrast=factor,
                                      result=contrast(((1,cell),(-1,ref)))))
            for factor, joint in (("missing","high/mcar55/short"),("latency","high/mcar0/long")):
                terms = ((1,joint),(-1,conditions["input"]),(-1,conditions[factor]),(1,ref))
                contrasts.append(dict(scenario=scenario,strategy=strategy,contrast=f"input_x_{factor}",
                                      result=contrast(terms)))
            for mode in ("mcar55", "mar", "mnar"):
                values = [r["cells"][f"clean/{mode}/short"]["metrics"][strategy]["selection_bias"] for r in group]
                contrasts.append(dict(scenario=scenario,strategy=strategy,contrast=f"selection_{mode}",
                                      result=interval(values,critical)))
    if len(contrasts) != protocol["primary_family_size"]:
        raise ValueError("primary family differs from preregistration")
    return aggregates, contrasts


def run(protocol, frozen, lock_sha):
    started = time.monotonic()
    runs = ROOT / "round35-runs"
    runs.mkdir()
    records = {scenario:[] for scenario in protocol["scenarios"]}
    for seed in protocol["report_seeds"]:
        dump(runs/f"{seed}-reserved.json", dict(seed=seed, lock_sha256=lock_sha))
    raw_evidence = {}
    for seed in protocol["report_seeds"]:
        for scenario in protocol["scenarios"]:
            if time.monotonic()-started >= protocol["max_wall_seconds"]:
                raise TimeoutError("report resource limit exceeded")
            trace, cells = cases_for(scenario, seed, protocol, frozen["source_sha256"]["protocol35.json"])
            path = runs/f"{scenario}-{seed}.json.gz"
            with path.open("xb") as stream:
                data = json.dumps(trace, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
                stream.write(gzip.compress(data, compresslevel=4, mtime=0))
            raw_evidence[path.relative_to(ROOT).as_posix()] = sha(path)
            records[scenario].append(dict(seed=seed,cells=cells))
    if freeze(protocol) != frozen or sha(ROOT/"round35-lock.json") != lock_sha:
        raise ValueError("lock or source changed during execution")
    aggregated, contrasts = aggregate(records, protocol)
    dump(ROOT/"round35-results.json", dict(lock_sha256=lock_sha, records=records, aggregates=aggregated,
                                           contrasts=contrasts, raw_sha256=raw_evidence,
                                           seconds=time.monotonic()-started))


def main():
    if len(sys.argv) != 2 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit("Use Python -I -S -B study35.py freeze|report")
    protocol = read("protocol35.json")
    frozen = freeze(protocol)
    lock = ROOT/"round35-lock.json"
    if sys.argv[1] == "freeze":
        if (ROOT/"round35-runs").exists() or (ROOT/"round35-results.json").exists():
            raise ValueError("report already started")
        dump(lock, dict(frozen=frozen, created_utc=datetime.now(timezone.utc).isoformat()))
        print(sha(lock))
    elif sys.argv[1] == "report":
        lock_sha = sha(lock)
        receipt = read("round35-independent-freeze.json")
        if (read(lock.name)["frozen"] != frozen or receipt["lock_sha256"] != lock_sha
                or receipt["status"] != "verified_before_report"):
            raise ValueError("freeze witness mismatch")
        run(protocol, frozen, lock_sha)
        print(f"{protocol['max_sequences']} baseline triplets completed")
    else:
        raise SystemExit("Unknown operation")


if __name__ == "__main__":
    main()

