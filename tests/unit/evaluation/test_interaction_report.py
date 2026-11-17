"""Synthetic controls for evaluator-side interaction outcomes; no model, runtime or capture is claimed."""
from copy import deepcopy
import json

import pytest

from agent_harness_lab.evaluation import behavior, interaction_report
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.interaction_contract import contract_sha256, evaluate
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from tests.unit.evaluation.test_interaction_contract import (
    AMBIGUOUS, ASK, DONE, EDIT, NO_CHANGE, ORIGINAL, REPLY, STOP, contract, episode)

TASK = 'task-1'
OTHER = 'other-task'


def bundle(root, spec=AMBIGUOUS, *, name='grader'):
    """A frozen grader bundle holding one contract; returns its folder and digest."""
    folder = root / name
    folder.mkdir()
    (folder / 'expected.json').write_text('{}')
    if spec is not None:
        (folder / 'interaction-contract.json').write_text(json.dumps(spec))
    return folder, digest(folder)


def captured(root, spec=AMBIGUOUS):
    folder, sha = bundle(root, spec)
    return interaction_report.capture({sha: folder}, {spec['task_id']: dict(bundle_sha256=sha)})[spec['task_id']]


def execution(spec, evidence, *, outcome=None, **changes):
    """A grader run whose typed output declares `evidence`; the stated outcome defaults to the true verdict."""
    stated = evaluate(spec, evidence)['passed'] is True if outcome is None else outcome
    envelope = dict(schema_version=1, valid=True, outcome=stated, grader_id='synthetic-grader', grader_version='v1',
                    interaction=dict(schema_version=1, profile='interaction-contract-v1',
                                     contract_sha256=contract_sha256(spec), evidence=evidence))
    return dict(stdout=json.dumps(envelope), exit_code=0 if stated else 1,
                status='completed' if stated else 'failed', **changes)


def run(spec, binding, *events, outcome=None, final=DONE, **kwargs):
    return interaction_report.observation(binding, execution(spec, episode(*events, final=final, **kwargs), outcome=outcome))


def test_capture_binds_the_contract_identity_of_a_verified_bundle(tmp_path):
    binding = captured(tmp_path)
    assert binding['contract'] == AMBIGUOUS
    assert binding['contract_sha256'] == contract_sha256(AMBIGUOUS)


def test_capture_leaves_a_bundle_without_a_contract_out_of_interaction_grading(tmp_path):
    folder, sha = bundle(tmp_path, None)
    assert interaction_report.capture({sha: folder}, {TASK: dict(bundle_sha256=sha)}) == {}


def test_capture_rejects_a_bundle_whose_bytes_changed_after_freezing(tmp_path):
    folder, sha = bundle(tmp_path)
    (folder / 'expected.json').write_text('{"changed": true}')
    with pytest.raises(ValueError, match='integrity'):
        interaction_report.capture({sha: folder}, {TASK: dict(bundle_sha256=sha)})


def test_capture_rejects_a_contract_written_for_another_task(tmp_path):
    folder, sha = bundle(tmp_path, contract(task_id=OTHER))
    with pytest.raises(ValueError, match='invalid frozen interaction contract'):
        interaction_report.capture({sha: folder}, {TASK: dict(bundle_sha256=sha)})


@pytest.mark.parametrize('raw', [
    pytest.param('{"schema_version":1,"schema_version":1}', id='duplicate-key'),
    pytest.param('{"schema_version":NaN}', id='nonfinite'),
    pytest.param('[' * 2000 + ']' * 2000, id='deeply-nested'),
    pytest.param('[]', id='not-an-object'),
    pytest.param(json.dumps(dict(AMBIGUOUS, endpoint='both')), id='unsupported-endpoint'),
    pytest.param('', id='empty'),
])
def test_capture_rejects_a_malformed_contract_file(tmp_path, raw):
    folder, _ = bundle(tmp_path, None)
    (folder / 'interaction-contract.json').write_text(raw)
    sha = digest(folder)
    with pytest.raises(ValueError, match='invalid frozen interaction contract'):
        interaction_report.capture({sha: folder}, {TASK: dict(bundle_sha256=sha)})


def test_capture_rejects_a_contract_replaced_by_a_symlink_after_freezing(tmp_path):
    folder, sha = bundle(tmp_path, None)
    (folder / 'interaction-contract.json').symlink_to(tmp_path / 'absent')
    with pytest.raises(ValueError):
        interaction_report.capture({sha: folder}, {TASK: dict(bundle_sha256=sha)})


