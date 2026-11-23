"""Causal online simulations; never imports original holdout or real data."""
import copy
import json
import unittest
from unittest import mock

import prequential_synthetic31 as p


def rehash(run):
    run['audit_sha256']=p._digest({k:v for k,v in run.items() if k!='audit_sha256'})
    return run


class OnlineCausalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=p.generate('relation_reverse',41001)
        cls.result=p.run_online(cls.rows)

    def test_four_strategies_same_arrived_universe(self):
        result=self.result
        self.assertTrue(p.verify_run(result,result['audit_sha256'],rows=self.rows))
        by={s:{f['sample_id'] for f in result['feedback'] if f['strategy']==s} for s in p.STRATEGIES}
        self.assertTrue(all(v==by['fixed_ridge'] for v in by.values()))
        self.assertTrue(all(m['n_scored']==len(by['fixed_ridge']) for m in result['metrics'].values()))

    def test_model_activates_after_alarm_and_cooldown(self):
        events=self.result['events']; alarms={a['alarm_time'] for a in events if a.get('event_type')=='alarm' and a['alarm']}
        updates=[e for e in events if e.get('event_type')=='activation' and e.get('strategy')=='adaptive' and e['version']>0]
        self.assertTrue(updates)
        for e in updates:
            self.assertIn(e['alarm_time'],alarms); self.assertGreater(e['model_activation_time'],e['alarm_time'])
        self.assertTrue(all(b['retrain_time']-a['retrain_time']>=p.CONFIG['cooldown'] for a,b in zip(updates,updates[1:])))

    def test_model_ids_bind_datasets(self):
        other=p.run_online(p.generate('stable_signal',41001))
        a={e['model_id'] for e in self.result['events'] if e['event_type']=='activation'}
        b={e['model_id'] for e in other['events'] if e['event_type']=='activation'}
        self.assertFalse(a&b)

    def test_changed_future_target_cannot_change_prior_prediction(self):
        rows=copy.deepcopy(self.rows); t=150; at=rows[t]['label_available_time']; rows[t]['target']+=100
        altered=p.run_online(rows)
        old=[v for v in self.result['predictions'] if v['decision_time']<=at]
        new=[v for v in altered['predictions'] if v['decision_time']<=at]
        self.assertEqual(old,new)

    def test_out_of_order_labels_processed_at_arrival(self):
        seq=[f for f in self.result['feedback'] if f['strategy']=='fixed_ridge']
        positions={int(f['sample_id'].split(':')[-1]):i for i,f in enumerate(seq)}
        self.assertLess(positions[78],positions[77])
        self.assertTrue(all(f['label_time']==self.rows[int(f['sample_id'].split(':')[-1])]['label_available_time'] for f in seq))

    def test_unavailable_targets_never_accessed_even_for_fingerprint(self):
        class NoTarget(dict):
            def __getitem__(self,key):
                if key=='target': raise AssertionError('unavailable target accessed')
                return super().__getitem__(key)
            def get(self,key,default=None):
                if key=='target': raise AssertionError('unavailable target accessed')
                return super().get(key,default)
        rows=p.generate('insufficient_training',41888)
        rows=[NoTarget(r) if r['label_available_time'] is None else r for r in rows]
        result=p.run_online(rows)
        self.assertTrue(p.verify_run(result,result['audit_sha256'],rows=rows))
        self.assertGreater(result['censored_prediction_samples'],0)

    def test_incomplete_labels_relation_alarm_unavailable(self):
        result=p.run_online(p.generate('long_delay',41001))
        events=[e for e in result['events'] if e['event_type']=='alarm']
        self.assertTrue(any(e['relationship_status']=='unavailable' and e['relationship_alarm'] is None for e in events))

    def test_invalid_or_premature_label_rejected_at_arrival_step(self):
        rows=copy.deepcopy(self.rows); rows[120]['label_available_time']=120
        with self.assertRaises(p.CausalInputError) as context:p.run_online(rows)
        self.assertEqual(context.exception.decision_time,120)
        self.assertTrue(context.exception.predictions);self.assertTrue(context.exception.feedback)
        self.assertTrue(all(v['decision_time']<120 for v in context.exception.predictions))

    def test_invalid_future_feature_does_not_prevent_prior_predictions(self):
        rows=copy.deepcopy(self.rows); rows[120]['features']=[float('nan')]
        with self.assertRaises(p.CausalInputError) as context:p.run_online(rows)
        self.assertEqual(context.exception.decision_time,120)
        expected=[v for v in self.result['predictions'] if v['decision_time']<120]
        self.assertEqual(context.exception.predictions,expected)
        self.assertTrue(context.exception.feedback)

    def test_invalid_arrived_label_preserves_earlier_decisions(self):
        rows=copy.deepcopy(self.rows); rows[77]['target']=float('nan')
        at=rows[77]['label_available_time']
        with self.assertRaises(p.CausalInputError) as context:p.run_online(rows)
        self.assertEqual(context.exception.decision_time,at)
        self.assertEqual(context.exception.predictions,
                         [v for v in self.result['predictions'] if v['decision_time']<=at])
        self.assertTrue(context.exception.feedback)

    def test_training_failure_has_mean_fallback(self):
        result=p.run_online(self.rows,injected_failure_times=[100])
        failed=[e for e in result['events'] if e.get('event_type')=='activation' and e.get('retrain_time')==100 and 'fallback_mean' in e['reason']]
        self.assertTrue(failed);self.assertTrue(all(e['kind']=='mean' for e in failed))
        self.assertTrue(p.verify_run(result,result['audit_sha256'],rows=self.rows))

    def test_invalid_fitted_model_falls_back_before_activation(self):
        original=p._fit
        def broken(available,now):
            model,meta=original(available,now)
            if now==100: return p.Model(model.means,model.scales,model.target_mean,(float('nan'),),model.alpha),meta
            return model,meta
        with mock.patch.object(p,'_fit',side_effect=broken): result=p.run_online(self.rows)
        events=[e for e in result['events'] if e.get('event_type')=='activation' and e.get('retrain_time')==100 and e['strategy']!='mean']
        self.assertTrue(events);self.assertTrue(all(e['kind']=='mean' and 'fallback_mean' in e['reason'] for e in events))
        self.assertTrue(p.verify_run(result,result['audit_sha256'],rows=self.rows))

    def test_ordinary_training_runtime_error_falls_back(self):
        original=p._fit
        def fault(available,now):
            if now==100:raise RuntimeError('synthetic training failure')
            return original(available,now)
        with mock.patch.object(p,'_fit',side_effect=fault): result=p.run_online(self.rows)
        updates=[e for e in result['events'] if e.get('event_type')=='activation' and e.get('retrain_time')==100 and e['strategy']!='mean']
        self.assertTrue(updates); self.assertTrue(all(e['kind']=='mean' and 'fallback_mean' in e['reason'] for e in updates))
        self.assertTrue(p.verify_run(result,result['audit_sha256'],rows=self.rows))

    def test_interrupted_update_preserves_predictions_and_losses(self):
        with self.assertRaises(p.ModelUpdateInterrupted) as context:p.run_online(self.rows,injected_interrupt_times=[100])
        ex=context.exception
        self.assertEqual(ex.decision_time,100);self.assertTrue(ex.predictions);self.assertTrue(ex.feedback)
        self.assertFalse(any(e.get('strategy')==ex.strategy and e.get('retrain_time')==100 for e in ex.events))
        self.assertEqual(ex.predictions,[v for v in self.result['predictions'] if v['decision_time']<=100])

    def test_receipt_roundtrip_and_missing_rejected(self):
        loaded=json.loads(json.dumps(self.result));self.assertTrue(p.verify_run(loaded,loaded['audit_sha256'],rows=self.rows))
        altered=copy.deepcopy(self.result);altered['predictions'].pop()
        with self.assertRaises(ValueError):p.verify_run(rehash(altered),altered['audit_sha256'],rows=self.rows)

    def test_prediction_feedback_or_label_time_tampering_rejected(self):
        for mode in ('prediction','feedback','label_time','model'):
            result=copy.deepcopy(self.result)
            if mode=='prediction':result['predictions'][0]['prediction']+=1
            elif mode=='feedback':result['feedback'][0]['squared_loss']+=1
            elif mode=='label_time':result['feedback'][0]['label_time']+=1
            else:result['predictions'][0]['active_model_id']='invalid'
            with self.subTest(mode=mode),self.assertRaises(ValueError):p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)

    def test_missing_alarm_and_cooldown_tampering_rejected(self):
        result=copy.deepcopy(self.result)
        alarm=next(e['alarm_time'] for e in result['events'] if e.get('event_type')=='activation' and e.get('strategy')=='adaptive' and e['version']>0)
        result['events']=[e for e in result['events'] if not (e.get('event_type')=='alarm' and e['alarm_time']==alarm)]
        with self.assertRaises(ValueError):p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)
        result=copy.deepcopy(self.result);result['config']['cooldown']=0
        with self.assertRaises(ValueError):p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)

    def test_training_lineage_and_metric_tampering_rejected(self):
        result=copy.deepcopy(self.result)
        model=next(e for e in result['events'] if e.get('kind')=='ridge' and e.get('version')==0)
        model['training']['train_ids'][0]=self.rows[0]['sample_id']
        with self.assertRaises(ValueError):p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)
        result=copy.deepcopy(self.result); result['metrics']['adaptive']['updates']=999
        with self.assertRaises(ValueError):p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)

    def test_mean_training_lineage_tampering_rejected(self):
        result=copy.deepcopy(self.result)
        model=next(e for e in result['events'] if e.get('kind')=='mean' and e.get('version')==1)
        model['training']['sample_count']=999
        with self.assertRaises(ValueError):p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)
        result=copy.deepcopy(self.result)
        model=next(e for e in result['events'] if e.get('kind')=='mean' and e.get('version')==1)
        model['training']['mean_ids'].pop()
        with self.assertRaises(ValueError):p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)

    def test_reordered_prediction_or_feedback_rejected(self):
        for key in ('predictions','feedback'):
            result=copy.deepcopy(self.result); result[key]=list(reversed(result[key]))
            with self.subTest(key=key),self.assertRaises(ValueError):
                p.verify_run(rehash(result),result['audit_sha256'],rows=self.rows)


if __name__=='__main__':unittest.main()
