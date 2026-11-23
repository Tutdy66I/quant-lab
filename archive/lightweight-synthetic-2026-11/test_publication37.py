"""Round 37 qualification, transport, and publication regression tests."""
import copy
import unittest

from engine_five34 import BASELINES
from missing36 import fit_response, weighted
from publication37 import build_report, choose_winner, fraction, publication
from study37 import freeze, interval_coverage, read
from transfer37 import cases


def report(n=40,forecast=40,returned=16,common=16,quality=None):
    coverage=dict(planned=n,common=fraction(common,n),legal_features=fraction(forecast,n),
                  labels={k:fraction(v,n) for k,v in (("arrived",returned),("unknown",n-returned),
                           ("pending_offline",0),("permanent_missing_offline",0))},
                  quality=quality or {"valid":fraction(forecast,n),"unavailable":fraction(n-forecast,n)},
                  models={s:dict(predicted=fraction(forecast,n),verified=fraction(forecast,n),
                                 scored=fraction(common,n)) for s in BASELINES})
    return dict(identity_verified=True,target=coverage,config_sha256="1"*64,manifest_sha256="2"*64,
                cohort_sha256="3"*64,versions_sha256="4"*64)


def release(r=None, *, diagnose=None, weights=None, **overrides):
    r=r or report()
    diagnostics=diagnose or dict(stability="pass",probabilities=dict(unknown=0,minimum=.2))
    w=weights if weights is not None else {s:dict(status="identified_under_assumptions",ess=15.,
                 clipped=0,planned=40,observed=16) for s in BASELINES}
    params=dict(assumption="MAR",transport_attested=True,mature=True,sensitivity_available=False,expected_planned=40,expected_cohort_sha256="3"*64)
    params.update(overrides)
    return publication(r,"1"*64,diagnostics,w,**params)


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw,cls.summary=cases("relation_reverse",67200,"9"*64)

    def test_denominator_shrink_and_model_mismatch(self):
        self.assertEqual(release(report(n=16,forecast=16,returned=16,common=16))["status"],"invalid")
        r=report()
        r["target"]["common"]["denominator"]=16
        self.assertEqual(release(r)["status"],"invalid")
        r=report()
        r["target"]["models"]["periodic_ridge"]["scored"]=fraction(15,40)
        self.assertEqual(release(r)["status"],"invalid")

    def test_observational_calibration_cannot_prove_mar(self):
        self.assertEqual(release(assumption="unknown")["status"],"valid_observed_only")
        self.assertEqual(release(transport_attested=False)["status"],"valid_observed_only")
        self.assertEqual(release()["status"],"population_supported_under_assumptions")

    def test_unknown_feature_and_anonymous_version_rejected(self):
        r=report(forecast=39,quality={"valid":fraction(39,40),"unknown":fraction(1,40)})
        self.assertEqual(release(r)["status"],"invalid")
        for name in ("identity_verified","versions_sha256","cohort_sha256"):
            r=report()
            del r[name]
            self.assertEqual(release(r)["status"],"invalid")

    def test_wrong_configuration_and_time_flags(self):
        r=report()
        r["config_sha256"]="0"*64
        self.assertEqual(release(r)["status"],"invalid")
        self.assertEqual(release(mature=False)["status"],"valid_observed_only")

    def test_low_overlap_and_effective_sample_size(self):
        self.assertEqual(release(diagnose=dict(stability="pass",
                  probabilities=dict(unknown=0,minimum=.02)))["status"],"valid_observed_only")
        w={s:dict(weighted([1.]*16+[None]*24,[.125]*3+[1.]*37)) for s in BASELINES}
        self.assertLess(w["mean"]["ess"],12)
        self.assertEqual(release(weights=w)["status"],"valid_observed_only")

    def test_all_labels_return_with_partial_forecasts(self):
        r=report(forecast=20,returned=40,common=20)
        value=release(r)
        self.assertEqual(value["target"],"verified_forecast_cohort")
        self.assertEqual(value["status"],"population_supported_under_assumptions")
        self.assertEqual(release(report(returned=40,common=40))["target"],"planned")

    def test_mar_omission_drift_and_mnar_cannot_publish_total(self):
        summary=self.summary
        self.assertNotEqual(summary["omitted"]["publication"]["status"],"population_supported_under_assumptions")
        self.assertNotEqual(summary["sudden"]["publication"]["status"],"population_supported_under_assumptions")
        self.assertEqual(summary["mnar"]["publication"]["status"],"sensitivity_only")
        self.assertIsNone(summary["mnar"]["models"]["mean"]["oracle"])

    def test_missing_label_not_in_calibration_and_immature_ascertainment(self):
        rows=copy.deepcopy(self.summary["stable"]["calibration"])
        rows[0]["target"]=3.
        with self.assertRaisesRegex(ValueError,"visible covariate"):
            fit_response(rows,cutoff=121)
        with self.assertRaisesRegex(ValueError,"mature"):
            fit_response(self.summary["stable"]["calibration"],cutoff=120)
        immature=self.summary["immature"]
        self.assertGreater(immature["report"]["target"]["labels"]["pending_offline"]["numerator"],0)
        self.assertNotEqual(immature["publication"]["status"],"population_supported_under_assumptions")

    def test_missing_contrast_conflicting_ranks_and_intervals(self):
        sup=dict(status="population_supported_under_assumptions",target="planned")
        ranking=list(BASELINES)
        rivals=[dict(a="fixed_ridge",b=s,n=24,ci=[-.5,-.2]) for s in BASELINES[1:]]
        duplicate=[rivals[0],rivals[0]]
        self.assertIsNone(choose_winner([sup]*24,duplicate,[ranking]*2,24)["winner"])
        self.assertEqual(choose_winner([sup]*24,rivals,[ranking]*2,24)["winner"],"fixed_ridge")
        self.assertIsNone(choose_winner([sup]*24,rivals,[ranking,ranking[::-1]],24)["winner"])
        rivals[0]["ci"]=[-.2,.2]
        self.assertIsNone(choose_winner([sup]*24,rivals,[ranking]*2,24)["winner"])
        self.assertIsNone(choose_winner([release(assumption="MNAR",sensitivity_available=True)]*24,
                                         rivals,[ranking]*2,24)["winner"])

    def test_lower_gate_only_changes_eligibility(self):
        r=report(returned=9,common=9)
        self.assertEqual(release(r)["status"],"insufficient_evidence")
        self.assertEqual(release(r,threshold=(8,.2),assumption="unknown")["status"],"valid_observed_only")

    def test_tampered_active_model_rejected_before_report(self):
        original=self.raw["arms"]["stable"]
        trace=copy.deepcopy(original["trace"])
        trace["forecasts"][0]["model_id"]="forged"
        fixture=self.raw["fixtures"]["base"]
        with self.assertRaisesRegex(ValueError,"model"):
            build_report(trace,fixture["truth"],fixture["manifest"],fixture["anchor"],original["plan"],{},"9"*64)

    def test_full_development_matrix_and_earlier_seed_denied(self):
        self.assertEqual(len(self.summary),13)
        for arm in self.summary.values():
            self.assertEqual(arm["report"]["full"]["planned"],120)
        cfg=read("protocol37.json")
        with self.assertRaisesRegex(ValueError,"report seeds"):
            freeze(dict(cfg,report_seeds=[66600]+cfg["report_seeds"][1:]))
        self.assertEqual(interval_coverage([3.]*24,[3.]*24,cfg["secondary"]),
                         dict(covered=6,assessed=6,planned=6))

