"""Deterministic constraint results are observations over captured events, never diagnoses (#10)."""
import unittest

from agent_harness_lab.diagnostics import constraints


def definition(kind='authorization_scope', **changes):
    params = dict(tool_contract=dict(tool='Edit'), temporal_prerequisite=dict(requires='Read', then='Edit'),
                  authorization_scope=dict(forbidden_tools=['Deploy']), state_consistency=dict(tool='Read'),
                  outcome_integrity={})[kind]
    base = dict(schema=constraints.DEFINITION_SCHEMA, constraint_id=kind + '-1', version=1, kind=kind,
                origin='reviewed', scope='any', params=params)
    base.update(changes)
    return base


def row(index, activity='tool_call', **fields):
    return dict(case_id='case', event_id=f'e{index}', activity=activity, capture_order=index, **fields)


def check(kind, rows, capture='complete', **changes):
    return constraints.evaluate(definition(kind, **changes), rows, scope='dev', capture=capture)


def verdict(kind, rows, capture='complete', **changes):
    result = check(kind, rows, capture, **changes)
    return result['status'], result['reason']


class DefinitionTests(unittest.TestCase):
    def test_frozen_schema_and_trusted_origin(self):
        self.assertEqual(constraints.validate(definition()), definition())
        for bad in (dict(origin='model'), dict(origin=None), dict(kind='free_text'), dict(kind=['x']), dict(version=0),
                    dict(version=True), dict(scope='bad scope'), dict(constraint_id=''), dict(params={}),
                    dict(params=dict(forbidden_tools='Deploy')), dict(params=dict(forbidden_tools=[])),
                    dict(params=dict(forbidden_tools=['a', 'a'])), dict(params=dict(forbidden_tools=[['a']])),
                    dict(schema='other')):
            with self.assertRaises(ValueError, msg=bad):
                constraints.validate({**definition(), **bad})
        with self.assertRaises(ValueError):
            constraints.validate({**definition(), 'explanation': 'because'})
        with self.assertRaises(ValueError):
            constraints.validate(definition('temporal_prerequisite', params=dict(requires='Edit', then='Edit')))
        with self.assertRaises(ValueError):
            constraints.validate(definition('tool_contract', params=dict(tool='Edit', extra='x')))

    def test_run_inputs_are_checked(self):
        for scope, capture in (('', 'complete'), ('dev', 'maybe'), (None, 'unknown')):
            with self.assertRaises(ValueError):
                constraints.evaluate(definition(), [], scope=scope, capture=capture)
        with self.assertRaises(ValueError):
            check('authorization_scope', [row(0), dict(row(1), case_id='other')])
        with self.assertRaises(ValueError):
            check('authorization_scope', [row(0), row(0)])
        with self.assertRaises(ValueError):
            check('authorization_scope', [dict(row(0), contract_violation='yes')])