def test_capture_rejects_a_bundle_that_also_declares_a_behavior_policy(tmp_path):
    folder, _ = bundle(tmp_path)
    (folder / 'behavior.json').write_text('{}')
    sha = digest(folder)
    with pytest.raises(ValueError, match='both a behavior policy and an interaction contract'):
        interaction_report.capture({sha: folder}, {TASK: dict(bundle_sha256=sha)})


def test_capture_requires_the_frozen_folder_of_every_bound_task(tmp_path):
    with pytest.raises(ValueError, match='missing frozen interaction bundle folder'):
        interaction_report.capture({}, {TASK: dict(bundle_sha256='a' * 64)})


def test_a_passing_ask_reply_act_episode_is_observed_as_complete_and_correct(tmp_path):
    observed = run(AMBIGUOUS, captured(tmp_path), ASK, REPLY, EDIT)
    assert (observed['complete'], observed['reason'], observed['passed']) == (True, 'observed', True)
    assert (observed['interaction_requirement_satisfied'], observed['whole_task_completed']) == (True, True)
    assert observed['valid_non_action_success'] is False and observed['correct_deliberate_stop'] is False


def test_a_correct_no_change_episode_is_a_valid_non_action_and_deliberate_stop(tmp_path):
    binding = captured(tmp_path, NO_CHANGE)
    observed = run(NO_CHANGE, binding, STOP, final=ORIGINAL, termination='stopped')
    assert (observed['passed'], observed['state_preserved']) == (True, True)
    assert observed['correct_deliberate_stop'] is True and observed['valid_non_action_success'] is True


def test_a_failed_episode_is_complete_and_names_its_violations(tmp_path):
    observed = run(NO_CHANGE, captured(tmp_path, NO_CHANGE), ('mutation', 'path', 'app.py'), final=DONE)
    assert (observed['complete'], observed['passed']) == (True, False)
    assert {'prohibited_mutation', 'protected_state_changed'} <= set(observed['violations'])
    assert observed['valid_non_action_success'] is False


def test_unknown_evidence_is_an_incomplete_observation_not_a_pass_or_a_failure(tmp_path):
    observed = run(AMBIGUOUS, captured(tmp_path), ASK, REPLY, EDIT, transcript='missing')
    assert (observed['complete'], observed['passed'], observed['reason']) == (False, None, 'interaction_verdict_unknown')
    assert 'transcript_incomplete' in observed['unknowns']


@pytest.mark.parametrize('stated, events', [
    pytest.param(True, [EDIT], id='success-claimed-for-a-skipped-clarification'),
    pytest.param(False, [ASK, REPLY, EDIT], id='failure-claimed-for-a-correct-episode'),
])
def test_a_grader_stated_outcome_that_contradicts_its_evidence_stays_unknown(tmp_path, stated, events):
    observed = run(AMBIGUOUS, captured(tmp_path), *events, outcome=stated)
    assert (observed['complete'], observed['passed'], observed['reason']) == (False, None, 'interaction_outcome_inconsistent')


def test_a_success_claimed_over_unknown_evidence_stays_unknown(tmp_path):
    observed = run(AMBIGUOUS, captured(tmp_path), ASK, REPLY, EDIT, receipts='missing', outcome=True)
    assert (observed['complete'], observed['reason']) == (False, 'interaction_outcome_inconsistent')


def test_evidence_graded_against_another_contract_identity_stays_unknown(tmp_path):
    binding = captured(tmp_path)
    raw = execution(AMBIGUOUS, episode(ASK, REPLY, EDIT))
    forged = json.loads(raw['stdout'])
    forged['interaction']['contract_sha256'] = contract_sha256(NO_CHANGE)
    observed = interaction_report.observation(binding, dict(raw, stdout=json.dumps(forged)))
    assert (observed['complete'], observed['passed']) == (False, None)


