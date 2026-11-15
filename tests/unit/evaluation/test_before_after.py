"""The before/after guard decides from synthetic paired outcomes only (#804); no model, project or recorded history is read."""
import argparse
import json
import math

import pytest

from agent_harness_lab.evaluation import before_after as guard
from agent_harness_lab.evaluation.benchmark import MATCH_FIELDS, public_export_allowed
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord

SHA = {name: format(index, 'x') * 64 for index, name in enumerate(guard.IDENTITY_FIELDS, 1) if name.endswith('_sha256')}


def arm(**changes):
    return guard.frozen_inputs(**{**SHA, 'budget_share': dict(trials=12, timeout_seconds=60.0), **changes})


def bank(baseline, candidate, *, areas=None):
    """One pair per root: the first outcomes are baseline's, the second candidate's; a string spells passes (P), fails (F), unknown (?)."""
    outcome = {'P': True, 'F': False, '?': None}
    return [dict(task=f'task-{index}', baseline=outcome[b], candidate=outcome[c], **(dict(areas=areas[index]) if areas else {}))
            for index, (b, c) in enumerate(zip(baseline, candidate))]


def decide(pairs, *, baseline=None, candidate=None, **options):
    return guard.decide(pairs, baseline=baseline or arm(), candidate=candidate or arm(), **options)


def test_a_deliberately_harmful_candidate_is_regressed_and_blocks_adoption():
    result = decide(bank('P' * 12, 'F' * 12), margin=0.05)
    assert (result['verdict'], result['adoption_blocked'], result['reasons']) == ('candidate_regressed', True, ['delta_below_negative_margin'])
    assert result['delta']['mean'] == -1 and result['delta']['high'] < -0.05 and result['delta']['interval_excludes_margin'] is True
    assert (result['scores']['baseline']['mean'], result['scores']['candidate']['mean']) == (1, 0)
    assert result['pairs']['flipped_to_fail'] == 12


def test_a_helpful_candidate_whose_interval_clears_the_margin_is_improved_and_not_blocked():
    result = decide(bank('F' * 12, 'P' * 12), margin=0.05)
    assert (result['verdict'], result['adoption_blocked'], result['reasons']) == ('improved', False, ['interval_above_margin'])
    assert result['delta']['low'] > 0.05 and result['delta']['interval_excludes_margin'] is False


@pytest.mark.parametrize('baseline, candidate', [
    pytest.param('PPPPPPFFFFFF', 'FFFFFFPPPPPP', id='equal_flips_in_both_directions'),
    pytest.param('FFFFFFFFFFFF', 'PFFFFFFFFFFF', id='one_gain_in_twelve_inside_noise'),
    pytest.param('PPPPPPPPPPPP', 'PPPPPPPPPPPP', id='identical_outcomes'),
])
def test_a_noisy_tie_is_inconclusive_and_never_improved(baseline, candidate):
    result = decide(bank(baseline, candidate), margin=0.0)
    assert result['verdict'] == 'inconclusive' and result['adoption_blocked'] is False
    assert 'interval_not_above_margin' in result['reasons']


@pytest.mark.parametrize('margin, verdict', [
    pytest.param(0.1, 'inconclusive', id='loss_equal_to_the_margin_is_tolerated'),
    pytest.param(0.11, 'inconclusive', id='loss_inside_the_margin'),
    pytest.param(0.09, 'candidate_regressed', id='loss_beyond_the_margin'),
    pytest.param(0.0, 'candidate_regressed', id='any_loss_with_no_margin'),
])
def test_a_loss_blocks_adoption_only_when_it_is_beyond_the_declared_margin(margin, verdict):
    result = decide(bank('P' * 10, 'F' + 'P' * 9), margin=margin)
    assert (result['delta']['mean'], result['verdict']) == (-0.1, verdict)


@pytest.mark.parametrize('baseline, candidate, eligible, unknown', [
    pytest.param('', '', 0, 0, id='no_pairs'),
    pytest.param('???', '???', 0, 3, id='every_outcome_unknown'),
    pytest.param('PPP', '???', 0, 3, id='candidate_never_graded'),
    pytest.param('???', 'PPP', 0, 3, id='baseline_never_graded'),
])
def test_without_a_paired_outcome_nothing_is_claimed(baseline, candidate, eligible, unknown):
    result = decide(bank(baseline, candidate))
    assert (result['verdict'], result['reasons'], result['adoption_blocked']) == ('inconclusive', ['no_paired_outcomes'], False)
    assert (result['pairs']['eligible'], result['pairs']['unknown'], result['delta'], result['scores']) == (eligible, unknown, None, None)


