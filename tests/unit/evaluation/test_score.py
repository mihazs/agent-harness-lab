"""`ahl score`: one row per declared capability area, each with one evidence state; unknown is never a result (#797)."""
from dataclasses import replace
import json

import pytest

from agent_harness_lab.evaluation import score
from agent_harness_lab.evaluation.manifest_contract import PROJECT, ContractError
from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.evaluation.statistics import TrialRecord
from agent_harness_lab.evaluation.task_bank import inspect
from tests.support import score_fixture as fixture
from tests.support.area_bank import REGISTRY, TAGS

DOCS = dict(id='docs', label='Docs', description='Write and correct documentation.')
REVIEW = dict(id='review', label='Review', description='Find defects in a change.')
# One synthetic bank that can show every state: coding and ux-ui are tagged, docs has no task, and the one review task is unreviewed.
FOUR = dict(registry=[*REGISTRY, DOCS, REVIEW], tags={**TAGS, 'resumption': ['review']}, reviewed=list(TAGS))


def run(tmp_path, *, areas=(), sessions=None, issues=None, rows=None, only=None, min_roots=3, alpha=0.05, ungraded=(), critical=(),
        arms=('baseline', 'candidate'), registry=REGISTRY, tags=None, reviewed=fixture.ALL, **fields):
    root = fixture.project(tmp_path, registry=registry, tags=tags, reviewed=reviewed)
    spec = fixture.experiment(root, only=only, min_roots=min_roots, alpha=alpha, **fields)
    found = fixture.records(spec, ungraded=ungraded, critical=critical, arms=arms) if rows is None else rows(spec)
    issues = inspect(root / 'bank/bank.json', registry=registry)['issues'] if issues is None else issues
    return score.report(registry, fixture.bank_tasks(root), issues, spec, found, areas, sessions)


def by_id(result):
    return {row['id']: row for row in result['areas']}


def states(result):
    return {row['id']: (row['state'], row['reason']) for row in result['areas']}


def test_a_synthetic_bank_shows_every_evidence_state(tmp_path):
    result = run(tmp_path, ungraded=('skill-routing',), **FOUR)
    assert states(result) == {'coding': ('scored', None), 'ux-ui': ('provisional', 'unknown_outcomes'),
                              'docs': ('no_evidence', 'no_experiment_task'), 'review': ('blocked', 'bank_not_qualified')}


def test_a_scored_area_success_interval_equals_the_hand_computed_value(tmp_path):
    # Three roots, alpha 0.99: radius = sqrt(2 ln(2/0.99) * 3 * (1/3)^2) = 0.6846885. The baseline fails every task (point 0, lower
    # cut at 0) and the candidate passes every task (point 1, upper cut at 1).
    coding = by_id(run(tmp_path, alpha=0.99))['coding']['success']
    assert (coding['baseline']['success_rate'], coding['baseline']['lower'], coding['candidate']['success_rate'],
            coding['candidate']['upper']) == (0.0, 0.0, 1.0, 1.0)
    assert coding['baseline']['upper'] == pytest.approx(0.6846885016382495, abs=1e-12)
    assert coding['candidate']['lower'] == pytest.approx(0.31531149836175054, abs=1e-12)


def test_the_paired_delta_is_the_analysis_of_the_areas_own_roots(tmp_path):
    coding = by_id(run(tmp_path))['coding']['paired']
    assert (coding['delta'], coding['independent_roots']) == (1.0, 3)  # the whole experiment has six roots


def test_a_task_in_two_areas_counts_in_each_and_an_untagged_task_in_none(tmp_path):
    result = run(tmp_path)
    assert {row['id']: (row['tasks'], row['bank_tasks']) for row in result['areas']} == {'coding': (4, 4), 'ux-ui': (3, 3)}
    assert result['untagged_tasks'] == 1  # resumption; instruction-conflict is counted in both areas, not pooled


