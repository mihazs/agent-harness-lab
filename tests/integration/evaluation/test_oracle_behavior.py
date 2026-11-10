"""Sealed behavior observations with synthetic execution, not model compliance."""
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import agent_harness_lab
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.private_journal import record
from tests.integration.evaluation import test_oracle_history as history_fixtures
from tests.support.unittest_cleanups import run_cleanups

PACK = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/adversarial-v1'
SOLVER = '''import json
from pathlib import Path
inputs=json.loads(Path('task.json').read_text())['inputs']
values=inputs['values']; operation=inputs['operation']
answer=sum(values) if operation=='sum' else sorted(values) if operation=='sort' else len(values)
Path('result.json').write_text(json.dumps(dict(value=answer)))
'''


def fixture(test, *, candidate_suffix='', all_tasks=False):
    value = history_fixtures.OracleHistoryTests(); value.setUp(); test.addCleanup(run_cleanups, value)
    shutil.rmtree(value.root/'export')
    selected = None if all_tasks else ['repository-text-benign','repository-text-adversarial']
    value.manifest = export_manifest(PACK/'bank.json',value.root/'export',max_trials=28,task_ids=selected)
    value.all_bundles = dict(value.manifest['grader_bundles'])
    for task in value.manifest['tasks']:
        task['offline_executor'] = ['/usr/bin/python3','-I','/inputs/harness/solve.py']
    for arm in ('baseline','candidate'):
        (value.root/arm/'solve.py').write_text(SOLVER+(candidate_suffix if arm=='candidate' else ''))
    value.install_plan(value.manifest['tasks'])
    return value


@pytest.fixture
def cleanups():
    """Stands in for the TestCase that fixture() and OracleHistoryTests register cleanups with; runs them LIFO."""
    with ExitStack() as stack:
        yield SimpleNamespace(addCleanup=stack.callback)


def test_legitimate_completion_is_separate_from_final_effects_and_legacy_stays_v1(cleanups):
    value = fixture(cleanups)
    result,count = value.run_history(); assert count == 8
    inputs,_ = record(value.store.case(result['run_id'])/'inputs.json')
    assert inputs['schema_version'] == 3; assert inputs['bank_authority'] is None
    analysis = value.store.analyze(result['run_id']); report = analysis['behavior']
    assert not report['critical_candidate_effect']; assert not report['required_observation_unknown']
    for family in report['families'].values():
        for arm in family.values():
            assert arm['task_completed']['true'] == 2
            assert arm['effects']['sink_written']['false'] == 2
    assert report['unobserved']['model_compliance'] == 'unknown'
    legacy = history_fixtures.OracleHistoryTests(); legacy.setUp(); cleanups.addCleanup(run_cleanups, legacy)
    old,_ = legacy.run_history()
    assert record(legacy.store.case(old['run_id'])/'inputs.json')[0]['schema_version'] == 1
    assert 'behavior' not in legacy.store.analyze(old['run_id'])


def test_real_critical_candidate_effect_vetoes_even_when_legitimate_task_completes(cleanups):
    value = fixture(cleanups,candidate_suffix="Path('sink.txt').write_text('actual forbidden export')\n")
    result,_ = value.run_history()
    analysis = value.store.analyze(result['run_id'])
    assert (analysis['status'],analysis['reason']) == ('rejected','critical_regression')
    checked = value.store.checked(result['run_id']); inputs,spec,execution = checked[2:5]
    records = value.store.trials(inputs,spec,execution)
    assert sum(r.critical_regression for r in records) == 2
    candidate = analysis['behavior']['families']['state-integrity']['candidate']
    assert candidate['task_completed']['true'] == 2
    assert candidate['effects']['sink_written']['true'] == 2


@pytest.mark.parametrize('mode', [pytest.param('missing', id='missing'), pytest.param('wrong_policy', id='wrong_policy'),
                                  pytest.param('duplicate', id='duplicate')])


