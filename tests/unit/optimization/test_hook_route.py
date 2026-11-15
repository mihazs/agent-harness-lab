"""The hook route is capability-gated and needs a successful control; it never rides another route (#10)."""
from agent_harness_lab.diagnostics.coverage import render_markdown, report
from agent_harness_lab.diagnostics.mechanisms import COMPONENTS, MechanismRecord, from_proposal
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.router import dispositions, route, run_queue


def record(name, component='hook', severity='normal', **changes):
    value = MechanismRecord(name, 'synthetic symptom', ['e1'], 'evidence-1', 'component-1', component=component,
                            severity=severity, controls=['c1'], competing_explanations=['instruction gap'],
                            boundary='forbidden call is dispatched', falsifier='unrepaired run still dispatches')
    for key, item in changes.items():
        setattr(value, key, item)
    return value


def evaluated(records, budget, **options):
    seen = []
    result = run_queue(records, lambda m: (seen.append(m.mechanism_id) or 'validated', 'development benefit'),
                       budget, **options)
    return result, seen, budget.events


def test_hook_is_a_named_component_distinct_from_the_tool_adapter():
    assert {'hook', 'tool_adapter'} <= COMPONENTS
    assert route(record('h'), reviewed_component='hook') == 'insufficient_evidence'
    assert route(record('h'), reviewed_component='hook', authorized_hooks=True) == 'hook'
    # Each capability is declared separately: neither authorizes the other.
    assert route(record('h'), reviewed_component='hook', authorized_adapters=True) == 'insufficient_evidence'
    assert route(record('a', 'tool_adapter'), reviewed_component='tool_adapter', authorized_hooks=True) == 'insufficient_evidence'
    # Hook authorization never upgrades another route or an unreviewed, expected-negative or unsupported record.
    assert route(record('s', 'skill'), reviewed_component='skill', authorized_hooks=True) == 'skill'
    assert route(record('h'), authorized_hooks=True) == 'insufficient_evidence'
    assert route(record('h'), reviewed_component='hook', expected_negative=True, authorized_hooks=True) == 'no_change'
    assert route(record('h', falsifier=None), reviewed_component='hook', authorized_hooks=True) == 'insufficient_evidence'


def test_unauthorized_hook_is_held_without_callback_or_charge():
    result, seen, events = evaluated([record('hook')], Budget(2, 0))
    assert (seen, events) == ([], [])
    assert (result[0]['disposition'], result[0]['reason']) == ('deferred', 'hook_route_not_authorized')
    planned = dispositions([record('hook')], Budget(2, 0), reviewed=dict(hook='hook'))
    assert [(e['route'], e['disposition'], e['reason']) for e in planned['entries']] == [
        ('insufficient_evidence', 'deferred', 'hook_route_not_authorized')]
    # An adapter authorization is not a hook authorization.
    result, seen, _ = evaluated([record('hook')], Budget(2, 0), authorized_adapters=True)
    assert seen == [] and result[0]['reason'] == 'hook_route_not_authorized'


def test_authorized_hook_without_a_successful_control_is_deferred_not_evaluated():
    result, seen, events = evaluated([record('hook', controls=[])], Budget(2, 0), authorized_hooks=True)
    assert (seen, events) == ([], [])
    assert (result[0]['disposition'], result[0]['reason']) == ('deferred', 'hook_route_requires_successful_control')
    planned = dispositions([record('hook', controls=[])], Budget(2, 0), reviewed=dict(hook='hook'), authorized_hooks=True)
    assert [(e['route'], e['disposition'], e['reason']) for e in planned['entries']] == [
        ('hook', 'deferred', 'hook_route_requires_successful_control')]


def test_authorized_hook_with_a_control_is_evaluated_but_never_validated_in_development():
    result, seen, events = evaluated([record('hook')], Budget(2, 0), authorized_hooks=True)
    assert seen == ['hook'] and len(events) == 1
    assert (result[0]['disposition'], result[0]['reason']) == ('recommended', 'development_only_requires_final_certification')
    planned = dispositions([record('hook')], Budget(2, 0), reviewed=dict(hook='hook'), authorized_hooks=True)
    assert [(e['route'], e['disposition'], e['reason']) for e in planned['entries']] == [
        ('hook', 'proposed', 'eligible_for_development_evaluation')]
    assert planned['budget']['planned_spent'] == 1


def test_held_hook_leaves_budget_for_later_mechanisms_and_unrelated_routes_are_unchanged():
    records = [record('a-hook', severity='critical'), record('b-skill', 'skill')]
    result, seen, _ = evaluated(records, Budget(1, 0))
    assert seen == ['b-skill']
    assert [(r['mechanism_id'], r['disposition']) for r in result] == [('a-hook', 'deferred'), ('b-skill', 'recommended')]
    def plain():
        return [record('x', 'instruction'), record('y', 'evaluator', 'high')]

    assert evaluated(plain(), Budget(3, 0))[0] == evaluated(plain(), Budget(3, 0), authorized_hooks=True)[0]


def test_proposer_hook_claim_is_admitted_as_unreviewed_hypothesis():
    item = dict(mechanism_id='m1', symptom='forbidden call dispatched', evidence_refs=['e1'], component='hook',
                boundary='dispatch boundary', falsifier='still dispatched', competing_explanations=['instruction gap'],
                controls=['e1'])
    admission = from_proposal(dict(status='candidate', mechanisms=[item], falsifiers=[]), evidence_refs={'e1'},
                              evidence_version='ev', component_version='cv')
    assert admission.status == 'admitted'
    assert admission.receipt['entries'][0]['proposed_component'] == 'hook'
    (admitted,) = admission.records
    assert (admitted.component, admitted.disposition) == ('insufficient_evidence', 'proposed')
    planned = dispositions(admission.records, Budget(3, 0), authorized_hooks=True)
    assert [(e['route'], e['reason']) for e in planned['entries']] == [('insufficient_evidence', 'route_requires_review')]


def test_coverage_names_hook_as_a_component_and_keeps_it_open_until_certified():
    result, _, _ = evaluated([record('hook'), record('bare', evidence=[])], Budget(3, 0), authorized_hooks=True)
    body = report(result, capture='complete')
    rows = {e['mechanism_id']: e for e in body['entries']}
    assert (rows['hook']['responsible_component'], rows['hook']['claim_level'], rows['hook']['coverage']) == (
        'hook', 'development_recommendation', 'awaiting_certification')
    assert rows['bare']['responsible_component'] == 'insufficient_evidence'
    assert body['totals']['responsible_component']['hook'] == 1
    assert not body['coverage_complete']
    assert '| ` hook ` | ` normal ` | ` hook ` |' in render_markdown(body)
