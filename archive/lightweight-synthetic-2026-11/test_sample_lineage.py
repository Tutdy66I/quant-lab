"""New synthetic sample identity, chronology and paired-loss regression tests."""
from dataclasses import replace
import math
import unittest

from sample_lineage import (LossRecord, Prediction, Sample, make_losses, pair_losses,
                            prediction_id_for, sample_id_for, validate_samples)


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.opts = dict(expected_dataset_id='toy-v1', expected_dataset_fingerprint='a'*64,
                         expected_config_hash='b'*64, split_id='validation', window_id='w1')
        self.samples = [Sample(sample_id_for('toy-v1','a'*64,i),'toy-v1','a'*64,'b'*64,10*i,10*i-1,
                               10*i,10*i+1,float(i),'validation','w1',i) for i in range(1,5)]
        self.pred_a = [Prediction(prediction_id_for(s.sample_id,'w1','a','b'*64),s.sample_id,'toy-v1','w1','a',s.target_value+1)
                       for s in self.samples]
        self.pred_b = [Prediction(prediction_id_for(s.sample_id,'w1','b','b'*64),s.sample_id,'toy-v1','w1','b',s.target_value+2)
                       for s in self.samples]

    def losses(self, pred, model):
        return make_losses(self.samples,pred,model_id=model,**self.opts)

    def paired(self,a,b,samples=None):
        return pair_losses(self.samples if samples is None else samples,a,b,model_a='a',model_b='b',**self.opts)

    def test_explicit_pair_identity_and_values(self):
        a,b=self.losses(self.pred_a,'a'),self.losses(self.pred_b,'b')
        self.assertEqual(self.paired(a,b),((1.0,)*4,(4.0,)*4))
        self.assertEqual(a[0].sample.sample_id,sample_id_for('toy-v1','a'*64,1))
        self.assertEqual(a[0].prediction.model_id,'a')

    def test_duplicate_sample_id_rejected_even_with_different_time(self):
        bad=self.samples.copy();bad[1]=replace(bad[1],sample_id=bad[0].sample_id)
        with self.assertRaisesRegex(ValueError,'duplicate sample_id'):validate_samples(bad,**self.opts)

    def test_predictions_reordered_and_missing_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'identity/order'):self.losses(self.pred_a[::-1],'a')
        with self.assertRaisesRegex(ValueError,'exactly one'):self.losses(self.pred_a[:-1],'a')

    def test_duplicate_prediction_identity_rejected(self):
        pred=self.pred_a.copy();pred[1]=replace(pred[1],prediction_id=pred[0].prediction_id)
        with self.assertRaisesRegex(ValueError,'duplicate prediction_id'):self.losses(pred,'a')

    def test_loss_reordering_missing_and_cross_model_ids_rejected(self):
        a,b=self.losses(self.pred_a,'a'),self.losses(self.pred_b,'b')
        with self.assertRaisesRegex(ValueError,'identity/order'):self.paired(a,b[::-1])
        with self.assertRaisesRegex(ValueError,'cover identical'):self.paired(a,b[:-1])
        # Both model arrays can agree after dropping the same item. The full
        # caller-supplied baseline still rejects that shared omission.
        with self.assertRaisesRegex(ValueError,'cover identical'):self.paired(a[:-1],b[:-1])
        bad=list(b);bad[0]=replace(bad[0],prediction=replace(bad[0].prediction,prediction_id=a[0].prediction.prediction_id))
        with self.assertRaisesRegex(ValueError,'prediction ID does not match'):self.paired(a,bad)

    def test_target_and_loss_tampering_rejected(self):
        a,b=self.losses(self.pred_a,'a'),self.losses(self.pred_b,'b')
        changed=list(b);changed[0]=replace(changed[0],sample=replace(changed[0].sample,target_value=999))
        with self.assertRaisesRegex(ValueError,'sample or definition'):self.paired(a,changed)
        changed=list(b);changed[0]=replace(changed[0],loss_value=0.0)
        with self.assertRaisesRegex(ValueError,'loss does not match'):self.paired(a,changed)

    def test_times_never_silently_sorted_and_future_features_rejected(self):
        with self.assertRaisesRegex(ValueError,'decision order'):validate_samples(self.samples[::-1],**self.opts)
        rows=self.samples.copy();rows[0]=replace(rows[0],feature_available_time=rows[0].decision_time+1)
        with self.assertRaisesRegex(ValueError,'not available'):validate_samples(rows,**self.opts)

    def test_label_window_purge_cutoff(self):
        validate_samples(self.samples,label_cutoff=41,**self.opts)
        with self.assertRaisesRegex(ValueError,'crosses split cutoff'):
            validate_samples(self.samples,label_cutoff=40,**self.opts)
        bad=self.samples.copy();bad[0]=replace(bad[0],target_start_time=bad[0].decision_time-1)
        with self.assertRaisesRegex(ValueError,'invalid future'):validate_samples(bad,**self.opts)

    def test_dataset_configuration_and_window_identity(self):
        for field,value in (('synthetic_dataset_id','other'),('dataset_fingerprint','c'*64),
                            ('config_hash','d'*64),('split_id','train'),('window_id','w2')):
            bad=self.samples.copy();bad[0]=replace(bad[0],**{field:value})
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'mismatch'):
                validate_samples(bad,**self.opts)
        with self.assertRaises(ValueError):validate_samples(self.samples,**{**self.opts,'expected_config_hash':'invalid'})

    def test_nonfinite_target_prediction_and_missing_metadata(self):
        bad=self.samples.copy();bad[0]=replace(bad[0],target_value=math.nan)
        with self.assertRaisesRegex(ValueError,'target_value'):validate_samples(bad,**self.opts)
        preds=self.pred_a.copy();preds[0]=replace(preds[0],prediction_value=math.inf)
        with self.assertRaisesRegex(ValueError,'prediction'):self.losses(preds,'a')
        preds=self.pred_a.copy();preds[0]=replace(preds[0],prediction_id='')
        with self.assertRaisesRegex(ValueError,'pred_id'):self.losses(preds,'a')

    def test_timestamps_cannot_be_used_as_ids(self):
        bad=self.samples.copy();bad[0]=replace(bad[0],sample_id='1700000000')
        with self.assertRaisesRegex(ValueError,'namespaced SHA-256'):validate_samples(bad,**self.opts)
        self.assertNotEqual(sample_id_for('toy-v1','a'*64,1),sample_id_for('toy-v1','a'*64,2))

    def test_digest_identity_binds_ordinal_and_prediction_config(self):
        bad=self.samples.copy();bad[0]=replace(bad[0],source_ordinal=99)
        with self.assertRaisesRegex(ValueError,'source ordinal'):validate_samples(bad,**self.opts)
        p=self.pred_a.copy();p[0]=replace(p[0],prediction_id=prediction_id_for(p[0].sample_id,'w1','a','c'*64))
        with self.assertRaisesRegex(ValueError,'configuration'):self.losses(p,'a')
        self.assertNotEqual(prediction_id_for(self.samples[0].sample_id,'w1','a','b'*64),
                            prediction_id_for(self.samples[0].sample_id,'w1','a','c'*64))


if __name__=='__main__':
    unittest.main(verbosity=2)