def test_an_unknown_outcome_is_never_read_as_a_pass_or_a_fail(tmp_path):
    ux = by_id(run(tmp_path, ungraded=('skill-routing',)))['ux-ui']
    baseline, candidate = ux['success']['baseline'], ux['success']['candidate']
    # The unknown root is left out: read as a pass the baseline would be 1/3, read as a fail the candidate 2/3.
    assert (baseline['success_rate'], candidate['success_rate'], baseline['unknown_roots'], baseline['independent_roots']) == (0.0, 1.0, 1, 2)
    assert (ux['state'], ux['paired']['status']) == ('provisional', 'incomplete') and 'delta' not in ux['paired']


def test_fewer_roots_than_the_plan_minimum_are_provisional_with_an_interval(tmp_path):
    coding = by_id(run(tmp_path, min_roots=4))['coding']  # coding has three roots
    assert (coding['state'], coding['reason'], coding['success']['baseline']['evidence']) == ('provisional', 'below_minimum_roots', 'provisional')


def test_an_area_tagged_in_the_bank_but_absent_from_the_experiment_has_no_evidence_not_a_zero(tmp_path):
    ux = by_id(run(tmp_path, only=('localization', 'regression', 'adversarial-input')))['ux-ui']
    assert (ux['state'], ux['reason'], ux['tasks'], ux['bank_tasks'], ux['success'], ux['paired']) == ('no_evidence', 'no_experiment_task', 0, 3, None, None)


def test_a_declared_area_without_a_task_is_no_evidence_with_no_number(tmp_path):
    docs = by_id(run(tmp_path, **FOUR))['docs']
    assert (docs['state'], docs['tasks'], docs['independent_roots'], docs['success'], docs['paired'], docs['critical_regressions']) == (
        'no_evidence', 0, 0, None, None, None)


def test_an_area_whose_every_outcome_is_ungraded_has_no_evidence(tmp_path):
    result = run(tmp_path, ungraded=('localization', 'regression', 'adversarial-input', 'instruction-conflict'))
    assert (by_id(result)['coding']['state'], by_id(result)['coding']['reason'], by_id(result)['coding']['success']) == (
        'no_evidence', 'no_graded_outcome', None)


def test_a_run_with_no_recorded_trial_leaves_every_area_without_evidence(tmp_path):
    result = run(tmp_path, rows=lambda spec: [])
    assert states(result) == {'coding': ('no_evidence', 'no_graded_outcome'), 'ux-ui': ('no_evidence', 'no_graded_outcome')}
    assert result['trials'] == dict(planned=14, recorded=0)


def test_a_baseline_only_record_set_is_scored_without_a_paired_result(tmp_path):
    result = run(tmp_path, arms=('baseline',))
    coding = by_id(result)['coding']
    assert (result['arms'], coding['state'], coding['paired'], coding['critical_regressions'], list(coding['success'])) == (
        ['baseline'], 'scored', None, None, ['baseline'])


def test_a_baseline_only_record_set_from_another_grader_is_blocked_not_scored(tmp_path):
    result = run(tmp_path, rows=lambda spec: fixture.records(spec, arms=('baseline',), grader_hash='z' * 64))
    assert states(result) == {'coding': ('blocked', 'provenance_mismatch'), 'ux-ui': ('blocked', 'provenance_mismatch')}
    assert all(row['success'] is None for row in result['areas'])


def test_a_critical_regression_is_counted_in_the_area_that_holds_the_task(tmp_path):
    result = by_id(run(tmp_path, critical=('localization',)))
    assert (result['coding']['critical_regressions'], result['coding']['paired']['status'], result['ux-ui']['critical_regressions']) == (1, 'rejected', 0)


