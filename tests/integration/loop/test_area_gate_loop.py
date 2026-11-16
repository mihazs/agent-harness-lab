"""FrozenBankEvaluator and the real controller gate each paired round per area on a synthetic tagged bank (#798)."""
import json
from pathlib import Path

from agent_harness_lab.loop.adapters import FrozenBankEvaluator
from tests.support import frozen_bank as fb

BASE, GOOD = fb.sha('base'), fb.sha('good')
AREAS = dict(declared=['coding', 'ux-ui'])
# t0-t3 exercise coding and t4-t7 exercise ux-ui; each task is its own root.
TAGS = {f't{n}': ['coding'] if n < 4 else ['ux-ui'] for n in range(8)}


def tagged_evaluator(base, outcomes, areas=AREAS, tags=TAGS, **declaration):
    """An evaluator over a scripted runner and a bank whose scoring tasks carry `tags` (task -> areas, or untagged for None)."""
    manifest = fb.write_bank(base, 8, 0)
    declared = json.loads(manifest.read_text())
    for task in declared['tasks']:
        if tags is not None: task['areas'] = tags[task['id']]
    manifest.write_text(json.dumps(declared))
    runner = fb.ScriptedRunner(outcomes)
    return FrozenBankEvaluator(manifest, runner, lambda name: name, Path(base) / 'work', spec=fb.declare(**declaration), areas=areas), runner


def run_loop(base, outcomes, **options):
    evaluator, _ = tagged_evaluator(base, outcomes, **options)
    proposer = fb.Proposer(BASE, [GOOD], evaluator, mechanisms=('m1',))
    result, ledger = fb.drive(base, evaluator, proposer, evaluator.spec)
    return result, ledger, evaluator


# The baseline passes t0 and two ux-ui tasks (t4, t5); the candidate passes all of coding but only t4 of ux-ui, so the pooled
# score rises from 3/8 to 5/8 while ux-ui falls from 2/4 to 1/4.
BASELINE = fb.outcome_table({0, 4, 5})
WIN_CODING_LOSE_UX = fb.outcome_table({0, 1, 2, 3, 4})
WIN_EVERYWHERE = fb.outcome_table(range(8))


def test_a_candidate_that_wins_coding_and_loses_ux_ui_is_stopped_as_an_area_regression_though_the_pooled_score_rose(tmp_path):
    result, ledger, evaluator = run_loop(tmp_path, {BASE: BASELINE, GOOD: WIN_CODING_LOSE_UX})
    gate = evaluator.runs[1]['area_gate']
    assert (gate['status'], gate['regressed'], gate['reason']) == ('blocked', ['ux-ui'], 'area_regressed')
    assert evaluator.runs[1]['evaluation'].point > evaluator.runs[0]['evaluation'].point
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('regressed', 'regression', BASE)
    decided = [payload for kind, payload in ledger.events() if kind == 'decided']
    assert [payload['regression'] for payload in decided] == ['area']


def test_the_loop_stops_on_the_area_regression_whatever_the_regression_policy(tmp_path):
    result, _, _ = run_loop(tmp_path, {BASE: BASELINE, GOOD: WIN_CODING_LOSE_UX}, on_regression='rollback_continue')
    assert (result['outcome'], result['stop']) == ('regressed', 'regression')


def test_a_candidate_that_wins_everywhere_is_not_stopped_by_the_gate(tmp_path):
    result, _, evaluator = run_loop(tmp_path, {BASE: fb.outcome_table({0, 4}), GOOD: WIN_EVERYWHERE})
    assert (evaluator.runs[1]['area_gate']['status'], evaluator.runs[1]['area_gate']['regressed']) == ('passed', [])
    assert evaluator.runs[1]['evaluation'].regressed_areas == () and result['stop'] != 'regression'
    assert result['best']['snapshot'] == GOOD


def test_a_project_that_declares_no_area_scores_the_same_candidate_without_a_gate(tmp_path):
    result, _, evaluator = run_loop(tmp_path, {BASE: BASELINE, GOOD: WIN_CODING_LOSE_UX}, areas=None)
    assert (evaluator.runs[1]['area_gate'], evaluator.runs[1]['evaluation'].regressed_areas) == (None, ())
    assert result['stop'] != 'regression'


def test_the_baseline_only_run_is_never_gated(tmp_path):
    evaluator, _ = tagged_evaluator(tmp_path, {BASE: BASELINE})
    evaluator.evaluate(BASE, evaluator.frozen_inputs)
    assert evaluator.runs[0]['area_gate'] is None


def test_an_untagged_bank_leaves_every_area_unmeasured_and_stops_nothing(tmp_path):
    result, _, evaluator = run_loop(tmp_path, {BASE: BASELINE, GOOD: WIN_CODING_LOSE_UX}, tags=None)
    gate = evaluator.runs[1]['area_gate']
    assert (gate['status'], gate['not_measured'], gate['regressed']) == ('not_measured', ['coding', 'ux-ui'], [])
    assert result['stop'] != 'regression'


def test_a_selection_gates_only_the_selected_area(tmp_path):
    result, _, evaluator = run_loop(tmp_path, {BASE: BASELINE, GOOD: WIN_CODING_LOSE_UX}, areas=dict(AREAS, selected=['coding']))
    assert (evaluator.runs[1]['area_gate']['status'], evaluator.runs[1]['area_gate']['selected']) == ('passed', ['coding'])
    assert result['stop'] != 'regression'


def test_an_unmeasured_area_is_never_a_regression_when_its_outcomes_are_unknown(tmp_path):
    unknown = {name: 'execution_error' if name in ('t4', 't5', 't6', 't7') else status for name, status in WIN_CODING_LOSE_UX.items()}
    _, _, evaluator = run_loop(tmp_path, {BASE: BASELINE, GOOD: unknown})
    gate = evaluator.runs[1]['area_gate']
    assert (gate['status'], gate['regressed'], gate['not_measured']) == ('passed', [], ['ux-ui'])