class StatusTests(unittest.TestCase):
    def test_a_violation_holds_under_any_capture_but_satisfaction_needs_a_complete_one(self):
        calls = [row(0, tool_name='Read'), row(1, tool_name='Deploy')]
        for capture in ('complete', 'partial', 'unknown'):
            self.assertEqual(verdict('authorization_scope', calls, capture), ('violated', 'violation_observed'))
        clean = [row(0, tool_name='Read')]
        self.assertEqual(verdict('authorization_scope', clean), ('satisfied', 'no_counter_evidence_in_complete_capture'))
        self.assertEqual(verdict('authorization_scope', clean, 'partial'), ('unknown', 'capture_partial'))
        self.assertEqual(verdict('authorization_scope', clean, 'unknown'), ('unknown', 'capture_unknown'))
        self.assertEqual(verdict('authorization_scope', []), ('satisfied', 'no_counter_evidence_in_complete_capture'))

    def test_a_row_reporting_an_incomplete_capture_lowers_a_declared_complete_one(self):
        rows = [row(0, tool_name='Read', capture_complete=False)]
        result = check('authorization_scope', rows)
        self.assertEqual((result['status'], result['declared_capture'], result['capture']),
                         ('unknown', 'complete', 'partial'))

    def test_nothing_exercised_is_nothing_learned_except_for_a_forbidden_call(self):
        self.assertEqual(verdict('tool_contract', [row(0, tool_name='Read')]), ('unknown', 'constraint_not_exercised'))
        self.assertEqual(verdict('temporal_prerequisite', [row(0, tool_name='Read')]),
                         ('unknown', 'constraint_not_exercised'))
        self.assertEqual(verdict('state_consistency', []), ('unknown', 'constraint_not_exercised'))

    def test_scope_mismatch_is_unknown_not_satisfied(self):
        self.assertEqual(verdict('authorization_scope', [], scope='other'), ('unknown', 'out_of_scope'))
        self.assertEqual(verdict('authorization_scope', [], scope='dev'), ('satisfied', 'no_counter_evidence_in_complete_capture'))

    def test_a_call_without_a_captured_name_cannot_rule_out_a_named_constraint(self):
        rows = [row(0, tool_name='Read'), row(1)]
        self.assertEqual(verdict('authorization_scope', rows), ('unknown', 'required_field_missing'))
        self.assertEqual(verdict('authorization_scope', rows + [row(2, tool_name='Deploy')])[0], 'violated')


class ToolContractTests(unittest.TestCase):
    def test_declared_contract_violation(self):
        ok = [row(0, tool_name='Edit', contract_violation=False)]
        bad = ok + [row(1, tool_name='Edit', contract_violation=True), row(2, tool_name='Read', contract_violation=True)]
        self.assertEqual(verdict('tool_contract', ok)[0], 'satisfied')
        result = check('tool_contract', bad)
        self.assertEqual(result['status'], 'violated')
        self.assertEqual(result['evidence'], [dict(case_id='case', event_id='e1')])

    def test_an_unreported_contract_flag_is_unknown_and_listed_as_missing_input(self):
        result = check('tool_contract', [row(0, tool_name='Edit')])
        self.assertEqual((result['status'], result['reason']), ('unknown', 'required_field_missing'))
        self.assertEqual(result['missing_inputs'], ['contract_violation'])
        self.assertEqual(result['required_inputs'], ['tool_name', 'contract_violation'])


class TemporalPrerequisiteTests(unittest.TestCase):
    def test_prerequisite_before_the_call(self):
        rows = [row(0, tool_name='Read'), row(1, tool_name='Edit')]
        self.assertEqual(verdict('temporal_prerequisite', rows)[0], 'satisfied')

    def test_missing_prerequisite_is_a_violation_only_in_a_complete_capture(self):
        late = [row(0, tool_name='Edit'), row(1, tool_name='Read')]
        self.assertEqual(verdict('temporal_prerequisite', late), ('violated', 'violation_observed'))
        self.assertEqual(verdict('temporal_prerequisite', late, 'partial'),
                         ('unknown', 'prerequisite_may_be_outside_capture'))
        self.assertEqual(verdict('temporal_prerequisite', late, 'unknown'),
                         ('unknown', 'prerequisite_may_be_outside_capture'))

    def test_undeclared_or_cross_file_order_is_never_invented(self):
        rows = [row(0, tool_name='Edit'), row(1, tool_name='Read')]
        unordered = [{k: v for k, v in r.items() if k != 'capture_order'} for r in rows]
        self.assertEqual(verdict('temporal_prerequisite', unordered), ('unknown', 'order_not_established'))
        # A timestamp is not an order: it never substitutes for a missing capture ordinal.
        stamped = [dict(r, timestamp=f'2026-01-01T00:00:0{i}Z') for i, r in enumerate(unordered)]
        self.assertEqual(verdict('temporal_prerequisite', stamped), ('unknown', 'order_not_established'))
        tied = [dict(r, capture_order=3) for r in rows]
        self.assertEqual(verdict('temporal_prerequisite', tied), ('unknown', 'order_not_established'))
        profile = dict(source_format='claude', source_version='claude-jsonl-2.1.316', source_order_profile='claude-native-order-v1')
        split = [dict(rows[0], source_order_segment=0, source_suborder=0, **profile),
                 dict(rows[1], source_order_segment=1, source_suborder=0, **profile)]
        self.assertEqual(verdict('temporal_prerequisite', split), ('unknown', 'order_not_established'))

    def test_a_declared_subordinal_orders_a_tie_inside_one_segment(self):
        profile = dict(source_format='claude', source_version='claude-jsonl-2.1.316', source_order_profile='claude-native-order-v1',
                       source_order_segment=0, capture_order=4)
        ordered = [dict(row(0, tool_name='Read'), source_suborder=0, **profile),
                   dict(row(1, tool_name='Edit'), source_suborder=1, **profile)]
        self.assertEqual(verdict('temporal_prerequisite', ordered)[0], 'satisfied')
        self.assertEqual(verdict('temporal_prerequisite', ordered[::-1])[0], 'satisfied')
        reversed_ = [dict(ordered[0], tool_name='Edit'), dict(ordered[1], tool_name='Read')]
        self.assertEqual(verdict('temporal_prerequisite', reversed_)[0], 'violated')

    def test_an_unnamed_earlier_call_may_be_the_prerequisite(self):
        rows = [row(0), row(1, tool_name='Edit')]
        self.assertEqual(verdict('temporal_prerequisite', rows), ('unknown', 'required_field_missing'))
        # A named prerequisite settles the call regardless of unnamed neighbours.
        self.assertEqual(verdict('temporal_prerequisite', [row(0), row(1, tool_name='Read'), row(2, tool_name='Edit')])[0], 'satisfied')