def test_an_unknown_outcome_is_not_counted_as_a_failure_and_withholds_improved():
    result = decide(bank('F' * 12 + 'F', 'P' * 12 + '?'), margin=0.05)
    assert result['pairs'] == dict(eligible=12, unknown=1, flipped_to_fail=0, flipped_to_pass=12, unchanged=0, repeats_per_task_arm=1)
    assert (result['verdict'], result['delta']['mean']) == ('inconclusive', 1) and 'unknown_outcomes' in result['reasons']


def test_a_regression_among_known_pairs_still_blocks_when_other_pairs_are_unknown():
    result = decide(bank('P' * 6 + '?' * 3, 'F' * 6 + 'P' * 3))
    assert (result['verdict'], result['pairs']['unknown']) == ('candidate_regressed', 3)


@pytest.mark.parametrize('field', guard.IDENTITY_FIELDS)
def test_a_known_difference_in_any_frozen_input_withholds_improved_and_is_named(field):
    other = dict(SHA, budget_share=dict(trials=12, timeout_seconds=60.0))
    other[field] = dict(trials=6, timeout_seconds=30.0) if field == 'budget_share' else 'f' * 64
    result = decide(bank('F' * 12, 'P' * 12), candidate=guard.frozen_inputs(**other))
    assert result['verdict'] == 'inconclusive' and 'frozen_inputs_mismatch' in result['reasons']
    assert result['frozen_inputs']['comparison'] == dict(status='mismatch', mismatched=[field], unknown=[])
    assert result['frozen_inputs']['baseline']['identity_sha256'] != result['frozen_inputs']['candidate']['identity_sha256']


@pytest.mark.parametrize('field', guard.IDENTITY_FIELDS)
def test_an_unobserved_frozen_input_is_unverified_and_never_a_match(field):
    result = decide(bank('F' * 12, 'P' * 12), candidate=arm(**{field: None}))
    assert (result['verdict'], result['frozen_inputs']['comparison']) == ('inconclusive', dict(status='unverified', mismatched=[], unknown=[field]))
    assert 'frozen_inputs_unverified' in result['reasons']


def test_identical_inputs_hash_to_one_identity_whatever_the_key_order():
    one = guard.frozen_inputs(**SHA, budget_share=dict(trials=3, timeout_seconds=9.0))
    two = guard.frozen_inputs(**SHA, budget_share=dict(timeout_seconds=9.0, trials=3))
    assert one['identity_sha256'] == two['identity_sha256'] and guard.compare_identities(one, two)['status'] == 'identical'


@pytest.mark.parametrize('fields', [
    pytest.param(dict(bank_sha256='not-a-digest'), id='malformed_digest'),
    pytest.param(dict(grader_sha256='A' * 64), id='uppercase_digest'),
    pytest.param(dict(budget_share=[1]), id='budget_not_an_object'),
    pytest.param(dict(seed=1), id='unknown_field'),
])
def test_a_malformed_frozen_input_is_refused(fields):
    with pytest.raises(ValueError):
        guard.frozen_inputs(**fields)


@pytest.mark.parametrize('options', [
    pytest.param(dict(margin=-0.01), id='negative_margin'),
    pytest.param(dict(margin=1), id='margin_of_one'),
    pytest.param(dict(margin=math.nan), id='nan_margin'),
    pytest.param(dict(margin=True), id='boolean_margin'),
    pytest.param(dict(alpha=0), id='zero_alpha'),
    pytest.param(dict(alpha=1), id='alpha_of_one'),
])
def test_an_unsupported_margin_or_alpha_is_refused(options):
    with pytest.raises(ValueError):
        decide(bank('PP', 'FF'), **options)


