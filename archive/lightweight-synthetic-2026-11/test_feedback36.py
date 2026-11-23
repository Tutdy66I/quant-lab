"""Round 36 artificial contract, missingness and recovery failure probes."""
import copy
import math
import unittest

from analyses36 import recovery_metrics, selection
from baselines35 import InvalidStream, run_online
from design36 import execute, fixture, make_plan, stream
from missing36 import estimated_probabilities, fit_response, sensitivity, weighted
from qualification36 import qualify, strata
from study36 import freeze, read, seed_case


class FeedbackStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset,cls.truth,cls.population,cls.anchor=fixture("relation_reverse",66200,"2"*64)
        cls.plan=make_plan(cls.truth,"relation_reverse",66200,initial=8,missing=.55,delay="batch")
        cls.trace=execute(cls.dataset,cls.truth,66200,"dev","clean",cls.plan)

    def qualification(self, cutoff=181, plan=True):
        return qualify(self.trace,self.truth,self.population,self.anchor,cutoff,
                       offline_plan=self.plan if plan else None)

    def test_fixed_scoring_denominator_and_strata(self):
        q=self.qualification()
        summary=strata(q)
        self.assertEqual(summary["overall"]["denominator"],120)
        self.assertEqual(sum(s["denominator"] for k,s in summary.items() if k.startswith("window:")),120)
        self.assertLess(summary["overall"]["common"],120)
        with self.assertRaisesRegex(ValueError,"denominator"):
            strata(dict(q,records=q["records"][:-1],planned=119))
        with self.assertRaisesRegex(ValueError,"order"):
            strata(dict(q,records=list(reversed(q["records"]))))

    def test_pending_permanent_and_unknown_are_distinct(self):
        q=self.qualification(111)
        labels=strata(q)["overall"]["labels"]
        self.assertGreater(labels["pending_offline"],0)
        self.assertGreater(labels["permanent_missing_offline"],0)
        self.assertEqual(labels["arrived"],0)
        self.assertEqual(strata(q)["overall"]["legal_features"],52)
        self.assertTrue(all(v["prediction"] is None for r in q["records"] if r["tick"]>111
                            for v in r["forecasts"].values()))
        self.assertEqual(strata(self.qualification(111,False))["overall"]["labels"]["unknown"],120)
        self.assertTrue(all(r["label"] is None for r in q["records"]))
        self.assertEqual(strata(q)["overall"]["ranking"],"insufficient_evidence")

    def test_permanent_false_release_and_future_claim_are_rejected(self):
        spoof=list(self.plan)
        missing=next(i for i,t in enumerate(spoof) if i>=60 and t is None)
        spoof[missing]=missing+2
        with self.assertRaisesRegex(ValueError,"frozen delivery plan"):
            qualify(self.trace,self.truth,self.population,self.anchor,181,offline_plan=spoof)
        spoof[missing]=missing
        with self.assertRaisesRegex(ValueError,"invalid cutoff or offline"):
            qualify(self.trace,self.truth,self.population,self.anchor,181,offline_plan=spoof)

    def test_propensity_disallows_truth_and_future_features(self):
        rows=[dict(tick=t,positive=t%2==0,returned=t%3!=0) for t in range(60,120)]
        fit=fit_response(rows,cutoff=121)
        self.assertEqual(set(fit),{True,False})
        with self.assertRaisesRegex(ValueError,"complete, mature"):
            fit_response(rows,cutoff=120)
        rows[0]["target"]=9.
        with self.assertRaisesRegex(ValueError,"visible covariate"):
            fit_response(rows,cutoff=121)
        with self.assertRaisesRegex(ValueError,"evaluation time"):
            estimated_probabilities([dict(tick=121,positive=True)],fit)
        with self.assertRaisesRegex(ValueError,"evaluation time"):
            estimated_probabilities([dict(tick=124,positive=True),dict(tick=122,positive=False)],fit)

    def test_extreme_weights_ess_and_status(self):
        low=weighted([.5]*12,[.025]*12)
        self.assertEqual(low["status"],"overlap_failed")
        self.assertIsNone(low["estimate"])
        clip=weighted([.5]*12,[.1]*12)
        self.assertEqual(clip["clipped"],12)
        self.assertEqual(clip["status"],"clipped_diagnostic")
        equal=weighted([1.]*12,[1.5/11]*12)
        self.assertTrue(math.isclose(equal["ess"],12))
        self.assertEqual(equal["estimate"],1)
        self.assertIsNone(weighted([1.]*12,[.2]*6+[.9]*6)["estimate"])
        with self.assertRaises(ValueError):
            weighted([1.],[float("inf")])

    def test_manual_hajek_and_missingness_sensitivity(self):
        x=weighted([1.]*10+[4.]*10+[None]*2,[.25]*10+[.75]*10+[.75]*2)
        self.assertAlmostEqual(x["estimate"],1.75)
        observations=[0.]*12+[None]*8
        pred={"a":[0.]*20,"b":[1.]*20}
        out=sensitivity(pred,observations)
        self.assertEqual(out["status"],"partially_identified")
        self.assertEqual(out["unrestricted_status"],"not_identifiable")
        self.assertEqual(out["pairs"][0]["unrestricted_order"],"undetermined")
        self.assertIsNone(out["unrestricted_loss"]["a"][1])
        # Two common target completions share all observed data and reverse the order.
        for missing,expected in ((0.,"a"),(10.,"b")):
            complete=[0.]*12+[missing]*8
            winner=min(pred,key=lambda s:sum((p-y)**2 for p,y in zip(pred[s],complete)))
            self.assertEqual(winner,expected)
        self.assertEqual(sensitivity(pred,[None]*20)["status"],"insufficient_evidence")

    def test_batch_feedback_never_changes_prior_forecast(self):
        changed=copy.deepcopy(self.truth)
        t=next(t for t in range(70,100) if self.plan[t]==112)
        changed[t]["target"]+=100
        alternative=execute(self.dataset,changed,66200,"dev","clean",self.plan)
        self.assertEqual([f for f in self.trace["forecasts"] if f["tick"]<=112],
                         [f for f in alternative["forecasts"] if f["tick"]<=112])
        self.assertEqual([x for x in self.trace["updates"] if x["retrain_time"]<112],
                         [x for x in alternative["updates"] if x["retrain_time"]<112])
        models=recovery_metrics(self.qualification(),self.trace,self.truth)
        self.assertGreater(models["periodic_ridge"]["first_using_new_feedback"],112)
        self.assertEqual(models["fixed_ridge"]["initial_kind"],"mean")
        self.assertIsNone(models["fixed_ridge"]["first_ridge_fit"])

    def test_early_or_double_return_stops_learner(self):
        transport=list(stream(self.dataset,self.truth,66200,"clean",self.plan))
        eventual=next(label for label in transport[112][2] if label.tick>=60)
        tick,feature,labels=transport[111]
        transport[111]=tick,feature,labels+(eventual,)
        with self.assertRaisesRegex(InvalidStream,"timing"):
            run_online(self.dataset,"fault",transport)
        transport=list(stream(self.dataset,self.truth,66200,"clean",self.plan))
        t,p,batch=transport[112]
        transport[112]=t,p,batch+(eventual,)
        with self.assertRaisesRegex(InvalidStream,"duplicate"):
            run_online(self.dataset,"fault",transport)

    def test_bad_population_labels_and_forecast_clock_block_scoring(self):
        with self.assertRaises(ValueError):
            qualify(self.trace,self.truth,self.population,None,181)
        damaged=copy.deepcopy(self.trace)
        damaged["forecasts"][0]["tick"]+=1
        with self.assertRaisesRegex(ValueError,"identity, clock"):
            qualify(damaged,self.truth,self.population,self.anchor,181)
        damaged=copy.deepcopy(self.trace)
        damaged["forecasts"][0]["prediction"]+=1
        with self.assertRaisesRegex(ValueError,"active model"):
            qualify(damaged,self.truth,self.population,self.anchor,181)

    def test_no_feedback_recovery_cannot_be_confirmed(self):
        plan=make_plan(self.truth,"relation_reverse",66200,initial=45,missing=1.,delay="batch")
        tr=execute(self.dataset,self.truth,66200,"permanent","clean",plan)
        q=qualify(tr,self.truth,self.population,self.anchor,181,offline_plan=plan)
        self.assertEqual(strata(q)["overall"]["common"],0)
        self.assertTrue(all(v["confirm_censored"] for v in recovery_metrics(q,tr,self.truth).values()))

    def test_mar_models_use_same_forecasts_and_offline_truth_is_only_comparator(self):
        plan=make_plan(self.truth,"relation_reverse",66200,mode="mar")
        tr=execute(self.dataset,self.truth,66200,"mar","clean",plan)
        q=qualify(tr,self.truth,self.population,self.anchor,181,offline_plan=plan)
        answer=selection(q,self.truth,"mar")
        revised=copy.deepcopy(self.truth)
        for r in q["records"]:
            if not r["common"]:
                revised[r["tick"]]["target"]+=100
        after=selection(q,revised,"mar")
        self.assertEqual(answer["calibration"],after["calibration"])
        self.assertEqual(answer["probabilities"],after["probabilities"])
        for s,m in after["models"].items():
            self.assertEqual(m["estimated"],answer["models"][s]["estimated"])
            self.assertEqual(m["complete_case"],answer["models"][s]["complete_case"])
            self.assertNotEqual(m["complete_truth"],answer["models"][s]["complete_truth"])

    def test_development_matrix_and_report_seed_ledger(self):
        cfg=read("protocol36.json")
        bad=dict(cfg,report_seeds=[65500]+cfg["report_seeds"][1:])
        with self.assertRaisesRegex(ValueError,"seed"):
            freeze(bad)
        raw,result=seed_case("stable_signal",66201,"3"*64)
        self.assertEqual(len(raw["selection"]),7)
        self.assertEqual(len(raw["recovery"]),26)
        self.assertEqual(len(result["factor_clock"]),68)

