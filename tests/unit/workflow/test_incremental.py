"""Incremental-run detection decides from synthetic harness states and receipts only (#806); no model, project or history is read."""
import argparse
import json
import os

import pytest

from agent_harness_lab import incremental
from agent_harness_lab.evaluation import before_after
from agent_harness_lab.evaluation.benchmark import public_export_allowed

A, B, C, D = ('a' * 64, 0o644), ('b' * 64, 0o644), ('c' * 64, 0o644), ('d' * 64, 0o644)
EXECUTABLE_A = ('a' * 64, 0o755)
EVIDENCE = dict(key='e' * 64, run='run-00', cause='candidate_caused', historical=dict(frozen_inputs=None))


def plain(**states):
    return {name: list(state) for name, state in states.items()}


def record(baseline, candidate=None, *, scope=None, attempt=0):
    return dict(scope=scope, baseline=plain(**baseline), candidate=None if candidate is None else dict(attempt=attempt, states=plain(**candidate)),
                regressions=[])


@pytest.mark.parametrize('baseline, candidate, current, disposition, applied, ignored, edited', [
    pytest.param(dict(file=A), dict(file=B), dict(file=B), 'applied', ['file'], [], [], id='candidate_bytes_now_in_the_file'),
    pytest.param(dict(file=A), dict(file=B), dict(file=A), 'ignored', [], ['file'], [], id='file_still_the_previous_baseline'),
    pytest.param(dict(file=A), dict(file=B), dict(file=C), 'edited', [], [], ['file'], id='file_matches_neither_arm'),
    pytest.param(dict(file=A), dict(file=A), dict(file=A), 'no_candidate', [], [], [], id='candidate_identical_to_baseline'),
    pytest.param(dict(file=A), None, dict(file=C), 'no_candidate', [], [], ['file'], id='no_frozen_candidate_and_an_edit'),
    pytest.param(dict(file=A), dict(file=EXECUTABLE_A), dict(file=EXECUTABLE_A), 'applied', ['file'], [], [], id='mode_only_change_applied'),
    pytest.param(dict(file=A), dict(file=EXECUTABLE_A), dict(file=A), 'ignored', [], ['file'], [], id='mode_only_change_ignored'),
    pytest.param(dict(one=A, two=A), dict(one=B, two=B), dict(one=B, two=A), 'partially_applied', ['one'], ['two'], [], id='one_of_two_applied'),
    pytest.param(dict(one=A, two=A), dict(one=B, two=B), dict(one=B, two=C), 'edited', ['one'], [], ['two'], id='one_applied_one_edited'),
    pytest.param(dict(one=A, two=A), dict(one=B, two=A), dict(one=B, two=C), 'applied', ['one'], [], ['two'], id='applied_with_an_unrelated_edit'),
    pytest.param(dict(file=A, gone=B), dict(file=A), dict(file=A), 'applied', ['gone'], [], [], id='candidate_removal_applied_by_deleting'),
    pytest.param(dict(file=A, gone=B), dict(file=A), dict(file=A, gone=B), 'ignored', [], ['gone'], [], id='candidate_removal_ignored'),
    pytest.param(dict(file=A), dict(file=A, new=B), dict(file=A, new=B), 'applied', ['new'], [], [], id='candidate_addition_applied'),
    pytest.param(dict(file=A), dict(file=A, new=B), dict(file=A), 'ignored', [], ['new'], [], id='candidate_addition_ignored'),
    pytest.param(dict(file=A), dict(file=A, new=B), dict(file=A, new=C), 'edited', [], [], ['new'], id='candidate_addition_edited'),
    pytest.param({}, dict(new=B), {}, 'ignored', [], ['new'], [], id='empty_baseline_and_nothing_written'),
    pytest.param({}, None, {}, 'no_candidate', [], [], [], id='empty_everything'),
])
def test_each_path_is_applied_ignored_or_edited_against_the_previous_baseline_and_candidate(baseline, candidate, current, disposition, applied, ignored, edited):
    found = incremental.compare(record(baseline, candidate), plain(**current), None)
    paths = found['paths']
    assert found['disposition'] == disposition
    assert ([name for name, kind in paths.items() if kind == 'applied'], [name for name, kind in paths.items() if kind == 'ignored'],
            [name for name, kind in paths.items() if kind == 'edited']) == (applied, ignored, edited)


