"""Mechanism coverage keeps claim levels distinct and never counts unknown capture as covered (#10)."""
from copy import deepcopy
import json
import unittest

from agent_harness_lab.diagnostics.coverage import COVERAGE_PROFILE, render_markdown, report
from agent_harness_lab.diagnostics.mechanisms import MechanismRecord
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.router import run_queue

PROVENANCE = dict(source_kind='synthetic', approved_publication=True, source_ref='synthetic:fixture',
                  derived_from_private=False)


def record(name, component='instruction', severity='normal', **changes):
    value = MechanismRecord(name, 'synthetic symptom ' + name, ['synthetic:' + name], 'evidence-1', 'component-1',
                            component=component, severity=severity, exposure=4,
                            competing_explanations=['grader defect'], boundary='acceptance artifact',
                            falsifier='unrepaired artifact fails')
    for key, item in changes.items():
        setattr(value, key, item)
    return value


# One synthetic record per mechanism class the issue's acceptance criteria name.
OUTCOMES = {'instruction_contradiction': ('rejected', 'tie_with_baseline'),
            'missing_skill_activation': ('validated', 'development benefit'),
            'grader_bug': ('deferred', 'evaluator_repair_needs_new_oracle_version'),
            'rare_severe_defect': ('validated', 'development benefit')}


def synthetic_run():
    records = [record('instruction_contradiction'),
               record('missing_skill_activation', 'skill'),
               record('tool_error', 'tool_adapter'),
               record('grader_bug', 'evaluator'),
               record('unknown_capture', evidence=[]),
               record('necessary_repetition', 'no_change'),
               record('rare_severe_defect', severity='critical', exposure=1)]
    return run_queue(records, lambda m: OUTCOMES[m.mechanism_id], Budget(10, 2))


def by_id(result):
    return {entry['mechanism_id']: entry for entry in result['entries']}