@pytest.mark.parametrize('pairs', [
    pytest.param([dict(task='a', baseline=True, candidate=False), dict(task='a', baseline=True, candidate=True)], id='duplicate_task_and_repeat'),
    pytest.param([dict(task='', baseline=True, candidate=False)], id='empty_task'),
    pytest.param([dict(task='a', baseline='passed', candidate=False)], id='string_outcome_is_not_coerced'),
    pytest.param([dict(task='a', baseline=1, candidate=0)], id='integer_outcome_is_not_coerced'),
    pytest.param([dict(task='a', baseline=True, candidate=False, areas='coding')], id='areas_not_a_list'),
])
def test_malformed_pairs_are_refused_rather_than_guessed(pairs):
    with pytest.raises(ValueError):
        decide(pairs)


def test_repeats_inside_a_root_weigh_the_root_once():
    pairs = ([dict(task=f'a-{n}', root='a', repeat=n, baseline=True, candidate=False) for n in range(3)]
             + [dict(task='b', root='b', baseline=True, candidate=True)])
    result = decide(pairs)
    assert result['delta']['mean'] == -0.5 and result['pairs']['eligible'] == 4 and result['unit'] == 'root'
    assert result['independence'] == 'unverified'


def test_the_receipt_carries_no_authority_and_serializes_without_nan():
    result = decide(bank('P' * 12, 'F' * 12))
    assert (result['claim'], result['adoption_authority'], result['automatic_promotion'], result['publication_allowed']) == ('observed_pattern', False, False, False)
    json.dumps(result, allow_nan=False)


def investigated(baseline, candidate, *, areas=None, candidate_arm=None, **flags):
    pairs = bank(baseline, candidate, areas=areas)
    block = decide(pairs, candidate=candidate_arm or arm())
    return guard.investigate(pairs, block, **flags)


def test_the_investigation_lists_per_task_and_per_area_deltas_and_the_trials_that_flipped():
    areas = [['coding'], ['coding'], ['docs'], ['docs', 'review'], None, ['coding']]
    result = investigated('PPPPFF', 'FPFPPP', areas=areas, grader_changed=False, inputs_unchanged=True)
    assert [(row['task'], row['baseline'], row['candidate'], row['delta']) for row in result['per_task']] == [
        ('task-0', 'passed', 'failed', -1), ('task-1', 'passed', 'passed', 0), ('task-2', 'passed', 'failed', -1),
        ('task-3', 'passed', 'passed', 0), ('task-4', 'failed', 'passed', 1), ('task-5', 'failed', 'passed', 1)]
    assert {name: (row['pairs'], row['delta'], row['flipped_to_fail'], row['flipped_to_pass']) for name, row in result['per_area'].items()} == {
        'coding': (3, 0.0, 1, 1), 'docs': (2, -0.5, 1, 0), 'review': (1, 0.0, 0, 0), 'untagged': (1, 1.0, 0, 1)}
    assert [row['task'] for row in result['flipped']['to_fail']] == ['task-0', 'task-2']
    assert [row['task'] for row in result['flipped']['to_pass']] == ['task-4', 'task-5']


def test_unknown_pairs_are_listed_per_task_but_belong_to_no_area_and_no_flip():
    result = investigated('P?', 'F?', areas=[['coding'], ['coding']])
    assert [(row['baseline'], row['candidate'], row['delta']) for row in result['per_task']] == [('passed', 'failed', -1), ('unknown', 'unknown', None)]
    assert result['per_area']['coding']['pairs'] == 1 and len(result['flipped']['to_fail']) == 1 and result['flipped']['to_pass'] == []


def test_identical_frozen_inputs_make_the_regression_candidate_caused():
    result = investigated('P' * 4, 'F' * 4, grader_changed=False, inputs_unchanged=True)
    assert result['cause']['class'] == 'candidate_caused' and result['changes'] == dict(grader='unchanged', frozen_inputs='unchanged', budget='identical')
    assert result['next_step'] == guard.NEXT_STEP['candidate_caused'] and result['cause']['caveats']


@pytest.mark.parametrize('field', ['bank_sha256', 'grader_sha256', 'seed_schedule_sha256', 'budget_share', 'runtime_sha256'])
def test_a_difference_in_a_frozen_input_makes_the_regression_pipeline_caused(field):
    other = dict(SHA, budget_share=dict(trials=12, timeout_seconds=60.0))
    other[field] = dict(trials=6, timeout_seconds=30.0) if field == 'budget_share' else 'f' * 64
    result = investigated('P' * 4, 'F' * 4, candidate_arm=guard.frozen_inputs(**other), grader_changed=False, inputs_unchanged=True)
    assert result['cause']['class'] == 'pipeline_caused' and f'{field}_differed' in result['cause']['basis']
    assert result['next_step'] == guard.NEXT_STEP['pipeline_caused']


