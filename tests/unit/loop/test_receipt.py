"""The loop receipt: the validator that fails closed, the stop-criterion table, the exit mapping and the guidance text (#814).

The ten stop criteria are reached through the real controller over scripted ports (no process, no model, no clock); every bank,
snapshot and outcome is synthetic.
"""
import copy
import json

import pytest

from agent_harness_lab import exit_guidance, typed_exit
from agent_harness_lab.loop import machine, receipt
from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.monitoring.state import EXIT_CODES, exit_code
from tests.support import frozen_bank as fb
from tests.support.loop_claims import NEGATED, claims

BASE, CAND = fb.sha('base'), fb.sha('candidate')
PASS_ALL, FAIL_ALL = fb.outcome_table(range(8)), fb.outcome_table(())
LOW, TIE = fb.outcome_table({0, 1}), fb.outcome_table({0, 1})
UNKNOWN = {f't{number}': 'execution_error' for number in range(8)}
TABLE = {  # criterion -> (status, exit code): the mapping the issue and docs/loop-reference.md give
    'accepted_without_change': ('accepted', 0), 'acceptance_score_reached': ('accepted', 0),
    'budget_exhausted': ('requires_intervention', 1), 'interrupted': ('requires_intervention', 1), 'comparison_invalid': ('requires_intervention', 1),
    'max_rounds': ('not_accepted', 3), 'plateau': ('not_accepted', 3), 'regression': ('not_accepted', 3), 'no_actionable_mechanism': ('not_accepted', 3),
    'evidence_insufficient': ('not_accepted', 3),
}


def interrupt():
    raise KeyboardInterrupt


def scenario(base, name):
    """(evaluator, proposer, spec) of the scripted run that ends at the named criterion."""
    def go(outcomes, candidates, *, hook=None, **declaration):
        evaluator, runner = fb.scripted(base, outcomes, accept_score=declaration.pop('accept_score', 0.3), **declaration)
        proposer = fb.Proposer(BASE, candidates, evaluator, on_propose=hook(runner) if hook else None)
        return evaluator, proposer, evaluator.spec
    if name == 'accepted_without_change': return go({BASE: PASS_ALL}, [CAND])
    if name == 'acceptance_score_reached': return go({BASE: LOW, CAND: PASS_ALL}, [CAND])
    if name == 'max_rounds': return go({BASE: LOW, CAND: PASS_ALL}, [CAND], accept_score=0.99, max_rounds=1)
    if name == 'plateau': return go({BASE: TIE, CAND: TIE, fb.sha('c2'): TIE}, [CAND, fb.sha('c2')], accept_score=0.9, max_rounds=3, plateau_rounds=2)
    if name == 'regression': return go({BASE: fb.outcome_table(range(6)), CAND: LOW}, [CAND], accept_score=0.99, regression_margin=0.1)
    if name == 'no_actionable_mechanism': return go({BASE: LOW}, [], accept_score=0.99)
    if name == 'evidence_insufficient': return go({BASE: UNKNOWN}, [CAND])
    if name == 'budget_exhausted': return go({BASE: LOW, CAND: PASS_ALL}, [CAND], trials=20)
    if name == 'comparison_invalid': return go({BASE: LOW, CAND: PASS_ALL}, [CAND], hook=lambda runner: lambda: setattr(runner, 'runtime', 'b'*64))
    if name == 'interrupted': return go({BASE: LOW, CAND: PASS_ALL}, [CAND], hook=lambda runner: interrupt)
    raise LookupError(name)


def run(tmp_path, name):
    evaluator, proposer, spec = scenario(tmp_path, name)
    result, ledger = fb.drive(tmp_path, evaluator, proposer, spec)
    return spec, result, ledger, evaluator, proposer


def block(tmp_path, name):
    spec, result, ledger, evaluator, _ = run(tmp_path, name)
    return receipt.build(spec, result, ledger.events(), [item['guard'] for item in evaluator.runs])


@pytest.mark.parametrize('name', TABLE)
def test_each_stop_criterion_is_reachable_and_its_receipt_names_it_and_validates(tmp_path, name):
    found = block(tmp_path, name)
    assert receipt.validate_receipt(found) is found and found['stop_criterion'] == name


@pytest.mark.parametrize('name, expected', list(TABLE.items()), ids=list(TABLE))
def test_each_stop_criterion_maps_onto_its_coarse_status_and_exit_code(name, expected):
    status = receipt.status_of(name)
    assert (status, exit_code(status)) == expected and receipt.STATUS[name] == status and receipt.EXIT[status] == expected[1]


def test_the_table_covers_every_stop_criterion_of_the_state_machine_and_nothing_else():
    assert set(receipt.STATUS) == set(machine.STOPS) == set(TABLE) == set(receipt.STEPS)


