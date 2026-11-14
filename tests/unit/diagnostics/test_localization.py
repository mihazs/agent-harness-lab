"""Localization of violated constraints is a bounded hypothesis, never a diagnosis or a route (#10)."""
import copy
import unittest

from agent_harness_lab.diagnostics import constraints, localization
from agent_harness_lab.diagnostics.mechanisms import MechanismRecord
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.router import dispositions, route
from tests.unit.diagnostics.test_constraints import definition, row


def locate(kinds, rows, capture='complete', **changes):
    return localization.localize([definition(k, **changes) for k in kinds], rows, scope='dev', capture=capture)


def only(body):
    (entry,) = body['localizations']
    return entry


def ids(refs):
    return [r['event_id'] for r in refs]


GRADED = dict(outcome='failure', outcome_provenance='executable_grader', grader_id='g', grader_version='1', grader_status='valid')


class LocalizationTests(unittest.TestCase):
    def test_first_visible_symptom_downstream_failure_and_absent_upstream_are_distinguished(self):
        rows = [row(0, tool_name='Grep'), row(1, tool_name='Edit', call_id='k1'),
                row(2, 'tool_result', call_id='k1', execution_status='error'), row(3, 'grade', **GRADED)]
        entry = only(locate(['temporal_prerequisite'], rows))
        self.assertEqual((entry['status'], entry['localization'], entry['upstream']), ('violated', 'bounded_candidates', 'candidates_present'))
        self.assertEqual((ids(entry['earliest_visible']), entry['earliest_is_unique']), (['e1'], True))
        self.assertEqual(entry['absent_upstream'], ['Read'])
        self.assertEqual([(c['event_id'], c['basis'], c['relation']) for c in entry['candidates']],
                         [('e2', 'call_result_link', 'downstream'), ('e3', 'declared_later_outcome', 'downstream')])
        # The unrelated earlier call is neither a symptom nor a candidate: nothing implicates it.
        self.assertNotIn('e0', ids(entry['candidates']))
        self.assertFalse(entry['root_cause_established'])

    def test_a_prerequisite_declared_after_its_dependent_is_not_an_upstream_cause(self):
        entry = only(locate(['temporal_prerequisite'], [row(0, tool_name='Edit'), row(1, tool_name='Read')]))
        self.assertEqual((entry['localization'], entry['absent_upstream']), ('symptom_only', []))
        self.assertEqual([(c['event_id'], c['basis'], c['relation']) for c in entry['candidates']],
                         [('e1', 'required_event', 'downstream')])
        self.assertIn('upstream_cause_not_captured', entry['competing_causes'])

    def test_missing_upstream_evidence_stays_unresolved(self):
        entry = only(locate(['authorization_scope'], [row(0, tool_name='Read'), row(1, tool_name='Deploy')]))
        self.assertEqual((entry['status'], entry['localization'], entry['upstream']), ('violated', 'symptom_only', 'not_established'))
        self.assertEqual((entry['candidates'], entry['absent_upstream']), ([], []))
        self.assertIn('upstream_cause_not_captured', entry['competing_causes'])
        self.assertFalse(entry['root_cause_established'])

    def test_a_result_the_constraint_left_unknown_is_unresolved_and_names_no_candidate(self):
        late = [row(0, tool_name='Edit'), row(1, tool_name='Read')]
        entry = only(locate(['temporal_prerequisite'], late, capture='partial'))
        self.assertEqual((entry['status'], entry['localization']), ('unknown', 'unresolved'))
        self.assertEqual(entry['unresolved_reasons'], ['prerequisite_may_be_outside_capture'])
        self.assertFalse({'candidates', 'earliest_visible', 'competing_causes', 'intervention'} & set(entry))

    def test_a_satisfied_constraint_is_listed_without_a_localization(self):
        entry = only(locate(['temporal_prerequisite'], [row(0, tool_name='Read'), row(1, tool_name='Edit')]))
        self.assertEqual((entry['status'], entry['localization']), ('satisfied', 'not_applicable'))

    def test_state_inconsistency_links_each_result_to_its_call_and_a_declared_mutation_before_both(self):
        read = lambda index, result, call: [
            row(index, tool_name='Read', call_id=call, input_fingerprint='a' * 16, tool_effect='read_only'),
            row(index + 1, 'tool_result', call_id=call, state_revision='r1', result_fingerprint=result, execution_status='returned')]
        rows = [row(0, tool_name='Edit', call_id='k0', mutation=True)] + read(1, 'x' * 16, 'k1') + read(3, 'y' * 16, 'k2')
        entry = only(locate(['state_consistency'], rows))
        self.assertEqual(entry['localization'], 'bounded_candidates')
        self.assertEqual([(c['event_id'], c['basis'], c['relation']) for c in entry['candidates']],
                         [('e0', 'declared_mutation', 'upstream'), ('e1', 'call_result_link', 'upstream'),
                          ('e3', 'call_result_link', 'upstream')])
        self.assertEqual(ids(entry['earliest_visible']), ['e2'])
        self.assertIn('undeclared_external_state_change', entry['competing_causes'])

    def test_order_is_never_invented_from_timestamps_or_missing_ordinals(self):
        rows = [row(i, tool_name='Deploy', timestamp=f'2026-01-01T00:00:0{i}Z') for i in range(2)]
        unordered = [{k: v for k, v in r.items() if k != 'capture_order'} for r in rows]
        entry = only(locate(['authorization_scope'], unordered))
        self.assertEqual((ids(entry['earliest_visible']), entry['earliest_is_unique']), (['e0', 'e1'], False))
        ordered = only(locate(['authorization_scope'], rows))
        self.assertEqual((ids(ordered['earliest_visible']), ordered['earliest_is_unique']), (['e0'], True))

    def test_an_outcome_with_no_declared_order_is_not_a_downstream_failure(self):
        later = row(1, 'grade', **GRADED)
        entry = only(locate(['authorization_scope'], [row(0, tool_name='Deploy'), later]))
        self.assertEqual(ids(entry['candidates']), ['e1'])
        unordered = {k: v for k, v in later.items() if k != 'capture_order'}
        entry = only(locate(['authorization_scope'], [row(0, tool_name='Deploy'), unordered]))
        self.assertEqual(entry['candidates'], [])


