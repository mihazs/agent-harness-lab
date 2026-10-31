import json
import unittest
from mine_traces import mine


def events(*rows):
    return [json.dumps(dict(case_id=c, event_id=str(i), activity=a, **extra))
            for i, (c, a, extra) in enumerate(rows)]


class MiningTests(unittest.TestCase):
    def test_interleaved_cases_do_not_create_cross_case_edges(self):
        result = mine(events(('a', 'x', {}), ('b', 'y', {}), ('a', 'z', {})))
        self.assertEqual(result['raw_edges'], [dict(source='x', target='z', occurrences=1, root_cases=1)])

    def test_child_traces_do_not_inflate_independent_support(self):
        result = mine(events(('a', 'x', {'root_case_id': 'r'}), ('a', 'y', {'root_case_id': 'r'}),
                             ('b', 'x', {'root_case_id': 'r'}), ('b', 'y', {'root_case_id': 'r'})))
        self.assertEqual(result['raw_edges'][0]['occurrences'], 2)
        self.assertEqual(result['raw_edges'][0]['root_cases'], 1)

    def test_projection_does_not_change_raw_counts(self):
        result = mine(events(('a', 'x', {}), ('a', 'unknown', {}), ('a', 'x', {}), ('a', 'y', {})), {'unknown'})
        self.assertEqual(len(result['raw_edges']), 3)
        self.assertEqual(result['projected_run_collapsed_edges'], [dict(source='x', target='y', occurrences=1, root_cases=1)])
        self.assertIn('unknown', result['activities'])

    def test_duplicate_ids_fail_without_printing_body(self):
        row = json.dumps(dict(case_id='a', event_id='1', activity='x', body='PRIVATE'))
        with self.assertRaisesRegex(ValueError, '^invalid normalized event or duplicate id at line 2$'):
            mine([row, row])

    def test_bad_input_and_inconsistent_lineage_fail(self):
        for value in ['null', '{}', '[]', '{"case_id": 1}']:
            with self.assertRaises(ValueError):
                mine([value])
        with self.assertRaises(ValueError):
            mine(events(('a', 'x', {'root_case_id': 'r'}), ('a', 'y', {'root_case_id': 'other'})))

    def test_order_and_unknown_outcome_are_preserved(self):
        result = mine(events(('a', 'stop', {'timestamp': '2026-01-02T00:00:00Z'}),
                             ('a', 'continue', {'timestamp': '2026-01-01T00:00:00Z'})))
        self.assertEqual(result['timestamp_reversals'], 1)
        self.assertEqual(result['raw_edges'][0]['source'], 'stop')
        self.assertNotIn('success', result)

    def test_extra_fields_are_not_exported(self):
        result = mine(events(('a', 'unknown', {'body': 'PRIVATE', 'outcome': 'success'})))
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['actors'], {'unknown': 1})


if __name__ == '__main__':
    unittest.main()
