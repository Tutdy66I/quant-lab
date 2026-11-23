"""R34 engine checks on development seeds only."""
import copy
import unittest
from unittest.mock import patch

import engine_cross33
import engine_five34 as engine
from paths33 import make_case
from prequential_synthetic31 import _digest


THRESHOLDS = {"mean": 1.6, "variance": 5.0, "relation": 4.0}


class FocusedEngineTests(unittest.TestCase):
    def test_five_policies_preserve_projected_round33_results(self):
        rows, args = make_case("full_short", 64100)
        previous = engine_cross33.run_online(rows, alarm_config=THRESHOLDS, **args)
        with patch.object(engine, "choose_update", wraps=engine.choose_update) as update:
            with patch.object(engine.robust, "Confirmation", side_effect=AssertionError):
                focused = engine.run_online(rows, alarm_config=THRESHOLDS, **args)
        self.assertEqual(tuple(focused["metrics"]), engine.STRATEGIES)
        self.assertEqual(focused["metrics"], {
            name: previous["metrics"][name] for name in engine.STRATEGIES})
        self.assertEqual(update.call_count, 2 * sum(
            event["event_type"] == "alarm" for event in focused["events"]))
        self.assertEqual({call.args[0] for call in update.call_args_list}, {"original"})
        for kind in ("predictions", "feedback"):
            self.assertEqual(focused[kind], [record for record in previous[kind]
                                           if record["strategy"] in engine.STRATEGIES])
        self.assertEqual(focused["observed_data_sha256"], previous["observed_data_sha256"])
        self.assertEqual(focused["initial_label_ids"], previous["initial_label_ids"])

    def test_future_target_cannot_change_already_issued_predictions(self):
        rows, args = make_case("full_long", 64101)
        due = rows[75]["label_available_time"]
        original = engine.run_online(rows, alarm_config=THRESHOLDS, **args)
        altered = copy.deepcopy(rows)
        altered[75]["target"] += 100
        changed = engine.run_online(altered, alarm_config=THRESHOLDS, **args)
        for field, key in (("predictions", "decision_time"), ("events", "decision_time")):
            if field == "events":
                # Events at the delivery tick may reflect the now released target.
                ceiling = due - 1
            else:
                ceiling = due
            def prefix(result):
                return [record for record in result[field]
                        if record.get(key, record.get("retrain_time")) <= ceiling]
            self.assertEqual(prefix(original), prefix(changed))
        self.assertNotEqual(original["feedback"], changed["feedback"])

    def test_causal_replay_rejects_rehashed_activation_identity(self):
        rows, args = make_case("full_short", 64102)
        result = engine.run_online(rows, alarm_config=THRESHOLDS, **args)
        self.assertTrue(engine.verify_run(result, result["audit_sha256"],
                                          rows=rows, alarm_config=THRESHOLDS, **args))
        result["predictions"][-1]["active_model_id"] = "invalid-model"
        result["audit_sha256"] = _digest({key: value for key, value in result.items()
                                          if key != "audit_sha256"})
        with self.assertRaisesRegex(ValueError, "causal replay"):
            engine.verify_run(result, result["audit_sha256"],
                              rows=rows, alarm_config=THRESHOLDS, **args)