class NegativeControlTests(unittest.TestCase):
    def rows(self):
        call = lambda i, k, **f: row(i, tool_name='Read', call_id=k, input_fingerprint='a' * 16, tool_effect='read_only', **f)
        return [call(0, 'k1'), row(1, 'tool_result', call_id='k1', execution_status='error', state_revision='r1', result_fingerprint='e' * 16),
                call(2, 'k2'), row(3, 'tool_result', call_id='k2', execution_status='returned', state_revision='r1', result_fingerprint='x' * 16),
                call(4, 'k3'), row(5, 'tool_result', call_id='k3', execution_status='returned', state_revision='r2', result_fingerprint='y' * 16)]

    def test_recovered_errors_and_polling_over_a_changing_state_are_listed_not_flagged(self):
        body = locate(['state_consistency', 'authorization_scope'], self.rows())
        self.assertEqual([(c['event_id'], c['kind']) for c in body['negative_controls']],
                         [('e1', 'recovered_error'), ('e3', 'repeat_over_changing_state'), ('e5', 'repeat_over_changing_state')])
        self.assertEqual(body['counts'], dict(bounded_candidates=0, symptom_only=0, unresolved=0, not_applicable=2))
        drafts = localization.mechanism_records(body, evidence_version='ev1', component_version='c1')
        self.assertEqual(drafts, [])

    def test_a_result_with_no_named_tool_or_unknown_input_is_never_a_control(self):
        rows = [row(0, 'tool_result', execution_status='error', result_fingerprint='e' * 16),
                row(1, 'tool_result', execution_status='returned', state_revision='r1')]
        self.assertEqual(locate(['authorization_scope'], rows)['negative_controls'], [])

    def test_an_error_without_a_later_returned_peer_is_not_a_recovery(self):
        rows = self.rows()[:2] + [row(2, tool_name='Edit', call_id='k9')]
        self.assertEqual(locate(['authorization_scope'], rows)['negative_controls'], [])
        late = [dict(r, capture_order=r['capture_order'] + 10) if r['event_id'] in ('e0', 'e1') else r for r in self.rows()[:4]]
        # The error now follows the returned results, so nothing recovered it; the unchanged state is no polling either.
        self.assertEqual([c['event_id'] for c in locate(['authorization_scope'], late)['negative_controls']], [])

    def test_controls_are_bounded_and_the_overflow_is_counted(self):
        rows = []
        for i in range(constraints.MAX_REFS + 3):
            rows += [row(4 * i, tool_name='Read', call_id=f'a{i}', input_fingerprint='a' * 16),
                     row(4 * i + 1, 'tool_result', call_id=f'a{i}', execution_status='error'),
                     row(4 * i + 2, tool_name='Read', call_id=f'b{i}', input_fingerprint='a' * 16),
                     row(4 * i + 3, 'tool_result', call_id=f'b{i}', execution_status='returned')]
        body = locate(['authorization_scope'], rows)
        self.assertEqual((len(body['negative_controls']), body['negative_controls_omitted']),
                         (constraints.MAX_REFS, 3))


