"""The coverage report lists every reconciled prediction and never lets a contradicted one close a mechanism (#10)."""
import unittest

from agent_harness_lab.diagnostics.coverage import render_markdown, report
from agent_harness_lab.diagnostics.predictions import PredictionLedger, reconcile
from tests.unit.diagnostics.test_mechanism_coverage import record
from tests.unit.diagnostics.test_predictions import obs, pairs, prediction


def reconciled(*cases):
    ledger = PredictionLedger()
    observations = {}
    for rec, observation in cases:
        ledger.register(rec)
        if observation is not None:
            observations[rec.prediction_id] = observation
    return reconcile(ledger, observations)


class CoveragePredictionTests(unittest.TestCase):
    def test_wrong_and_untested_predictions_stay_listed(self):
        table = reconciled((prediction(), obs()),
                           (prediction(prediction_id='p2', mechanism_id='m2'), obs(pairs=pairs(ctl=(True, False)))),
                           (prediction(prediction_id='p3', mechanism_id='m3'), None))
        result = report([record('m1')], predictions=table)
        got = result['predictions']
        self.assertEqual(got['eligible_predictions'], 3)
        self.assertEqual([row['status'] for row in got['entries']],
                         ['supported_under_test', 'contradicted_under_test', 'not_tested'])
        self.assertFalse(got['certifies'])
        self.assertIn('mechanism_not_recorded', got['entries'][1]['limitations'])
        text = render_markdown(result)
        for status in ('supported_under_test', 'contradicted_under_test', 'not_tested'):
            self.assertIn(status, text)

    def test_contradicted_prediction_reopens_validated_mechanism(self):
        mechanism = record('m1', disposition='validated')
        self.assertEqual(report([mechanism])['entries'][0]['coverage'], 'resolved')
        table = reconciled((prediction(), obs(pairs=pairs(ctl=(True, False)))))
        entry = report([mechanism], capture='complete', predictions=table)
        self.assertEqual(entry['entries'][0]['coverage'], 'open')
        self.assertIn('prediction_contradicted', entry['entries'][0]['limitations'])
        self.assertFalse(entry['coverage_complete'])

    def test_absent_table_is_declared_and_foreign_input_rejected(self):
        self.assertIsNone(report([record('m1')])['predictions'])
        self.assertIn('no_predictions_reconciled', report([record('m1')])['limitations'])
        with self.assertRaises(ValueError):
            report([record('m1')], predictions={'profile': 'other', 'entries': []})
        with self.assertRaises(ValueError):
            report([record('m1')], predictions={'profile': 'prediction-reconciliation-v1', 'entries': [{'status': 'x'}]})

    def test_markdown_escapes_untrusted_prediction_text(self):
        table = reconciled((prediction(observable_effect='x | <b>\n# heading'), None))
        text = render_markdown(report([record('m1')], predictions=table))
        self.assertNotIn('\n# heading', text)


if __name__ == '__main__':
    unittest.main()
