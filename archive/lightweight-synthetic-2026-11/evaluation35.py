"""Offline-only evaluation of frozen forecasts against generated truth."""
import math

from engine_five34 import BASELINES, CONFIG, _score
from feedback35 import CUTOFF, RECOVERY, STOP, WARMUP
from quality35 import Feature, check_population, feature_status, identity


def losses(predictions, truth, clock):
    if not clock:
        return dict(n=0, mse=None, mae=None, rmse=None)
    errors = [predictions[t] - truth[t] for t in sorted(clock)]
    mse = math.fsum(v * v for v in errors) / len(errors)
    return dict(n=len(errors), mse=mse, mae=math.fsum(map(abs, errors))/len(errors),
                rmse=math.sqrt(mse))


def verify_run(run, rows, population, anchor):
    check_population(rows, population, anchor)
    dataset = population["dataset_id"]
    if run["dataset"] != dataset or len(rows) != STOP:
        raise ValueError("result dataset or planned population mismatch")
    clock = range(WARMUP, STOP)
    expected = {(e["source_ordinal"], identity(dataset, e["source_ordinal"]))
                for e in population["samples"] if e["source_ordinal"] in clock}
    decisions = {item["tick"]: item for item in run["decisions"]}
    if (len(run["decisions"]) != len(clock) or
            [(d["tick"], d["sample"]) for d in run["decisions"]] !=
            [(t, identity(dataset, t)) for t in clock]):
        raise ValueError("incomplete, duplicate or unordered decision receipt")
    known_quality = {}
    warmup = run["warmup_features"]
    if len(warmup) != WARMUP:
        raise ValueError("incomplete warmup receipt")
    def canonical(values):
        return tuple(values) if values is not None else None
    for tick in range(STOP):
        if tick < WARMUP:
            item = warmup[tick]
            packet = Feature(**dict(item, values=canonical(item["values"])))
        else:
            item = decisions[tick]
            packet = Feature(dataset, item["sample"], tick, canonical(item["features"]),
                             item["available"], item["source"], item["quality"] == "degraded")
        actual = feature_status(packet, dataset, tick)
        if (actual == "invalid" or (tick >= WARMUP and actual != item["quality"])
                or actual in ("valid", "degraded") and packet.values != tuple(rows[packet.source]["features"])):
            raise ValueError("feature provenance or value differs from anchored path")
        known_quality[tick] = actual
    targets = {row["time"]: row["target"] for row in rows}
    released = {}
    last = -1
    for event in run["releases"]:
        t = event["tick"]
        if (t in released or t not in targets or event["dataset"] != dataset
                or event["sample"] != identity(dataset, t) or event["target"] != targets[t]
                or event['target_end'] != t + 1 or event['eligible'] is not (known_quality[t] == 'valid')
                or not t + 1 < event["available"] <= event["received"] <= CUTOFF
                or event["received"] < last):
            raise ValueError("release does not match the anchored truth and time")
        released[t] = event["received"]
        last = event["received"]

    selected, update_counts = {}, {}
    if len(run["forecasts"]) != len(clock) * len(BASELINES):
        raise ValueError("missing or duplicate forecast evidence")
    for strategy in BASELINES:
        entries = [r for r in run["forecasts"] if r["strategy"] == strategy]
        versions = [v for v in run["updates"] if v["strategy"] == strategy]
        if (len(entries) != len(clock) or [(r["tick"], r["sample"]) for r in entries] !=
                [(t, identity(dataset, t)) for t in clock] or
                {(r["tick"], r["sample"]) for r in entries} != expected):
            raise ValueError("forecast identity, clock or count mismatch")
        period = {"fixed_ridge": None, "mean": CONFIG["check_interval"],
                  "periodic_ridge": CONFIG["periodic_interval"]}[strategy]
        schedule = [WARMUP - 1] + ([t for t in clock if t % period == 0] if period else [])
        for version, (model, tick) in enumerate(zip(versions, schedule)):
            if (model["version"] != version or model["retrain_time"] != tick
                    or model["model_activation_time"] != tick + 1
                    or model["model_id"] != f"{dataset}|{run['arm']}:{strategy}:v{version}"):
                raise ValueError("model activation schedule mismatch")
            if model["active"] is not (model["kind"] != "unavailable"):
                raise ValueError("activation status mismatch")
            used = model["training"]
            eligible = {identity(dataset, t) for t in released if released[t] <= tick
                        and known_quality[t] == "valid"}
            ids = used.get("mean_ids", []) + used.get("train_ids", []) + used.get("tune_ids", [])
            if len(ids) != len(set(ids)) or not set(ids) <= eligible:
                raise ValueError("model training included unavailable or repeated labels")
        if len(versions) != len(schedule):
            raise ValueError("incomplete model receipt")
        update_counts[strategy] = sum(v["active"] for v in versions[1:])
        predictions = {}
        for r in entries:
            tick = r["tick"]
            decision = decisions[tick]
            active = next(v for v in reversed(versions) if v["model_activation_time"] <= tick)
            if (active["model_id"] != r["model_id"] or active["kind"] != r["model_kind"]
                    or r["quality"] != decision["quality"]):
                raise ValueError("forecast model or quality mismatch")
            valid = decision["quality"] == "valid" and active["kind"] != "unavailable"
            if valid:
                if r["status"] != "valid" or r["prediction"] != _score(active, decision["features"]):
                    raise ValueError("forecast differs from active model")
                predictions[tick] = r["prediction"]
            elif r["prediction"] is not None or r["status"] != (
                    decision["quality"] if decision["quality"] != "valid" else "unavailable"):
                raise ValueError("abstention falsely reported as forecast")
        selected[strategy] = predictions
    if any(
            set(p) != set(selected[BASELINES[0]]) for p in selected.values()):
        raise ValueError("strategies have different scoreable prediction clocks")
    return targets, released, selected, update_counts


