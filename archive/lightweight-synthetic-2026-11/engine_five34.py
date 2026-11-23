"""Five existing R33 policies for the R34 synthetic development experiment.

The online loop preserves the R33 time and fitting semantics. Its candidate
loops use the fixed five below, so excluded policy variants never fit or
predict. The historical R33 module and its evidence stay byte-identical.
"""
from dataclasses import asdict
import math

import drift_robust32 as robust
import prequential_synthetic31 as baseline
from engine_cross33 import (
    BASELINES, CONFIG as CROSS_CONFIG, InitialRidgeError,
    _delivery_schedule, _number, _score, _validate_row, choose_update,
)
from ridge_synthetic import predict_ridge

CANDIDATES = ("original__original", "robust__original")
STRATEGIES = (*BASELINES, *CANDIDATES)
CONFIG = {
    **CROSS_CONFIG,
    "alpha_candidates": tuple(CROSS_CONFIG["alpha_candidates"]),
    "version": "prequential34-v1",
}


def run_online(rows, *, initial_limit=None, alarm_config=None, failure_times=(),
               interrupt_times=(), deliveries=None, strict_initial_ridge=False):
    """Record predictions before receiving labels and schedule all updates for t+1."""
    warmup = CONFIG["warmup"]
    if type(strict_initial_ridge) is not bool:
        raise ValueError("strict_initial_ridge must be Boolean")
    if not isinstance(rows, (list, tuple)) or len(rows) < 100:
        raise ValueError("expected complete synthetic sequence")
    if initial_limit is not None and (type(initial_limit) is not int or initial_limit < 1):
        raise ValueError("initial_limit must be a positive integer")
    if not isinstance(alarm_config, dict) or set(alarm_config) != {"mean", "variance", "relation"}:
        raise ValueError("frozen robust thresholds required")
    if any(_number(value) <= 0 for value in alarm_config.values()) or alarm_config["variance"] < 1:
        raise ValueError("thresholds must be positive and variance ratio threshold >= 1")
    alarm_config = dict(alarm_config)
    for times in (failure_times, interrupt_times):
        if not isinstance(times, (tuple, list)) or any(type(tick) is not int for tick in times):
            raise ValueError("injected times must be integer sequences")
    dataset = rows[0].get("synthetic_dataset_id") if isinstance(rows[0], dict) else None
    if not isinstance(dataset, str) or not dataset.startswith(("synthetic31:", "synthetic32:", "synthetic33:")):
        raise ValueError("synthetic dataset identity required")
    transport = _delivery_schedule(deliveries)
    queue, visible, available, arrived = {}, {}, [], set()
    predictions, feedback, events = [], [], []
    models, versions, per_sample, fixed_losses = {}, dict.fromkeys(STRATEGIES, 0), {}, {}

    def observe(row, tick):
        _validate_row(row, tick, dataset)
        visible[row["sample_id"]] = row
        due = row["label_available_time"]
        if transport is None and due is not None:
            queue.setdefault(due, []).append(row)

    def receive(tick):
        packets = queue.get(tick, ()) if transport is None else transport.get(tick, ())
        for packet in packets:
            sample = packet["sample_id"]
            row = visible.get(sample)
            if sample in arrived or row is None:
                raise ValueError("duplicate or unknown feedback identity")
            due = row["label_available_time"]
            if due is None or tick < due or tick < row["target_end_time"]:
                raise ValueError("feedback arrived before its declared availability")
            target = _number(packet.get("target"))
            if transport is not None and target != _number(row.get("target")):
                raise ValueError("feedback target does not match source sample")
            observed = {key: row[key] for key in ("sample_id", "decision_time", "features", "target_end_time")}
            observed.update(label_available_time=tick, target=target)
            available.append(observed)
            arrived.add(sample)
            for strategy, pred in per_sample.get(sample, {}).items():
                error = pred["prediction"] - target
                loss = _number(error ** 2)
                feedback.append(dict(sample_id=sample, strategy=strategy, label_time=tick,
                                     target=target, squared_loss=loss, absolute_loss=abs(error),
                                     active_model_id=pred["active_model_id"]))
                if strategy == "fixed_ridge":
                    fixed_losses[sample] = loss

    for tick, row in enumerate(rows[:warmup]):
        observe(row, tick)
        receive(tick)
    # R31 warm-up collected known labels in decision order.
    available.sort(key=lambda sample: sample["decision_time"])
    initial = available if initial_limit is None else available[-initial_limit:]
    initial_ids = [sample["sample_id"] for sample in initial]
    if strict_initial_ridge and (len(initial) < CONFIG["min_train"]
                                 or initial_limit is not None and len(initial) != initial_limit):
        raise InitialRidgeError(f"insufficient initial released labels for strict Ridge: {len(initial)}")
    try:
        fitted, fit_meta = baseline._fit(initial, warmup - 1)
        predict_ridge(fitted, initial[-1]["features"])
    except Exception as exc:
        if strict_initial_ridge:
            raise InitialRidgeError(f"strict initial Ridge fit failed: {type(exc).__name__}") from exc
        fitted, fit_meta = None, None
    init_mean = baseline._mean(initial)
    for strategy in STRATEGIES:
        kind = "mean" if strategy == "mean" or fitted is None else "ridge"
        value = init_mean if kind == "mean" else asdict(fitted)
        meta = baseline._mean_evidence(initial) if kind == "mean" else fit_meta
        model = baseline._version(dataset, strategy, 0, kind, value, warmup - 1,
                                  warmup, meta, "initial")
        models[strategy] = model
        events.append(dict(model, event_type="activation"))

    reference = [row["features"][0] for row in rows[:warmup]]
    reference_mean = math.fsum(reference) / len(reference)
    reference_var = math.fsum((x - reference_mean) ** 2 for x in reference) / len(reference)
    if reference_var <= 1e-12:
        raise ValueError("degenerate feature reference")
    reference_losses = [(row["target"] - _score(models["fixed_ridge"], row["features"])) ** 2
                        for row in available]
    residual_reference = max(math.fsum(reference_losses) / len(reference_losses), 1e-9) if reference_losses else 1.0
    previous_residual_ids = ()
    last_attempt = dict.fromkeys(CANDIDATES, -10**9)
    for tick in range(warmup, len(rows)):
        row = rows[tick]
        try:
            observe(row, tick)
        except ValueError as exc:
            raise baseline.CausalInputError(str(exc), tick, predictions, feedback, events) from exc
        sample = row["sample_id"]
        entries = {}
        for strategy in STRATEGIES:
            model = models[strategy]
            if model["model_activation_time"] > tick or sample in per_sample:
                raise ValueError("invalid model activation or duplicate prediction")
            record = {key: row[key] for key in ("sample_id", "synthetic_dataset_id", "decision_time",
                      "feature_available_time", "target_start_time", "target_end_time", "label_available_time")}
            record.update(features=list(row["features"]), strategy=strategy,
                          prediction=_score(model, row["features"]), active_model_id=model["model_id"],
                          alarm_time=model["alarm_time"], retrain_time=model["retrain_time"],
                          model_activation_time=model["model_activation_time"])
            entries[strategy] = record
            predictions.append(record)
        per_sample[sample] = entries
        before_feedback = len(feedback)
        try:
            receive(tick)
        except (ValueError, OverflowError) as exc:
            raise baseline.CausalInputError(str(exc), tick, predictions, feedback, events) from exc
        recent = range(max(warmup, tick - CONFIG["monitor_window"] + 1), tick + 1)
        recent_ids = [rows[i]["sample_id"] for i in recent]
        coverage = sum(key in arrived for key in recent_ids) / len(recent_ids)
        pending = sum(key not in arrived for key in per_sample)
        events.append(dict(event_type="decision", decision_time=tick, sample_id=sample,
                           features=list(row["features"]),
                           active_model_ids={s: model["model_id"] for s, model in models.items()},
                           new_feedback_ids=list(dict.fromkeys(f["sample_id"] for f in feedback[before_feedback:])),
                           available_labels=len(available), recent_label_coverage=coverage,
                           pending_feedback=pending))
        if tick % CONFIG["check_interval"]:
            continue
        w = [rows[i]["features"][0] for i in recent]
        mean_alarm = variance_alarm = False
        if len(w) >= CONFIG["monitor_window"]:
            mean = math.fsum(w) / len(w)
            var = math.fsum((x - mean) ** 2 for x in w) / len(w)
            mean_alarm = abs(mean - reference_mean) / math.sqrt(reference_var) >= CONFIG["mean_threshold"]
            variance_alarm = var <= 1e-12 or max(var / reference_var, reference_var / var) >= CONFIG["variance_threshold"]
        labeled = [sample for sample in available if sample["sample_id"] in fixed_losses][-CONFIG["monitor_window"]:]
        current_losses = [fixed_losses[sample["sample_id"]] for sample in labeled]
        ready = len(current_losses) >= CONFIG["monitor_window"]
        relation = math.fsum(current_losses) / len(current_losses) >= CONFIG["residual_threshold"] * residual_reference if ready else None
        original = mean_alarm or variance_alarm or relation is True
        robust_axes = robust.evaluate(reference, w, reference_losses, current_losses, alarm_config)
        residual_ids = tuple(v["sample_id"] for v in labeled)
        fresh_relation = residual_ids != previous_residual_ids
        previous_residual_ids = residual_ids
        events.append(dict(event_type="alarm", decision_time=tick, alarm_time=tick,
                           mean_alarm=mean_alarm, variance_alarm=variance_alarm,
                           relationship_status="available" if ready else "unavailable",
                           relationship_alarm=relation, alarm=original, robust_axes=robust_axes,
                           fresh_relation=fresh_relation,
                           available_labels=len(available),
                           max_label_time=max((v["label_available_time"] for v in labeled), default=None)))
        axes_by_alarm = {
            "original": {"mean": mean_alarm, "variance": variance_alarm, "relation": relation},
            "robust": robust_axes,
        }
        policies = {"mean": (True, "periodic_mean"),
                    "periodic_ridge": (tick % CONFIG["periodic_interval"] == 0, "periodic_ridge")}
        for strategy in CANDIDATES:
            alarm, gate = strategy.split("__")
            axes = axes_by_alarm[alarm]
            raw_signal, triggers, blocked = choose_update(
                gate, axes, fresh_relation=fresh_relation, available=len(available),
                coverage=coverage, pending=pending, tick=tick, last_attempt=last_attempt[strategy])
            events.append(dict(event_type="candidate_update", strategy=strategy, decision_time=tick,
                               alarm_rule=alarm, update_gate=gate, axes=dict(axes), raw_alarm=raw_signal,
                               qualified=not blocked, triggers=triggers,
                               blocked=blocked, status="held" if blocked else "attempt"))
            policies[strategy] = (not blocked, strategy + ":" + ",".join(triggers))
        for strategy, (update, reason) in policies.items():
            if not update:
                continue
            if strategy != "mean" and tick in interrupt_times:
                raise baseline.ModelUpdateInterrupted(tick, strategy, predictions, feedback, events)
            if strategy in last_attempt:
                last_attempt[strategy] = tick
            kind, value, meta = "mean", baseline._mean(available), baseline._mean_evidence(available)
            if strategy != "mean":
                try:
                    if tick in failure_times:
                        raise ValueError("injected training failure")
                    fitted, meta = baseline._fit(available, tick)
                    predict_ridge(fitted, row["features"])
                    kind, value = "ridge", asdict(fitted)
                except Exception as exc:
                    keep_model = strict_initial_ridge
                    events.append(dict(event_type="fit_failure", strategy=strategy, decision_time=tick,
                                       reason=type(exc).__name__, fallback_activation=not keep_model,
                                       retained_model_id=models[strategy]["model_id"] if keep_model else None))
                    if keep_model:
                        continue
                    reason += ":fallback_mean"
                    meta = baseline._mean_evidence(available)
            versions[strategy] += 1
            alarm_time = tick if strategy in last_attempt else None
            model = baseline._version(dataset, strategy, versions[strategy], kind, value, tick,
                                      tick + 1, meta, reason, alarm_time)
            models[strategy] = model
            events.append(dict(model, event_type="activation"))

    metrics = {}
    for strategy in STRATEGIES:
        losses = [f for f in feedback if f["strategy"] == strategy]
        updates = [e for e in events if e["event_type"] == "activation"
                   and e["strategy"] == strategy and e["version"] > 0]
        failed = sum(e["event_type"] == "fit_failure" and e["strategy"] == strategy for e in events)
        candidate_fits = sum(e["training"].get("candidate_fits", 0) for e in updates)
        n = len(losses)
        cost = len(updates) * CONFIG["update_penalty"]
        mse = math.fsum(f["squared_loss"] for f in losses) / n if n else None
        metrics[strategy] = dict(n_scored=n, mse=mse, rmse=math.sqrt(mse) if n else None,
            mae=math.fsum(f["absolute_loss"] for f in losses) / n if n else None,
            updates=len(updates), penalty=cost, mean_update_cost=cost / n if n else None,
            objective=mse + cost / n if n else None, completed_candidate_fit_count=candidate_fits,
            failed_fit_attempts=failed, candidate_fit_attempts=len([e for e in updates if e["kind"] == "ridge"]) + failed)
    observed_digest = baseline._digest({
        "features": [{key: row[key] for key in ("sample_id", "decision_time",
                      "feature_available_time", "target_start_time", "target_end_time",
                      "label_available_time", "features")} for row in rows],
        "delivered_labels": [{key: row[key] for key in ("sample_id", "label_available_time", "target")}
                             for row in available]})
    result = dict(scope="synthetic development", synthetic_dataset_id=dataset,
                  config={**CONFIG, "alpha_candidates": list(CONFIG["alpha_candidates"])},
                  alarm_config=alarm_config, strict_initial_ridge=strict_initial_ridge,
                  prediction_start=warmup, horizon=len(rows) - 1,
                  observed_data_sha256=observed_digest,
                  initial_available_labels=len(initial_ids), initial_label_ids=initial_ids,
                  predictions=predictions, feedback=feedback, events=events, metrics=metrics,
                  censored_prediction_samples=len(per_sample) - metrics["fixed_ridge"]["n_scored"])
    result["audit_sha256"] = baseline._digest(result)
    return result


def verify_run(result, expected_sha256, *, rows, **run_args):
    body = {k: v for k, v in result.items() if k != "audit_sha256"}
    if baseline._digest(body) != expected_sha256 or result.get("audit_sha256") != expected_sha256:
        raise ValueError("online evidence bytes do not match expected digest")
    regenerated = run_online(rows, **run_args)
    if baseline._digest(regenerated) != baseline._digest(result):
        raise ValueError("logged decisions do not match causal replay")
    return True
