"""Resuming a loop from its round ledger over the simulation test-bed: equivalence, abandoned rounds, refusals and the receipt (#815).

The ports, the clock and the trial counter are the bed's scripted ones; no model, sleep, network or real clock is involved.
An interrupted run and its resume use two Meters, so what the bed charged in total is the sum of both, and a run that was never
interrupted is the reference every resumed run must match.
"""
from dataclasses import replace
import json

import pytest

from agent_harness_lab.loop import controller, receipt
from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.ledger import LoopLedger
from tests.support import loop_sim
from tests.support.loop_sim import InterruptedLedger, Meter, ScriptedPorts

SCRIPT = loop_sim.interrupted_after_each_event()
NOMINAL = replace(SCRIPT, interrupt_after=None)
COMPARED = ('stop', 'outcome', 'best', 'mechanisms', 'mode', 'spec_sha256', 'frozen_inputs_sha256')


def ledger_for(base):
    (base/'project').mkdir(parents=True, exist_ok=True)
    return LoopLedger(base/'store', base/'project')


def offsets(events):
    """How many evaluations and proposals a ledger holds that completed: the next ones of a resumed run are the script's next rounds."""
    done = [payload for kind, payload in events if kind == 'candidate_evaluated' and payload.get('abandoned') is not True]
    return (sum(kind == 'baseline_evaluated' for kind, _ in events) + len(done)), len(done)


def interrupted(script, base, after):
    """Run the script and interrupt it as soon as its ledger holds `after` events, at the next port call or ledger write; nothing in flight is lost."""
    (base/'project').mkdir(parents=True, exist_ok=True)
    ledger, ports = InterruptedLedger(base/'store', base/'project', after), ScriptedPorts(script, Meter())

    def gate():
        if ledger.count == after and ledger.before is not None:
            ledger.before = None
            raise KeyboardInterrupt
    ports.on_call = gate
    return loop_sim.drive(script, base, ledger=ledger, ports=ports)


def resume(script, base, ledger_events, **options):
    """Resume with fresh ports that continue the script where the ledger left off; returns (result, ports)."""
    evaluated, proposed = offsets(ledger_events)
    ports = options.pop('ports', None) or ScriptedPorts(script, Meter(), evaluated=evaluated, proposed=proposed)
    # The synthetic proposer sends no external requests, so a retry cannot duplicate one.
    ports.proposal_retry_safe = lambda events: True
    result = controller.resume(script.spec, ports, ports, ledger_for(base), lambda: ports.meter.seconds, **options)
    return result, ports


def stored_events(base):
    ledger = ledger_for(base).reopen(NOMINAL.spec)
    events = ledger.events()
    ledger.unclaim()
    return events


@pytest.fixture(scope='module')
def nominal(tmp_path_factory):
    result, ledger, ports = loop_sim.drive(NOMINAL, tmp_path_factory.mktemp('nominal'))
    return dict(result=result, trials=ports.meter.trials, seconds=ports.meter.seconds, events=ledger.events())


@pytest.mark.parametrize('after', range(1, 11))
def test_resuming_after_every_ledger_event_reaches_the_same_stop_and_charges_the_same_totals(tmp_path, nominal, after):
    first, ledger, before = interrupted(SCRIPT, tmp_path, after)
    assert first['stop'] == 'interrupted'
    result, after_ports = resume(SCRIPT, tmp_path, ledger.events())
    reference = nominal['result']
    assert {key: result[key] for key in COMPARED} == {key: reference[key] for key in COMPARED}
    assert (result['usage']['trials'], result['usage']['seconds']) == (reference['usage']['trials'], reference['usage']['seconds'])
    assert (before.meter.trials + after_ports.meter.trials, before.meter.seconds + after_ports.meter.seconds) == (nominal['trials'], nominal['seconds'])