def test_a_record_from_another_scope_is_unknown_and_never_applied_or_ignored():
    found = incremental.compare(record(dict(file=A), dict(file=B), scope=['file']), plain(file=B), ['file', 'other'])
    assert found == dict(disposition='unknown', reason='scope_changed')


def prior_run(*, regressed=True, cause='candidate_caused', attempt=0, runtime='9' * 64, baseline=None, candidate=None, scope=None, receipt='default', verdict=None):
    arm = dict(runtime_sha256=runtime)
    block = dict(profile=before_after.PROFILE, verdict=verdict or ('candidate_regressed' if regressed else 'inconclusive'), bindings=dict(attempt=attempt),
                 scores=dict(baseline=dict(mean=1.0), candidate=dict(mean=0.0)), delta=dict(mean=-1.0), frozen_inputs=dict(baseline=arm, candidate=arm))
    full = dict(status='blocked' if regressed else 'inconclusive', reason='candidate_regressed' if regressed else None, before_after=block,
                investigation=dict(cause=cause))
    return dict(run='run-previous', record=record(baseline or dict(file=A), candidate or dict(file=B), scope=scope, attempt=0),
                receipt=full if receipt == 'default' else receipt)


def current_state(prior, *, baseline=None, scope=None):
    return dict(prior=prior, scope=scope, baseline=plain(**(baseline or dict(file=A))), skipped=False,
                regressions=[] if prior is None else incremental.carried(prior))


def test_the_same_candidate_on_the_same_baseline_and_runtime_repeats_the_regression():
    prior = prior_run()
    found = incremental.repeats_regression(current_state(prior), dict(file=B), '9' * 64)
    assert found['run'] == 'run-previous' and found['cause'] == 'candidate_caused' and found['historical']['verdict'] == 'candidate_regressed'


@pytest.mark.parametrize('prior, state_changes, proposed, runtime', [
    pytest.param(prior_run(regressed=False), {}, dict(file=B), '9' * 64, id='previous_candidate_did_not_regress'),
    pytest.param(prior_run(cause='pipeline_caused'), {}, dict(file=B), '9' * 64, id='regression_owned_by_the_pipeline_not_the_candidate'),
    pytest.param(prior_run(cause='undetermined'), {}, dict(file=B), '9' * 64, id='regression_cause_undetermined'),
    pytest.param(prior_run(attempt=1), {}, dict(file=B), '9' * 64, id='verdict_describes_another_attempt_than_the_frozen_candidate'),
    pytest.param(prior_run(receipt=None), {}, dict(file=B), '9' * 64, id='previous_receipt_unreadable'),
    pytest.param(prior_run(), {}, dict(file=C), '9' * 64, id='a_different_candidate'),
    pytest.param(prior_run(), dict(baseline=dict(file=C)), dict(file=B), '9' * 64, id='a_different_baseline'),
    pytest.param(prior_run(), dict(scope=['file']), dict(file=B), '9' * 64, id='a_different_scope'),
    pytest.param(prior_run(), {}, dict(file=B), '8' * 64, id='a_different_runtime'),
    pytest.param(prior_run(), {}, dict(file=B), None, id='unknown_runtime'),
    pytest.param(prior_run(runtime=None), {}, dict(file=B), '9' * 64, id='previous_runtime_unknown'),
    pytest.param(prior_run(candidate=dict(file=B)), {}, dict(file=EXECUTABLE_A), '9' * 64, id='a_mode_only_candidate_difference'),
])
def test_nothing_else_repeats_a_regression(prior, state_changes, proposed, runtime):
    state = dict(current_state(prior), **{key: plain(**value) if key == 'baseline' else value for key, value in state_changes.items()})
    assert incremental.repeats_regression(state, proposed, runtime) is None