def test_the_new_statuses_exit_through_the_existing_contract_and_a_status_outside_it_still_exits_1():
    assert (EXIT_CODES['accepted'], EXIT_CODES['not_accepted'], exit_code('requires_intervention'), exit_code('no_such_status')) == (0, 3, 1, 1)
    assert {exit_code(status) for status in ('no_change', 'applied', 'blocked', 'inconclusive', 'invalid_input', 'budget_exhausted')} == {0, 1, 2, 3}


def test_every_nonzero_loop_status_seals_with_a_registered_reason_and_a_next_step():
    for status in ('not_accepted', 'requires_intervention'):
        sealed = typed_exit.seal(dict(status=status))
        assert sealed['reason'] in typed_exit.REASONS and sealed['exit_receipt']['code'] == exit_code(status) and sealed['exit_receipt']['next_step']


def test_a_receipt_records_the_spec_the_frozen_inputs_and_the_authority_constants(tmp_path):
    spec, result, ledger, evaluator, _ = run(tmp_path, 'acceptance_score_reached')
    found = receipt.build(spec, result, ledger.events(), [item['guard'] for item in evaluator.runs])
    assert found['spec'] == spec.to_dict() and found['spec']['sources']['accept_score'] == 'project' and found['spec']['digest'] == spec.digest
    assert (found['frozen_inputs_sha256'], found['mode'], found['looks'], found['alpha_per_look']) == (result['frozen_inputs_sha256'], 'absolute', spec.max_rounds, spec.per_look_alpha)
    assert (found['recommendation_only'], found['automatic_promotion'], found['certification']) == (True, False, 'unverified')
    assert found['remediation'] == receipt.STEPS['acceptance_score_reached'] and found['events'] == ledger.count


def test_the_rounds_name_each_mechanism_snapshot_evaluation_score_decision_and_paired_delta(tmp_path):
    spec, result, ledger, evaluator, _ = run(tmp_path, 'acceptance_score_reached')
    found = receipt.build(spec, result, ledger.events(), [item['guard'] for item in evaluator.runs])
    (first,) = found['rounds']
    assert (found['before']['evaluation_id'], found['before']['snapshot'], found['before']['validity']) == ('evaluation-0', BASE, 'valid')
    assert (first['round'], first['mechanism'], first['base'], first['candidate'], first['evaluation_id'], first['validity']) == (1, 'm1', BASE, CAND, 'evaluation-1', 'valid')
    assert (first['decision'], first['disposition'], found['best_round'], found['after']['snapshot'], found['after']['evaluation_id']) == ('accepted', 'accepted', 1, CAND, 'evaluation-1')
    assert first['score']['lower'] <= first['score']['point'] <= first['score']['upper'] and first['paired']['delta'] == pytest.approx(0.75)
    assert first['paired']['lower'] <= first['paired']['delta'] <= first['paired']['upper']


def test_a_regression_is_kept_out_of_the_best_and_the_best_round_stays_the_baseline(tmp_path):
    found = block(tmp_path, 'regression')
    assert (found['best_round'], found['after']['snapshot'], found['rounds'][0]['decision'], found['rounds'][0]['disposition']) == (0, BASE, 'regressed', 'rejected')
    assert found['rounds'][0]['paired']['delta'] < 0


def test_a_comparison_that_ran_on_other_inputs_has_no_score_decision_or_paired_delta(tmp_path):
    found = block(tmp_path, 'comparison_invalid')
    assert [(item['validity'], item['score'], item['decision'], item['paired']) for item in found['rounds']] == [('comparison_invalid', None, None, None)]


def test_an_interrupted_run_names_the_criterion_and_keeps_what_finished(tmp_path):
    found = block(tmp_path, 'interrupted')
    assert (found['stop_criterion'], found['before']['validity'], [item['decision'] for item in found['rounds']]) == ('interrupted', 'valid', [None])


def test_a_budget_stop_records_why_and_calls_no_proposer(tmp_path):
    spec, result, ledger, evaluator, proposer = run(tmp_path, 'budget_exhausted')
    found = receipt.build(spec, result, ledger.events(), [item['guard'] for item in evaluator.runs])
    assert (found['stop_criterion'], found['stop_reason'], found['rounds'], proposer.calls) == ('budget_exhausted', 'reserve_for_evaluation_unavailable', [], [])


def test_a_deferral_is_listed_and_the_receipt_holds_no_free_text(tmp_path):
    spec, result, ledger, evaluator, _ = run(tmp_path, 'acceptance_score_reached')
    found = receipt.build(spec, result, ledger.events(), [], deferrals=[dict(mechanism='m2', reason='no_change')])
    assert found['deferrals'] == [dict(mechanism='m2', reason='no_change')] and found['rounds'][0]['paired'] is None
    receipt.validate_receipt(found)


