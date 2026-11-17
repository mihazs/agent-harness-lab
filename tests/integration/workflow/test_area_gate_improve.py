"""`ahl improve --area` and the per-area gate on a synthetic tagged bank and a synthetic runtime; no model or real project is read (#798).

The synthetic runtime solves a task when the harness text names the task's group (`coding` or `ux`); the two control tasks
(tool-recovery and adversarial-input) are solved by every harness, so each area keeps a calibration and a held-out task.
"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import config
from agent_harness_lab.cli import main
from tests.integration.evaluation.test_offline_evaluation import SOLVER
from tests.integration.workflow.test_one_call_bank import invoke, pinned, write_config  # noqa: F401  (pinned is a fixture)
from tests.support.area_bank import REGISTRY

# Starter task -> areas. localization and regression exercise coding, the next three ux-ui; tool-recovery is the calibration and
# adversarial-input the held-out control, and both belong to both areas.
TAGS = {'localization': ['coding'], 'regression': ['coding'], 'instruction-conflict': ['ux-ui'], 'skill-routing': ['ux-ui'],
        'resumption': ['ux-ui'], 'tool-recovery': ['coding', 'ux-ui'], 'adversarial-input': ['coding', 'ux-ui']}
AGENT = '''#!/usr/bin/env python3
import json,pathlib,sys
p=pathlib.Path
if '--version' in sys.argv: print('synthetic-area-runtime'); raise SystemExit(0)
sys.stdin.read()
if p('context.json').exists():
 c=json.loads(p('context.json').read_text())
 (p(c['candidate'])/'AGENTS.md').write_text((p(c['project'])/'candidate-harness').read_text())
 (p(c['evaluation'])/'proposal.json').write_text(json.dumps(dict(status='candidate',reason='synthetic',mechanisms=['synthetic'],falsifiers=['artifact'])))
else:
 inputs=json.loads(p('task.json').read_text())['inputs']
 group={'pairs':'coding','settings':'coding','values':'ux','requests':'ux','queue':'ux'}
 mine=next((group[key] for key in group if key in inputs),'control')
 if mine=='control' or mine in p('AGENTS.md').read_text().split(): exec(SOLVER)
 else: p('result.json').write_text('{}')
print(json.dumps(dict(type='turn.completed',usage=dict(output_tokens=1))))
'''.replace('exec(SOLVER)', 'exec(' + repr(SOLVER) + ')')


def retag(project, tags, registry):
    """Rewrite the bank with `tags` (task -> areas, None for untagged), repin its digest and declare `registry` (None for no areas)."""
    declared = json.loads(project.bank.read_text())
    for task in declared['tasks']:
        task.pop('areas', None)
        if tags is not None: task['areas'] = tags[task['task_id']]
    project.bank.write_text(json.dumps(declared))
    project.value['bank']['sha256'] = hashlib.sha256(project.bank.read_bytes()).hexdigest()
    project.value.pop('areas', None)
    if registry is not None: project.value['areas'] = registry
    write_config(project)


ROUTES = [pytest.param(('--single-pass',), id='single_pass'), pytest.param((), id='default_loop')]  # the loop is the default route (#974)
LOOPS = [pytest.param(False, id='single_pass'), pytest.param(True, id='default_loop')]  # the same two routes for `invoke`


def refused(tagged, route, *options):
    """Run `ahl improve` on a route and return the exit code of the typed input error it ends with (argparse exits through SystemExit)."""
    with pytest.raises(SystemExit) as stopped:
        main(['improve', str(tagged.project), *route, *options])
    return stopped.value.code


@pytest.fixture
def tagged(request):
    """The pinned synthetic bank, tagged with two areas and declared in ahl.json, and a runtime that follows the harness text."""
    project = request.getfixturevalue('pinned')  # the synthetic project of test_one_call_bank, imported above so pytest finds it
    retag(project, TAGS, REGISTRY)
    project.fixture.agent.write_text(AGENT)

    def go(baseline, candidate, *options, loop=False):
        (project.project / 'AGENTS.md').write_text(baseline)
        (project.project / 'candidate-harness').write_text(candidate)
        code, result = invoke(project, *options, loop=loop)
        return code, result, Path(result['output']) if 'output' in result else None
    project.go = go
    return project


def test_a_candidate_that_wins_ux_and_loses_coding_is_blocked_as_area_regressed_though_the_pooled_result_did_not_fall(tagged):
    code, result, output = tagged.go('coding', 'ux')
    assert (code, result['status'], result['reason']) == (3, 'blocked', 'area_regressed')
    gate = result['area_gate']
    assert (gate['status'], gate['regressed'], gate['not_measured']) == ('blocked', ['coding'], [])
    assert gate['remediation'] and gate['multiplicity']['areas_examined'] == 2
    assert result['before_after']['delta']['mean'] > 0 and result['before_after']['verdict'] != 'candidate_regressed'
    assert 'recommendation' not in result and (tagged.project / 'AGENTS.md').read_text() == 'coding'
    assert result['exit_receipt']['stage'] == 'trials' and result['exit_receipt']['next_step'].startswith('Read area_gate')
    artifact = json.loads((output / 'investigation.json').read_text())
    assert artifact['per_area']['coding']['delta'] < 0 < artifact['per_area']['ux-ui']['delta']


def test_the_same_candidate_without_declared_areas_keeps_the_ordinary_failed_evaluation(tagged):
    retag(tagged, None, None)
    code, result, _ = tagged.go('coding', 'ux')
    assert (result['status'], result['reason']) == ('evaluation_failed', 'evaluation_run_failed') and 'area_gate' not in result


def test_a_candidate_that_wins_everywhere_passes_the_gate_and_stays_a_recommendation_requiring_certification(tagged):
    code, result, _ = tagged.go('none', 'coding ux')
    assert (code, result['status'], result['recommendation']) == (3, 'inconclusive', 'candidate_requires_independent_certification')
    assert (result['area_gate']['status'], result['area_gate']['regressed'], result['area_gate']['reason']) == ('passed', [], None)
    assert {row['id']: row['state'] for row in result['area_gate']['areas']} == {'coding': 'scored', 'ux-ui': 'scored'}


@pytest.mark.parametrize('selected, tasks', [
    pytest.param(['coding'], 4, id='coding_and_the_two_shared_controls'),
    pytest.param(['ux-ui'], 5, id='ux_ui_and_the_two_shared_controls'),
    pytest.param(['coding', 'ux-ui'], 7, id='both_areas_run_the_whole_bank_once'),
])
def test_a_selection_runs_only_the_tasks_of_the_selected_areas_in_a_budget_sized_to_them(tagged, selected, tasks):
    options = [item for area in selected for item in ('--area', area)]
    code, result, output = tagged.go('none', 'coding ux', *options)
    manifest = json.loads((output / 'preparation/evaluation-0/manifest.json').read_text())
    assert (len(manifest['tasks']), manifest['max_trials']) == (tasks, 2 * tasks)
    assert manifest['bank_digest'] == tagged.value['bank']['sha256']
    assert len(json.loads((output / 'evaluation-0/results.json').read_text())['trials']) == 2 * tasks
    assert result['area_gate']['selected'] == selected and [row['id'] for row in result['area_gate']['areas']] == selected


def test_a_selection_leaves_the_tasks_of_other_areas_unrun_and_ungated(tagged):
    code, result, output = tagged.go('coding', 'ux', '--area', 'ux-ui')  # the coding-only tasks that this candidate would lose never run
    assert (result['status'], result['recommendation']) == ('inconclusive', 'candidate_requires_independent_certification')
    gate = result['area_gate']
    assert (gate['status'], gate['areas_examined'], gate['multiplicity'], [row['id'] for row in gate['areas']]) == ('passed', 1, None, ['ux-ui'])
    assert {trial['task'] for trial in json.loads((output / 'evaluation-0/results.json').read_text())['trials']} == {
        'instruction-conflict', 'skill-routing', 'resumption', 'tool-recovery', 'adversarial-input'}


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('options, field', [
    pytest.param(['--area', 'docs'], '--area[0]', id='undeclared'),
    pytest.param(['--area', 'coding', '--area', 'docs'], '--area[1]', id='undeclared_second'),
])
def test_an_undeclared_area_stops_before_any_model_call_and_names_the_position(tagged, route, options, field, capsys, monkeypatch):
    with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
        code = refused(tagged, route, *options)
    captured = capsys.readouterr()
    assert code == 2 and 'area_selection_undeclared' in captured.err + captured.out and field in captured.err + captured.out
    assert 'docs' not in captured.err.replace('area_selection_undeclared', '')


@pytest.mark.parametrize('route', ROUTES)
def test_a_repeated_area_is_a_typed_error_naming_the_position(tagged, route, capsys):
    code = refused(tagged, route, '--area', 'coding', '--area', 'coding')
    captured = capsys.readouterr()
    assert code == 2 and 'area_duplicate' in captured.err + captured.out and '--area[1]' in captured.err + captured.out


@pytest.mark.parametrize('route', ROUTES)
def test_a_selection_that_names_no_task_stops_with_a_typed_reason(tagged, route, capsys):
    retag(tagged, {name: ['coding'] for name in TAGS}, REGISTRY)
    code = refused(tagged, route, '--area', 'ux-ui')
    captured = capsys.readouterr()
    assert code == 2 and 'area_selection_empty' in captured.err + captured.out and '--area' in captured.err + captured.out


@pytest.mark.parametrize('loop', LOOPS)
def test_a_selection_without_a_calibration_and_held_out_task_stops_before_dispatch(tagged, loop):
    retag(tagged, {name: ['coding'] if name in ('tool-recovery', 'adversarial-input') else ['ux-ui'] for name in TAGS}, REGISTRY)
    code, result, _ = tagged.go('none', 'coding ux', '--area', 'ux-ui', loop=loop)
    assert (code, result['status'], result['reason']) == (3, 'blocked', 'area_selection_requires_development_regression_and_heldout')
    assert result['remediation'] and (tagged.project / 'AGENTS.md').read_text() == 'none'


@pytest.mark.parametrize('loop', LOOPS)
def test_a_selection_without_a_pinned_bank_stops_before_dispatch(tagged, loop):
    tagged.value['bank'] = config.defaults()['bank']
    write_config(tagged)
    code, result, _ = tagged.go('none', 'coding ux', '--area', 'coding', loop=loop)
    assert (code, result['status'], result['reason']) == (3, 'blocked', 'area_selection_requires_pinned_bank')
    assert 'ux-ui' not in result['remediation'] and result['remediation']
