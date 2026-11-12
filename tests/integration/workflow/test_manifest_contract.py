"""Synthetic construction-manifest contract: controller-owned digests, typed reasons and one bounded repair."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import re
import shutil
import time
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.evaluation import construction, manifest_contract as contract
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.working_state import WorkingState
from agent_harness_lab.workflow import improve, validate_manifest
from tests.integration.workflow import test_workflow as base

# The observed shape: artifacts and prompt per task, three controls with only task/state/fixture,
# per-task bundle declarations with path only, and the run limits an author tends to echo.
OLD_MANIFEST = "(evaluation/'manifest.json').write_text(json.dumps(dict(harness_paths=c['harness_paths'],skills=[],tasks=tasks,grader_self_tests=controls)))"
NEW_MANIFEST = '''oracle=evaluation/'oracle'; oracle.mkdir(exist_ok=True)
  (oracle/'grade.py.txt').write_text('# synthetic'); (oracle/'expected.json').write_text('{}')
  for t in tasks: t['artifacts']=['artifact']
  manifest=dict(harness_paths=c['harness_paths'],skills=[],tasks=tasks,grader_self_tests=controls,
   grader_bundles={t['id']:{'path':'oracle'} for t in tasks},max_trials=10,budget_seconds=60,no_nested_agents=True)
  mode=(project/'repair-mode').read_text() if (project/'repair-mode').exists() else ''
  seen=evaluation/'.seen'
  if mode=='twice' or mode=='field' and not seen.exists(): manifest.pop('grader_self_tests')
  with (evaluation/'prompts.txt').open('a') as log: log.write(prompt+'\\n=====\\n')
  seen.write_text('1')
  (evaluation/'manifest.json').write_text(json.dumps(manifest))'''
assert OLD_MANIFEST in base.FAKE
OLD_GRADER = '''grader.write_text("from pathlib import Path; import sys; w=Path(sys.argv[1]); assert (w/'artifact').read_text()=='done'; assert (w/'original').read_text()=='preserve'\\n")'''
NEW_GRADER = '''grader.write_text("from pathlib import Path; import json,sys; w=Path(sys.argv[1]); ok=(w/'artifact').read_text()=='done' and (w/'original').read_text()=='preserve'\\nprint(json.dumps(dict(schema_version=1,valid=True,outcome=ok,grader_id='synthetic',grader_version='v1'))); sys.exit(0 if ok else 1)\\n")'''
assert OLD_GRADER in base.FAKE, 'grader line moved'
AGENT = (base.FAKE.replace(OLD_GRADER, NEW_GRADER).replace(OLD_MANIFEST, NEW_MANIFEST).replace('folder.mkdir()', 'folder.mkdir(exist_ok=True)')
         .replace('control.mkdir()', 'control.mkdir(exist_ok=True)'))


@pytest.fixture
def wf():
    with ExitStack() as cleanups:
        fixture = base.WorkflowTests(cleanups.callback)
        fixture.agent.write_text(AGENT)
        yield fixture


def run_dir(args):
    return Path(args._run_output)


def test_path_only_bundles_complete_the_native_run_with_controller_computed_digests(wf):
    args = wf.args()
    result = improve(args)
    assert result['status'] == 'inconclusive', result
    evaluation = run_dir(args) / 'preparation/evaluation-0'
    frozen = json.loads((evaluation / 'manifest.json').read_text())
    assert all(b['digest'] == digest(evaluation / b['path']) for b in frozen['grader_bundles'].values())
    assert all(c['digest'] == digest(evaluation / c['fixture']) for c in frozen['grader_self_tests'])
    results = json.loads((Path(result['output']) / 'evaluation-0/results.json').read_text())
    assert results['inputs_unchanged'] and results['grader_bundle_digests'], results


def test_agent_supplied_control_digest_never_overrides_the_controller_value(wf):
    path, manifest = synthetic_manifest(wf.root)
    for control in manifest['grader_self_tests']: control['digest'] = 'f' * 64
    path.write_text(json.dumps(manifest))
    validated = validate_manifest(path, None, None, [], base_runner(), lambda: 60, set())
    assert all(c['digest'] == digest(path.parent / c['fixture']) for c in validated['grader_self_tests'])


def test_control_changed_between_author_output_and_validation_is_rejected(wf):
    path, manifest = synthetic_manifest(wf.root)
    snapshot = WorkingState(path.parent, time.monotonic() + 30).snapshot
    (path.parent / manifest['grader_self_tests'][0]['fixture'] / 'artifact').write_text('changed after freeze')
    with pytest.raises(ValueError, match='differs from actual author output'):
        validate_manifest(path, None, None, [], base_runner(), lambda: 60, set(), expected_snapshot=snapshot,
                          expected_deadline=time.monotonic() + 30)


def test_field_defect_is_repaired_once_and_recorded_in_the_receipt(wf):
    (wf.project / 'repair-mode').write_text('field')
    args = wf.args()
    result = improve(args)
    assert result['status'] == 'inconclusive', result
    repair = result['evaluation_construction_repairs']
    assert [(r['reason'], r['field'], r['outcome'], r['origin']) for r in repair] == [
        ('evaluation_manifest_field_missing', 'grader_self_tests', 'repaired', 'agent_output_defect')]
    prompts = (run_dir(args) / 'preparation/evaluation-0/prompts.txt').read_text().split('=====')
    assert len([p for p in prompts if p.strip()]) == 2 and 'evaluation_manifest_field_missing' in prompts[1]
    assert json.loads((Path(result['output']) / 'workflow.json').read_text())['evaluation_construction_repairs'] == repair


def test_second_defect_ends_the_run_with_the_typed_reason_and_one_repair(wf):
    (wf.project / 'repair-mode').write_text('twice')
    args = wf.args()
    with pytest.raises(contract.ContractError) as caught:
        improve(args)
    assert caught.value.reason == 'evaluation_manifest_field_missing' and caught.value.field == 'grader_self_tests'
    receipt = json.loads((run_dir(args) / 'workflow.json').read_text())
    assert receipt['status'] == 'invalid_input' and receipt['reason'] == 'evaluation_manifest_field_missing'
    assert receipt['contract_violation'] == dict(reason='evaluation_manifest_field_missing', stage='evaluation_construction',
        file='manifest.json', field='grader_self_tests', origin='agent_output_defect', next_step=caught.value.next_step)
    assert [r['outcome'] for r in receipt['evaluation_construction_repairs']] == ['requested']
    prompts = (run_dir(args) / 'preparation/evaluation-0/prompts.txt').read_text()
    assert prompts.count('=====') == 2  # the original pass and exactly one repair


def synthetic_manifest(root, name='manifest-case'):
    """A minimal manifest built only from the fields the construction prompt asks for, with tiny fixtures."""
    folder = Path(root) / name; folder.mkdir()
    (folder / 'oracle').mkdir(); (folder / 'oracle/grade.py.txt').write_text('# synthetic'); (folder / 'oracle/expected.json').write_text('{}')
    (folder / 'grade.py').write_text("import sys; sys.exit(0)\n")
    tasks, controls = [], []
    for split in contract.SPLITS:
        (folder / split).mkdir(); (folder / split / 'task.json').write_text(split)
        tasks.append(dict(id=split, split=split, fixture=split, prompt='Produce the artifact', artifacts=['artifact'],
                          grader=['/usr/bin/python3', str(folder / 'grade.py'), '{workspace}']))
        for state in contract.STATES:
            (folder / f'{split}-{state}').mkdir(); (folder / f'{split}-{state}' / 'artifact').write_text(state)
            controls.append(dict(task=split, state=state, fixture=f'{split}-{state}'))
    manifest = dict(skills=[], harness_paths=[], tasks=tasks, grader_self_tests=controls,
                    grader_bundles={t['id']: dict(path='oracle') for t in tasks})
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    return folder / 'manifest.json', manifest


def base_runner():
    from agent_harness_lab.cli import evaluator
    runner = evaluator()
    # Controls pass or fail by name; the synthetic grader program is never a real command.
    runner.execute = lambda argv, cwd, seconds, env=None: dict(timeout=False, exit_code=0 if 'complete' in argv[-1] else 1)
    runner.native_environment = lambda role: {}
    return runner


def minimal(controller_grader):
    """A manifest assembled from AUTHOR_FIELDS alone: the contract the prompts promise, nothing else."""
    a = contract.AUTHOR_FIELDS
    tasks = [{**{f: {'id': s, 'split': s, 'fixture': s, 'prompt': 'p', 'artifacts': ['a']}[f] for f in a['task']},
              **({} if controller_grader else {'grader': ['g', '{workspace}']})} for s in contract.SPLITS]
    controls = [{f: {'task': s, 'state': st, 'fixture': f'{s}-{st}'}[f] for f in a['control']}
                for s in contract.SPLITS for st in contract.STATES]
    return {'skills': [], 'harness_paths': [], 'tasks': tasks, 'grader_self_tests': controls,
            'grader_bundles': {s: {f: 'oracle' for f in a['bundle']} for s in contract.SPLITS}}


def deletions(manifest):
    """Every single-field deletion: top level, one task, one control, one bundle declaration."""
    for name in manifest:
        yield name, {k: v for k, v in manifest.items() if k != name}
    for name in manifest['tasks'][0]:
        value = copy.deepcopy(manifest); del value['tasks'][0][name]; yield f'tasks[0].{name}', value
    for name in manifest['grader_self_tests'][0]:
        value = copy.deepcopy(manifest); del value['grader_self_tests'][0][name]; yield f'grader_self_tests[0].{name}', value
    for name in manifest['grader_bundles']['development']:
        value = copy.deepcopy(manifest); del value['grader_bundles']['development'][name]
        yield f'grader_bundles[0].{name}', value


@pytest.mark.parametrize('controller_grader', [True, False])
def test_every_deleted_field_yields_a_typed_defect_naming_the_field(controller_grader):
    base_manifest = minimal(controller_grader)
    contract.check_authored(base_manifest, stage='evaluation_construction', controller_grader=controller_grader)
    seen = 0
    for field, value in deletions(base_manifest):
        if field.endswith('.grader') and controller_grader or not controller_grader and field in ('skills', 'harness_paths'): continue
        with pytest.raises(contract.ContractError) as caught:
            contract.check_authored(value, stage='evaluation_construction', controller_grader=controller_grader)
        error = caught.value
        assert error.reason in ('evaluation_manifest_field_missing', 'evaluation_manifest_controls_incomplete'), (field, error)
        assert error.stage == 'evaluation_construction' and error.file == 'manifest.json' and error.origin == contract.AGENT
        assert error.field and error.next_step and 'Next:' in str(error)
        assert not re.fullmatch(r"'[^']*'", str(error)), 'bare exception text'
        seen += 1
    assert seen >= 12


def test_deleted_fields_never_reach_validation_as_a_bare_key_error(wf):
    for field, value in deletions(synthetic_manifest(wf.root, 'fuzz-source')[1]):
        folder = Path(wf.root) / ('fuzz-' + re.sub(r'\W', '_', field)); folder.mkdir()
        path = folder / 'manifest.json'
        shutil.copytree(Path(wf.root) / 'fuzz-source', folder, dirs_exist_ok=True); path.write_text(json.dumps(value))
        try:
            with contract.boundary('manifest_validation'):
                validate_manifest(path, None, None, [], base_runner(), lambda: 60, set())
        except (ValueError, TimeoutError) as exc:  # a rejection or a typed internal error, never a bare lookup message
            assert not re.fullmatch(r"'[^']*'", str(exc)), (field, exc)


def test_unexpected_lookup_errors_become_internal_contract_errors_with_the_stage():
    for raised in (KeyError('digest'), TypeError('x'), AttributeError('x'), IndexError('x')):
        with pytest.raises(contract.ContractError) as caught, contract.boundary('evaluation'):
            raise raised
        assert caught.value.reason == 'internal_contract_error' and caught.value.stage == 'evaluation'
        assert caught.value.origin == contract.AHL and 'digest' not in str(caught.value)
    assert contract.typed(KeyError('digest')).reason == 'internal_contract_error'
    assert 'digest' not in str(contract.typed(KeyError('digest')))


def test_reason_table_is_complete_typed_and_free_of_exception_text():
    from agent_harness_lab.exit_guidance import REMEDIATION, summary
    for reason, (origin, repairable, remediation) in contract.REASONS.items():
        assert origin in (contract.AGENT, contract.AHL) and isinstance(repairable, bool)
        assert REMEDIATION[reason] == remediation and remediation.endswith('.')
        assert not re.search(r"Error|Traceback|'\w+'", remediation), remediation
        error = contract.ContractError(reason, 'evaluation_construction', 'tasks[0].id')
        text = str(error)
        for part in (f'{reason}:', 'stage=evaluation_construction', 'file=manifest.json', 'field=tasks[0].id', 'origin=', 'Next:'):
            assert part in text
        rendered = summary(dict(status='invalid_input', reason=reason, contract_violation=error.receipt()))
        assert 'Stage: evaluation_construction; file: manifest.json; field: tasks[0].id' in rendered
    source = Path(contract.__file__).read_text()
    assert 'str(exc)' not in source and 'repr(exc)' not in source


def test_construction_prompt_and_every_consumer_agree_on_the_authored_fields(wf):
    seen = {}
    manifest = minimal(True)
    # Prompt side: the controlled author prompt names every field the shared contract asks for.
    folder = Path(wf.root) / 'author-case'; folder.mkdir()
    def capture(client, backend, workspace, inputs, role, instructions, prompt, deadline):
        seen['controlled'] = instructions
        raise RuntimeError('prompt captured')
    with patch.object(construction, 'run_role', side_effect=capture), pytest.raises(RuntimeError):
        construction.author(Mock(), Mock(), folder, folder, Path(wf.root) / 'author-work', Path(wf.root) / 'author-out',
                            time.monotonic() + 30)
    for scope, text in (('controlled', seen['controlled']),):
        fields = set(contract.AUTHOR_FIELDS['manifest'] + contract.AUTHOR_FIELDS['task']
                     + contract.AUTHOR_FIELDS['control'] + contract.AUTHOR_FIELDS['bundle'])
        assert {f for f in fields if f not in text} == set(), scope
        assert contract.contract_text() in text
    args = wf.args(); improve(args)
    native = (run_dir(args) / 'preparation/evaluation-0/prompts.txt').read_text()
    assert contract.contract_text(controller_grader=False) in native
    # Consumer side: a manifest holding exactly the promised fields passes the shape check, and the validators
    # then add every controller-owned field without needing anything more from the agent.
    for owner in (True, False):
        contract.check_authored(minimal(owner), stage='evaluation_construction', controller_grader=owner)
    path, authored = synthetic_manifest(wf.root, 'sufficient')
    assert set(authored) == set(contract.AUTHOR_FIELDS['manifest']) and set(authored['tasks'][0]) - {'grader'} == set(contract.AUTHOR_FIELDS['task'])
    assert set(authored['grader_self_tests'][0]) == set(contract.AUTHOR_FIELDS['control'])
    validated = validate_manifest(path, None, None, [], base_runner(), lambda: 60, set())
    assert all(field in validated['grader_bundles']['development'] for field in contract.CONTROLLER_FIELDS['bundle'])
    assert all(field in validated['grader_self_tests'][0] for field in contract.CONTROLLER_FIELDS['control'])
    assert all(field in validated for field in contract.CONTROLLER_FIELDS['manifest'])
    assert manifest  # the minimal manifest is built from AUTHOR_FIELDS alone


@pytest.mark.parametrize('raised, expected', [
    (KeyError('digest'), 'internal_contract_error'),
    (contract.ContractError('evaluation_manifest_field_missing', 'evaluation_construction', 'tasks[0].prompt'), 'evaluation_manifest_field_missing')])
def test_cli_prints_a_typed_reason_and_never_a_bare_exception_message(wf, raised, expected, capsys):
    from agent_harness_lab.cli import main
    with patch('agent_harness_lab.workflow.improve', side_effect=raised), pytest.raises(SystemExit) as caught:
        main(['improve', str(wf.project), '--agent', 'codex', '--codex', str(wf.agent)])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert f'ahl: {expected}:' in err and "ahl: 'digest'" not in err
    assert 'origin=' in err and 'Next:' in err