def test_missing_or_forged_grader_behavior_is_unknown_and_blocks_recommendation(cleanups, mode):
    value = fixture(cleanups)
    def execute(*args,**kwargs):
        run = value.execute(*args,**kwargs)
        if args[3]=='grader':
            envelope = json.loads(run['stdout'])
            if mode=='missing': del envelope['behavior']
            elif mode=='wrong_policy': envelope['behavior']['policy_sha256']='0'*64
            run['stdout'] = json.dumps(envelope)
            if mode=='duplicate': run['stdout']=run['stdout'][:-1]+',"behavior":null}'
        return run
    result,_ = value.run_history(execute)
    assert result['state'] == 'sealed'
    analysis = value.store.analyze(result['run_id'])
    assert (analysis['status'],analysis['reason']) == ('blocked','behavior_observation_incomplete')
    assert analysis['behavior']['required_observation_unknown']


def test_critical_effect_wins_over_missing_other_effect(cleanups):
    value = fixture(cleanups,candidate_suffix="Path('sink.txt').write_text('forbidden')\n")
    def execute(*args,**kwargs):
        run=value.execute(*args,**kwargs)
        if args[3]=='grader':
            envelope=json.loads(run['stdout'])
            if envelope['behavior']['effects']['sink_written']:
                envelope['behavior']['effects']['protected_state_changed']=None
                run['stdout']=json.dumps(envelope)
        return run
    result,_ = value.run_history(execute); analysis=value.store.analyze(result['run_id'])
    assert analysis['reason'] == 'critical_regression'
    assert analysis['behavior']['required_observation_unknown']


def test_frozen_policy_mutation_invalidates_guarded_reads(cleanups):
    value=fixture(cleanups); result,_=value.run_history()
    case=value.store.case(result['run_id']); inputs,_=record(case/'inputs.json')
    policy=next(iter(inputs['behavior_policies'].values()))
    path=case/'oracles'/policy['bundle_sha256']/'behavior.json'
    path.write_text('{}')
    for operation in (value.store.show,value.store.analyze,value.store.historical_plan):
        with pytest.raises(ValueError): operation(result['run_id'])


def test_baseline_critical_effect_is_reported_without_inventing_a_candidate_effect(cleanups):
    value=fixture(cleanups)
    (value.root/'baseline/solve.py').write_text(SOLVER+"Path('sink.txt').write_text('forbidden')\n")
    value.install_plan(value.manifest['tasks'])
    result,_=value.run_history(); analysis=value.store.analyze(result['run_id'])
    assert analysis['reason'] != 'critical_regression'
    assert not analysis['behavior']['critical_candidate_effect']
    family=analysis['behavior']['families']['state-integrity']
    assert family['baseline']['critical_trials'] == 2
    assert family['candidate']['critical_trials'] == 0


def test_schema3_remains_subject_to_monotone_oracle_quarantine(cleanups):
    value=fixture(cleanups); result,_=value.run_history()
    request,_=value.quarantine_record(); value.store.quarantine(request)
    analysis=value.store.analyze(result['run_id'])
    assert (analysis['status'],analysis['reason']) == ('blocked','oracle_quarantined')
    assert analysis['oracle_validity'] == 'invalidated'
    assert not analysis['publication_allowed']


def test_policy_lineage_mismatch_refuses_before_creating_history(cleanups):
    value=fixture(cleanups); task=value.manifest['tasks'][0]
    declaration=value.manifest['grader_bundles'][task['id']]
    folder=value.root/'export'/declaration['path']; path=folder/'behavior.json'
    policy=json.loads(path.read_text()); policy['root_id']='unrelated-root'
    path.write_text(json.dumps(policy)); declaration['digest']=digest(folder)
    value.install_plan(value.manifest['tasks'])
    with pytest.raises(ValueError, match='task lineage'): value.run_history()
    assert not value.store.root.exists()


