"""Explicitly new synthetic tests: no formal tests, market files or services."""
import math
import random
import unittest

from synthetic_pipeline import Bar, SyntheticMemorySource, synthetic_predict, two_lag_returns

SEED = 20261113


def synthetic_bars(count):
    rng = random.Random(SEED)
    prices = []
    value = 100.0
    for day in range(count):
        value *= 1.0 + rng.uniform(-0.02, 0.02)
        prices.append(Bar(day, value))
    return prices


class SyntheticFeatureTests(unittest.TestCase):
    def test_known_returns(self):
        recent, preceding = two_lag_returns([Bar(1, 100), Bar(2, 110), Bar(3, 99)], 3)
        self.assertAlmostEqual(recent, -0.1, places=14)
        self.assertAlmostEqual(preceding, 0.1, places=14)

    def test_three_rows_and_missing_history(self):
        self.assertEqual(two_lag_returns([Bar(1, 1), Bar(2, 1), Bar(3, 1)], 3), (0.0, 0.0))
        with self.assertRaises(ValueError):
            two_lag_returns([Bar(1, 1), Bar(2, 2)], 2)

    def test_invalid_values_and_order_rejected(self):
        for bad in (float('nan'), float('inf'), -1.0, 0.0, True, '2'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                two_lag_returns([Bar(1, 1), Bar(2, bad), Bar(3, 1)], 3)
        for rows in ([Bar(1, 1), Bar(1, 2), Bar(3, 3)], [Bar(2, 2), Bar(1, 1), Bar(3, 3)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                two_lag_returns(rows, 3)

    def test_future_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'future'):
            two_lag_returns([Bar(1, 1), Bar(2, 2), Bar(3, 3)], 2)

    def test_extreme_finite_inputs_do_not_emit_infinite_features(self):
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            two_lag_returns([Bar(1, 1.0), Bar(2, 1e-308), Bar(3, 1e308)], 3)


class SyntheticIntegrationTests(unittest.TestCase):
    def test_fixed_seed_is_reproducible(self):
        self.assertEqual(synthetic_bars(30), synthetic_bars(30))
        self.assertNotEqual(synthetic_bars(30)[1].close, synthetic_bars(30)[2].close)

    def test_fake_dependency_query_and_reference_value(self):
        fake = SyntheticMemorySource([Bar(1, 100), Bar(2, 110), Bar(3, 99), Bar(4, 130)])
        self.assertAlmostEqual(synthetic_predict(fake, 3), 0.025, places=14)
        self.assertEqual(fake.calls, [3])

    def test_future_price_mutation_cannot_change_prediction(self):
        bars = synthetic_bars(20)
        reference = synthetic_predict(SyntheticMemorySource(bars), 10)
        changed = [Bar(x.day, x.close * 1000 if x.day > 10 else x.close) for x in bars]
        self.assertEqual(reference, synthetic_predict(SyntheticMemorySource(changed), 10))

    def test_noncompliant_dependency_future_data_fails(self):
        class BadSource:
            def fetch_until(self, cutoff):
                return [Bar(1, 1), Bar(2, 2), Bar(3, 3), Bar(4, 4)]
        with self.assertRaisesRegex(ValueError, 'future'):
            synthetic_predict(BadSource(), 3)

    def test_source_failure_is_not_swallowed(self):
        class FailedSource:
            def fetch_until(self, cutoff):
                raise RuntimeError('synthetic provider unavailable')
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            synthetic_predict(FailedSource(), 3)

    def test_predictions_are_finite_for_synthetic_random_input(self):
        fake = SyntheticMemorySource(synthetic_bars(30))
        self.assertTrue(all(math.isfinite(synthetic_predict(fake, day)) for day in range(2, 30)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