@pytest.mark.parametrize('flags, change', [
    pytest.param(dict(grader_changed=True, inputs_unchanged=True), ('grader', 'changed'), id='grader_changed'),
    pytest.param(dict(grader_changed=False, inputs_unchanged=False), ('frozen_inputs', 'changed'), id='frozen_inputs_changed'),
])
def test_a_changed_grader_or_changed_inputs_make_the_regression_pipeline_caused(flags, change):
    result = investigated('P' * 4, 'F' * 4, **flags)
    assert result['cause']['class'] == 'pipeline_caused' and result['changes'][change[0]] == change[1]


@pytest.mark.parametrize('flags, candidate_arm', [
    pytest.param(dict(grader_changed=None, inputs_unchanged=True), None, id='grader_change_not_checked'),
    pytest.param(dict(grader_changed=False, inputs_unchanged=None), None, id='input_change_not_checked'),
    pytest.param(dict(grader_changed=False, inputs_unchanged=True), arm(runtime_sha256=None), id='runtime_not_observed'),
])
def test_an_unknown_fact_never_becomes_a_cause(flags, candidate_arm):
    result = investigated('P' * 4, 'F' * 4, candidate_arm=candidate_arm, **flags)
    assert result['cause']['class'] == 'undetermined' and result['next_step'] == guard.NEXT_STEP['undetermined']


def test_repeated_trials_drop_the_single_run_variance_caveat():
    pairs = [dict(task='a', repeat=n, baseline=True, candidate=False) for n in range(3)]
    result = guard.investigate(pairs, decide(pairs), grader_changed=False, inputs_unchanged=True)
    assert result['cause']['caveats'] == []


def test_run_pairs_reads_an_unreported_or_mismatched_split_as_unknown():
    manifest = dict(tasks=[dict(id='a', split='development'), dict(id='b', split='regression', root='r', areas=['coding']), dict(id='c', split='heldout')])
    trials = [dict(version='baseline', task='a', split='development', status='failed'), dict(version='candidate', task='a', split='development', status='passed'),
              dict(version='baseline', task='b', split='regression', status='passed'), dict(version='candidate', task='b', split='heldout', status='passed'),
              dict(version='baseline', task='c', split='heldout', status='timeout'), dict(version='candidate', task='c', split='heldout', status='passed')]
    pairs = guard.run_pairs(manifest, trials)
    assert [(p['task'], p['baseline'], p['candidate']) for p in pairs] == [('a', False, True), ('b', True, None), ('c', None, True)]
    assert (pairs[1]['root'], pairs[1]['areas']) == ('r', ['coding'])


def test_the_schedule_identity_follows_task_order_repeats_and_seed():
    tasks = [dict(id='a'), dict(id='b')]
    base = guard.schedule_sha256(tasks)
    assert base == guard.schedule_sha256([dict(id='a'), dict(id='b')])
    assert len({base, guard.schedule_sha256(tasks[::-1]), guard.schedule_sha256(tasks, repeats=2), guard.schedule_sha256(tasks, seed=7)}) == 4


def test_the_launch_identity_binds_the_agent_the_executable_and_the_environment_policy(tmp_path):
    (tmp_path / 'one').write_text('x'); (tmp_path / 'two').write_text('x')
    base = guard.launch_sha256('codex', tmp_path / 'one')
    assert base == guard.launch_sha256('codex', tmp_path / 'one')
    assert len({base, guard.launch_sha256('claude', tmp_path / 'one'), guard.launch_sha256('codex', tmp_path / 'two'),
                guard.launch_sha256('codex', tmp_path / 'one', inherit_env=True)}) == 4


def exportable(**wrapper):
    """A record the public export accepts apart from the one wrapper under test."""
    return dict(baseline_runtime={name: 'x' for name in MATCH_FIELDS}, candidate_runtime={name: 'x' for name in MATCH_FIELDS},
                provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='synthetic', derived_from_private=False), **wrapper)


