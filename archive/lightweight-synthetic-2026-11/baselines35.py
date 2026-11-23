"""Frozen Round 34 baseline decisions over visible packets only."""
from dataclasses import asdict

from engine_five34 import BASELINES, CONFIG, _score
from feedback35 import CUTOFF, STOP, WARMUP
from prequential_synthetic31 import _fit, _mean, _mean_evidence, _version
from quality35 import check_label, check_split, feature_status, finite


class InvalidStream(ValueError):
    def __init__(self, message, history):
        super().__init__(message)
        self.predictions = tuple(history)


def run_online(dataset, arm, stream):
    """Predict, process feedback, then schedule a model for the next tick."""
    if not isinstance(dataset, str) or not dataset.startswith("synthetic35:"):
        raise ValueError("requires Round 35 synthetic scope")
    seen, arrived, models = {}, {}, {}
    available, forecasts, releases, updates, decisions = [], [], [], [], []
    iterator = iter(stream)

    def update(strategy, tick, initial=False):
        fit_set = available[-CONFIG["train_window"]:] if initial else available
        model_kind, fit_status, value, evidence = "unavailable", "no_labels", None, {}
        if fit_set:
            model_kind, fit_status = "mean", "mean"
            value, evidence = _mean(fit_set), _mean_evidence(fit_set)
        if strategy != "mean":
            try:
                fitted, fitted_evidence = _fit(fit_set, tick)
                model_kind, fit_status = "ridge", "ridge"
                value, evidence = asdict(fitted), fitted_evidence
            except (ValueError, ArithmeticError):
                fit_status = "fallback_mean" if fit_set else "no_labels"
        version = 0 if initial else models[strategy]["version"] + 1
        model = _version(f"{dataset}|{arm}", strategy, version, model_kind, value,
                         tick, tick + 1, evidence, fit_status)
        updates.append(dict(model, train_available=len(fit_set),
                            fit_status=fit_status, active=model_kind != "unavailable"))
        models[strategy] = model

    for tick in range(CUTOFF + 1):
        event = next(iterator, None)
        if not isinstance(event, tuple) or len(event) != 3 or type(event[0]) is not int or event[0] != tick:
            raise InvalidStream("missing, repeated or out-of-order decision tick", forecasts)
        _, packet, feedback = event
        if not isinstance(feedback, tuple):
            raise InvalidStream("invalid feedback batch", forecasts)
        if tick < STOP:
            quality = feature_status(packet, dataset, tick)
            if quality == "invalid":
                raise InvalidStream("invalid feature", forecasts)
            seen[packet.sample] = (asdict(packet), quality)
            if tick >= WARMUP:
                decisions.append(dict(tick=tick, sample=packet.sample, quality=quality,
                                      features=packet.values, source=packet.source,
                                      available=packet.available, train_available=len(available)))
                for strategy, model in models.items():
                    if model["model_activation_time"] > tick:
                        raise InvalidStream("model used before activation", forecasts)
                    can_predict = quality == "valid" and model["kind"] != "unavailable"
                    prediction = _score(model, packet.values) if can_predict else None
                    if can_predict and not finite(prediction):
                        raise InvalidStream("non-finite prediction", forecasts)
                    forecasts.append(dict(tick=tick, sample=packet.sample, strategy=strategy,
                                          quality=quality, status="valid" if can_predict else
                                          (quality if quality != "valid" else "unavailable"),
                                          prediction=prediction, model_id=model["model_id"],
                                          model_kind=model["kind"]))
        elif packet is not None:
            raise InvalidStream("unexpected feature past decision horizon", forecasts)

        for label in feedback:
            try:
                check_label(label, dataset, tick, seen, arrived)
            except ValueError as exc:
                raise InvalidStream(str(exc), forecasts) from exc
            feature, quality = seen[label.sample]
            arrived[label.sample] = asdict(label)
            eligible = quality == "valid"
            releases.append(dict(asdict(label), received=tick, eligible=eligible))
            if eligible:
                available.append(dict(sample_id=label.sample, decision_time=label.tick,
                                      features=feature["values"], target_end_time=label.target_end,
                                      label_available_time=tick, target=label.target))

        if tick == WARMUP - 1:
            check_split([r["decision_time"] for r in available], range(WARMUP, STOP), WARMUP)
            available.sort(key=lambda row: row["decision_time"])
            for strategy in BASELINES:
                update(strategy, tick, initial=True)
        if WARMUP <= tick < STOP and tick % CONFIG["check_interval"] == 0:
            update("mean", tick)
            if tick % CONFIG["periodic_interval"] == 0:
                update("periodic_ridge", tick)

    end = object()
    if next(iterator, end) is not end:
        raise InvalidStream("extra event after cutoff", forecasts)
    return dict(dataset=dataset, arm=arm, forecasts=forecasts, releases=releases,
                decisions=decisions, updates=updates,
                warmup_features=[packet for packet, _ in list(seen.values())[:WARMUP]])