class DraftTests(unittest.TestCase):
    def broken_oracle(self):
        return locate(['outcome_integrity'], [row(0, tool_name='Edit'), row(1, 'grade', **dict(GRADED, grader_status='broken'))])

    def test_a_broken_grader_is_an_evaluator_hypothesis_and_never_an_instruction_edit(self):
        body = self.broken_oracle()
        entry = only(body)
        self.assertEqual((entry['status'], entry['kind']), ('violated', 'outcome_integrity'))
        self.assertEqual(entry['competing_causes'][0], 'evaluator_or_label_defect')
        (record,) = localization.mechanism_records(body, evidence_version='ev1', component_version='c1')
        self.assertIsInstance(record, MechanismRecord)
        self.assertEqual((record.component, record.disposition), ('insufficient_evidence', 'proposed'))
        self.assertIn('evaluator or label defect', record.competing_explanations)
        self.assertTrue(record.actionable)
        self.assertEqual(route(record), 'insufficient_evidence')
        self.assertEqual(route(record, reviewed_component='evaluator'), 'evaluator')

    def test_a_draft_cites_the_boundary_competing_causes_counterexamples_and_a_falsifying_replay(self):
        rows = NegativeControlTests.rows(self) + [row(6, tool_name='Deploy')]
        body = locate(['authorization_scope'], rows)
        (record,) = localization.mechanism_records(body, evidence_version='ev1', component_version='c1')
        self.assertEqual(record.boundary, 'authorization_scope constraint authorization_scope-1:v1')
        self.assertEqual(record.evidence, ['e6'])
        self.assertEqual(record.counterexamples, ['e1', 'e3', 'e5'])
        self.assertIn('upstream cause not captured', record.competing_explanations)
        self.assertIn('replay with the call denied', record.falsifier)
        self.assertIn('falsified', record.falsifier)

    def test_drafts_wait_for_review_in_the_queue_and_do_not_close_other_mechanisms(self):
        records = localization.mechanism_records(self.broken_oracle(), evidence_version='ev1', component_version='c1')
        other = MechanismRecord('other', 'a different symptom', ['x'], 'ev1', 'c1')
        waiting = dispositions(records + [other], Budget(10, 1))
        self.assertEqual({e['mechanism_id']: e['reason'] for e in waiting['entries']},
                         {records[0].mechanism_id: 'route_requires_review', 'other': 'missing_reviewed_boundary_evidence_or_falsifier'})
        reviewed = dispositions(records + [other], Budget(10, 1), reviewed={records[0].mechanism_id: 'evaluator'})
        self.assertEqual([e['reason'] for e in reviewed['entries'] if e['mechanism_id'] == records[0].mechanism_id],
                         ['eligible_for_development_evaluation'])
        self.assertIn('other', [e['mechanism_id'] for e in reviewed['entries']])

    def test_only_an_untampered_localization_report_yields_drafts(self):
        body = self.broken_oracle()
        for tamper in (lambda b: b.update(root_cause_established=True), lambda b: b['localizations'][0].update(kind='tool_contract'),
                       lambda b: b.pop('sha256'), lambda b: b.update(profile='other')):
            bad = copy.deepcopy(body)
            tamper(bad)
            with self.assertRaises(ValueError):
                localization.mechanism_records(bad, evidence_version='ev1', component_version='c1')

    def test_one_constraint_violated_in_two_cases_gets_two_drafts_with_distinct_identities(self):
        other = [dict(r, case_id='other') for r in (row(0, tool_name='Edit'), row(1, 'grade', **dict(GRADED, grader_status='broken')))]
        first = localization.mechanism_records(self.broken_oracle(), evidence_version='ev1', component_version='c1')
        second = localization.mechanism_records(locate(['outcome_integrity'], other), evidence_version='ev1', component_version='c1')
        self.assertNotEqual(first[0].mechanism_id, second[0].mechanism_id)


