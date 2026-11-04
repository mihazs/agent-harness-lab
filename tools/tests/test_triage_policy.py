import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import benchmark_models as bench


def policy(**changes):
    return dict(schema_version=1, threshold=.5, min_precision=.8,
                min_recall=.8, min_coverage=.5, **changes)


def row(root, label):
    return dict(root_case_id=root, label=label, split='heldout', text='synthetic evidence')


class TriagePolicyTests(unittest.TestCase):
    def test_majority_accuracy_cannot_hide_zero_incident_recall(self):
        rows = [row(str(i), i >= 990) for i in range(1000)]
        scores = [.01] * len(rows)
        self.assertEqual(bench.choose_threshold(rows, scores), .5)
        result = bench.evaluate_triage_policy(rows, scores, policy())
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['metrics']['recall'], 0)
        self.assertEqual(result['metrics']['fn'], 10)
        self.assertIsNone(result['metrics']['precision'])
        self.assertIn('recall_below_minimum', result['reasons'])

    def test_good_empirical_scores_do_not_establish_independent_validation(self):
        result = bench.evaluate_triage_policy([row('p', True), row('n', False)], [.99, .01], policy())
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertEqual(result['metrics']['coverage'], 1)
        self.assertEqual(result['root_independence'], 'unverified')
        self.assertFalse(result['automatic_promotion'])

    def test_repeated_root_counts_once_and_conflicting_decisions_abstain(self):
        rows = [row('p', True)] * 20 + [row('n', False)] * 20
        first = bench.evaluate_triage_policy(rows, [.99] * 20 + [.01] * 20, policy())
        self.assertEqual(first['metrics']['labeled_roots'], 2)
        scores = [.99] * 19 + [.01] + [.01] * 20
        result = bench.evaluate_triage_policy(rows, scores, policy())
        self.assertEqual(result['metrics']['abstained_positive'], 1)
        self.assertEqual(result['metrics']['recall'], 0)

    def test_abstention_and_missing_class_cannot_qualify(self):
        abstained = bench.evaluate_triage_policy([row('p', True), row('n', False)], [.6, .4],
                                                 dict(policy(), threshold=.9))
        self.assertEqual(abstained['status'], 'rejected')
        self.assertEqual(abstained['metrics']['coverage'], 0)
        missing = bench.evaluate_triage_policy([row('n', False)], [.01], policy())
        self.assertIsNone(missing['metrics']['recall'])
        self.assertNotEqual(missing['status'], 'accepted_for_shadow')

    def test_invalid_policy_and_validation_input_reject(self):
        for value in (dict(policy(), threshold=float('nan')), dict(policy(), min_recall=True),
                      dict(policy(), extra=1), dict(policy(), schema_version=2)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bench.evaluate_triage_policy([row('p', True)], [.99], value)
        for rows, scores in (([dict(row('p', True), split='calibration')], [.99]),
                             ([row('p', True), row('p', False)], [.99, .01]),
                             ([row('p', True)], [float('nan')])):
            with self.assertRaises(ValueError):
                bench.evaluate_triage_policy(rows, scores, policy())

    def test_public_cli_uses_frozen_policy_on_heldout_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, configuration = root/'examples.jsonl', root/'policy.json'
            source.write_text('\n'.join(json.dumps(r) for r in
                [dict(row('train', False), split='train'), row('p', True), row('n', False)]))
            configuration.write_text(json.dumps(policy()))
            process = subprocess.run([sys.executable, '-m', 'tools.benchmark_models', str(source),
                '--method', 'baseline', '--triage-policy', str(configuration)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result['triage']['metrics']['labeled_roots'], 2)
            self.assertEqual(result['triage']['status'], 'rejected')
            self.assertEqual(result['threshold_authority'], 'descriptive_smoke_only')
            self.assertTrue(all(p['abstain'] for p in result['predictions']))
            self.assertEqual(result['triage']['decision_threshold_source'], 'frozen_policy')
            self.assertTrue(all(p['value'] is False for p in result['triage']['decisions']))
