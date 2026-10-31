import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('benchmark_models', Path(__file__).resolve().parents[1] / 'benchmark_models.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def row(root, label, split='calibration'):
    return dict(root_case_id=root, label=label, text='redacted evidence', split=split)


class BenchmarkTests(unittest.TestCase):
    def test_abstention_hurts_recall_unknowns_are_not_negative_labels(self):
        result = bench.measure([row('a', True), row('b', True), row('c', False), row('d', None)], [.99, .6, .01, .99], .9)
        self.assertEqual(result['precision'], 1)
        self.assertEqual(result['recall'], .5)
        self.assertEqual(result['accepted'], 2)
        self.assertEqual(result['unknown'], 1)
        self.assertAlmostEqual(result['brier'], (.0001+.16+.0001)/3)

    def test_no_labels_and_small_calibration_abstain(self):
        self.assertIsNone(bench.choose_threshold([row('a', True)], [.99]))
        self.assertIsNone(bench.measure([row('a', None)], [.99], .9)['brier'])
        self.assertEqual(bench.measure([row('a', True)], [.99], None)['coverage'], 0)

    def test_repeated_events_do_not_inflate_independent_calibration(self):
        rows = [row('one-root', True) for _ in range(100)]
        self.assertIsNone(bench.choose_threshold(rows, [.99]*100))
        rows = [row(str(i), True) for i in range(40)]
        self.assertEqual(bench.choose_threshold(rows, [.99]*40), .5)

    def test_probability_validation(self):
        for values in ([float('nan')], [-.1], [1.1], []):
            with self.assertRaises(ValueError):
                bench.measure([row('a', True)], values, .9)

    def test_split_leakage_and_nonboolean_labels_fail(self):
        for rows in ([row('same', True, 'train'), row('same', False, 'heldout')],
                     [row('a', 1)], [{'root_case_id': 'a', 'text': '', 'split': 'train'}]):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp)/'cases.jsonl'
                path.write_text('\n'.join(json.dumps(r) for r in rows))
                with self.assertRaises(ValueError):
                    bench.read_examples(path)

    def test_limit_and_valid_missing_label(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'cases.jsonl'
            path.write_text(json.dumps(row('a', None))+'\n'+json.dumps(row('b', True)))
            self.assertEqual(len(bench.read_examples(path)), 2)
            with self.assertRaises(ValueError):
                bench.read_examples(path, limit=1)


if __name__ == '__main__':
    unittest.main()

class FixtureTests(unittest.TestCase):
    def test_example_fixture_has_separate_roots_and_both_labels(self):
        fixture_spec = importlib.util.spec_from_file_location('fixture', Path(__file__).parents[2]/'examples/classifier_fixture.py')
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        rows = list(fixture.examples())
        self.assertEqual(len(rows), len({r['root_case_id'] for r in rows}))
        for split in bench.SPLITS:
            self.assertEqual({r['label'] for r in rows if r['split'] == split}, {False, True})
