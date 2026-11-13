"""Public CLI tests use independently synthetic projects; no model or network."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent_harness_lab

from agent_harness_lab.cli import main
from agent_harness_lab import config
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.one_call import structural_candidate
from tests.support.one_call import one as one


def test_single_call_applies_verified_contract_and_is_idempotent(one):
    with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no model')):
        code,result=one.invoke('--repair-contracts')
    assert code == 0
    assert result['status'] == 'applied'
    assert one.skill.read_bytes() == one.valid
    assert one.skill.stat().st_mode&0o777 == 0o640
    journal=Journal(one.project,result['journal'])
    assert journal.load(result['deployment'])['status'] == 'committed'
    assert journal.load(result['deployment'])['metadata']['plan_sha256'] == result['plan_sha256']
    assert not result['behavioral_certification']
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    plan=json.loads(Path(result['plan']).read_bytes())
    from agent_harness_lab.harness_change_plan import HarnessChangePlan
    assert HarnessChangePlan.parse(plan['harness_change_plan']).sha256 == result['plan_sha256']
    for value in (result,plan):
        forged=dict(value,publication_allowed=True,provenance=dict(source_kind='synthetic',
            approved_publication=True,source_ref='synthetic:fixture',derived_from_private=False))
        assert not public_export_allowed([forged])
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['rollback',result['deployment'],'--project',str(one.project)]) == 0
    assert one.skill.read_bytes() == b'\xef\xbb\xbf'+one.valid
    one.invoke('--repair-contracts')
    assert one.invoke('--repair-contracts')[1]['status'] == 'no_change'


@pytest.mark.parametrize('error,expected', [(errno.EROFS, 'target_filesystem_read_only'), (errno.EACCES, None)])
def test_journal_write_error_preserves_target_and_reports_only_read_only(one, error, expected):
    original = one.skill.read_bytes(), one.skill.stat().st_mode & 0o777
    open_file = os.open
    def fault(path, flags, *args, **kwargs):
        if str(path).startswith(str(one.skill) + '.ahl-'):
            raise OSError(error, os.strerror(error), str(path))
        return open_file(path, flags, *args, **kwargs)
    with patch('agent_harness_lab.promotion.journal.os.open', side_effect=fault):
        code, result = one.invoke('--repair-contracts')
    assert code != 0 and result['status'] == 'execution_error'
    assert result.get('reason') == (expected or 'execution_failed') and result['changed_files'] == []  # typed, never null (#837)
    assert (one.skill.read_bytes(), one.skill.stat().st_mode & 0o777) == original


def test_read_only_state_directory_is_not_reported_as_target(one):
    original = one.skill.read_bytes()
    error = OSError(errno.EROFS, 'read-only', str(one.root/'state/journal'))
    with patch.object(Journal, 'prepare', side_effect=error):
        code, result = one.invoke('--repair-contracts')
    assert code != 0 and result['status'] == 'execution_error'
    assert result['reason'] == 'execution_failed' != 'target_filesystem_read_only' and result['changed_files'] == []  # the read-only target is not claimed (#837)
    assert one.skill.read_bytes() == original


def test_real_repair_reports_measured_budget_after_controller_started(one, capsys):
    code, result = one.invoke('--repair-contracts', '--timeout', '60')
    assert (code, result['status']) == (0, 'applied')
    summary = capsys.readouterr().err
    assert 'budget_spent_seconds=' in summary
    assert 'budget_spent_seconds=unknown' not in summary
    assert 'remaining_seconds=unknown' not in summary
    assert 'trials_spent=unknown' in summary


def test_skill_whose_description_mentions_hooks_is_still_repaired(one):
    valid=one.valid.replace(b'Use for synthetic checks.',b'Use to review git hooks and React hooks in synthetic checks.')
    one.skill.write_bytes(b'\xef\xbb\xbf'+valid)
    code,result=one.invoke('--repair-contracts')
    assert (code,result['status']) == (0,'applied')
    assert one.skill.read_bytes() == valid


def test_two_operations_share_one_plan_and_preserve_unrelated_changes(one):
    second=one.project/'.agents/skills/second/SKILL.md';second.parent.mkdir()
    second.write_bytes(b'\xef\xbb\xbf'+one.valid.replace(b'name: example',b'name: second'))
    unrelated=one.project/'dirty.txt';unrelated.write_text('user change')
    code,result=one.invoke('--repair-contracts')
    assert (code,result['status']) == (0,'applied')
    plan=json.loads(Path(result['plan']).read_bytes())
    assert len(plan['harness_change_plan']['operations']) == 2
    assert len(result['changed_files']) == 2
    assert unrelated.read_text() == 'user change'
    assert not second.read_bytes().startswith(b'\xef\xbb\xbf')
    with contextlib.redirect_stdout(io.StringIO()):main(['rollback',result['deployment'],'--project',str(one.project)])
    assert second.read_bytes().startswith(b'\xef\xbb\xbf')
    assert unrelated.read_text() == 'user change'


def assert_unresolved_batch_is_review_only(one, second=None):
    one.skill.chmod(0o750)
    paths=[one.skill]+([second] if second else [])
    before={path:(path.read_bytes(),path.stat().st_mode&0o777) for path in paths}
    with patch.object(Journal,'prepare',side_effect=AssertionError('unresolved batch must not prepare a journal')):
        code,result=one.invoke('--repair-contracts')
    assert (code,result['status']) == (3,'blocked')
    assert result['reason'] == 'unresolved_operation_requires_hook_contract'
    assert result['changed_files'] == []
    from agent_harness_lab.harness_change_plan import HarnessChangePlan
    plan=json.loads(Path(result['plan']).read_bytes())
    common=HarnessChangePlan.parse(plan['harness_change_plan'])
    assert plan['plan_sha256'] == common.sha256
    assert result['plan_sha256'] == common.sha256
    assert common.document()['unresolved_operations'] == [str(one.skill.relative_to(one.project))]
    assert len(common.operations) == len(paths)
    for path,state in before.items():
        assert (path.read_bytes(),path.stat().st_mode&0o777) == state
    assert not (one.root/'state/promotions').exists()


def test_executable_skill_contract_repair_requires_hook_contract(one):
    assert_unresolved_batch_is_review_only(one)


def test_mixed_unresolved_batch_does_not_apply_nonexecutable_subset(one):
    second=one.project/'.agents/skills/second/SKILL.md';second.parent.mkdir()
    second.write_bytes(b'\xef\xbb\xbf'+one.valid.replace(b'name: example',b'name: second'))
    second.chmod(0o640)
    assert_unresolved_batch_is_review_only(one, second)


def test_foreign_mode_change_during_transaction_rejects_plan(one):
    instruction=one.project/'AGENTS.md';instruction.write_text('unchanged instruction')
    instruction.chmod(0o644)
    write=Journal._write_snapshot
    def changed(journal,*args,**kwargs):
        instruction.chmod(0o600)
        return write(journal,*args,**kwargs)
    before=one.skill.read_bytes()
    with patch.object(Journal,'_write_snapshot',changed),pytest.raises(SystemExit):one.invoke('--repair-contracts')
    assert one.skill.read_bytes() == before
    assert instruction.stat().st_mode&0o777 == 0o600


def test_foreign_similarly_named_staging_file_cannot_be_hidden(one):
    prepare=Journal.prepare
    foreign=one.skill.with_name('SKILL.md.ahl-foreign')
    def extra(journal,*args,**kwargs):
        record=prepare(journal,*args,**kwargs)
        foreign.write_text('foreign change')
        return record
    before=one.skill.read_bytes()
    with patch.object(Journal,'prepare',extra),pytest.raises(SystemExit):one.invoke('--repair-contracts')
    assert one.skill.read_bytes() == before
    assert foreign.read_text() == 'foreign change'


def test_owned_staging_mode_mismatch_is_rejected_before_replace(one):
    chmod=Path.chmod
    staged=[]
    def wrong_mode(path,mode,*args,**kwargs):
        if path.parent==one.skill.parent and path.name.startswith(one.skill.name+'.ahl-'):
            staged.append(path)
            return chmod(path,0o600,*args,**kwargs)
        return chmod(path,mode,*args,**kwargs)
    before=one.skill.read_bytes()
    with patch.object(Path,'chmod',wrong_mode),pytest.raises(SystemExit):one.invoke('--repair-contracts')
    assert len(staged) == 1
    assert one.skill.read_bytes() == before
    assert not staged[0].exists()


def test_recommendation_config_never_applies_contract_patch(one):
    settings=config.defaults(); settings['harness_paths']=['.agents/skills/example/SKILL.md']
    config.create(one.project,settings)
    before=one.skill.read_bytes()
    code,result=one.invoke('--repair-contracts')
    assert code == 3
    assert result['reason'] == 'configuration_requires_recommendation_only'
    assert one.skill.read_bytes() == before


@pytest.mark.parametrize('header', [
    pytest.param(b'name: wrong\ndescription: Example.\n', id='mismatched-name'),
    pytest.param(b'name: example\ndescription: Example.\nhooks: value\n', id='executable-hooks'),
    pytest.param(b'name: example\nname: example\ndescription: Example.\n', id='duplicate-name'),
])
def test_unsupported_executable_or_ambiguous_metadata_is_not_repaired(header):
    raw=b'\xef\xbb\xbf---\n'+header+b'---\nBody\n'
    assert structural_candidate(raw,'example') is None


def test_source_symlink_is_not_modified(one):
    one.skill.unlink(); external=one.root/'external'; external.write_bytes(b'\xef\xbb\xbf'+one.valid)
    one.skill.symlink_to(external)
    with pytest.raises(SystemExit): one.invoke('--repair-contracts')
    assert external.read_bytes().startswith(b'\xef\xbb\xbf')


def test_ambiguous_runtime_blocks_before_inference(one):
    with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'):
        code,result=one.invoke()
    assert code == 3
    assert result['reason'] == 'select_one_runtime_with_agent'


def test_noninteractive_ambiguous_runtime_does_not_prompt_or_dispatch(one):
    before = one.skill.read_bytes()
    with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'), \
            patch('builtins.input', side_effect=AssertionError('no prompt')), \
            patch('sys.stdin.read', side_effect=AssertionError('no input')), \
            patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
        code, result = one.invoke('--agent', 'auto')
    assert (code, result['status'], result['reason']) == (3, 'blocked', 'select_one_runtime_with_agent')
    assert '--agent' in result['remediation']
    assert result['preflight_plan']['choice'] == dict(option='--agent', values=['codex', 'claude'], remember_option='--remember-runtime')
    assert result['preflight_plan']['runtime_selection'] == dict(policy='ask_once', runtime=None, host=None)
    assert 'authorizes the bounded default workflow' in result['preflight_plan']['authorization']
    assert one.skill.read_bytes() == before


@pytest.mark.parametrize('marker,host', [('CLAUDECODE', 'claude'), ('CODEX_THREAD_ID', 'codex')])
def test_ambiguous_runtime_resolves_to_the_invoking_host(one, monkeypatch, marker, host):
    monkeypatch.setenv(marker, '1')
    from agent_harness_lab.one_call import preflight
    args = SimpleNamespace(agent='auto', codex='codex', claude='claude', trial_limit=config.DEFAULT_TRIALS)
    with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'):
        assert preflight(args) is None
    assert (args.agent, args._runtime_selection) == (host, dict(policy='invoking_host', runtime=host))


def test_both_host_markers_or_an_explicit_agent_do_not_guess(one, monkeypatch):
    monkeypatch.setenv('CLAUDECODE', '1'); monkeypatch.setenv('CODEX_THREAD_ID', 'x')
    with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'):
        assert one.invoke()[1]['reason'] == 'select_one_runtime_with_agent'


def test_single_runtime_is_selected_and_recorded(one):
    from agent_harness_lab.one_call import preflight
    args = SimpleNamespace(agent='auto', codex='codex', claude='claude', trial_limit=config.DEFAULT_TRIALS)
    with patch('agent_harness_lab.one_call.shutil.which', side_effect=lambda name: '/x' if name == 'claude' else None):
        assert preflight(args) is None
    assert (args.agent, args._runtime_selection) == ('claude', dict(policy='single_installed', runtime='claude'))


def test_blocked_preflight_gives_a_reason_specific_remediation(one):
    from agent_harness_lab.one_call import REMEDIATION, AUTHORIZATION
    with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'):
        _, ambiguous=one.invoke()
    config.create(one.project,config.defaults())
    _, offline=one.invoke()
    assert ambiguous['remediation'] == REMEDIATION['select_one_runtime_with_agent']
    assert offline['remediation'] == REMEDIATION['configured_profile_does_not_allow_cooperative_improvement']
    assert ambiguous['remediation'] != offline['remediation']
    assert ambiguous['preflight_plan'] == dict(requested_runtime='auto', observed_runtimes=['codex', 'claude'],
        configured_scope=None, limits=dict(seconds=2700, trials=config.DEFAULT_TRIALS),
        runtime_selection=dict(policy='ask_once', runtime=None, host=None),
        choice=dict(option='--agent', values=['codex', 'claude'], remember_option='--remember-runtime'),
        next_step=ambiguous['remediation'], authorization=AUTHORIZATION, candidate_prepared=False, evidence_prepared=False, execution_authority=False)
    plan = offline['preflight_plan']
    assert plan['observed_runtimes'] is None
    assert plan['configured_scope'] == []
    assert plan['next_step'] == offline['remediation']
    assert json.loads((Path(offline['output'])/'workflow.json').read_text())['preflight_plan'] == plan


def test_every_preflight_reason_has_its_own_remediation():
    import inspect, re
    from agent_harness_lab import one_call
    reasons=set(re.findall(r"return '([a-z_]+)'",inspect.getsource(one_call.preflight)))
    assert reasons <= set(one_call.REMEDIATION)
    assert len(set(one_call.REMEDIATION.values())) == len(one_call.REMEDIATION)
    assert one_call.DEFAULT_REMEDIATION not in one_call.REMEDIATION.values()


@pytest.mark.parametrize('reason',['configured_profile_does_not_allow_cooperative_improvement','configured_bank_requires_cooperative_runtime'])
def test_runtime_remediation_names_runtime_and_executable(reason):
    # profile or runtime.name alone leaves config.validate rejecting an explicit runtime without an absolute executable.
    from agent_harness_lab.one_call import REMEDIATION
    assert 'executable' in REMEDIATION[reason]
    assert 'runtime' in REMEDIATION[reason]
    assert 'absolute' in REMEDIATION[reason]


def test_offline_configuration_blocks_before_inference(one):
    config.create(one.project,config.defaults())
    code,result=one.invoke()
    assert code == 3
    assert result['reason'] == 'configured_profile_does_not_allow_cooperative_improvement'


def test_fresh_configuration_uses_documented_trial_default_without_writing(one):
    from argparse import Namespace
    from agent_harness_lab.one_call import resolve
    args=Namespace(project=one.project,timeout=None,agent='auto',codex='codex',claude='claude')
    resolve(args,set())
    assert args.trial_limit == config.defaults()['limits']['trials']
    assert not (one.project/'ahl.json').exists()


def test_configuration_and_cli_precedence(one):
    from argparse import Namespace
    from agent_harness_lab.one_call import resolve
    settings=config.prepare(dict(profile='cooperative',runtime=dict(name='codex',executable='/synthetic/codex'), limits=dict(seconds=100,trials=8)))
    config.create(one.project,settings)
    args=Namespace(project=one.project,timeout=None,agent='auto',codex='codex',claude='claude')
    resolve(args,set())
    assert (args.agent,args.codex,args.timeout,args.trial_limit) == ('codex','/synthetic/codex',100,8)
    args=Namespace(project=one.project,timeout=20,agent='claude',codex='/explicit/codex',claude='claude')
    resolve(args,{'--codex','--timeout','--agent'})
    assert (args.agent,args.codex,args.timeout) == ('claude','/explicit/codex',20)


@pytest.mark.parametrize('flag', ['--agent', '--ag'])
def test_explicit_auto_runtime_overrides_config_with_abbreviation(one, flag):
    settings=config.prepare(dict(profile='cooperative',runtime=dict(name='codex',executable='/synthetic/codex')))
    config.create(one.project,settings)
    with patch('agent_harness_lab.workflow.improve', return_value={'status': 'no_change'}) as run:
        one.invoke(flag, 'auto')
    assert run.call_args.args[0].agent == 'auto'


@pytest.mark.parametrize('route, target', [(['watch'], 'watch'), (['skills', 'invoke'], 'improve')])
def test_abbreviated_runtime_override_reaches_watch_and_skill(one, route, target):
    settings=config.prepare(dict(profile='cooperative',runtime=dict(name='codex',executable='/synthetic/codex')))
    config.create(one.project,settings)
    result = 0 if target == 'watch' else {'status': 'no_change'}
    with patch('agent_harness_lab.workflow.'+target, return_value=result) as run:
        with contextlib.redirect_stdout(io.StringIO()):
            main([*route, str(one.project), '--ag=auto', '--cod=/synthetic/override'])
    args=run.call_args.args[0]
    assert (args.agent, args.codex) == ('auto', '/synthetic/override')


def test_selected_layout_is_actual():
    import os
    expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected:
        assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())


@pytest.mark.parametrize('change, error', [
    pytest.param('none', KeyboardInterrupt, id='none-KeyboardInterrupt'),
    pytest.param('none', OSError, id='none-OSError'),
    pytest.param('body', KeyboardInterrupt, id='body-KeyboardInterrupt'),
    pytest.param('mode', OSError, id='mode-OSError'),
    pytest.param('readback', KeyboardInterrupt, id='readback-KeyboardInterrupt'),
    pytest.param('journal', OSError, id='journal-OSError'),
])
def test_postcommit_record_failure_reports_actual_committed_repair(one, change, error):
    from agent_harness_lab import workflow
    original=workflow.write_json
    failed=[]
    def record(path,value):
        if path.name=='workflow.json' and value.get('status')=='applied' and not failed:
            assert one.skill.read_bytes() == one.valid
            if change=='body':one.skill.write_bytes(b'foreign edit')
            if change=='mode':one.skill.chmod(0o600)
            if change=='readback':one.skill.unlink();one.skill.mkdir(mode=0o700)
            if change=='journal':(Path(value['journal'])/'active.json').unlink()
            failed.append(path);raise error('synthetic post-commit failure')
        return original(path,value)
    with patch.object(workflow,'write_json',side_effect=record):
        code,result=one.invoke('--repair-contracts')
    assert len(failed) == 1
    assert json.loads(failed[0].read_bytes()) == result
    assert Journal(one.project,result['journal']).load(result['deployment'])['status'] == 'committed'
    if change=='journal':assert Journal(one.project,result['journal']).active is None
    assert result['changed_files'] == ['.agents/skills/example/SKILL.md']
    assert (result['commit_observation'],result['post_commit_error_type']) == ('committed',error.__name__)
    assert 'rollback' in result
    if change=='none':
        assert (code,result['status'],result['target_readback']) == (0,'applied','matched')
        assert 'remaining_change_ownership' not in result
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(['rollback',result['deployment'],'--project',str(one.project)]) == 0
    else:
        assert (code,result['status'],result['target_readback']) == \
            (1,'requires_intervention','changed_or_unavailable')
        assert result['remaining_change_ownership'] == 'unverified'
        if change=='readback':assert one.skill.is_dir()
        else:assert one.skill.read_bytes() == (b'foreign edit' if change=='body' else one.valid)
        assert one.skill.stat().st_mode&0o777 == {'mode':0o600,'readback':0o700}.get(change,0o640)


def test_postcommit_record_failure_keeps_result_when_rerecording_fails(one):
    from agent_harness_lab import workflow
    original=workflow.write_json
    def record(path,value):
        if path.name=='workflow.json' and value.get('status')=='applied':raise OSError('synthetic full disk')
        return original(path,value)
    with patch.object(workflow,'write_json',side_effect=record):
        code,result=one.invoke('--repair-contracts')
    assert (code,result['status'],result['workflow_completion']) == (0,'applied','unrecorded')
    assert one.skill.read_bytes() == one.valid


def test_precommit_failure_still_reports_no_changed_files(one):
    from agent_harness_lab.promotion.journal import Journal as Owned
    with patch.object(Owned,'_execute',side_effect=KeyboardInterrupt):
        code,result=one.invoke('--repair-contracts')
    assert (code,result['status'],result['changed_files']) == (1,'interrupted',[])
    assert one.skill.read_bytes() == b'\xef\xbb\xbf'+one.valid


def test_nested_example_skill_is_preserved(one):
    example=one.skill.parent/'examples/nested/SKILL.md'
    example.parent.mkdir(parents=True)
    raw=b'\xef\xbb\xbf'+one.valid.replace(b'name: example',b'name: nested')
    example.write_bytes(raw)
    one.invoke('--repair-contracts')
    assert example.read_bytes() == raw


def test_watch_honors_the_same_offline_preflight(one):
    config.create(one.project,config.defaults())
    with patch('agent_harness_lab.workflow._improve',side_effect=AssertionError('no inference')),contextlib.redirect_stdout(io.StringIO()):
        assert main(['watch',str(one.project),'--once']) == 3


@pytest.fixture
def configured_workflow(one, request):
    """Builds cooperative WorkflowTests projects inside the one-call state patch; cleans them up first."""
    from tests.integration.workflow.test_workflow import WorkflowTests
    cleanups = contextlib.ExitStack(); request.addfinalizer(cleanups.close)
    def build(trials=20):
        fixture=WorkflowTests(cleanups.callback)
        (fixture.project/'CLAUDE.md').write_text('SYNTHETIC_OUT_OF_SCOPE')
        config.create(fixture.project,config.prepare(dict(profile='cooperative',
            runtime=dict(name='codex',executable=str(fixture.agent)),harness_paths=['AGENTS.md'],limits=dict(trials=trials))))
        return fixture
    yield build


def test_configured_scope_is_frozen_before_preparation(configured_workflow):
    fixture=configured_workflow()
    with contextlib.redirect_stdout(io.StringIO()): code=main(['improve',str(fixture.project)])
    assert code == 3
    outputs=list((fixture.root/'state/runs').glob('*/run-*'))
    assert len(outputs) == 1
    assert not (outputs[0]/'preparation/baseline/CLAUDE.md').exists()
    assert (outputs[0]/'change-plan.json').exists()


def test_configured_trial_ceiling_stops_before_a_second_proposal(configured_workflow):
    fixture=configured_workflow(trials=8)
    (fixture.project/'mode').write_text('refine')
    stdout=io.StringIO()
    with contextlib.redirect_stdout(stdout): main(['improve',str(fixture.project)])
    result=json.loads(stdout.getvalue())
    assert result['status'] == 'budget_exhausted'
    output=Path(result['output'])
    assert (output/'evaluation-0/results.json').exists()
    assert not (output/'preparation/proposal-1').exists()
    exhausted=result['budget_exhaustion']  # an exhausted run says where the budget went and concludes nothing
    assert (exhausted['evidence_label'],exhausted['conclusion'],exhausted['observed']['trials_recorded']) == ('insufficient','not_established',6)
    assert result['budget_stages']['stages']['trials']['spent_seconds'] >= 0 and result['budget_stages']['raise_budget_option'] == '--timeout SECONDS'
    assert (fixture.project/'AGENTS.md').read_text() == 'bad'


def test_infeasible_trial_ceiling_blocks_before_runtime(configured_workflow):
    fixture=configured_workflow(trials=7)
    stdout=io.StringIO()
    with contextlib.redirect_stdout(stdout),patch('agent_harness_lab.cli.evaluator',side_effect=AssertionError('no runtime')):
        assert main(['improve',str(fixture.project)]) == 3
    assert json.loads(stdout.getvalue())['reason'] == \
        'trial_budget_requires_two_preparation_and_six_paired_trials'