@pytest.mark.parametrize('after', range(1, 11))
def test_a_resumed_run_differs_from_the_uninterrupted_one_only_by_its_abandoned_round_and_the_resumption(tmp_path, nominal, after):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, after)
    result, _ = resume(SCRIPT, tmp_path, ledger.events())
    kinds = [kind for kind, _ in stored_events(tmp_path)]
    abandoned = result['resumed']['abandoned_rounds']
    assert result['resumed'] == dict(count=1, abandoned_rounds=abandoned) and result['rounds'] == nominal['result']['rounds'] + abandoned
    assert [kind for kind in kinds if kind not in ('stopped', 'resumed')][:after] == [kind for kind, _ in nominal['events']][:after] and kinds.count('resumed') == 1
    assert abandoned == (1 if after in (3, 4, 7, 8) else 0)  # an interruption inside a round, after it started and before its evaluation completed


def test_the_ledger_of_a_resumed_run_replays_and_names_one_final_stop(tmp_path):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 4)
    resume(SCRIPT, tmp_path, ledger.events())
    events = stored_events(tmp_path)
    reopened = ledger_for(tmp_path).reopen(SCRIPT.spec)
    assert [kind for kind, _ in events].count('resumed') == 1 and reopened.replay(SCRIPT.spec).stop == 'acceptance_score_reached'


def test_a_decision_already_recorded_is_finalized_without_asking_a_port_again(tmp_path):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 10)  # every round is decided; only the stop was not recorded
    result, ports = resume(SCRIPT, tmp_path, ledger.events())
    assert (result['stop'], ports.order, ports.settling) == ('acceptance_score_reached', [], [])


def test_legacy_ledger_before_baseline_requires_settlement(tmp_path):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 1)
    ports = ScriptedPorts(SCRIPT, Meter(), settled=None)
    result, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ports)
    assert result['stop_reason'] == 'unresolved_request' and ports.order == []
    assert len(ports.settling) == 1


def test_legacy_ledger_after_candidate_freeze_requires_settlement(tmp_path):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 4)
    ports = ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0, settled=None)
    result, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ports)
    assert result['stop_reason'] == 'unresolved_request' and ports.order == []
    assert len(ports.settling) == 1


def test_resume_preserves_area_regression_before_decision(tmp_path):
    script = loop_sim.build('area-regression-on-resume', [loop_sim.score(0.6),
        loop_sim.score(0.9, regressed_areas=('ux-ui',), cause='candidate')])
    reference, _, _ = loop_sim.drive(script, tmp_path/'reference')
    first, ledger, _ = interrupted(script, tmp_path/'interrupted', 5)
    assert first['stop'] == 'interrupted'
    result, ports = resume(script, tmp_path/'interrupted', ledger.events())
    assert (reference['stop'], reference['outcome']) == ('regression', 'regressed')
    assert (result['stop'], result['outcome'], result['best']) == (reference['stop'], reference['outcome'], reference['best'])
    assert ports.order == []


def test_a_baseline_that_was_never_recorded_is_evaluated_again_when_the_evaluator_confirms_nothing_is_in_flight(tmp_path, nominal):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 1)
    result, ports = resume(SCRIPT, tmp_path, ledger.events())
    assert (ports.settling, ports.order[0], result['usage']['trials']) == ([loop_sim.ScriptedPorts(SCRIPT, Meter()).baseline], 'evaluate', nominal['result']['usage']['trials'])


def evaluation_that_stops_midway(script, base, *, spent_trials=0, spent_seconds=0.0):
    """Run until the evaluator is interrupted partway through the first candidate's evaluation, after it consumed some trials and time."""
    (base/'project').mkdir(parents=True, exist_ok=True)
    ports = ScriptedPorts(script, Meter())
    real = ports.evaluate

    def evaluate(snapshot, frozen_inputs):
        if len(ports.evaluations.calls) == 1:  # the first candidate
            ports.meter.spend(spent_trials, spent_seconds)
            raise KeyboardInterrupt
        return real(snapshot, frozen_inputs)
    ports.evaluate = evaluate
    return loop_sim.drive(replace(script, interrupt_after=None), base, ports=ports)