class StateConsistencyTests(unittest.TestCase):
    def read(self, index, result, revision='r1', call='k1', **fields):
        return [row(index, tool_name='Read', call_id=call, input_fingerprint='a' * 16),
                row(index + 1, 'tool_result', call_id=call, state_revision=revision, result_fingerprint=result, **fields)]

    def test_same_input_and_revision_with_different_results_is_inconsistent(self):
        rows = self.read(0, 'x' * 16, call='k1') + self.read(2, 'y' * 16, call='k2')
        result = check('state_consistency', rows)
        self.assertEqual((result['status'], result['reason']), ('violated', 'violation_observed'))
        self.assertEqual([e['event_id'] for e in result['evidence']], ['e1', 'e3'])

    def test_a_changed_state_revision_or_equal_results_are_consistent(self):
        changed = self.read(0, 'x' * 16, 'r1', 'k1') + self.read(2, 'y' * 16, 'r2', 'k2')
        self.assertEqual(verdict('state_consistency', changed)[0], 'satisfied')
        equal = self.read(0, 'x' * 16, 'r1', 'k1') + self.read(2, 'x' * 16, 'r1', 'k2')
        self.assertEqual(verdict('state_consistency', equal)[0], 'satisfied')

    def test_missing_state_signatures_are_unknown_never_satisfied(self):
        rows = [row(0, tool_name='Read', call_id='k1'), row(1, 'tool_result', call_id='k1', result_fingerprint='x' * 16)]
        result = check('state_consistency', rows)
        self.assertEqual((result['status'], result['reason']), ('unknown', 'required_field_missing'))
        self.assertEqual(result['missing_inputs'], ['input_fingerprint', 'state_revision'])


class OutcomeIntegrityTests(unittest.TestCase):
    def test_outcome_label_without_independent_provenance_is_a_violation(self):
        label = row(0, 'assistant', outcome='success', outcome_provenance='model_hypothesis')
        self.assertEqual(verdict('outcome_integrity', [label]), ('violated', 'violation_observed'))

    def test_verified_outcome_satisfies_only_in_a_complete_capture(self):
        graded = row(0, 'grade', outcome='failure', outcome_provenance='executable_grader', grader_id='g',
                     grader_version='1', grader_status='valid')
        self.assertEqual(verdict('outcome_integrity', [graded])[0], 'satisfied')
        self.assertEqual(verdict('outcome_integrity', [graded], 'partial'), ('unknown', 'capture_partial'))
        self.assertEqual(verdict('outcome_integrity', [dict(graded, grader_status='broken')])[0], 'violated')

    def test_an_absent_final_outcome_is_unknown(self):
        self.assertEqual(verdict('outcome_integrity', [row(0, tool_name='Read')]), ('unknown', 'final_outcome_not_captured'))
        self.assertEqual(verdict('outcome_integrity', []), ('unknown', 'final_outcome_not_captured'))