class MechanismCoverageTests(unittest.TestCase):
    def test_synthetic_classes_keep_routes_claims_and_coverage_distinct(self):
        result = report(synthetic_run(), capture='complete')
        self.assertEqual(result['profile'], COVERAGE_PROFILE)
        self.assertEqual(result['entries'][0]['mechanism_id'], 'rare_severe_defect')
        rows = {name: (e['responsible_component'], e['claim_level'], e['disposition'], e['coverage'])
                for name, e in by_id(result).items()}
        self.assertEqual(rows, {
            'rare_severe_defect': ('instruction', 'development_recommendation', 'recommended', 'awaiting_certification'),
            'instruction_contradiction': ('instruction', 'supported_diagnosis', 'rejected', 'resolved'),
            'missing_skill_activation': ('skill', 'development_recommendation', 'recommended', 'awaiting_certification'),
            'tool_error': ('tool_adapter', 'supported_diagnosis', 'deferred', 'open'),
            'grader_bug': ('evaluator', 'supported_diagnosis', 'deferred', 'open'),
            'unknown_capture': ('insufficient_evidence', 'none', 'deferred', 'unknown_capture'),
            'necessary_repetition': ('no_change', 'observed_pattern', 'already_covered', 'resolved')})
        self.assertEqual(by_id(result)['tool_error']['reason'], 'adapter_route_not_authorized')
        self.assertEqual(result['totals']['coverage'],
                         dict(resolved=2, awaiting_certification=2, open=2, unknown_capture=1))
        self.assertEqual(result['totals']['claim_level']['validated_improvement'], 0)
        self.assertFalse(result['coverage_complete'])
        self.assertFalse(result['diagnosis_established'])
        self.assertFalse(result['causal_claim'])
        self.assertIn('taxonomy_and_routes_are_hypotheses_not_causal_proof', result['limitations'])

    def test_unknown_capture_is_never_a_coverage_pass(self):
        unseen = record('unseen', evidence=[]).transition('validated', 'claimed without capture')
        result = report([unseen], capture='complete')
        entry = result['entries'][0]
        self.assertEqual((entry['capture'], entry['claim_level'], entry['coverage']), ('unknown', 'none', 'unknown_capture'))
        self.assertIn('disposition_without_captured_evidence', entry['limitations'])
        self.assertFalse(result['coverage_complete'])
        resolved = [record('a').transition('rejected', 'tie'), record('b', 'no_change').transition('already_covered', 'expected')]
        self.assertFalse(report(resolved)['coverage_complete'])
        self.assertIn('capture_unknown_unseen_mechanisms_possible', report(resolved)['limitations'])
        self.assertFalse(report(resolved, capture='partial')['coverage_complete'])
        self.assertTrue(report(resolved, capture='complete')['coverage_complete'])
        empty = report([], capture='complete')
        self.assertFalse(empty['coverage_complete'])
        self.assertIn('no_mechanisms_recorded', empty['limitations'])

    def test_unsupported_disposition_cannot_raise_the_claim_or_close_coverage(self):
        loose = record('loose', falsifier=None).transition('validated', 'asserted')
        entry = report([loose], capture='complete')['entries'][0]
        self.assertEqual((entry['responsible_component'], entry['claim_level'], entry['coverage']),
                         ('insufficient_evidence', 'observed_pattern', 'open'))
        self.assertIn('disposition_exceeds_supported_claim', entry['limitations'])
        certified = record('certified').transition('validated', 'final certification passed')
        entry = report([certified], capture='complete')['entries'][0]
        self.assertEqual((entry['claim_level'], entry['coverage']), ('validated_improvement', 'resolved'))

    def test_tie_for_one_mechanism_leaves_the_rest_tracked_and_deferred_on_budget(self):
        results = run_queue([record('a'), record('b')], lambda m: ('rejected', 'tie'), Budget(2, 1))
        rows = {e['mechanism_id']: (e['disposition'], e['coverage']) for e in report(results)['entries']}
        self.assertEqual(rows, dict(a=('rejected', 'resolved'), b=('deferred', 'open')))
        self.assertEqual(by_id(report(results))['b']['reason'], 'parent_budget_or_final_reserve_exhausted')

    def test_duplicates_are_suppressed_only_for_matching_versions(self):
        newer = record('a', evidence_version='evidence-2')
        result = report([record('a'), record('a'), newer, record('b')])
        self.assertEqual(result['totals']['mechanisms'], 3)
        self.assertEqual(result['totals']['duplicates_suppressed'], 1)
        self.assertEqual(list(result['multiple_versions']), ['a'])
        self.assertEqual(len(result['multiple_versions']['a']), 2)

    def test_conflicting_dispositions_for_one_key_keep_coverage_open_in_any_order(self):
        def resolved():
            return record('a').transition('rejected', 'tie')

        def deferred():
            return record('a').transition('deferred', 'parent_budget_or_final_reserve_exhausted')
        for order in ([resolved(), deferred()], [deferred(), resolved()]):
            result = report(order, capture='complete')
            self.assertFalse(result['coverage_complete'])
            self.assertEqual(result['entries'][0]['coverage'], 'open')
            self.assertIn('conflicting_records_for_key', result['entries'][0]['limitations'])
            self.assertIn('conflicting_records_for_key', result['limitations'])
            self.assertEqual(result['totals']['duplicates_suppressed'], 1)
            self.assertTrue(result['duplicates'][0]['conflict'])
            self.assertEqual(result['duplicates'][0]['disposition'], order[1].disposition)
        exact = report([resolved(), resolved()], capture='complete')
        self.assertTrue(exact['coverage_complete'])
        self.assertFalse(exact['duplicates'][0]['conflict'])
        self.assertNotIn('conflicting_records_for_key', exact['limitations'])

    def test_same_key_records_that_derive_different_coverage_conflict_in_any_order(self):
        pairs = {'actionability': (lambda: record('a').transition('validated', 'ok'),
                                   lambda: record('a', falsifier=None).transition('validated', 'ok')),
                 'evidence': (lambda: record('a').transition('rejected', 'tie'),
                              lambda: record('a', evidence=[]).transition('rejected', 'tie')),
                 'exposure': (lambda: record('a').transition('rejected', 'tie'),
                              lambda: record('a', exposure=None).transition('rejected', 'tie'))}
        for name, (first, second) in pairs.items():
            for order in ([first(), second()], [second(), first()]):
                with self.subTest(name, first=order[0].as_dict()):
                    result = report(order, capture='complete')
                    self.assertFalse(result['coverage_complete'])
                    self.assertNotEqual(result['entries'][0]['coverage'], 'resolved')
                    self.assertEqual([d['conflict'] for d in result['duplicates']], [True])
                    self.assertIn('conflicting_records_for_key', result['entries'][0]['limitations'])
                    self.assertIn('conflicting_records_for_key', result['limitations'])

    def test_report_is_pure_deterministic_private_and_accepts_queue_output(self):
        records = [record('a', exposure=None), record('b', 'skill')]
        before = deepcopy([r.as_dict() for r in records])
        first = report(records, capture='partial')
        self.assertEqual([r.as_dict() for r in records], before)
        self.assertEqual(report([r.as_dict() for r in records], capture='partial'), first)
        self.assertEqual(json.loads(json.dumps(first)), first)
        self.assertEqual(first['totals']['exposure_unknown'], 1)
        self.assertIn('exposure_denominator_unknown', by_id(first)['a']['limitations'])
        self.assertIs(first['publication_allowed'], False)
        self.assertFalse(public_export_allowed([first]))
        self.assertFalse(public_export_allowed([dict(first, publication_allowed=True, provenance=PROVENANCE)]))
        with self.assertRaises(ValueError):
            report(records, capture='assumed')
        with self.assertRaises(ValueError):
            report([dict(before[0], schema_version=2)])
        with self.assertRaises(ValueError):
            report([dict(before[0], unexpected=True)])

    def test_markdown_escapes_untrusted_text_and_requires_a_private_report(self):
        hostile = record('a').transition('rejected', 'tie | injected\n# Heading `code`')
        text = render_markdown(report([hostile], capture='complete'))
        self.assertIn('# Private mechanism coverage report', text)
        self.assertNotIn('\n# Heading', text)
        self.assertIn('\\| injected', text)
        self.assertIn('Coverage complete: yes', text)
        self.assertIn('not causal proof', text)
        with self.assertRaises(ValueError):
            render_markdown(dict(report([hostile]), publication_allowed=True))


if __name__ == '__main__':
    unittest.main()