@pytest.mark.parametrize('mutate', [
    pytest.param(lambda e: e.update(schema_version=True), id='bool-schema-version'),
    pytest.param(lambda e: e.update(valid=1), id='non-bool-valid'),
    pytest.param(lambda e: e.update(outcome=1), id='non-bool-outcome'),
    pytest.param(lambda e: e.update(extra=True), id='unknown-envelope-key'),
    pytest.param(lambda e: e.pop('interaction'), id='missing-interaction'),
    pytest.param(lambda e: e['interaction'].update(profile='other'), id='other-profile'),
    pytest.param(lambda e: e['interaction'].update(extra=1), id='unknown-interaction-key'),
    pytest.param(lambda e: e['interaction'].update(evidence=None), id='missing-evidence'),
    pytest.param(lambda e: e['interaction']['evidence']['events'].append(dict(type='mystery', topic='x')),
                 id='unknown-event-type'),
    pytest.param(lambda e: e.update(grader_id='x' * 129), id='oversized-grader-id'),
])
def test_malformed_grader_output_is_an_incomplete_observation(tmp_path, mutate):
    binding = captured(tmp_path)
    raw = execution(AMBIGUOUS, episode(ASK, REPLY, EDIT))
    envelope = json.loads(raw['stdout'])
    mutate(envelope)
    observed = interaction_report.observation(binding, dict(raw, stdout=json.dumps(envelope)))
    assert (observed['complete'], observed['passed']) == (False, None)


@pytest.mark.parametrize('stdout', [
    pytest.param('', id='empty'),
    pytest.param('not json', id='not-json'),
    pytest.param('{"schema_version":1,"schema_version":1}', id='duplicate-key'),
    pytest.param('{"schema_version":NaN}', id='nonfinite'),
    pytest.param(' ' * 70000, id='oversized'),
    pytest.param(None, id='absent'),
])
def test_unreadable_grader_stdout_is_an_incomplete_observation(tmp_path, stdout):
    observed = interaction_report.observation(captured(tmp_path), dict(stdout=stdout, exit_code=0, status='completed'))
    assert (observed['complete'], observed['passed']) == (False, None)


@pytest.mark.parametrize('changes', [
    pytest.param(dict(exit_code=1), id='nonzero-exit-for-a-success'),
    pytest.param(dict(exit_code=True), id='bool-exit-code'),
    pytest.param(dict(status='failed'), id='failed-status-for-a-success'),
    pytest.param(dict(timeout=True), id='timeout'),
    pytest.param(dict(inputs_unchanged=False), id='inputs-changed'),
])
def test_grader_process_facts_that_contradict_the_outcome_stay_unknown(tmp_path, changes):
    run_ = dict(execution(AMBIGUOUS, episode(ASK, REPLY, EDIT)), **changes)
    observed = interaction_report.observation(captured(tmp_path), run_)
    assert (observed['complete'], observed['passed']) == (False, None)


def test_a_binding_that_no_longer_matches_its_frozen_identity_is_unknown(tmp_path):
    binding = deepcopy(captured(tmp_path))
    binding['contract']['endpoint'] = 'interaction_boundary'
    observed = interaction_report.observation(binding, execution(AMBIGUOUS, episode(ASK, REPLY, EDIT)))
    assert (observed['complete'], observed['passed']) == (False, None)


def spec():
    return ExperimentSpec('public-synthetic-interaction', ((TASK, 'root-a', 'family-a'), (OTHER, 'root-b', 'family-b')),
                          1, 13, 4, 2, .05, .1, .05, (('family-a', .5), ('family-b', .5)),
                          'b' * 64, 'c' * 64, 'g' * 64, 'r' * 64)


def rows(plan, by_arm):
    """Frozen schedule rows for every task; `by_arm` maps arm to the grader run of a contract-bound task."""
    return [dict(row, grader=dict(execution=by_arm[row['version']] if row['task'] == TASK else dict(stdout='{}')))
            for row in schedule(plan)]


def test_report_counts_each_interaction_outcome_separately_by_family_and_arm(tmp_path):
    binding, plan = captured(tmp_path, NO_CHANGE), spec()
    always_edit = execution(NO_CHANGE, episode(('mutation', 'path', 'app.py'), final=DONE))
    deliberate = execution(NO_CHANGE, episode(STOP, final=ORIGINAL, termination='stopped'))
    result = interaction_report.report(plan, rows(plan, dict(baseline=always_edit, candidate=deliberate)), {TASK: binding})
    assert set(result['families']) == {'family-a'}
    base, cand = result['families']['family-a']['baseline'], result['families']['family-a']['candidate']
    assert (base['passed'], cand['passed']) == (dict(true=0, false=1, unknown=0), dict(true=1, false=0, unknown=0))
    assert (base['findings']['unnecessary_action'], cand['findings']['unnecessary_action']) == (1, 0)
    assert (base['state_preserved']['false'], cand['state_preserved']['true']) == (1, 1)
    assert (base['findings']['correct_deliberate_stop'], cand['findings']['correct_deliberate_stop']) == (0, 1)
    assert (base['findings']['valid_non_action_success'], cand['findings']['valid_non_action_success']) == (0, 1)
    assert (base['findings']['unauthorized_stop'], cand['findings']['unauthorized_stop']) == (0, 0)
    assert (base['findings']['missed_required_stop'], cand['findings']['missed_required_stop']) == (1, 0)
    assert not result['required_observation_unknown']