class ReportTests(unittest.TestCase):
    def rows(self):
        return [row(0, tool_name='Read'), row(1, tool_name='Deploy'), row(2, tool_name='Edit', contract_violation=False)]

    def defs(self):
        return [definition('tool_contract'), definition('authorization_scope'), definition('outcome_integrity')]

    def test_every_definition_is_listed_with_its_own_status_and_nothing_is_dropped(self):
        body = constraints.report(self.defs(), self.rows(), scope='dev', capture='complete')
        self.assertEqual({r['kind']: r['status'] for r in body['results']},
                         dict(tool_contract='satisfied', authorization_scope='violated', outcome_integrity='unknown'))
        self.assertEqual(body['counts'], dict(satisfied=1, violated=1, unknown=1))

    def test_results_are_observations_with_no_authority_or_diagnosis(self):
        body = constraints.report(self.defs(), self.rows(), scope='dev', capture='complete')
        for item in (body, *body['results']):
            self.assertEqual((item['authority'], item['diagnosis_established'], item['causal_claim']), ('none', False, False))
            self.assertFalse(item['publication_allowed'])
        for item in body['results']:
            self.assertFalse({'component', 'route', 'disposition', 'root_cause'} & set(item))
        self.assertIn('earliest_visible_violation_is_not_the_root_cause', body['limitations'])
        self.assertIn('unknown_is_not_satisfied', body['limitations'])

    def test_report_is_deterministic_and_order_independent(self):
        first = constraints.report(self.defs(), self.rows(), scope='dev', capture='complete')
        second = constraints.report(self.defs()[::-1], self.rows()[::-1], scope='dev', capture='complete')
        self.assertEqual(first, second)
        self.assertEqual(len(first['sha256']), 64)
        changed = constraints.report(self.defs(), self.rows(), scope='dev', capture='partial')
        self.assertNotEqual(first['sha256'], changed['sha256'])

    def test_a_changed_definition_changes_its_digest_and_duplicates_are_refused(self):
        a = check('authorization_scope', [])
        b = check('authorization_scope', [], params=dict(forbidden_tools=['Deploy', 'Drop']))
        self.assertNotEqual(a['constraint_sha256'], b['constraint_sha256'])
        with self.assertRaises(ValueError):
            constraints.report([definition(), definition()], [], scope='dev')
        two = constraints.report([definition(), definition(version=2)], [], scope='dev', capture='complete')
        self.assertEqual([r['constraint_version'] for r in two['results']], [1, 2])

    def test_evidence_references_are_bounded_and_the_overflow_is_counted(self):
        rows = [row(i, tool_name='Deploy') for i in range(constraints.MAX_REFS + 5)]
        result = check('authorization_scope', rows)
        self.assertEqual((len(result['evidence']), result['evidence_omitted']), (constraints.MAX_REFS, 5))

    def test_expected_negative_polling_and_recovery_are_not_violations(self):
        rows = [row(0, tool_name='Read', call_id='k1', input_fingerprint='a' * 16),
                row(1, 'tool_result', call_id='k1', state_revision='r1', result_fingerprint='x' * 16, execution_status='error'),
                row(2, tool_name='Read', call_id='k2', input_fingerprint='a' * 16),
                row(3, 'tool_result', call_id='k2', state_revision='r1', result_fingerprint='x' * 16)]
        self.assertEqual(verdict('state_consistency', rows)[0], 'satisfied')
        self.assertEqual(verdict('authorization_scope', rows)[0], 'satisfied')


if __name__ == '__main__':
    unittest.main()