def test_an_evaluation_interrupted_midway_is_recorded_abandoned_and_a_fresh_round_starts(tmp_path, nominal):
    first, ledger, _ = evaluation_that_stops_midway(SCRIPT, tmp_path, spent_trials=2, spent_seconds=4.0)
    assert first['stop'] == 'interrupted'
    result, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0, settled=2))
    events = stored_events(tmp_path)
    abandoned = [payload for kind, payload in events if kind == 'decided' and payload.get('abandoned')]
    assert [(item['disposition'], item['value'], item['mechanism']) for item in abandoned] == [('abandoned', 'inconclusive', 'm1')]
    assert (result['stop'], result['rounds'], result['mechanisms']) == ('acceptance_score_reached', 3, nominal['result']['mechanisms'])


def test_an_abandoned_round_keeps_the_trials_it_consumed_in_the_budget_but_never_counts_them_as_a_result(tmp_path, nominal):
    _, ledger, _ = evaluation_that_stops_midway(SCRIPT, tmp_path, spent_trials=2, spent_seconds=4.0)
    result, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0, settled=2))
    reference = nominal['result']['usage']
    assert result['usage']['trials'] == reference['trials'] + 2  # the consumed trials stay spent
    assert result['usage']['seconds'] == reference['seconds'] + 4.0  # the clock the interrupted run stopped at stays spent
    candidates = [payload for kind, payload in stored_events(tmp_path) if kind == 'candidate_evaluated']
    assert [item.get('abandoned') is True for item in candidates] == [True, False, False] and 'evaluation' not in candidates[0]


def test_an_abandoned_round_is_a_round_without_a_score_in_the_receipt(tmp_path):
    _, ledger, _ = evaluation_that_stops_midway(SCRIPT, tmp_path, spent_trials=2)
    result, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0, settled=2))
    block = receipt.build(SCRIPT.spec, result, stored_events(tmp_path), [])
    first = block['rounds'][0]
    assert receipt.validate_receipt(block) is block and block['resumed'] == dict(count=1, abandoned_rounds=1)
    assert (first['abandoned'], first['disposition'], first['decision'], first['score'], first['validity']) == (True, 'abandoned', 'inconclusive', None, 'incomplete')
    assert first['evaluation_id'] == 'evaluation-1'  # legacy ledgers keep their original receipt projection
    receipt.verify_projection(block, stored_events(tmp_path))


@pytest.mark.parametrize('answer', [pytest.param('missing', id='evaluator-has-no-settle'), pytest.param(None, id='settle-says-unknown')])
def test_an_evaluation_the_evaluator_cannot_settle_is_never_repeated_and_the_run_ends_interrupted(tmp_path, answer):
    _, ledger, _ = evaluation_that_stops_midway(SCRIPT, tmp_path, spent_trials=2)
    ports = ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0, settled=None)
    if answer == 'missing': ports = type('NoSettle', (), {name: getattr(ports, name) for name in ('evaluate', 'propose', 'frozen_inputs', 'baseline', 'mechanisms', 'order', 'meter')})()
    result, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ports)
    assert (result['stop'], result['stop_reason'], ports.order, ports.meter.trials) == ('interrupted', 'unresolved_request', [], 0)
    block = receipt.build(SCRIPT.spec, result, stored_events(tmp_path), [])
    assert block['remediation'] == receipt.RECONCILE and receipt.validate_receipt(block) is block


def test_a_run_ended_for_an_unresolved_request_resumes_once_the_evaluator_reports_it_settled(tmp_path, nominal):
    _, ledger, _ = evaluation_that_stops_midway(SCRIPT, tmp_path, spent_trials=2)
    stuck, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ScriptedPorts(SCRIPT, Meter(), evaluated=1, settled=None))
    result, _ = resume(SCRIPT, tmp_path, stored_events(tmp_path), ports=ScriptedPorts(SCRIPT, Meter(), evaluated=1, settled=2))
    assert (stuck['stop'], result['stop'], result['resumed']['count']) == ('interrupted', nominal['result']['stop'], 2)
    assert result['usage']['trials'] == nominal['result']['usage']['trials'] + 2