@pytest.fixture
def valid(tmp_path):
    return block(tmp_path, 'acceptance_score_reached')


def without(value, *path):
    value = copy.deepcopy(value)
    target = value
    for key in path[:-1]: target = target[key]
    del target[path[-1]]
    return value


def setting(value, *path_and_value):
    *path, new = path_and_value
    value = copy.deepcopy(value)
    target = value
    for key in path[:-1]: target = target[key]
    target[path[-1]] = new
    return value


REJECTED = [
    pytest.param(lambda v: without(v, 'stop_criterion'), 'stop_criterion', id='stop-criterion-missing'),
    pytest.param(lambda v: setting(v, 'stop_criterion', None), 'stop_criterion', id='stop-criterion-null'),
    pytest.param(lambda v: setting(v, 'stop_criterion', 'finished'), 'stop_criterion', id='stop-criterion-unknown'),
    pytest.param(lambda v: setting(v, 'stop_criterion', 'ACCEPTANCE_SCORE_REACHED'), 'stop_criterion', id='stop-criterion-wrong-case'),
    pytest.param(lambda v: setting(v, 'stop_criterion', ['plateau']), 'stop_criterion', id='stop-criterion-not-a-name'),
    pytest.param(lambda v: setting(v, 'rounds', []), 'rounds', id='accepted-with-no-post-improvement-evaluation'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'decision', 'improved'), 'rounds', id='accepted-stop-but-the-last-round-only-improved'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'decision', None), 'rounds', id='accepted-stop-but-the-last-round-has-no-decision'),
    pytest.param(lambda v: without(v, 'rounds', 0, 'evaluation_id'), 'rounds.0.evaluation_id', id='round-score-with-no-evaluation-id'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'evaluation_id', ''), 'rounds.0.evaluation_id', id='round-score-with-an-empty-evaluation-id'),
    pytest.param(lambda v: without(v, 'before', 'evaluation_id'), 'before.evaluation_id', id='baseline-score-with-no-evaluation-id'),
    pytest.param(lambda v: without(v, 'after', 'evaluation_id'), 'after.evaluation_id', id='after-score-with-no-evaluation-id'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'validity', 'vacuous'), 'rounds', id='vacuous-evaluation-behind-an-accepted-stop'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'vacuity', 'zero_trials'), 'rounds', id='vacuity-label-behind-an-accepted-stop'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'evidence', 'provisional'), 'rounds', id='unscored-evidence-behind-an-accepted-stop'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'score', None), 'rounds', id='accepted-stop-with-no-score'),
    pytest.param(lambda v: setting(v, 'before', 'validity', 'incomplete'), 'rounds', id='accepted-stop-over-an-invalid-baseline'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'score', 'lower', 0.9), 'rounds.0.score', id='interval-lower-above-the-point'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'score', 'upper', 1.5), 'rounds.0.score', id='interval-above-one'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'score', 'point', float('nan')), 'rounds.0.score', id='score-not-finite'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'paired', 'delta', 'big'), 'rounds.0.paired', id='paired-delta-not-a-number'),
    pytest.param(lambda v: setting(v, 'rounds', 0, 'decision', 'won'), 'rounds.0.decision', id='decision-outside-the-closed-set'),
    pytest.param(lambda v: setting(v, 'automatic_promotion', True), 'authority', id='promotion-enabled'),
    pytest.param(lambda v: setting(v, 'certification', 'verified'), 'authority', id='certification-claimed'),
    pytest.param(lambda v: without(v, 'certification'), 'authority', id='certification-missing'),
    pytest.param(lambda v: setting(v, 'recommendation_only', False), 'authority', id='recommendation-only-lifted'),
    pytest.param(lambda v: setting(v, 'remediation', ''), 'remediation', id='no-next-step'),
    pytest.param(lambda v: without(v, 'remediation'), 'remediation', id='next-step-missing'),
    pytest.param(lambda v: setting(v, 'profile', 'ahl-loop-receipt-v0'), 'profile', id='other-profile'),
    pytest.param(lambda v: setting(v, 'rounds', [copy.deepcopy(v['rounds'][0])]*11), 'rounds', id='more-rounds-than-the-hard-cap'),
    pytest.param(lambda v: setting(v, 'mechanisms', {'m%d' % number: 'rejected' for number in range(6000)}), 'size', id='oversized'),
    pytest.param(lambda v: setting(v, 'spec', 'inline'), 'spec', id='spec-not-recorded'),
    pytest.param(lambda v: setting(v, 'frozen_inputs_sha256', 'abc'), 'frozen_inputs_sha256', id='frozen-inputs-not-a-digest'),
    pytest.param(lambda v: 'not a receipt', 'profile', id='not-a-mapping'),
    pytest.param(lambda v: None, 'profile', id='null'),
]