def describe(run, rows, population, anchor):
    truth, releases, predictions, counts = verify_run(run, rows, population, anchor)
    planned = set(range(WARMUP, STOP))
    valid = set(predictions[BASELINES[0]])
    labels = planned & releases.keys()
    observed = valid & labels
    metrics = {}
    for strategy, forecast in predictions.items():
        updates = [u for u in run["updates"] if u["strategy"] == strategy and u["version"] > 0]
        intended = "mean" if strategy == "mean" else "ridge"
        oracle = losses(forecast, truth, valid)
        visible = losses(forecast, truth, observed)
        charge = CONFIG["update_penalty"] * counts[strategy]
        metrics[strategy] = dict(
            oracle=oracle, observed=visible, recovery=losses(forecast, truth, {t for t in valid if t >= RECOVERY}),
            full_planned=oracle if valid == planned else None, updates=counts[strategy],
            update_attempts=len(updates), intended_updates=sum(u["fit_status"] == intended for u in updates),
            update_success_rate=(sum(u["fit_status"] == intended for u in updates)/len(updates)
                                 if updates else None),
            large_error_count=sum(abs(value - truth[t]) > 1. for t, value in forecast.items()),
            fallback_count=sum(u["fit_status"] in {"fallback_mean", "no_labels"}
                               for u in run["updates"] if u["strategy"] == strategy),
            initial_kind=next(u["kind"] for u in run["updates"] if u["strategy"] == strategy),
            forecast_using_mean_fallback=sum(f["strategy"] == strategy and f["status"] == "valid"
                and f["model_kind"] == "mean" and strategy != "mean" for f in run["forecasts"]),
            cost_per_planned=charge/len(planned), cost_per_scored=charge/len(observed) if observed else None,
            selection_bias=(visible["mse"] - oracle["mse"]) if observed else None,
            train_available_mean=math.fsum(d["train_available"] for d in run["decisions"])/len(planned))
    return dict(planned=len(planned), forecast_count=len(valid), label_count=len(labels),
                score_count=len(observed), never_or_delayed=len(planned - labels),
                quality_counts={status: sum(d["quality"] == status for d in run["decisions"])
                                for status in ("valid","unavailable","degraded","invalid","unknown")},
                no_forecast_count=len(planned-valid), no_score_count=len(planned-observed),
                prediction_clock=sorted(valid), label_clock=sorted(labels), scoring_clock=sorted(observed),
                metrics=metrics)