def test_a_repeated_trial_id_is_unknown_and_never_one_of_its_rows(tmp_path):
    def twice(spec):
        rows, ids = fixture.records(spec), {t['trial_id'] for t in schedule(spec) if t['task'] == 'localization'}
        return rows + [next(row for row in rows if row.trial_id in ids)]  # one trial of a coding-only task, recorded twice
    result = run(tmp_path, rows=twice)
    assert result['trials']['recorded'] == 13
    assert states(result) == {'coding': ('provisional', 'unknown_outcomes'), 'ux-ui': ('scored', None)}


def test_a_record_the_plan_never_scheduled_is_not_counted(tmp_path):
    def stray(spec):
        return fixture.records(spec) + [TrialRecord('f' * 64, True, 'graded', 'executable_grader', **fixture.HASHES)]
    assert run(tmp_path, rows=stray)['trials'] == dict(planned=14, recorded=14)


@pytest.mark.parametrize('options, reason', [
    pytest.param(dict(rows=lambda spec: fixture.records(spec, grader_hash='z' * 64)), 'provenance_mismatch', id='records-from-another-grader'),
    pytest.param(dict(cohort_manifest_sha256='a' * 64), 'experiment_design_unsupported', id='cohort-plan'),
    pytest.param(dict(root_similarity_sha256='a' * 64), 'root_similarity_binding_missing_or_invalid', id='unbound-root-similarity'),
])
def test_an_experiment_the_fixed_analysis_cannot_bind_blocks_every_area_and_shows_no_number(tmp_path, options, reason):
    result = run(tmp_path, **options)
    assert states(result) == {'coding': ('blocked', reason), 'ux-ui': ('blocked', reason)}
    assert all(row['success'] is None and row['paired'] is None for row in result['areas'])


@pytest.mark.parametrize('options, blocked', [
    pytest.param(dict(reviewed=[task for task in TAGS if task != 'skill-routing']), {'ux-ui'}, id='unreviewed-task'),
    pytest.param(dict(issues=[dict(task='localization', status='task_quarantined')]), {'coding'}, id='quarantined-task'),
    pytest.param(dict(issues=[dict(status='independence_unverified', reason='group_overlap_not_enforced', tasks=['tool-recovery'])]),
                 {'ux-ui'}, id='overlap-naming-tasks'),
    pytest.param(dict(issues=[dict(status='independence_unverified', reason='prompt_similarity_incomplete')]),
                 {'coding', 'ux-ui'}, id='bank-wide-issue'),
])
def test_a_bank_not_qualified_for_an_areas_task_blocks_that_area_and_shows_no_number(tmp_path, options, blocked):
    result = run(tmp_path, **options)
    assert {row['id'] for row in result['areas'] if row['state'] == 'blocked'} == blocked
    assert all(row['success'] is None and row['reason'] == 'bank_not_qualified' for row in result['areas'] if row['state'] == 'blocked')


def test_an_experiment_task_outside_the_bank_is_untagged_and_counts_in_no_area(tmp_path):
    root = fixture.project(tmp_path)
    spec = fixture.experiment(root)
    result = score.report(REGISTRY, [t for t in fixture.bank_tasks(root) if t['task_id'] != 'localization'], [], spec, fixture.records(spec))
    assert (by_id(result)['coding']['tasks'], result['untagged_tasks']) == (3, 2)  # resumption was never tagged


def test_an_experiment_task_whose_root_differs_from_its_bank_declaration_is_rejected(tmp_path):
    root = fixture.project(tmp_path)
    spec = fixture.experiment(root)
    moved = replace(spec, tasks=tuple((task, 'elsewhere' if task == 'tool-recovery' else r, f) for task, r, f in spec.tasks))
    with pytest.raises(ValueError, match='does not match'):
        score.report(REGISTRY, fixture.bank_tasks(root), [], moved, [])


def test_the_overall_figure_is_shown_only_when_every_declared_area_is_scored(tmp_path):
    result = run(tmp_path)
    assert (result['overall_withheld'], result['overall']['state'], result['overall']['tasks']) == (None, 'scored', 7)