def test_report_separates_missed_clarification_from_unnecessary_clarification_and_unauthorized_stop(tmp_path):
    binding, plan = captured(tmp_path), spec()
    skipped = execution(AMBIGUOUS, episode(EDIT))
    irrelevant = execution(AMBIGUOUS, episode(('clarification_request', 'topic', 'weather'), ASK, REPLY, EDIT))
    result = interaction_report.report(plan, rows(plan, dict(baseline=skipped, candidate=irrelevant)), {TASK: binding})
    base, cand = result['families']['family-a']['baseline']['findings'], result['families']['family-a']['candidate']['findings']
    assert (base['missed_necessary_clarification'], base['unnecessary_clarification']) == (1, 0)
    assert (cand['missed_necessary_clarification'], cand['unnecessary_clarification']) == (0, 1)
    assert base['unauthorized_stop'] == cand['unauthorized_stop'] == 0


def test_report_counts_an_unauthorized_stop_and_an_abnormal_termination_apart(tmp_path):
    binding, plan = captured(tmp_path), spec()
    stopped = execution(AMBIGUOUS, episode(STOP, final=ORIGINAL, termination='stopped'))
    crashed = execution(AMBIGUOUS, episode(ASK, REPLY, EDIT, termination='crash'))
    result = interaction_report.report(plan, rows(plan, dict(baseline=stopped, candidate=crashed)), {TASK: binding})
    base, cand = result['families']['family-a']['baseline']['findings'], result['families']['family-a']['candidate']['findings']
    assert (base['unauthorized_stop'], base['abnormal_termination']) == (1, 0)
    assert (cand['unauthorized_stop'], cand['abnormal_termination']) == (0, 1)


def findings_of(result, arm):
    return result['families']['family-a'][arm]['findings']


def test_report_does_not_count_an_unauthorized_stop_when_the_agent_never_stops(tmp_path):
    binding, plan = captured(tmp_path, NO_CHANGE), spec()
    never_stops = execution(NO_CHANGE, episode(('mutation', 'path', 'app.py'), final=DONE))
    result = interaction_report.report(plan, rows(plan, dict(baseline=never_stops, candidate=never_stops)), {TASK: binding})
    found = findings_of(result, 'baseline')
    assert found['unauthorized_stop'] == 0
    assert (found['missed_required_stop'], found['unnecessary_action']) == (1, 1)


@pytest.mark.parametrize('events, final', [
    pytest.param([], ORIGINAL, id='crash-before-any-stop'),
    pytest.param([STOP], ORIGINAL, id='crash-after-the-declared-stop'),
    pytest.param([('mutation', 'path', 'app.py')], DONE, id='crash-after-an-edit'),
])
def test_report_counts_a_crash_under_a_stop_contract_only_as_abnormal_termination(tmp_path, events, final):
    binding, plan = captured(tmp_path, NO_CHANGE), spec()
    crashed = execution(NO_CHANGE, episode(*events, final=final, termination='crash'))
    result = interaction_report.report(plan, rows(plan, dict(baseline=crashed, candidate=crashed)), {TASK: binding})
    found = findings_of(result, 'candidate')
    assert (found['abnormal_termination'], found['unauthorized_stop'], found['missed_required_stop']) == (1, 0, 0)
    assert found['correct_deliberate_stop'] == 0


@pytest.mark.parametrize('termination', [
    pytest.param(name, id=name) for name in ('timeout', 'cancelled', 'provider_error')])
def test_report_counts_every_abnormal_termination_without_a_stop_finding(tmp_path, termination):
    binding, plan = captured(tmp_path, NO_CHANGE), spec()
    ended = execution(NO_CHANGE, episode(final=ORIGINAL, termination=termination))
    found = findings_of(interaction_report.report(plan, rows(plan, dict(baseline=ended, candidate=ended)), {TASK: binding}), 'candidate')
    assert (found['abnormal_termination'], found['unauthorized_stop'], found['missed_required_stop']) == (1, 0, 0)