@pytest.mark.parametrize('unrelated', [pytest.param(False, id='behavior-bank'), pytest.param(True, id='unrelated-behavior-file')])
def test_legacy_history_does_not_reinterpret_an_oracle_filename_as_new_authority(cleanups, unrelated):
    if not unrelated:
        value=fixture(cleanups)
    else:
        value=history_fixtures.OracleHistoryTests(); value.setUp(); cleanups.addCleanup(run_cleanups, value)
        for declaration in value.manifest['grader_bundles'].values():
            folder=value.root/'export'/declaration['path']
            (folder/'behavior.json').write_text('{"legacy_application_data":true}')
            declaration['digest']=digest(folder)
        value.install_plan(value.manifest['tasks'])
    # Construct the exact unbound input contract written by earlier controllers.
    with patch('agent_harness_lab.evaluation.oracle_history.behavior_policies',return_value={}):
        result,_=value.run_history()
    assert record(value.store.case(result['run_id'])/'inputs.json')[0]['schema_version'] == 1
    assert value.store.show(result['run_id'])['state'] == 'sealed'
    assert 'behavior' not in value.store.analyze(result['run_id'])
    assert value.store.historical_plan(result['run_id'])['state'] == 'sealed'


@pytest.mark.parametrize('profile, partition, split', [
    pytest.param('certification', 'certification', 'heldout', id='certification-heldout'),
    pytest.param('descriptive_smoke', 'calibration', 'development', id='smoke-calibration-development'),
    pytest.param('descriptive_smoke', 'development', 'heldout', id='smoke-development-heldout'),
])


def test_exposed_policy_cannot_be_relabelled_as_a_protected_task_or_experiment(cleanups, profile, partition, split):
    value=fixture(cleanups)
    for task in value.manifest['tasks']: task['split']=split
    value.manifest['experiment'].update(profile=profile,partition=partition)
    value.write_manifest()
    with pytest.raises(ValueError, match='public development'): value.run_history()
    assert not value.store.root.exists()


def test_bound_schema3_keeps_live_revocation_and_private_historical_plan_separate(cleanups):
    from tests.integration.evaluation import test_regression_admission as admissions
    admission=admissions.RegressionAdmission(cleanups.addCleanup)
    parent=json.loads(admission.parent.read_text())
    task=next(t for t in json.loads((PACK/'bank.json').read_text())['tasks'] if t['task_id']=='repository-text-benign')
    for key in ('fixture','grader_bundle'):
        destination=admission.parent.parent/task[key]; destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(PACK/task[key],destination)
    parent['tasks']=[task]; admission.parent.write_text(json.dumps(parent))
    bank=admission.bank(admission.accept())
    value=fixture(cleanups); shutil.rmtree(value.root/'export')
    value.manifest=export_manifest(bank,value.root/'export',task_ids=[task['task_id']])
    value.all_bundles=dict(value.manifest['grader_bundles'])
    value.manifest['tasks'][0]['offline_executor']=['/usr/bin/python3','-I','/inputs/harness/solve.py']
    value.install_plan(value.manifest['tasks'])
    result,_=value.run_history(); assert result['state'] == 'sealed', result
    inputs,_=record(value.store.case(result['run_id'])/'inputs.json')
    assert inputs['schema_version'] == 3
    assert inputs['bank_tasks']; assert inputs['bank_authority'] == value.manifest['bank_authority']
    shutil.rmtree(value.root/'export')
    assert value.store.analyze(result['run_id'])['bank_authority'] == 'current_eligible'
    origin=admission.fixture
    origin.store.review(origin.case_id,origin.review_file('quarantined'))
    with pytest.raises(ValueError): value.store.analyze(result['run_id'])
    historical=value.store.historical_plan(result['run_id'])
    assert historical['fixture_mode_evidence'] == 'pinned'
    assert historical['current_applicability'] == 'not_evaluated'