def test_a_settled_evaluation_is_charged_once_even_when_the_resume_is_itself_interrupted_and_resumed_again(tmp_path, nominal):
    _, ledger, _ = evaluation_that_stops_midway(SCRIPT, tmp_path, spent_trials=2)
    again = ScriptedPorts(SCRIPT, Meter(), evaluated=1, settled=2)
    again.on_call = lambda: (_ for _ in ()).throw(KeyboardInterrupt)
    middle, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=again)
    result, final = resume(SCRIPT, tmp_path, stored_events(tmp_path), ports=ScriptedPorts(SCRIPT, Meter(), evaluated=1, settled=2))
    assert middle['stop'] == 'interrupted' and final.settling == []  # the first resume had settled it; the second does not ask again
    assert (result['stop'], result['usage']['trials']) == (nominal['result']['stop'], nominal['result']['usage']['trials'] + 2)


def test_an_interrupt_while_settling_leaves_the_ledger_untouched_and_still_resumable(tmp_path, nominal):
    _, ledger, _ = evaluation_that_stops_midway(SCRIPT, tmp_path, spent_trials=2)
    before = stored_events(tmp_path)
    ports = ScriptedPorts(SCRIPT, Meter(), evaluated=1)
    ports.settle = lambda snapshot: (_ for _ in ()).throw(KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        resume(SCRIPT, tmp_path, ledger.events(), ports=ports)
    assert stored_events(tmp_path) == before
    result, _ = resume(SCRIPT, tmp_path, before, ports=ScriptedPorts(SCRIPT, Meter(), evaluated=1, settled=0))
    assert result['stop'] == nominal['result']['stop']


def test_a_resume_that_is_interrupted_again_can_be_resumed_again(tmp_path, nominal):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 4)
    second = ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0)
    second.on_call = lambda: (_ for _ in ()).throw(KeyboardInterrupt)
    middle, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=second)
    result, _ = resume(SCRIPT, tmp_path, stored_events(tmp_path))
    assert (middle['stop'], result['stop'], result['resumed']['count']) == ('interrupted', nominal['result']['stop'], 2)
    assert result['usage'] == nominal['result']['usage']


@pytest.mark.parametrize('at, lost', [pytest.param(6, 0, id='before-a-round-starts'), pytest.param(4, 4, id='after-an-evaluation-before-it-is-recorded')])
def test_a_run_that_crashed_with_no_stop_event_resumes_from_its_last_durable_event_and_charges_only_what_was_recorded(tmp_path, nominal, at, lost):
    ledger, ports = ledger_for(tmp_path), ScriptedPorts(NOMINAL, Meter())
    real = ledger.write

    def write(kind, payload, **options):
        if ledger.count == at: raise RuntimeError('power loss')
        return real(kind, payload, **options)
    ledger.write = write
    with pytest.raises(RuntimeError, match='power loss'):
        loop_sim.drive(NOMINAL, tmp_path, ledger=ledger, ports=ports)
    events = stored_events(tmp_path)
    assert len(events) == at and events[-1][0] != 'stopped'
    result, after = resume(NOMINAL, tmp_path, events)
    assert result['stop'] == nominal['result']['stop'] and result['usage'] == nominal['result']['usage']
    assert ports.meter.trials + after.meter.trials == nominal['trials'] + lost  # the evaluation that was never recorded was still run once


def test_resuming_a_stopped_run_returns_its_recorded_result_and_calls_no_port(tmp_path, nominal):
    first, ledger, _ = loop_sim.drive(NOMINAL, tmp_path)
    before = ledger.events()
    result, ports = resume(NOMINAL, tmp_path, before)
    assert result == first and (ports.order, ports.settling) == ([], []) and stored_events(tmp_path) == before