@pytest.mark.parametrize('options, why', [
    pytest.param(dict(ungraded=('skill-routing',)), 'not_every_area_scored', id='one-area-provisional'),
    pytest.param(dict(**FOUR), 'not_every_area_scored', id='one-area-without-evidence'),
    pytest.param(dict(areas=('coding',)), 'area_selection', id='narrowed-report'),
    pytest.param(dict(registry=[], tags={}), 'no_declared_area', id='no-declared-area'),
])
def test_the_overall_figure_is_withheld_with_its_reason_otherwise(tmp_path, options, why):
    result = run(tmp_path, **options)
    assert (result['overall'], result['overall_withheld']) == (None, why)


@pytest.mark.parametrize('areas, shown', [
    pytest.param(('ux-ui',), ['ux-ui'], id='one-area'),
    pytest.param(('ux-ui', 'coding'), ['coding', 'ux-ui'], id='declared-order-not-argument-order'),
    pytest.param((), ['coding', 'ux-ui'], id='no-selection'),
])
def test_area_selection_narrows_the_rows_in_declared_order(tmp_path, areas, shown):
    assert [row['id'] for row in run(tmp_path, areas=areas)['areas']] == shown


@pytest.mark.parametrize('areas, reason, field', [
    pytest.param(('design',), 'score_area_undeclared', '--area[0]', id='undeclared'),
    pytest.param(('coding', 'design'), 'score_area_undeclared', '--area[1]', id='undeclared-after-a-declared-one'),
    pytest.param(('coding', 'coding'), 'area_duplicate', '--area[1]', id='listed-twice'),
])
def test_a_selected_area_the_project_does_not_declare_is_a_typed_error_naming_the_position(tmp_path, areas, reason, field):
    with pytest.raises(ContractError) as caught:
        run(tmp_path, areas=areas)
    error = caught.value
    assert (error.reason, error.origin, error.file, error.field) == (reason, PROJECT, 'ahl.json', field)
    assert 'design' not in str(error) and 'design' not in str(error.receipt())


def test_the_report_is_deterministic_and_bounded_for_the_largest_registry(tmp_path):
    registry = [dict(id=f'area-{number}', label='Area', description='One declared area.') for number in range(32)]
    first, second = run(tmp_path / 'one', registry=registry, tags={}), run(tmp_path / 'two', registry=registry, tags={})
    assert first == second and {row['state'] for row in first['areas']} == {'no_evidence'}
    assert len(json.dumps(first)) < 16 * 1024


def test_the_markdown_report_shows_no_number_for_an_area_without_evidence(tmp_path):
    lines = score.markdown(run(tmp_path, **FOUR)).splitlines()
    docs = next(line for line in lines if line.startswith('| Docs'))
    assert docs.split(' | ')[1:3] == ['no_evidence', 'no_experiment_task'] and docs.count('n/a') == 5 and '0.000' not in docs


def test_a_report_narrowed_to_one_area_notes_that_it_was_read_out_of_several_examined(tmp_path):
    result = run(tmp_path, areas=('ux-ui',), **FOUR)
    assert (result['multiplicity']['areas_examined'], result['multiplicity']['correction']) == (4, 'none')
    assert 'Multiplicity: 4 areas were examined' in score.markdown(result)


def test_narrowed_report_keeps_multiplicity_and_advisory_sessions(tmp_path):
    result = run(tmp_path, areas=('ux-ui',), sessions=[], **FOUR)
    assert result['multiplicity']['areas_examined'] == 4
    assert result['advisory_sessions']['recorded'] == 0
    rendered = score.markdown(result)
    assert 'Multiplicity: 4 areas were examined' in rendered
    assert 'Advisory: recorded sessions' in rendered


def test_a_report_over_every_declared_area_carries_no_multiplicity_note(tmp_path):
    result = run(tmp_path, **FOUR)
    assert result['multiplicity'] is None and 'Multiplicity' not in score.markdown(result)