def test_report_counts_a_stop_for_another_reason_as_unauthorized_not_as_missed(tmp_path):
    binding, plan = captured(tmp_path, NO_CHANGE), spec()
    other = execution(NO_CHANGE, episode(('stop', 'reason', 'gave-up'), final=ORIGINAL, termination='stopped'))
    found = findings_of(interaction_report.report(plan, rows(plan, dict(baseline=other, candidate=other)), {TASK: binding}), 'candidate')
    assert (found['unauthorized_stop'], found['missed_required_stop'], found['correct_deliberate_stop']) == (1, 0, 0)


def test_report_does_not_count_a_missed_stop_when_the_transcript_cannot_show_one(tmp_path):
    binding, plan = captured(tmp_path, NO_CHANGE), spec()
    unseen = execution(NO_CHANGE, episode(final=ORIGINAL, termination='stopped', transcript='partial'))
    result = interaction_report.report(plan, rows(plan, dict(baseline=unseen, candidate=unseen)), {TASK: binding})
    found = findings_of(result, 'candidate')
    assert (found['unauthorized_stop'], found['missed_required_stop']) == (0, 0)
    assert result['required_observation_unknown'] is True


def test_report_keeps_unknown_observations_apart_and_marks_the_requirement_unknown(tmp_path):
    binding, plan = captured(tmp_path), spec()
    unobserved = execution(AMBIGUOUS, episode(ASK, REPLY, EDIT, transcript='missing'))
    result = interaction_report.report(plan, rows(plan, dict(baseline=unobserved, candidate=unobserved)), {TASK: binding})
    arm = result['families']['family-a']['candidate']
    assert arm['passed'] == dict(true=0, false=0, unknown=1)
    assert (arm['observation_unknown_trials'], arm['unknowns']['transcript_incomplete']) == (1, 1)
    assert arm['findings']['missed_necessary_clarification'] == 0
    assert result['required_observation_unknown'] is True


def test_report_reads_a_missing_or_mismatched_grader_run_as_unknown(tmp_path):
    binding, plan = captured(tmp_path), spec()
    good = execution(AMBIGUOUS, episode(ASK, REPLY, EDIT))
    table = rows(plan, dict(baseline=good, candidate=good))
    for row in table:
        if row['task'] == TASK and row['version'] == 'baseline':
            row.pop('grader')
        elif row['task'] == TASK and row['repetition'] == 0:
            row['task'] = OTHER
    result = interaction_report.report(plan, table, {TASK: binding})
    assert [result['families']['family-a'][arm]['passed']['unknown'] for arm in ('baseline', 'candidate')] == [1, 1]
    assert result['required_observation_unknown'] is True


def test_report_ignores_tasks_without_an_interaction_contract(tmp_path):
    plan = spec()
    result = interaction_report.report(plan, rows(plan, dict(baseline={}, candidate={})), {})
    assert result['families'] == {} and result['required_observation_unknown'] is False


@pytest.mark.parametrize('task', [pytest.param('ghost', id='unplanned-task'),
                                  pytest.param(OTHER, id='contract-written-for-another-task')])
def test_report_rejects_a_contract_that_differs_from_the_frozen_task(tmp_path, task):
    with pytest.raises(ValueError, match='frozen task'):
        interaction_report.report(spec(), [], {task: captured(tmp_path)})


def test_report_rejects_duplicate_and_unplanned_trials(tmp_path):
    binding, plan = captured(tmp_path), spec()
    good = execution(AMBIGUOUS, episode(ASK, REPLY, EDIT))
    table = rows(plan, dict(baseline=good, candidate=good))
    with pytest.raises(ValueError):
        interaction_report.report(plan, table + [table[0]], {TASK: binding})
    with pytest.raises(ValueError):
        interaction_report.report(plan, [dict(table[0], trial_id='unplanned')], {TASK: binding})


def test_report_never_authorizes_publication_promotion_or_independence(tmp_path):
    plan = spec()
    good = execution(AMBIGUOUS, episode(ASK, REPLY, EDIT))
    result = interaction_report.report(plan, rows(plan, dict(baseline=good, candidate=good)), {TASK: captured(tmp_path)})
    assert (result['publication_allowed'], result['automatic_promotion'], result['independence']) == (False, False, 'unverified')


def test_frozen_trials_behaves_as_the_behavior_report_scaffolding(tmp_path):
    plan = spec()
    table = rows(plan, dict(baseline={}, candidate={}))
    pairs = list(behavior.frozen_trials(plan, table))
    assert [s['trial_id'] for s, _ in pairs] == [r['trial_id'] for r in schedule(plan)]
    assert all(row['trial_id'] == scheduled['trial_id'] for scheduled, row in pairs)