@pytest.mark.parametrize('mutate, field', REJECTED)
def test_the_validator_fails_closed_naming_the_first_field_that_is_not_honest(valid, mutate, field):
    with pytest.raises(LoopError) as caught:
        receipt.validate_receipt(mutate(valid))
    assert (caught.value.reason, caught.value.field) == ('loop_receipt_invalid', field) and caught.value.stage == 'run'


@pytest.mark.parametrize('mutate, field', [
    pytest.param(lambda v: setting(v, 'rounds', [copy.deepcopy(v['rounds'][0]) if v['rounds'] else {}]), 'before', id='accepted-without-change-after-a-round'),
    pytest.param(lambda v: setting(v, 'before', 'validity', 'vacuous'), 'before', id='accepted-without-change-over-a-vacuous-baseline'),
    pytest.param(lambda v: setting(v, 'before', 'vacuity', 'grader_not_executed'), 'before', id='accepted-without-change-over-a-vacuity-label'),
    pytest.param(lambda v: setting(v, 'before', 'evidence', 'provisional'), 'before', id='accepted-without-change-over-provisional-evidence'),
    pytest.param(lambda v: setting(v, 'before', 'score', None), 'before', id='accepted-without-change-with-no-score'),
    pytest.param(lambda v: without(v, 'stop_criterion'), 'stop_criterion', id='stop-criterion-missing'),
])
def test_accepted_without_change_needs_a_sound_baseline_and_no_improvement_round(tmp_path, mutate, field):
    found = block(tmp_path, 'accepted_without_change')
    receipt.validate_receipt(found)
    with pytest.raises(LoopError) as caught:
        receipt.validate_receipt(mutate(found))
    assert (caught.value.reason, caught.value.field) == ('loop_receipt_invalid', field)


@pytest.mark.parametrize('name', [name for name in TABLE if name not in receipt.ACCEPTING])
def test_a_stop_that_accepts_nothing_validates_without_a_post_improvement_evaluation(tmp_path, name):
    found = block(tmp_path, name)
    receipt.validate_receipt(found)
    receipt.validate_receipt(setting(found, 'rounds', []))


def test_the_unchanged_receipt_is_valid_and_json_clean(tmp_path):
    found = block(tmp_path, 'accepted_without_change')
    assert receipt.validate_receipt(found)['rounds'] == [] and json.loads(json.dumps(found, allow_nan=False)) == found


def test_a_stop_criterion_that_is_never_written_is_caught_by_the_validator(tmp_path):
    """The deliberate mutation: the same build with the stop_criterion write deleted never reaches a result."""
    spec, result, ledger, evaluator, _ = run(tmp_path, 'plateau')
    built = receipt.build(spec, result, ledger.events(), [item['guard'] for item in evaluator.runs])
    mutated = {key: value for key, value in built.items() if key != 'stop_criterion'}
    with pytest.raises(LoopError) as caught:
        receipt.validate_receipt(mutated)
    assert (caught.value.reason, caught.value.field) == ('loop_receipt_invalid', 'stop_criterion')


@pytest.mark.parametrize('name', TABLE)
def test_no_step_claims_the_harness_is_fine_or_improved_and_each_is_one_sentence(name):
    step = receipt.STEPS[name]
    assert not claims(step) and step.endswith('.') and '. ' not in step
    if receipt.STATUS[name] == 'accepted': assert 'not certification' in step and 'nothing' in step.lower()
    elif 'fine' in step: assert NEGATED in step


@pytest.mark.parametrize('name, expected', list(TABLE.items()), ids=list(TABLE))
def test_guidance_for_a_loop_result_reads_the_stop_step_and_never_claims_a_fine_or_improved_harness(name, expected):
    status = expected[0]
    result = dict(status=status, reason=f'loop_{name}')
    category, _, action = exit_guidance.guidance(result)
    assert action == receipt.STEPS[name] == exit_guidance.REMEDIATION[f'loop_{name}'] and category != 'Inconclusive'
    assert not claims(category) and not claims(action)
    if status == 'accepted': assert 'unverified' in category.lower() and 'not certification' in action
    assert '\n' not in exit_guidance.summary(result)


@pytest.mark.parametrize('status', ['accepted', 'not_accepted'])
def test_each_new_status_has_a_status_action_and_a_reasonless_result_still_gets_one(status):
    assert exit_guidance.STATUS_ACTION[status] and exit_guidance.guidance(dict(status=status))[2] == exit_guidance.STATUS_ACTION[status]
    assert not claims(exit_guidance.STATUS_ACTION[status])


def test_every_reason_the_receipt_can_carry_has_a_remediation_entry():
    assert {f'loop_{name}' for name in TABLE} <= set(exit_guidance.REMEDIATION) and {'loop_requires_frozen_bank', 'loop_receipt_invalid'} <= set(exit_guidance.REMEDIATION)
