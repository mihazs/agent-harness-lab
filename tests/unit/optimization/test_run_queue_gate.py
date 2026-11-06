"""run_queue evaluates only what route() allows; unauthorized adapters never reach the callback (#233)."""
import unittest
from agent_harness_lab.diagnostics.mechanisms import MechanismRecord
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.router import dispositions, run_queue


def record(name, component='instruction', severity='normal', **changes):
    value = MechanismRecord(name, 'synthetic symptom', ['e1'], 'evidence-1', 'component-1', component=component,
                            severity=severity, competing_explanations=['grader defect'],
                            boundary='acceptance artifact', falsifier='unrepaired artifact fails')
    for key, item in changes.items():
        setattr(value, key, item)
    return value


def evaluated(records, budget, **options):
    seen = []
    result = run_queue(records, lambda m: (seen.append(m.mechanism_id) or 'validated', 'development benefit'),
                       budget, **options)
    return result, seen, budget.events


class RunQueueGateTests(unittest.TestCase):
    def test_unauthorized_adapter_is_held_without_callback_or_charge(self):
        result, seen, events = evaluated([record('adapter', 'tool_adapter')], Budget(2, 0))
        self.assertEqual(seen, [])
        self.assertEqual(events, [])
        self.assertEqual((result[0]['disposition'], result[0]['reason']), ('deferred', 'adapter_route_not_authorized'))
        planned = dispositions([record('adapter', 'tool_adapter')], Budget(2, 0), reviewed=dict(adapter='tool_adapter'))
        self.assertEqual((planned['entries'][0]['disposition'], planned['entries'][0]['reason']),
                         (result[0]['disposition'], result[0]['reason']))

    def test_explicitly_authorized_adapter_is_still_evaluated(self):
        result, seen, events = evaluated([record('adapter', 'tool_adapter')], Budget(2, 0), authorized_adapters=True)
        self.assertEqual(seen, ['adapter'])
        self.assertEqual(len(events), 1)
        self.assertEqual(result[0]['disposition'], 'recommended')

    def test_unauthorized_adapter_does_not_consume_budget_of_later_mechanisms(self):
        records = [record('a-adapter', 'tool_adapter', 'critical'), record('b-instruction')]
        result, seen, _ = evaluated(records, Budget(1, 0))
        self.assertEqual(seen, ['b-instruction'])
        self.assertEqual([r['disposition'] for r in result], ['deferred', 'recommended'])

    def test_non_adapter_queue_outcomes_are_unchanged(self):
        # Every non-adapter path: duplicates, order, actionable gaps, no-change, review, budget, failure, prior state.
        def queue():
            failing = record('fails', 'skill')
            return [record('ordinary'), record('ordinary'), record('severe', 'evaluator', 'critical'),
                    record('controlled', 'state_recovery', 'high', controls=['c1']), record('covered', 'no_change'),
                    record('review', 'insufficient_evidence'), record('gap', falsifier=None), failing,
                    record('settled', disposition='rejected'), record('late', 'runtime_or_tool_defect')]

        def evaluate(m):
            if m.mechanism_id == 'fails':
                raise RuntimeError('synthetic infrastructure failure')
            return ('validated', 'benefit') if m.severity == 'critical' else ('rejected', 'tie')

        budget = Budget(5, 1, [dict(kind='other', amount=1, event_id='prior', final=False)])
        result = run_queue(queue(), evaluate, budget)
        self.assertEqual([(r['mechanism_id'], r['disposition'], r['reason'], r['history']) for r in result], [
            ('severe', 'recommended', 'development_only_requires_final_certification',
             [dict(previous='proposed', disposition='evaluating', reason='development_evaluation_started'),
              dict(previous='evaluating', disposition='recommended', reason='development_only_requires_final_certification')]),
            ('controlled', 'rejected', 'tie',
             [dict(previous='proposed', disposition='evaluating', reason='development_evaluation_started'),
              dict(previous='evaluating', disposition='rejected', reason='tie')]),
            ('covered', 'already_covered', 'expected_or_required_behavior',
             [dict(previous='proposed', disposition='already_covered', reason='expected_or_required_behavior')]),
            ('fails', 'deferred', 'evaluation_infrastructure_failed',
             [dict(previous='proposed', disposition='evaluating', reason='development_evaluation_started'),
              dict(previous='evaluating', disposition='deferred', reason='evaluation_infrastructure_failed')]),
            ('gap', 'deferred', 'missing_reviewed_boundary_evidence_or_falsifier',
             [dict(previous='proposed', disposition='deferred', reason='missing_reviewed_boundary_evidence_or_falsifier')]),
            ('late', 'deferred', 'parent_budget_or_final_reserve_exhausted',
             [dict(previous='proposed', disposition='deferred', reason='parent_budget_or_final_reserve_exhausted')]),
            ('ordinary', 'deferred', 'parent_budget_or_final_reserve_exhausted',
             [dict(previous='proposed', disposition='deferred', reason='parent_budget_or_final_reserve_exhausted')]),
            ('review', 'deferred', 'route_requires_review',
             [dict(previous='proposed', disposition='deferred', reason='route_requires_review')]),
            ('settled', 'rejected', None, [])])
        self.assertEqual([e['event_id'] for e in budget.events][0], 'prior')
        self.assertEqual(len(budget.events), 4)
        # The authorization flag has no effect on a queue without adapter routes.
        again = Budget(5, 1, [dict(kind='other', amount=1, event_id='prior', final=False)])
        self.assertEqual(run_queue(queue(), evaluate, again, authorized_adapters=True), result)
        self.assertEqual(again.events, budget.events)


if __name__ == '__main__':
    unittest.main()