def test_resuming_a_stopped_run_with_a_changed_declaration_is_refused_before_anything_is_returned(tmp_path):
    _, ledger, _ = loop_sim.drive(NOMINAL, tmp_path)
    before = ledger.events()
    with pytest.raises(LoopError) as caught:
        resume(replace(NOMINAL, declaration={**NOMINAL.declaration, 'regression_margin': 0.2}), tmp_path, before)
    assert caught.value.reason == 'resume_spec_changed' and stored_events(tmp_path) == before


@pytest.mark.parametrize('change', ['declaration', 'noise_margin', 'limit'])
def test_a_changed_declaration_is_resume_spec_changed_and_runs_nothing(tmp_path, change):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 4)
    before = ledger.events()
    other = {'declaration': replace(SCRIPT, declaration={**SCRIPT.declaration, 'accept_score': 0.7}), 'noise_margin': replace(SCRIPT, declaration={**SCRIPT.declaration, 'noise_margin': 0.1}),
             'limit': replace(SCRIPT, trials=SCRIPT.trials + 1)}[change]
    ports = ScriptedPorts(other, Meter(), evaluated=1, proposed=0)
    with pytest.raises(LoopError) as caught:
        resume(other, tmp_path, before, ports=ports)
    assert (caught.value.reason, ports.order, ports.settling) == ('resume_spec_changed', [], []) and stored_events(tmp_path) == before


@pytest.mark.parametrize('change', ['frozen_inputs', 'baseline', 'mechanisms', 'dispositions'])
def test_other_inputs_than_the_run_was_frozen_with_are_resume_inputs_changed_and_run_nothing(tmp_path, change):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 4)
    before = ledger.events()
    ports = ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0)
    options = {}
    if change == 'frozen_inputs': ports.frozen_inputs = 'f'*64
    if change == 'baseline': ports.baseline = 'e'*64
    if change == 'mechanisms': ports.mechanisms = ('m2', 'm1', 'm3', 'm4', 'm5')
    if change == 'dispositions': options['dispositions'] = {'m5': 'rejected'}
    with pytest.raises(LoopError) as caught:
        resume(SCRIPT, tmp_path, before, ports=ports, **options)
    assert (caught.value.reason, ports.order, ports.settling) == ('resume_inputs_changed', [], []) and stored_events(tmp_path) == before


@pytest.mark.parametrize('damage', ['edit-middle', 'edit-newest', 'delete-event', 'edit-spec'])
def test_a_tampered_ledger_fails_verification_and_runs_nothing(tmp_path, damage):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 5)
    case = next((tmp_path/'store'/'cases').iterdir())/'events'
    newest = sorted(case.iterdir())[-1]
    if damage == 'edit-middle': (case/'0001.json').write_text((case/'0001.json').read_text().replace('"valid"', '"vacuous"'))
    if damage == 'edit-newest': newest.write_text(newest.read_text().replace('"interrupted"', '"budget_exhausted"'))
    if damage == 'delete-event': (case/'0003.json').unlink()
    if damage == 'edit-spec':
        path = case.parent/'spec.json'
        spec = json.loads(path.read_text())
        spec['accept_score'] = 0.99
        path.write_text(json.dumps(spec))
    ports = ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0)
    with pytest.raises(LoopError) as caught:
        controller.resume(SCRIPT.spec, ports, ports, ledger_for(tmp_path), lambda: ports.meter.seconds)
    assert (caught.value.reason, ports.order, ports.settling, ports.asked) == ('loop_ledger_invalid', [], [], [])


@pytest.mark.parametrize('caller', ['original', 'changed'])
def test_rehashed_stored_spec_cannot_override_the_frozen_event(tmp_path, caller):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 5)
    case = next((tmp_path/'store'/'cases').iterdir())
    changed = replace(SCRIPT.spec, accept_score=0.99)
    (case/'spec.json').write_text(json.dumps(changed.to_dict()))
    ports = ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0)
    with pytest.raises(LoopError) as caught:
        controller.resume(changed if caller == 'changed' else SCRIPT.spec, ports, ports, ledger_for(tmp_path), lambda: ports.meter.seconds)
    assert caught.value.reason == 'loop_ledger_invalid' and ports.order == ports.settling == ports.asked == [] and ledger.events()