def test_a_first_run_and_a_previous_run_without_a_frozen_candidate_never_repeat_a_regression():
    assert incremental.repeats_regression(None, dict(file=B), '9' * 64) is None
    assert incremental.repeats_regression(current_state(None), dict(file=B), '9' * 64) is None
    prior = prior_run()
    prior['record']['candidate'] = None
    assert incremental.repeats_regression(current_state(prior), dict(file=B), '9' * 64) is None


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A private run store for one synthetic project, found where the workflow looks for it."""
    monkeypatch.setattr('agent_harness_lab.workflow.state_root', lambda: tmp_path / 'state')
    args = argparse.Namespace(project=tmp_path / 'project')
    from agent_harness_lab.workflow import identity
    folder = tmp_path / 'state' / 'runs' / identity(args.project)
    folder.mkdir(parents=True)
    return args, folder


def start(args, folder, name, baseline=None, scope=None):
    output = folder / name
    output.mkdir()
    return incremental.begin(args, output, baseline or dict(file=A), scope), output


def test_a_run_records_its_baseline_and_becomes_the_next_runs_previous(store):
    args, folder = store
    first, first_output = start(args, folder, 'run-one')
    assert first['prior'] is None and first['problem'] is None
    assert json.loads((first_output / incremental.RECORD).read_text()) == dict(
        profile=incremental.RECORD_PROFILE, scope=None, baseline=plain(file=A), candidate=None, regressions=[])
    incremental.froze(first, 0, dict(file=B))
    second, _ = start(args, folder, 'run-two')
    assert second['prior']['run'] == 'run-one' and second['prior']['record']['candidate'] == dict(attempt=0, states=plain(file=B))
    assert json.loads((folder / incremental.POINTER).read_text()) == dict(run='run-two')


def test_a_run_outside_the_private_run_store_reads_the_previous_run_but_writes_no_record(store, tmp_path):
    args, folder = store
    start(args, folder, 'run-one')
    elsewhere = tmp_path / 'chosen'
    elsewhere.mkdir()
    state = incremental.begin(args, elsewhere, dict(file=A), None)
    incremental.froze(state, 0, dict(file=B))
    assert state['prior']['run'] == 'run-one' and not (elsewhere / incremental.RECORD).exists()
    assert json.loads((folder / incremental.POINTER).read_text()) == dict(run='run-one')


@pytest.mark.parametrize('damage', [
    pytest.param(lambda text: '{not json', id='not_json'),
    pytest.param(lambda text: '[]', id='not_an_object'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), profile='other')), id='wrong_profile'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), baseline={'file': ['xyz', 420]})), id='sha_not_hex'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), baseline={'file': ['a' * 64, '420']})), id='mode_not_int'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), baseline={'../escape': ['a' * 64, 420]})), id='path_leaves_the_project'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), baseline={'/abs': ['a' * 64, 420]})), id='absolute_path'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), baseline={f'f{n}': ['a' * 64, 420] for n in range(incremental.MAX_FILES + 1)})), id='oversized_inventory'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), candidate=dict(attempt='0', states={}))), id='attempt_not_int'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), scope='file')), id='scope_not_a_list'),
    pytest.param(lambda text: '', id='empty'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions='x')), id='regressions_not_a_list'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions=['x'])), id='evidence_not_an_object'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions=[dict(EVIDENCE, key='xyz')])), id='evidence_key_not_hex'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions=[dict(EVIDENCE, run='../outside')])), id='evidence_run_not_a_run_folder_name'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions=[dict(EVIDENCE, cause='pipeline_caused')])), id='evidence_cause_not_owned_by_the_candidate'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions=[dict(EVIDENCE, historical=None)])), id='evidence_without_the_measurement'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions=[dict(EVIDENCE, historical=dict(frozen_inputs='x'))])), id='evidence_inputs_not_an_object'),
    pytest.param(lambda text: json.dumps(dict(json.loads(text), regressions=[EVIDENCE] * (incremental.REGRESSIONS_KEPT + 1))), id='oversized_memory'),
])
def test_a_damaged_previous_record_is_unknown_and_never_a_first_run(store, damage):
    args, folder = store
    _, output = start(args, folder, 'run-one')
    path = output / incremental.RECORD
    path.write_text(damage(path.read_text()))
    state, _ = start(args, folder, 'run-two')
    assert (state['prior'], state['problem']) == (None, 'previous_run_unreadable')
    result = dict(status='no_change')
    incremental.attach(result, state, dict(file=A))
    assert result['incremental']['status'] == 'unavailable' and result['incremental']['reason'] == 'previous_run_unreadable'
    assert result['status'] == 'no_change' and 'disposition' not in result['incremental']


@pytest.mark.parametrize('pointer', [
    pytest.param('../outside', id='path_traversal'),
    pytest.param('not-a-run', id='not_a_run_folder_name'),
    pytest.param('run-missing', id='run_folder_deleted'),
    pytest.param(None, id='pointer_without_a_run'),
])
def test_a_pointer_that_does_not_name_a_readable_run_is_unknown(store, pointer):
    args, folder = store
    (folder / incremental.POINTER).write_text(json.dumps(dict(run=pointer)))
    state, _ = start(args, folder, 'run-two')
    assert (state['prior'], state['problem']) == (None, 'previous_run_unreadable')


def test_a_symlinked_record_is_refused_and_not_followed(store, tmp_path):
    args, folder = store
    _, output = start(args, folder, 'run-one')
    real = tmp_path / 'real.json'
    real.write_text((output / incremental.RECORD).read_text())
    (output / incremental.RECORD).unlink()
    os.symlink(real, output / incremental.RECORD)
    state, _ = start(args, folder, 'run-two')
    assert state['problem'] == 'previous_run_unreadable'


def test_attach_adds_nothing_to_a_first_run_and_leaves_the_result_when_it_fails(store, monkeypatch):
    args, folder = store
    first, _ = start(args, folder, 'run-one')
    result = dict(status='no_change')
    incremental.attach(result, first, dict(file=A))
    incremental.attach(result, None, dict(file=A))
    assert result == dict(status='no_change')
    second, _ = start(args, folder, 'run-two')
    monkeypatch.setattr(incremental, 'compare', lambda *a: 1 / 0)
    incremental.attach(result, second, dict(file=A))
    assert result['status'] == 'no_change' and result['incremental']['status'] == 'unavailable' and result['incremental']['reason'] == 'ZeroDivisionError'


def test_an_edit_outside_the_candidate_is_flagged_beside_an_applied_candidate(store):
    args, folder = store
    first, _ = start(args, folder, 'run-one', dict(one=A, two=A))
    incremental.froze(first, 0, dict(one=B, two=A))
    state, _ = start(args, folder, 'run-two', dict(one=B, two=C))
    result = {}
    incremental.attach(result, state, dict(one=B, two=C))
    block = result['incremental']
    assert block['disposition'] == 'applied' and block['flags'] == ['harness_edited_outside_candidate']
    assert block['harness']['edited'] == dict(count=1, paths=['two'])


def test_a_long_path_list_is_counted_exactly_and_listed_to_a_bound(store):
    args, folder = store
    many = {f'skills/s{n:03d}/SKILL.md': A for n in range(incremental.LISTED + 5)}
    first, _ = start(args, folder, 'run-one', many)
    incremental.froze(first, 0, {name: B for name in many})
    state, _ = start(args, folder, 'run-two', {name: B for name in many})
    result = {}
    incremental.attach(result, state, {name: B for name in many})
    applied = result['incremental']['harness']['applied']
    assert applied['count'] == len(many) and len(applied['paths']) == incremental.LISTED and applied['paths'] == sorted(many)[:incremental.LISTED]


def fresh_block(*, bank='1' * 64, baseline_mean=0.5, candidate_mean=1.0, runtime='9' * 64):
    arm = before_after.frozen_inputs(bank_sha256=bank, grader_sha256='2' * 64, seed_schedule_sha256='3' * 64,
                                     budget_share=dict(trials=3), runtime_sha256=runtime)
    return dict(profile=before_after.PROFILE, verdict='inconclusive', scores=dict(baseline=dict(mean=baseline_mean), candidate=dict(mean=candidate_mean)),
                delta=dict(mean=candidate_mean - baseline_mean), bindings=dict(attempt=0), frozen_inputs=dict(baseline=arm, candidate=arm))


def compared(previous_block, fresh):
    historical = incremental._historical(dict(before_after=previous_block))
    return incremental._fresh(dict(before_after=fresh), historical)


def test_scores_on_identical_frozen_inputs_are_shown_as_differences_beside_the_labelled_history():
    shown = compared(fresh_block(baseline_mean=0.25, candidate_mean=0.75), fresh_block(baseline_mean=0.75, candidate_mean=1.0))
    assert shown['comparison']['status'] == 'identical' and shown['label'] == 'fresh_evaluation'
    assert (shown['fresh_baseline_minus_historical_candidate'], shown['fresh_baseline_minus_historical_baseline']) == (0, 0.5)


@pytest.mark.parametrize('changes, field', [
    pytest.param(dict(bank='4' * 64), 'bank_sha256', id='another_bank'),
    pytest.param(dict(runtime='5' * 64), 'runtime_sha256', id='another_runtime'),
])
def test_scores_on_different_frozen_inputs_are_never_subtracted(changes, field):
    shown = compared(fresh_block(), fresh_block(**changes))
    assert shown['comparison']['status'] == 'mismatch' and field in shown['comparison']['mismatched']
    assert not any(key.startswith('fresh_baseline_minus') for key in shown)


def test_a_history_without_recorded_frozen_inputs_is_unverified_not_identical():
    previous = fresh_block()
    previous['frozen_inputs'] = None
    shown = compared(previous, fresh_block())
    assert shown['comparison'] == dict(status='unverified', reason='frozen_inputs_unrecorded')


@pytest.mark.parametrize('block', [
    pytest.param(None, id='no_block'),
    pytest.param(dict(status='unavailable'), id='unavailable_guard'),
    pytest.param(dict(profile='other', scores=dict(baseline=dict(mean=1.0))), id='other_profile'),
    pytest.param(dict(profile=before_after.PROFILE, scores=None), id='no_scores'),
    pytest.param(dict(profile=before_after.PROFILE, scores=dict(baseline=dict(mean='1.0'))), id='score_not_a_number'),
])
def test_a_receipt_without_a_usable_paired_evaluation_has_no_historical_observation(block):
    assert incremental._historical(dict(before_after=block)) is None
    assert incremental._historical(None) is None


def test_the_run_block_and_its_record_are_not_publicly_exportable_whatever_their_publication_flag_says():
    from tests.unit.evaluation.test_before_after import exportable
    assert public_export_allowed([exportable()])  # the baseline record is accepted, so a refusal below is the profile's
    for profile in (incremental.PROFILE, incremental.RECORD_PROFILE):
        value = dict(profile=profile, publication_allowed=False)
        assert not public_export_allowed([exportable(receipt=value)])
        assert not public_export_allowed([exportable(receipt=dict(value, publication_allowed=True))])


def finish(output, receipt):
    (output / 'workflow.json').write_text(json.dumps(receipt))


def run_after(args, folder, name, candidate, receipt, baseline=None):
    """One more run of the project: it records its baseline, freezes `candidate` (or none) and ends with `receipt`."""
    state, output = start(args, folder, name, baseline)
    if candidate is not None: incremental.froze(state, 0, candidate)
    finish(output, receipt)
    return state


REPEAT = dict(status='blocked', reason='candidate_already_regressed', previous_run='run-00')


@pytest.mark.parametrize('between', [
    pytest.param([(dict(file=B), REPEAT)], id='one_blocked_repeat'),
    pytest.param([(dict(file=B), REPEAT)] * 3, id='three_blocked_repeats'),
    pytest.param([(None, dict(status='no_change'))], id='a_run_that_froze_no_candidate'),
    pytest.param([(dict(file=B), dict(status='collecting'))], id='an_interrupted_run'),
    pytest.param([(dict(file=C), prior_run(regressed=False)['receipt'])], id='another_candidate_that_did_not_regress'),
    pytest.param([(dict(file=C), prior_run()['receipt'])], id='another_candidate_that_regressed'),
])
def test_a_measured_regression_is_remembered_through_every_later_run(store, between):
    args, folder = store
    run_after(args, folder, 'run-00', dict(file=B), prior_run()['receipt'])
    for number, (candidate, receipt) in enumerate(between, 1):
        run_after(args, folder, f'run-{number:02d}', candidate, receipt)
    state, _ = start(args, folder, 'run-last')
    incremental.froze(state, 0, dict(file=B))
    found = incremental.repeats_regression(state, dict(file=B), '9' * 64)
    assert found and found['run'] == 'run-00'
    assert not incremental.repeats_regression(state, dict(file=B), '8' * 64)
    assert not incremental.repeats_regression(state, dict(file=B), None)
    assert not incremental.repeats_regression(state, dict(file=D), '9' * 64)


def test_a_blocked_repeat_still_reports_its_candidate_as_regressed_and_flags_it_when_applied(store):
    args, folder = store
    run_after(args, folder, 'run-00', dict(file=B), prior_run()['receipt'])
    run_after(args, folder, 'run-01', dict(file=B), REPEAT)
    state, _ = start(args, folder, 'run-02', dict(file=B))  # the candidate was applied by hand since
    result = {}
    incremental.attach(result, state, dict(file=B))
    block = result['incremental']
    assert block['previous']['candidate_regressed'] is True
    assert block['flags'] == ['regressed_candidate_applied']
    assert block['historical']['verdict'] == 'candidate_regressed' and block['historical']['run'] == 'run-00'


def test_the_regression_memory_is_bounded_and_forgets_the_oldest_first(store):
    args, folder = store
    for number in range(incremental.REGRESSIONS_KEPT + 1):
        run_after(args, folder, f'run-{number:03d}', dict(file=(f'{number + 1:064x}', 0o644)), prior_run()['receipt'])
    state, output = start(args, folder, 'run-last')
    assert len(json.loads((output / incremental.RECORD).read_text())['regressions']) == incremental.REGRESSIONS_KEPT
    newest, oldest = dict(file=(f'{incremental.REGRESSIONS_KEPT + 1:064x}', 0o644)), dict(file=(f'{1:064x}', 0o644))
    assert incremental.repeats_regression(state, newest, '9' * 64)
    assert not incremental.repeats_regression(state, oldest, '9' * 64)


def unmeasured(**block):
    return dict(status='inconclusive', before_after=dict(block))


@pytest.mark.parametrize('prior, expected', [
    pytest.param(prior_run(), True, id='measured_regression_owned_by_the_candidate'),
    pytest.param(prior_run(regressed=False), False, id='measured_inconclusive_pair_bound_to_the_candidate'),
    pytest.param(prior_run(regressed=False, verdict='improved'), False, id='measured_improvement_bound_to_the_candidate'),
    pytest.param(prior_run(receipt=dict(status='collecting')), None, id='interrupted_run_receipt'),
    pytest.param(prior_run(receipt=dict(status='budget_exhausted', reason='budget_exhausted')), None, id='budget_exhausted_before_any_trial'),
    pytest.param(prior_run(receipt=unmeasured(status='unavailable', reason='ValueError')), None, id='guard_block_unavailable'),
    pytest.param(prior_run(receipt=unmeasured(profile=before_after.PROFILE, verdict='inconclusive', scores=None, bindings=dict(attempt=0))), None,
                 id='guard_block_without_paired_outcomes'),
    pytest.param(prior_run(receipt=dict(status='blocked', reason='candidate_already_regressed', previous_run='run-00')), None,
                 id='blocked_repeat_whose_record_carries_no_evidence'),
    pytest.param(prior_run(attempt=1, regressed=False), None, id='measurement_of_another_attempt_than_the_frozen_candidate'),
    pytest.param(prior_run(attempt=1), None, id='regression_of_another_attempt_than_the_frozen_candidate'),
    pytest.param(prior_run(cause='pipeline_caused'), None, id='regression_owned_by_the_pipeline'),
    pytest.param(prior_run(cause='undetermined'), None, id='regression_cause_undetermined'),
    pytest.param(prior_run(receipt=None), None, id='previous_receipt_unreadable'),
])
def test_previous_candidate_regressed_is_true_false_or_unknown_from_what_the_receipt_measured(prior, expected):
    assert incremental.regressed(prior) is expected


def test_a_previous_run_that_froze_no_candidate_has_no_regression_to_report():
    prior = prior_run()
    prior['record']['candidate'] = None
    assert incremental.regressed(prior) is None


@pytest.mark.parametrize('receipt', [
    pytest.param(dict(prior_run()['receipt'], investigation=['candidate_caused']), id='investigation_not_an_object'),
    pytest.param(dict(prior_run()['receipt'], before_after=dict(prior_run()['receipt']['before_after'], bindings=['attempt'])), id='bindings_not_an_object'),
])
def test_a_previous_receipt_of_the_wrong_shape_is_unknown_and_never_stops_the_run(store, receipt):
    args, folder = store
    run_after(args, folder, 'run-00', dict(file=B), receipt)
    state, _ = start(args, folder, 'run-01')
    assert (state['prior'], state['problem'], state['regressions']) == (None, 'previous_run_unreadable', [])


DEFAULT_EVIDENCE = dict(action='rerun', reason='no_verified_prior_output')


def evidence_stage(store, reuse):
    """The `evidence_collection` row a second run reports, given the stage-reuse summary of this run (or none)."""
    args, folder = store
    start(args, folder, 'run-one')
    state, _ = start(args, folder, 'run-two')
    result = {}
    incremental.attach(result, state, dict(file=A), reuse)
    return result['incremental']['stages']


@pytest.mark.parametrize('reuse, expected', [
    pytest.param(dict(outcome='reused', reason='hit_verified'), dict(action='reused', reason='hit_verified'), id='verified_hit'),
    pytest.param(dict(outcome='executed', reason='miss_input_changed'), dict(action='rerun', reason='miss_input_changed'), id='input_changed'),
    pytest.param(dict(outcome='executed', reason='miss_no_prior'), dict(action='rerun', reason='miss_no_prior'), id='no_prior_record'),
    pytest.param(dict(outcome='executed', reason='invalid_integrity'), dict(action='rerun', reason='invalid_integrity'), id='stored_record_failed_verification'),
    pytest.param(None, DEFAULT_EVIDENCE, id='reuse_not_requested'),
    pytest.param('reused', DEFAULT_EVIDENCE, id='summary_not_an_object'),
    pytest.param({}, DEFAULT_EVIDENCE, id='empty_summary'),
    pytest.param(dict(outcome='reused'), DEFAULT_EVIDENCE, id='reused_without_a_reason'),
    pytest.param(dict(outcome='reused', reason='miss_input_changed'), DEFAULT_EVIDENCE, id='reused_with_a_miss_reason'),
    pytest.param(dict(outcome='reused', reason=['hit_verified']), DEFAULT_EVIDENCE, id='reason_not_a_string'),
    pytest.param(dict(outcome='unknown', reason='hit_verified'), DEFAULT_EVIDENCE, id='unknown_outcome_is_never_a_hit'),
    pytest.param(dict(outcome='executed'), DEFAULT_EVIDENCE, id='executed_without_a_reason'),
    pytest.param(dict(outcome='executed', reason='x' * 500), DEFAULT_EVIDENCE, id='oversized_reason'),
])
def test_the_evidence_stage_is_reused_only_for_a_verified_hit_and_says_why_otherwise(store, reuse, expected):
    assert evidence_stage(store, reuse)['evidence_collection'] == expected


def test_a_reused_evidence_stage_leaves_every_other_stage_rerun(store):
    stages = evidence_stage(store, dict(outcome='reused', reason='hit_verified'))
    assert {name: row['action'] for name, row in stages.items()} == dict(
        evidence_collection='reused', candidate_preparation='rerun', evaluation_construction='rerun', trials='rerun')
