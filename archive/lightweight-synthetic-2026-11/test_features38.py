"""Causal feature, frozen-fit and publication failure cases."""
import copy
from pathlib import Path
import tempfile
from dataclasses import replace
import unittest
from unittest.mock import patch

from baselines35 import run_online
from evaluation38 import evaluate
from features38 import Feature, check_features, feature_digest, fixture, select_noise, stream
from learner38 import run
from publication37 import canonical_sha, publication
from quality35 import Feature as ScalarFeature
from study38 import assert_unused, cases, freeze, read


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha="7"*64
        cls.d,cls.truth,cls.features,cls.plan,cls.manifest,cls.anchor=fixture("context_on",68300,cls.sha)
        cls.columns=("stable",)
        cls.trace=run(cls.d,"stable",cls.columns,stream(cls.d,cls.truth,cls.features,cls.plan,cls.columns))
        cls.feature_sha=feature_digest(cls.features,cls.columns)

    def assess(self,trace,features=None,plan=None):
        return evaluate(trace,self.truth,features if features is not None else self.features,
                        plan if plan is not None else self.plan,self.manifest,self.anchor,self.feature_sha,
                        canonical_sha(self.plan),self.sha,"context_on",
                        expected_arm="stable",expected_columns=self.columns)

    def test_future_feature_and_target_ancestry_rejected(self):
        t=72
        record=self.features[t]["stable"]
        planted=self.truth[t]["target"]
        for bad in (replace(record,available_time=t+2,value=planted),
                    replace(record,available_time=t,source="target_transform",value=2*planted),
                    replace(record,feature_id="transformed_label",available_time=t,value=planted)):
            with self.subTest(kind=bad), self.assertRaises(ValueError):
                check_features((bad,),self.d,t,self.columns)
        status,values=check_features((replace(record,available_time=t+6,value=None),),self.d,t,self.columns)
        self.assertEqual((status,values),("unavailable",None))

    def test_identity_version_and_unmarked_stale_rejected(self):
        t=80
        r=self.features[t]["stable"]
        for bad in (replace(r,version="old"),replace(r,sample_id="wrong"),
                    replace(r,observation_time=t-1),replace(r,value=float("inf")),None):
            with self.subTest(kind=bad),self.assertRaises(ValueError):
                check_features((bad,),self.d,t,self.columns)
        status,_=check_features((replace(r,source="unknown"),),self.d,t,self.columns)
        self.assertEqual(status,"unknown")

    def test_masked_label_value_relabelled_as_feature_breaks_prior_commitment(self):
        f=copy.deepcopy(self.features)
        f[102]["stable"]=replace(f[102]["stable"],value=self.truth[102]["target"]+1.)
        forged=run(self.d,"stable",self.columns,stream(self.d,self.truth,f,self.plan,self.columns))
        with self.assertRaisesRegex(ValueError,"feature, model"):
            self.assess(forged,features=f)

    def test_future_preprocessing_or_model_identity_tamper_fails(self):
        altered=copy.deepcopy(self.trace)
        future_mean=sum(row["features"][0] for row in self.truth[60:])/120
        altered["updates"][0]["value"]["means"]=(future_mean,)
        with self.assertRaisesRegex(ValueError,"preprocessing"):
            self.assess(altered)
        altered=copy.deepcopy(self.trace)
        altered["forecasts"][0]["model_id"]="wrong"
        with self.assertRaisesRegex(ValueError,"receipt"):
            self.assess(altered)

    def test_future_truth_cannot_affect_prior_fit_or_forecast(self):
        values=copy.deepcopy(self.truth)
        values[100]["target"]+=50
        altered=run(self.d,"stable",self.columns,stream(self.d,values,self.features,self.plan,self.columns))
        self.assertEqual([p for p in self.trace["forecasts"] if p["tick"]<=102],
                         [p for p in altered["forecasts"] if p["tick"]<=102])
        self.assertEqual([u for u in self.trace["updates"] if u["retrain_time"]<102],
                         [u for u in altered["updates"] if u["retrain_time"]<102])

    def test_rebound_plan_and_wrong_roster_rejected(self):
        plan=list(self.plan)
        plan[90]=None
        forged=run(self.d,"stable",self.columns,stream(self.d,self.truth,self.features,plan,self.columns))
        with self.assertRaisesRegex(ValueError,"receipt"):
            self.assess(forged,plan=plan)
        with self.assertRaisesRegex(ValueError,"receipt"):
            self.assess(dict(self.trace,arm="noise"))

    def test_fixed_denominator_and_shared_missing_clock(self):
        _,result=cases("mnar_outage",68301,self.sha,"noise0")
        stable,miss=result["arms"]["stable"],result["arms"]["missing"]
        q=miss["qualification"]["target"]
        self.assertEqual(q["planned"],120)
        self.assertLess(len(miss["legal"]),120)
        self.assertEqual(stable["qualification"]["target"]["labels"],q["labels"])
        self.assertEqual(set(miss["paired"]["observed_clock"]),set(stable["common"])&set(miss["common"]))
        self.assertIn(miss["publication"]["status"],("sensitivity_only","insufficient_evidence"))

    def test_delay_and_absence_share_paired_outage_and_cannot_reopen_cohort(self):
        _,item=cases("context_on",68302,self.sha,"noise0")
        a,b=item["arms"]["delayed"],item["arms"]["missing"]
        self.assertEqual(a["legal"],b["legal"])
        self.assertEqual(a["models"],b["models"])
        registered=read("protocol38.json")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"round38-runs").mkdir()
            with patch("study38.ROOT",root), self.assertRaisesRegex(ValueError,"reserved"):
                assert_unused(registered)

    def test_low_ess_and_unobserved_assumption_do_not_publish_total(self):
        _,item=cases("mnar_outage",68302,self.sha,"noise0")
        arm=item["arms"]["stable"]
        q=arm["qualification"]
        diag=dict(stability="pass",probabilities=dict(unknown=0,minimum=.5))
        w={s:dict(status="identified_under_assumptions",planned=120,observed=len(arm["common"]),
                  ess=2.,clipped=0) for s in q["target"]["models"]}
        result=publication(q,self.sha,diag,w,assumption="unknown",transport_attested=False,mature=True,
                           sensitivity_available=False,expected_planned=120,
                           expected_cohort_sha256=q["cohort_sha256"])
        self.assertEqual(result["status"],"valid_observed_only")
        self.assertIn("weight_or_ESS",result["reason"])

    def test_development_choice_and_timing_scope(self):
        cfg=read("protocol38.json")
        select=select_noise(cfg["development_seeds"],cfg["development_seeds"],self.sha)
        self.assertEqual(select["label_cutoff"],59)
        with self.assertRaisesRegex(ValueError,"development"):
            select_noise(cfg["report_seeds"],cfg["development_seeds"],self.sha)
        check=dict(cfg,development_seeds=cfg["development_seeds"][::-1])
        with self.assertRaisesRegex(ValueError,"development"):
            freeze(check)

    def test_one_dimension_equals_frozen_policy(self):
        # Same packet/release clock, normalized dataset prefix for the historical adapter.
        d="synthetic35:parity38"
        transport=[]
        for t,p,labels in stream(self.d,self.truth,self.features,self.plan,self.columns):
            x=ScalarFeature(d,f"{d}:{t}",t,(p[0].value,),t,t) if p else None
            transport.append((t,x,tuple(replace(y,dataset=d,sample=f"{d}:{y.tick}") for y in labels)))
        original=run_online(d,"stable",transport)
        key=lambda r:(r["tick"],r["strategy"],r["prediction"])
        self.assertEqual(list(map(key,original["forecasts"])),list(map(key,self.trace["forecasts"])))
        model=lambda m:(m["strategy"],m["retrain_time"],m["kind"],m["value"])
        self.assertEqual(list(map(model,original["updates"])),list(map(model,self.trace["updates"])))

    def test_redundancy_keeps_same_truth_and_finite_predictions(self):
        _,result=cases("thin_start",68303,self.sha,"noise0")
        cells=result["arms"]
        r=cells["redundant"]
        self.assertGreater(r["diagnostics"]["redundant_corr"],.999)
        self.assertEqual(r["models"]["fixed_ridge"]["initial_kind"],"mean")
        self.assertEqual(cells["wide"]["diagnostics"]["feature_dim"],7)
        self.assertEqual(r["models"]["periodic_ridge"]["oracle"]["n"],120)