def test_neither_block_is_publicly_exportable_whatever_its_publication_flag_says():
    pairs = bank('PPP', 'FFF')
    block = decide(pairs)
    assert public_export_allowed([exportable()])  # the baseline record is accepted, so a refusal below is the profile's
    for value in (block, guard.investigate(pairs, block)):
        assert not public_export_allowed([exportable(receipt=value)])
        assert not public_export_allowed([exportable(receipt=dict(value, publication_allowed=True))])


def test_the_bank_identity_ignores_the_run_budget_and_the_run_folder_but_sees_a_task_change(tmp_path):
    manifest = dict(tasks=[dict(id='a', fixture='a', grader=['python3', str(tmp_path / 'one' / 'grade.py')])], budget_seconds=29.5, timeout_seconds=14.0)
    moved = dict(tasks=[dict(id='a', fixture='a', grader=['python3', str(tmp_path / 'two' / 'grade.py')])], budget_seconds=12.0, timeout_seconds=6.0)
    changed = dict(moved, tasks=[dict(id='b', fixture='a', grader=['python3', str(tmp_path / 'two' / 'grade.py')])])
    assert guard.bank_sha256(manifest, tmp_path / 'one') == guard.bank_sha256(moved, tmp_path / 'two')
    assert guard.bank_sha256(moved, tmp_path / 'two') != guard.bank_sha256(changed, tmp_path / 'two')


def test_the_gain_margin_raises_the_bar_for_improved_and_leaves_the_regression_margin_alone():
    helpful = bank('F' * 12, 'P' * 12)
    assert decide(helpful, margin=0.0)['verdict'] == 'improved'
    demanding = decide(helpful, margin=0.0, gain_margin=0.9)
    assert (demanding['verdict'], demanding['adoption_blocked'], demanding['gain_margin']) == ('inconclusive', False, 0.9)
    assert 'interval_not_above_margin' in demanding['reasons']
    loss = bank('P' * 10, 'F' + 'P' * 9)
    assert decide(loss, margin=0.0, gain_margin=0.9)['verdict'] == 'candidate_regressed'
    assert decide(loss, margin=0.2, gain_margin=0.0)['verdict'] == 'inconclusive'
    assert 'gain_margin' not in decide(helpful, margin=0.0)


@pytest.mark.parametrize('gain_margin', [
    pytest.param(-0.01, id='negative'), pytest.param(1, id='one'), pytest.param(math.nan, id='nan'), pytest.param(True, id='boolean')])
def test_an_unsupported_gain_margin_is_refused(gain_margin):
    with pytest.raises(ValueError, match='gain_margin'):
        decide(bank('PP', 'FF'), gain_margin=gain_margin)


@pytest.mark.parametrize('text, value', [pytest.param('0', 0.0, id='zero'), pytest.param('0.05', 0.05, id='fraction'), pytest.param('0.999', 0.999, id='just_below_one')])
def test_the_margin_option_accepts_a_number_from_zero_up_to_but_not_including_one(text, value):
    assert guard.margin_option(text) == value


@pytest.mark.parametrize('text', [
    pytest.param('', id='empty'), pytest.param('abc', id='word'), pytest.param('-0.1', id='negative'), pytest.param('1', id='one'),
    pytest.param('nan', id='nan'), pytest.param('inf', id='infinite'), pytest.param('1e3', id='above_one')])
def test_the_margin_option_refuses_anything_else_as_an_input_error(text):
    with pytest.raises(argparse.ArgumentTypeError, match='margin'):
        guard.margin_option(text)


def fixed_spec(tasks=3, repeats=2):
    return ExperimentSpec('synthetic', tuple((f't{i}', f'r{i}', 'fam' if i % 2 else 'other') for i in range(tasks)), repeats, 7, 2 * tasks * repeats,
                          2, 0.01, 0.02, 0.05, (('fam', 0.5), ('other', 0.5)), 'b' * 64, 'c' * 64, 'd' * 64, 'e' * 64)


def graded(spec, outcome, **changes):
    """One graded record per scheduled trial; ``outcome(row)`` is the verdict and ``changes`` overrides record fields for one version."""
    rows = []
    for row in schedule(spec):
        value = outcome(row)
        fields = dict(trial_id=row['trial_id'], outcome=value, status='graded', provenance='executable_grader', baseline_hash=spec.baseline_hash,
                      candidate_hash=spec.candidate_hash, grader_hash=spec.grader_hash, runtime_hash=spec.runtime_hash)
        rows.append(TrialRecord(**{**fields, **(changes if row['version'] == 'candidate' else {})}))
    return rows


