"""Causal evidence and paired strategy tests using only development paths."""
import copy
import unittest
from unittest import mock

import prequential_synthetic31 as baseline
import prequential_synthetic32 as old_online
import engine_cross33 as online

THRESHOLDS = {"mean": 1.6, "variance": 5.0, "relation": 4.0}


class Online33Tests(unittest.TestCase):
    def setUp(self):
        self.rows = baseline.generate("relation_reverse", 32019)

    def run_case(self, **kwargs):
        return online.run_online(self.rows, alarm_config=THRESHOLDS, **kwargs)

    def test_preserves_six_existing_arms_on_development_seed(self):
        old = old_online.run_online(self.rows, alarm_config=THRESHOLDS)
        new = self.run_case()
        aliases = {
            "fixed_ridge": "fixed_ridge", "mean": "mean", "periodic_ridge": "periodic_ridge",
            "adaptive": "original__original", "delay_aware": "robust__delay_aware",
            "conservative": "confirmed__conservative",
        }
        for strategy, crossed_name in aliases.items():
            for field in ("predictions", "feedback"):
                def normalize(record):
                    return {k: v for k, v in record.items() if k not in ("strategy", "active_model_id")}
                before = [normalize(row) for row in old[field] if row["strategy"] == strategy]
                after = [normalize(row) for row in new[field] if row["strategy"] == crossed_name]
                self.assertEqual(before, after, (strategy, field))
            self.assertEqual(old["metrics"][strategy], new["metrics"][crossed_name])

    def test_all_nine_combinations_share_each_pre_update_alarm(self):
        result = self.run_case()
        alarms = {e["decision_time"]: e for e in result["events"] if e["event_type"] == "alarm"}
        for tick, alarm in alarms.items():
            updates = [e for e in result["events"] if e["event_type"] == "candidate_update"
                       and e["decision_time"] == tick]
            self.assertEqual({e["strategy"] for e in updates}, set(online.CANDIDATES))
            for update in updates:
                rule = update["alarm_rule"]
                if rule == "original":
                    self.assertEqual(update["raw_alarm"], alarm["alarm"])
                else:
                    self.assertEqual(update["axes"], alarm[rule + "_axes"])
                self.assertEqual(update["qualified"], update["status"] == "attempt")
                if not update["raw_alarm"]:
                    self.assertFalse(update["qualified"])
        for event in result["events"]:
            if event["event_type"] == "activation" and event["strategy"] in online.CANDIDATES and event["version"]:
                self.assertEqual(event["model_activation_time"], event["retrain_time"] + 1)
                self.assertIn(event["retrain_time"], alarms)

    def test_strict_initial_ridge_does_not_silently_fallback(self):
        for samples in (27, 45):
            run = self.run_case(initial_limit=samples, strict_initial_ridge=True, failure_times=(100,))
            self.assertEqual(len(run["initial_label_ids"]), samples)
            self.assertTrue(all(e["kind"] == "ridge" for e in run["events"]
                                if e["event_type"] == "activation" and e["strategy"] != "mean"))
        with self.assertRaises(online.InitialRidgeError):
            self.run_case(initial_limit=18, strict_initial_ridge=True)
        with mock.patch.object(baseline, "_fit", side_effect=ValueError("bad factorization")):
            with self.assertRaises(online.InitialRidgeError):
                self.run_case(initial_limit=27, strict_initial_ridge=True)

    def test_orthogonal_gates_distinguish_rejection_reasons(self):
        context = dict(fresh_relation=True, available=30, coverage=0.7, pending=12, tick=90, last_attempt=70)
        axes = {"mean": True, "variance": False, "relation": None}
        self.assertEqual(online.choose_update("original", axes, **context), (True, ["mean"], []))
        self.assertEqual(online.choose_update("delay_aware", axes, **context), (True, ["mean"], []))
        self.assertEqual(online.choose_update("conservative", axes, **context)[2], ["cooldown"])
        context.update(pending=13)
        self.assertEqual(online.choose_update("delay_aware", axes, **context)[2],
                         ["insufficient_feedback_coverage_or_backlog"])
        context.update(fresh_relation=False, last_attempt=0)
        self.assertEqual(online.choose_update("conservative", dict(axes, mean=False, relation=True),
                                             **context)[2], ["stale_relation_evidence"])

    def test_explicit_initial_cap_freezes_same_ids_despite_post_warmup_delay(self):
        short = self.run_case(initial_limit=18)
        for row in self.rows[60:]:
            row["label_available_time"] += 30
        long = self.run_case(initial_limit=18)
        self.assertEqual(short["initial_label_ids"], long["initial_label_ids"])
        self.assertEqual(short["predictions"][:len(online.STRATEGIES)], [dict(p, label_available_time=short["predictions"][i]["label_available_time"])
                                                  for i, p in enumerate(long["predictions"][:len(online.STRATEGIES)])])
        self.assertEqual(len(short["initial_label_ids"]), 18)
        self.assertLess(long["metrics"]["fixed_ridge"]["n_scored"], short["metrics"]["fixed_ridge"]["n_scored"])

    def test_delayed_reordered_and_missing_feedback_use_actual_arrival(self):
        deliveries = [{"sample_id": row["sample_id"], "arrival_time": row["label_available_time"],
                       "target": row["target"]} for row in self.rows[:60]]
        row65, row66 = self.rows[65:67]
        deliveries += [{"sample_id": row66["sample_id"], "arrival_time": 72, "target": row66["target"]},
                       {"sample_id": row65["sample_id"], "arrival_time": 75, "target": row65["target"]}]
        result = self.run_case(deliveries=sorted(deliveries, key=lambda record: record["arrival_time"]))
        feedback = [f for f in result["feedback"] if f["strategy"] == "fixed_ridge"]
        self.assertEqual([(f["sample_id"], f["label_time"]) for f in feedback],
                         [(row66["sample_id"], 72), (row65["sample_id"], 75)])
        self.assertEqual(result["censored_prediction_samples"], 118)

    def test_rejects_duplicate_wrong_premature_and_backdated_delivery(self):
        row = self.rows[65]
        packet = {"sample_id": row["sample_id"], "arrival_time": 75, "target": row["target"]}
        cases = [[packet, packet], [dict(packet, sample_id="unmatched")],
                 [dict(packet, target=999)], [dict(packet, arrival_time=65)],
                 [packet, dict(packet, arrival_time=74)]]
        for deliveries in cases:
            with self.subTest(deliveries=deliveries), self.assertRaises(ValueError):
                self.run_case(deliveries=deliveries)

    def test_late_or_never_target_is_not_read_by_online_receipt(self):
        class HiddenTarget(dict):
            def get(self, key, default=None):
                if key == "target":
                    raise AssertionError("unavailable target read")
                return super().get(key, default)
        source = HiddenTarget(self.rows[66])
        source["label_available_time"] = None
        self.rows[66] = source
        result = self.run_case()
        self.assertTrue(online.verify_run(result, result["audit_sha256"],
                                         rows=self.rows, alarm_config=THRESHOLDS))
        self.assertNotIn(source["sample_id"], [record["sample_id"] for record in result["feedback"]])

    def test_conservative_confirmation_cooldown_and_fit_failure_retention(self):
        active = self.run_case()
        requests = [e["decision_time"] for e in active["events"] if
                    e["event_type"] == "candidate_update" and e["strategy"] == "confirmed__conservative" and e["status"] == "attempt"]
        self.assertTrue(requests)
        self.assertTrue(all(b - a >= online.CONFIG["conservative_cooldown"] for a, b in zip(requests, requests[1:])))
        tick = requests[0]
        failure = self.run_case(failure_times=[tick])
        event = next(e for e in failure["events"] if e["event_type"] == "fit_failure"
                     and e["strategy"] == "confirmed__conservative" and e["decision_time"] == tick)
        next_prediction = next(p for p in failure["predictions"] if p["strategy"] == "confirmed__conservative"
                               and p["decision_time"] == tick + 1)
        self.assertEqual(next_prediction["active_model_id"], event["retained_model_id"])
        self.assertFalse(any(e["event_type"] == "activation" and e["strategy"] == "confirmed__conservative"
                             and e["retrain_time"] == tick for e in failure["events"]))

    def test_backlog_blocks_delay_aware_update(self):
        for row in self.rows[60:]:
            row["label_available_time"] += 35
        result = self.run_case()
        blocked = [e for e in result["events"] if e["event_type"] == "candidate_update"
                   and e["strategy"] == "robust__delay_aware"
                   and "insufficient_feedback_coverage_or_backlog" in e["blocked"]]
        self.assertTrue(blocked)
        self.assertTrue(all(e["status"] == "held" for e in blocked))

    def test_interruption_preserves_completed_predictions_and_feedback(self):
        with self.assertRaises(baseline.ModelUpdateInterrupted) as ctx:
            self.run_case(interrupt_times=[80])
        exc = ctx.exception
        self.assertEqual(exc.decision_time, 80)
        self.assertTrue(exc.feedback)
        self.assertEqual(exc.predictions, self.run_case()["predictions"][:21 * len(online.STRATEGIES)])

    def test_replay_rejects_rehashed_prediction_and_frozen_threshold_change(self):
        original = self.run_case()
        for key in ("prediction", "active_model_id"):
            updated = copy.deepcopy(original)
            updated["predictions"][0][key] = "wrong"
            updated["audit_sha256"] = baseline._digest({k: v for k, v in updated.items() if k != "audit_sha256"})
            with self.assertRaises(ValueError):
                online.verify_run(updated, updated["audit_sha256"], rows=self.rows, alarm_config=THRESHOLDS)
        with self.assertRaises(ValueError):
            online.verify_run(original, original["audit_sha256"], rows=self.rows,
                              alarm_config=dict(THRESHOLDS, mean=999))

    def test_candidate_invalid_parameters_do_not_overwrite_model(self):
        active = self.run_case()
        tick = next(e["decision_time"] for e in active["events"] if e["event_type"] == "candidate_update"
                    and e["strategy"] == "confirmed__conservative" and e["status"] == "attempt")
        fit = baseline._fit

        def corrupted(rows, now):
            model, metadata = fit(rows, now)
            if now == tick:
                model = online.Model(model.means, model.scales, model.target_mean, (float("nan"),), model.alpha)
            return model, metadata

        with mock.patch.object(baseline, "_fit", side_effect=corrupted):
            result = self.run_case()
        self.assertTrue(any(e["event_type"] == "fit_failure" and e["strategy"] == "confirmed__conservative"
                            and e["decision_time"] == tick for e in result["events"]))
