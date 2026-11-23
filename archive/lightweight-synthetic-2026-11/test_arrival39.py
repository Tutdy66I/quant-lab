"""Round 39 negative probes and frozen-baseline checks."""
import copy
from dataclasses import replace
import unittest

from arrival39 import check_arrival, run, snapshot
from design39 import contract, fixture, transport
from evaluation39 import evaluate
from study39 import allowed, cases, freeze, read
from learner38 import run as run38
from features38 import Feature as Packet38
from quality35 import identity


class FeatureArrivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config="a"*64
        cls.data=fixture("timing",69100,cls.config)
        cls.cols,cls.ttl,cls.events=contract(cls.data,"timing","short")
        cls.sha,seq=transport(cls.data,cls.events)
        cls.trace=run(cls.data["dataset"],"short",cls.cols,cls.ttl,seq)

    def changed(self,events):
        _,sequence=transport(self.data,events)
        return run(self.data["dataset"],"short",self.cols,self.ttl,sequence)

    def test_late_input_cannot_change_old_prediction(self):
        at=next(r for r in self.events if r.observation_time>=65 and r.available_time>r.observation_time)
        events=[replace(r,value=r.value+99.) if r==at else r for r in self.events]
        altered=self.changed(events)
        self.assertEqual([f for f in self.trace["forecasts"] if f["tick"]<at.available_time],
                         [f for f in altered["forecasts"] if f["tick"]<at.available_time])
        with self.assertRaisesRegex(ValueError,"receipt"):
            evaluate(altered,self.data,self.sha,self.config,"timing","short")

    def test_false_arrival_and_late_replacement_fail_replay(self):
        at=next(r for r in self.events if r.observation_time>=65 and r.available_time>r.observation_time)
        with self.assertRaisesRegex(ValueError,"availability"):
            check_arrival(at,self.data["dataset"],at.available_time-1,self.cols,None)
        altered=self.changed([replace(r,available_time=r.observation_time) if r==at else r for r in self.events])
        with self.assertRaisesRegex(ValueError,"receipt"):
            evaluate(altered,self.data,self.sha,self.config,"timing","short")
        edited=copy.deepcopy(self.trace)
        edited["forecasts"][0]["prediction"]+=1.
        with self.assertRaisesRegex(ValueError,"receipt"):
            evaluate(edited,self.data,self.sha,self.config,"timing","short")

    def test_revision_prospective_chain_and_duplicate(self):
        original=self.events[68]
        later=replace(original,available_time=original.available_time+1,revision=1,value=original.value+2.)
        altered=self.changed([*self.events,later])
        self.assertEqual([v for v in altered["forecasts"] if v["tick"]<later.available_time],
                         [v for v in self.trace["forecasts"] if v["tick"]<later.available_time])
        self.assertTrue(any(row["revision"]==1 for row in altered["features"]))
        for invalid in (original, replace(later,revision=2),replace(later,revision=0)):
            with self.subTest(kind=invalid.revision), self.assertRaisesRegex(ValueError,"revision"):
                self.changed([*self.events,invalid])

    def test_expired_old_source_and_illegal_numeric_probes(self):
        original=self.events[1]
        with self.assertRaisesRegex(ValueError,"future"):
            snapshot({"x":original},self.cols,0,6)
        stale=replace(original,available_time=10)
        state=snapshot({"x":stale},self.cols,10,6)
        self.assertEqual((state[0],state[1],state[2]),("unavailable","past_acceptance_age",None))
        for bad in (replace(original,value=float("nan")),replace(original,feature_id="g2"),
                    replace(original,feature_version="wrong"),replace(original,sample_id="forged"),
                    replace(original,source_identity="future-label-transform")):
            with self.subTest(packet=bad), self.assertRaisesRegex(ValueError,"invalid feature"):
                check_arrival(bad,self.data["dataset"],original.available_time,self.cols,None)

    def test_group_order_or_duplicate_dimension_is_rejected(self):
        _,sequence=transport(self.data,self.events)
        with self.assertRaisesRegex(ValueError,"contract"):
            run(self.data["dataset"],"short",("x","x"),6,sequence)
        data=fixture("partial",69100,self.config)
        columns,ttl,events=contract(data,"partial","full")
        bad=[replace(e,feature_group_id="x") if e.feature_id=="g2" else e for e in events]
        _,sequence=transport(data,bad)
        with self.assertRaisesRegex(ValueError,"identity"):
            run(data["dataset"],"full",columns,ttl,sequence)

    def test_future_preprocessing_does_not_validate(self):
        altered=copy.deepcopy(self.trace)
        altered["updates"][0]["value"]["means"]=(99.,)
        with self.assertRaisesRegex(ValueError,"preprocessing"):
            evaluate(altered,self.data,self.sha,self.config,"timing","short")

    def test_false_identity_feature_or_data_anchor_cannot_pass(self):
        edited=copy.deepcopy(self.trace)
        edited["decisions"][65]["feature_sources"][0]["sample_id"]="substitution"
        with self.assertRaisesRegex(ValueError,"receipt"):
            evaluate(edited,self.data,self.sha,self.config,"timing","short")
        wrong=copy.deepcopy(self.data)
        wrong["truth"]=wrong["truth"][:-1]
        with self.assertRaises(ValueError):
            evaluate(self.trace,wrong,self.sha,self.config,"timing","short")

    def test_mnar_scope_and_fixed_pair_denominator(self):
        _,record=cases("timing_mnar",69100,self.config)
        for a in record["arms"].values():
            self.assertEqual(a["evidence"]["target"]["planned"],120)
            self.assertIn(a["publication"]["status"],("sensitivity_only","insufficient_evidence"))
        short=record["arms"]["short"]
        instant=record["arms"]["instant"]
        pair=record["comparisons"]["short-instant"]
        self.assertEqual(set(pair["observed"]),set(short["common"])&set(instant["common"]))
        self.assertEqual(pair["models"]["mean"]["oracle"]["delta"],0.)
        self.assertFalse(any(a["publication"]["status"]=="population_supported_under_assumptions"
                             for a in record["arms"].values()))

    def test_same_clock_means_and_group_ablation(self):
        _,record=cases("partial",69101,self.config)
        for comp in record["comparisons"].values():
            self.assertEqual(len(comp["legal"]),90)
            self.assertEqual(comp["models"]["mean"]["oracle"]["delta"],0.)
        self.assertEqual(record["arms"]["group_lag"]["feature_dimensions"],3)

    def test_configuration_and_development_cohort_do_not_mix(self):
        config=read("protocol39.json")
        with self.assertRaisesRegex(ValueError,"fresh"):
            allowed(dict(config,report_seeds=[config["development_seeds"][0]]+config["report_seeds"][1:]))
        with self.assertRaisesRegex(ValueError,"fresh"):
            allowed(dict(config,report_seeds=[68800]+config["report_seeds"][1:]))
        with self.assertRaisesRegex(ValueError,"configuration"):
            freeze(dict(config,wall_seconds=999))

    def test_immediate_single_column_matches_prior_baseline(self):
        columns,ttl,events=contract(self.data,"timing","instant")
        _,sequence=transport(self.data,events)
        sequence=list(sequence)
        current=run(self.data["dataset"],"instant",columns,ttl,sequence)
        old="synthetic38:parity39"
        previous=[]
        for tick,arrivals,labels in sequence:
            pack=(Packet38("stable",identity(old,tick),tick,tick,tick,"feature38-v1",
                           "exogenous",arrivals[0].value),) if arrivals else None
            previous.append((tick,pack,tuple(replace(l,sample=identity(old,l.tick),dataset=old) for l in labels)))
        incumbent=run38(old,"instant",("stable",),previous)
        extract=lambda x:[(v["strategy"],v["tick"],v["prediction"]) for v in x["forecasts"]]
        self.assertEqual(extract(current),extract(incumbent))