class ReportTests(unittest.TestCase):
    rows = [row(0, tool_name='Read'), row(1, tool_name='Deploy', call_id='k1'), row(2, 'tool_result', call_id='k1'),
            row(3, 'grade', **GRADED)]

    def test_the_report_is_an_observation_with_no_authority_cause_or_route(self):
        body = locate(['authorization_scope', 'tool_contract', 'outcome_integrity'], self.rows)
        for item in (body, *body['localizations']):
            self.assertFalse(item.get('root_cause_established', False))
        self.assertEqual((body['authority'], body['claim_level'], body['diagnosis_established'], body['causal_claim']),
                         ('none', 'observed_pattern', False, False))
        self.assertFalse(body['publication_allowed'])
        for entry in body['localizations']:
            self.assertFalse({'component', 'route', 'disposition', 'root_cause'} & set(entry))
        self.assertIn('earliest_visible_violation_is_not_the_root_cause', body['limitations'])
        self.assertIn('missing_upstream_evidence_stays_unresolved', body['limitations'])

    def test_every_constraint_result_is_listed_and_the_report_binds_the_constraint_report(self):
        kinds = ['authorization_scope', 'tool_contract', 'outcome_integrity']
        body = locate(kinds, self.rows)
        report = constraints.report([definition(k) for k in kinds], self.rows, scope='dev', capture='complete')
        self.assertEqual(body['constraint_report_sha256'], report['sha256'])
        self.assertEqual([(e['kind'], e['status']) for e in body['localizations']],
                         [(r['kind'], r['status']) for r in report['results']])
        self.assertEqual(sum(body['counts'].values()), len(kinds))

    def test_the_report_is_deterministic_and_independent_of_input_order(self):
        kinds = ['authorization_scope', 'tool_contract', 'outcome_integrity']
        first = locate(kinds, self.rows)
        second = locate(kinds[::-1], self.rows[::-1])
        self.assertEqual(first, second)
        self.assertEqual(len(first['sha256']), 64)
        self.assertNotEqual(first['sha256'], locate(kinds, self.rows, capture='partial')['sha256'])

    def test_candidates_are_bounded_and_the_overflow_is_counted(self):
        rows = [row(0, tool_name='Edit', call_id='k0')] + [row(i, 'tool_result', call_id='k0') for i in range(1, constraints.MAX_REFS + 6)]
        entry = only(locate(['tool_contract'], [dict(rows[0], contract_violation=True)] + rows[1:]))
        self.assertEqual((len(entry['candidates']), entry['candidates_omitted']), (constraints.MAX_REFS, 5))

    def test_malformed_input_is_refused_like_the_constraint_report(self):
        with self.assertRaises(ValueError):
            localization.localize([definition(), definition()], [], scope='dev')
        with self.assertRaises(ValueError):
            localization.localize([definition()], [row(0), row(0)], scope='dev')
        with self.assertRaises(ValueError):
            localization.localize([dict(definition(), origin='model')], [], scope='dev')


if __name__ == '__main__':
    unittest.main()