def test_two_concurrent_resumes_give_one_busy_and_the_other_finishes_the_run(tmp_path, nominal):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 4)
    seen = []
    ports = ScriptedPorts(SCRIPT, Meter(), evaluated=1, proposed=0)
    real = ports.evaluate

    def evaluate(snapshot, frozen_inputs):
        if not seen:  # the first resume is mid-evaluation when the second one starts
            with pytest.raises(LoopError) as caught: resume(SCRIPT, tmp_path, ledger.events())
            seen.append(caught.value.reason)
        return real(snapshot, frozen_inputs)
    ports.evaluate = evaluate
    result, _ = resume(SCRIPT, tmp_path, ledger.events(), ports=ports)
    assert (seen, result['stop']) == (['resume_busy'], nominal['result']['stop'])


def test_a_resume_is_busy_while_the_original_run_is_still_alive(tmp_path):
    ports = ScriptedPorts(SCRIPT, Meter())
    real, seen = ports.evaluate, []

    def evaluate(snapshot, frozen_inputs):
        if not seen:
            with pytest.raises(LoopError) as caught: resume(SCRIPT, tmp_path, [])
            seen.append(caught.value.reason)
        return real(snapshot, frozen_inputs)
    ports.evaluate = evaluate
    result, _, _ = loop_sim.drive(NOMINAL, tmp_path, ports=ports)
    assert (seen, result['stop']) == (['resume_busy'], 'acceptance_score_reached')


@pytest.mark.parametrize('name', ['regresses_on_round_n', 'plateaus', 'budget_exhausted_before_reeval', 'no_mechanism_left'])
def test_resuming_after_every_event_of_other_scripts_reaches_their_stop(tmp_path, name):
    script = loop_sim.PRESETS[name]()
    reference, ledger, _ = loop_sim.drive(script, tmp_path/'nominal')
    for after in range(1, ledger.count):
        base = tmp_path/f'after-{after}'
        _, cut, _ = interrupted(script, base, after)
        result, _ = resume(script, base, cut.events())
        assert (after, result['stop'], result['outcome']) == (after, reference['stop'], reference['outcome'])


def test_a_stored_receipt_of_a_resumed_run_validates_and_names_the_ledger_head(tmp_path):
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 6)
    result, _ = resume(SCRIPT, tmp_path, ledger.events())
    block = receipt.build(SCRIPT.spec, result, stored_events(tmp_path), [])
    assert receipt.validate_receipt(json.loads(json.dumps(block))) and block['ledger_head_sha256'] == result['ledger']['head_sha256'] == ledger_for(tmp_path).verified()[1]
    assert block['events'] == result['ledger']['events']


def test_carried_dispositions_are_not_offered_again_and_a_resume_must_name_them_again(tmp_path):
    script = loop_sim.build('carried', [0.5, 0.6, 0.9], declaration=dict(max_rounds=3, plateau_rounds=3))
    (tmp_path/'project').mkdir()
    ports = ScriptedPorts(script, Meter())
    result = controller.run(script.spec, ports, ports, LoopLedger(tmp_path/'store', tmp_path/'project'), lambda: ports.meter.seconds, dispositions={'m1': 'rejected', 'm2': 'deferred'})
    assert [mechanism for _, mechanism in ports.asked] == ['m3', 'm4'] and result['mechanisms']['m1'] == 'rejected'
    ports.order.clear()
    with pytest.raises(LoopError) as caught:
        controller.resume(script.spec, ports, ports, LoopLedger(tmp_path/'store', tmp_path/'project'), lambda: 0.0)
    assert caught.value.reason == 'resume_inputs_changed' and ports.order == []