def test_a_sealed_fixed_comparison_yields_one_pair_per_task_and_repeat_with_the_declared_family_as_area():
    spec = fixed_spec()
    pairs, baseline, candidate = guard.experiment_inputs(spec, graded(spec, lambda row: row['version'] == 'baseline'), bank_sha256='a' * 64,
                                                         tasks=[dict(id='t0', split='development'), dict(id='t1', split='regression')])
    assert sorted((p['task'], p['repeat']) for p in pairs) == [(f't{i}', n) for i in range(3) for n in range(2)]
    assert all((p['baseline'], p['candidate']) == (True, False) for p in pairs)
    assert {(p['task'], p['root'], tuple(p['areas']), p['split']) for p in pairs} == {
        ('t0', 'r0', ('other',), 'development'), ('t1', 'r1', ('fam',), 'regression'), ('t2', 'r2', ('other',), None)}
    assert guard.compare_identities(baseline, candidate) == dict(status='identical', mismatched=[], unknown=[])
    assert baseline['budget_share'] == dict(trials=6) and baseline['bank_sha256'] == 'a' * 64
    assert (baseline['grader_sha256'], baseline['runtime_sha256']) == ('d' * 64, 'e' * 64)
    assert baseline['seed_schedule_sha256'] == candidate['seed_schedule_sha256'] and baseline['seed_schedule_sha256'] is not None


def test_a_fixed_comparison_that_lost_a_candidate_record_is_unverified_and_never_a_match():
    spec = fixed_spec()
    records = [r for r in graded(spec, lambda row: True) if r.trial_id != next(row['trial_id'] for row in schedule(spec) if row['version'] == 'candidate')]
    pairs, baseline, candidate = guard.experiment_inputs(spec, records, bank_sha256='a' * 64)
    comparison = guard.compare_identities(baseline, candidate)
    assert comparison['status'] == 'unverified' and 'budget_share' in comparison['unknown']
    assert [p['candidate'] for p in pairs].count(None) == 1
    result = guard.decide(pairs, baseline=baseline, candidate=candidate)
    assert result['verdict'] != 'improved' and result['pairs']['unknown'] == 1


@pytest.mark.parametrize('status, provenance, outcome', [
    pytest.param('ungraded', 'executable_grader', True, id='not_graded'),
    pytest.param('graded', 'self_reported', True, id='grader_not_executable'),
    pytest.param('graded', 'executable_grader', None, id='no_outcome'),
    pytest.param('graded', 'executable_grader', 1, id='integer_outcome_is_not_coerced'),
])
def test_a_trial_that_is_not_an_executable_graded_pass_or_fail_is_an_unknown_outcome(status, provenance, outcome):
    spec = fixed_spec(tasks=2, repeats=1)
    odd = next(row['trial_id'] for row in schedule(spec) if row['version'] == 'candidate')
    records = [TrialRecord(**{**vars(r), **(dict(status=status, provenance=provenance, outcome=outcome) if r.trial_id == odd else {})}) for r in graded(spec, lambda row: True)]
    pairs, _, _ = guard.experiment_inputs(spec, records, bank_sha256='a' * 64)
    assert [p['candidate'] for p in pairs].count(None) == 1 and all(p['baseline'] is True for p in pairs)


@pytest.mark.parametrize('field, name', [
    pytest.param('grader_hash', 'grader_sha256', id='grader'), pytest.param('runtime_hash', 'runtime_sha256', id='runtime')])
def test_a_candidate_arm_recorded_under_another_grader_or_runtime_is_a_named_mismatch(field, name):
    spec = fixed_spec()
    pairs, baseline, candidate = guard.experiment_inputs(spec, graded(spec, lambda row: True, **{field: 'f' * 64}), bank_sha256='a' * 64)
    assert guard.compare_identities(baseline, candidate) == dict(status='mismatch', mismatched=[name], unknown=[])
    block = guard.decide(pairs, baseline=baseline, candidate=candidate)
    assert guard.investigate(pairs, block, grader_changed=False, inputs_unchanged=True)['cause']['class'] == 'pipeline_caused'
