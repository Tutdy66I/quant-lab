"""Development-seed contract, leakage, and evaluation failure probes."""
import copy
from dataclasses import replace
import unittest

from engine_five34 import BASELINES, run_online as prior_engine
from paths33 import make_case
from baselines35 import InvalidStream, run_online
from evaluation35 import describe
from feedback35 import CUTOFF, STOP, packets, release_plan, truth_path
from quality35 import Feature, Label, check_split, feature_status, identity
from synthetic_manifest27 import build_manifest, manifest_sha256
from study35 import cases_for, freeze, ranking_flips, read


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = "1" * 64
        cls.dataset, cls.rows, cls.population, cls.anchor = truth_path("relation_reverse", 65200, cls.config)
        cls.clean = tuple(packets(cls.dataset, cls.rows, 65200, "clean", "mcar0", "short"))
        cls.online = run_online(cls.dataset, "test", cls.clean)

    def test_feature_statuses_and_malformed_metadata(self):
        p = self.clean[65][1]
        self.assertEqual(feature_status(p, self.dataset, 65), "valid")
        for bad in (replace(p, available=66), replace(p, values=(float("nan"),)),
                    replace(p, values=(float("inf"),)), replace(p, values=(10**400,)),
                    replace(p, values=(True,)), replace(p, values=(1, 2)),
                    replace(p, available=64, source=64), replace(p, sample="wrong")):
            self.assertEqual(feature_status(bad, self.dataset, 65), "invalid")
        self.assertEqual(feature_status(replace(p, source=None), self.dataset, 65), "unknown")
        self.assertEqual(feature_status(replace(p, values=None, source=None, available=None),
                                        self.dataset, 65), "unavailable")
        self.assertEqual(feature_status(replace(p, source=64, available=64, stale_marked=True),
                                        self.dataset, 65), "degraded")

    def test_early_label_and_duplicate_conflict_stop_online(self):
        base_tick, p, original = self.clean[70]
        future = self.clean[71][2][0]
        for packet, message in ((future, "release timing"), (original[0], "duplicate"),
                                (replace(original[0], target=original[0].target+1), "conflict")):
            stream = list(self.clean)
            stream[70] = (base_tick, p, original + (packet,))
            with self.subTest(message=message), self.assertRaisesRegex(InvalidStream, message) as raised:
                run_online(self.dataset, "test", stream)
            self.assertEqual(raised.exception.predictions[-1]["tick"], 70)
            self.assertEqual(list(raised.exception.predictions)[:30], self.online["forecasts"][:30])

    def test_future_value_cannot_change_past_predictions_or_training(self):
        changed = copy.deepcopy(self.rows)
        changed[72]["target"] += 100.
        a = run_online(self.dataset, "test", packets(self.dataset, self.rows, 65200, "high", "mcar0","long"))
        b = run_online(self.dataset, "test", packets(self.dataset, changed, 65200, "high", "mcar0","long"))
        release = release_plan(changed, 65200, "mcar0", "long")[72]
        self.assertEqual([e for e in a["forecasts"] if e["tick"] <= release],
                         [e for e in b["forecasts"] if e["tick"] <= release])
        self.assertEqual([e for e in a["updates"] if e["retrain_time"] < release],
                         [e for e in b["updates"] if e["retrain_time"] < release])
        self.assertEqual(a["decisions"], b["decisions"])

    def test_unavailable_training_abstains_and_never_fakes_ridge(self):
        stream = [(t, p, tuple(label for label in batch if label.tick >= 65))
                  for t, p, batch in self.clean]
        result = run_online(self.dataset, "test", stream)
        self.assertEqual([f["prediction"] for f in result["forecasts"][:3]], [None]*3)
        self.assertTrue(all(v["fit_status"] == "no_labels" for v in result["updates"][:3]))
        self.assertTrue(any(v["fit_status"] == "fallback_mean" for v in result["updates"]))
        self.assertTrue(all(f["prediction"] is None for f in result["forecasts"]
                            if f["strategy"] == "fixed_ridge"))

    def test_bad_clock_and_extra_event_rejected(self):
        for stream in (self.clean[:70]+self.clean[71:], self.clean[:70]+self.clean[70:71]+self.clean[70:],
                       self.clean+(None, self.clean[-1]), self.clean[:75]+(self.clean[76],)+self.clean[76:]):
            with self.subTest(length=len(stream)), self.assertRaises(InvalidStream):
                run_online(self.dataset, "test", stream)
        for training, scoring in (([59,60],[60]), ([59,59],[60]), ([59],[61,61])):
            with self.assertRaises(ValueError):
                check_split(training, scoring, 60)

    def test_fixed_denominator_and_permanent_missing(self):
        a = run_online(self.dataset, "test", packets(self.dataset, self.rows, 65200,"high","mcar55","long"))
        result = describe(a, self.rows, self.population, self.anchor)
        self.assertEqual(result["planned"], 120)
        self.assertLess(result["score_count"], result["forecast_count"])
        self.assertEqual(sum(result["quality_counts"].values()), 120)
        for metric in result["metrics"].values():
            self.assertIsNone(metric["full_planned"])
            self.assertEqual(metric["observed"]["n"], result["score_count"])

    def test_anchored_truncation_and_forecast_loss_mismatch_rejected(self):
        shortened = build_manifest(self.rows[:-1], dataset_id=self.dataset,
                                   config_hash=self.config, dataset_fingerprint=self.config)
        with self.assertRaisesRegex(ValueError, "prior trusted anchor"):
            describe(self.online, self.rows[:-1], shortened, self.anchor)
        for field in ("forecasts", "updates", "releases", "decisions"):
            result = copy.deepcopy(self.online)
            if field == "forecasts":
                result[field][0]["model_id"] = "replacement"
            elif field == "updates":
                result[field][0]["training"]["train_ids"].append(identity(self.dataset, 62))
            elif field == "releases":
                result[field][-1]["target"] += 1.
            else:
                result[field][0]["features"] = (99.,)
            with self.subTest(field=field), self.assertRaises(ValueError):
                describe(result, self.rows, self.population, self.anchor)

    def test_unverified_origin_and_clock_forgery_not_scored(self):
        t, p, batch = self.clean[64]
        stream = list(self.clean)
        stream[64] = t, replace(p, source=None), batch
        result = run_online(self.dataset, "test", stream)
        verified = describe(result, self.rows, self.population, self.anchor)
        self.assertEqual(verified["quality_counts"]["unknown"], 1)
        self.assertNotIn(64, verified["scoring_clock"])
        stream[64] = t, Feature(self.dataset, p.sample, t, self.clean[63][1].values,t,t), batch
        with self.assertRaisesRegex(ValueError, "feature provenance"):
            describe(run_online(self.dataset, "test", stream), self.rows,self.population,self.anchor)

    def test_label_mechanisms_and_control_clock(self):
        plan = {name:release_plan(self.rows,65200,name,"short") for name in ("mcar0","mcar55","mar","mnar")}
        for value in plan.values():
            self.assertEqual(value[:60], list(range(2,62)))
        self.assertEqual(len(set(tuple(a) for a in plan.values())),4)
        self.assertTrue(any(at is None for at in plan["mcar55"]))
        batch = release_plan(self.rows,65200,"mcar0","batch")
        self.assertEqual(batch[80:108], [112]*28)
        late = release_plan(self.rows,65200,"mcar0","long")
        self.assertTrue(any(at > CUTOFF for at in late))
        self.assertEqual(len(late),STOP)

    def test_clean_transport_matches_frozen_baselines(self):
        data, options = make_case("full_short", 65202)
        thresholds = read("round32-alarm-dev-results.json")["thresholds"]
        prior = prior_engine(data, alarm_config=thresholds, **options)
        dataset = "synthetic35:parity"
        deliveries = {}
        for row in data:
            tick = row["decision_time"]
            at = row["label_available_time"]
            deliveries.setdefault(at, []).append(
                Label(dataset, identity(dataset,tick), tick, row["target_end_time"], at, row["target"]))
        events = [(t, Feature(dataset, identity(dataset,t),t,tuple(data[t]["features"]),t,t)
                   if t < STOP else None, tuple(deliveries.get(t,()))) for t in range(CUTOFF+1)]
        tested = run_online(dataset, "parity", events)
        self.assertEqual([(r["strategy"],r["decision_time"],r["prediction"]) for r in prior["predictions"]
                          if r["strategy"] in BASELINES],
                         [(r["strategy"],r["tick"],r["prediction"]) for r in tested["forecasts"]])
        self.assertEqual([(r["strategy"],r["retrain_time"],r["value"]) for r in prior["events"]
                          if r["event_type"] == "activation" and r["strategy"] in BASELINES],
                         [(r["strategy"],r["retrain_time"],r["value"]) for r in tested["updates"]])

    def test_different_forecast_support_fast_fails_score(self):
        test_stream = [(t,p,tuple(label for label in batch if label.tick >= 65))
                       for t,p,batch in self.clean]
        result = run_online(self.dataset, "test", test_stream)
        with self.assertRaisesRegex(ValueError, "different scoreable prediction clocks"):
            describe(result, self.rows, self.population, self.anchor)

    def test_seed_and_shared_factorial_support(self):
        protocol = read("protocol35.json")
        bad = dict(protocol, report_seeds=[65200]+protocol["report_seeds"][1:])
        with self.assertRaisesRegex(ValueError,"seed"):
            freeze(bad)
        raw, metrics = cases_for("stable_signal",65201,protocol,self.config)
        common = set(raw["factor_clock"])
        self.assertEqual(len(metrics),21)
        self.assertEqual(common, set.intersection(*(set(c["prediction_clock"]) for c in metrics.values())))
        self.assertTrue(all(m["factor_oracle"]["n"] == len(common) for c in metrics.values()
                            for m in c["metrics"].values()))
        self.assertTrue(any(ranking_flips(c["metrics"])[1] > 0 for c in metrics.values()))

