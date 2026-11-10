from contextlib import ExitStack
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

from agent_harness_lab.workflow import improve, watch, project_lock, adopt, snapshot, candidate_states, validate_manifest
from agent_harness_lab.cli import main

FAKE = r'''#!/usr/bin/env python3
import json, pathlib, sys
p=pathlib.Path
if '--version' in sys.argv: print('synthetic-agent'); sys.exit(0)
prompt=sys.stdin.read()
if p('context.json').exists():
 c=json.loads(p('context.json').read_text()); project=p(c['project']); evaluation=p(c['evaluation'])
 if c['phase']=='candidate':
  mode=(project/'mode').read_text() if (project/'mode').exists() else 'ok'
  candidate=p(c['candidate'])
  if mode=='no_change':
   (evaluation/'proposal.json').write_text(json.dumps({'status':'no_change','reason':'No supported defect'}))
  else:
   if mode not in ('identical','ambient'): (candidate/'AGENTS.md').write_text('worse' if mode=='refine' and c['attempt']==0 else 'good\n')
   escaped=('settings.json','.claude/settings.json','.claude/hooks/pre.sh','CLAUDE.local.md') if mode=='escape' else ()
   nested={'nested':('app/AGENTS.md',),'unplanned':('app/AGENTS.md',),'unrelated':('other/AGENTS.md',),'unrequired':('app/AGENTS.md',),'ambient':('AGENTS.override.md',)}.get(mode,())
   planned=bool(nested) and mode!='unplanned' or mode in ('rooted','drift')
   if nested or planned: p('proposer-prompt.txt').write_text(prompt)
   for name in escaped+nested: (candidate/name).parent.mkdir(parents=True,exist_ok=True); (candidate/name).write_text('forbidden' if escaped else 'good\n')
   if mode=='concurrent': (project/'AGENTS.md').write_text('concurrent')
   proposal={'status':'candidate','mechanisms':['synthetic'],'falsifiers':['artifact check']}
   if planned:
    # Evaluated tasks declare no cwd, so they run at the root; a plan for app declares a task nobody evaluates.
    cwd='.' if mode in ('rooted','drift','ambient') else 'app'
    proposal['instruction_plan']=dict(schema_version=1,profile=dict(schema_version=1,runtime='codex',version='0.166.0',task_cwd=cwd,fallback_filenames=[]),
     required=[dict(id='absent',text='synthetic required text')] if mode=='unrequired' else [])
   (evaluation/'proposal.json').write_text(json.dumps(proposal))
 else:
  assert 'candidate' not in c
  if (project/'mode').exists() and (project/'mode').read_text()=='drift': (project/'app/AGENTS.md').write_text('drifted\n')
  grader=evaluation/'grade.py'
  grader.write_text("from pathlib import Path; import sys; w=Path(sys.argv[1]); assert (w/'artifact').read_text()=='done'; assert (w/'original').read_text()=='preserve'\n")
  tasks=[]; controls=[]
  for split in ('development','regression','heldout'):
   folder=evaluation/split; folder.mkdir(); (folder/'original').write_text('preserve')
   (folder/'phase').write_text(str(c['attempt'])+split)
   tasks.append(dict(id=split,split=split,fixture=split,prompt='Produce the synthetic artifact',grader=[sys.executable,str(grader),'{workspace}']))
   for state in ('broken','partial','complete'):
    control=evaluation/(split+'-'+state); control.mkdir()
    (control/'artifact').write_text('wrong' if state=='broken' else 'done')
    (control/'original').write_text('wrong' if state=='partial' else 'preserve')
    controls.append(dict(task=split,state=state,fixture=control.name))
  (evaluation/'manifest.json').write_text(json.dumps(dict(harness_paths=c['harness_paths'],skills=[],tasks=tasks,grader_self_tests=controls)))
else:
 phase=p('phase').read_text()
 good='good' in tuple(p(name).read_text().strip() for name in ('AGENTS.md','app/AGENTS.md','AGENTS.override.md') if p(name).exists())
 p('artifact').write_text('done' if good or phase.endswith('regression') else 'wrong')
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
'''


class WorkflowTests:
    """Synthetic project and fake agent."""
    def __init__(self, add_cleanup):
        self.temp = tempfile.TemporaryDirectory()
        add_cleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'AGENTS.md').write_text('bad')
        self.agent = self.root / 'agent'
        self.agent.write_text(FAKE)
        self.agent.chmod(0o700)
        self.state = patch('agent_harness_lab.workflow.state_root', return_value=self.root/'state')
        self.state.start()
        add_cleanup(self.state.stop)
        self.collect = patch('agent_harness_lab.workflow.collect', return_value=dict(
            analysis=dict(events=0,cases=0,field_coverage={},findings=[],recurring_sequences=[],
                          limitations=[],deferred_mechanisms={}),evaluation_failures=[],fingerprint='one',caveats=[]))
        self.collect.start()
        add_cleanup(self.collect.stop)

    def args(self):
        return argparse.Namespace(project=self.project,events=None,format='normalized',output=None,
            agent='codex',codex=str(self.agent),claude='missing',timeout=30,interval=1,once=True,focus=None)


@pytest.fixture
def wf():
    with ExitStack() as cleanups:
        yield WorkflowTests(cleanups.callback)


def test_cooperative_evaluation_recommends_without_adoption(wf):
    result=improve(wf.args())
    assert result['status'] == 'inconclusive'
    assert (wf.project/'AGENTS.md').read_text() == 'bad'
    assert result['changed_files'] == []
    assert 'role_isolation' in result['blocked_prerequisites']
    data=json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
    assert data['inputs_unchanged']
    assert 'failed' in [r['status'] for r in data['trials'] if r['version']=='baseline']


def test_optional_focus_is_not_required_for_analysis(wf):
    with patch('builtins.print'):
        assert main(['analyze', str(wf.project)]) == 0


def test_tie_keeps_original(wf):
    (wf.project/'AGENTS.md').write_text('good')
    result=improve(wf.args())
    assert result['status'] == 'no_change'
    assert result['reason'] == 'evaluation_no_improvement'
    evaluation=json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
    assert evaluation['inputs_unchanged']
    assert all(trial['status']=='passed' for trial in evaluation['trials'])
    assert (wf.project/'AGENTS.md').read_text() == 'good'


def test_refines_once_using_new_heldout_inputs(wf):
    (wf.project/'mode').write_text('refine')
    result=improve(wf.args())
    assert result['status'] == 'inconclusive'
    assert (Path(result['output'])/'evaluation-1/results.json').is_file()
    feedback=json.loads((Path(result['output'])/'feedback-0.json').read_text())
    assert all(t['split']!='heldout' for t in feedback['trials'])


def test_out_of_scope_candidate_is_never_applied(wf):
    (wf.project/'mode').write_text('escape')
    with pytest.raises(ValueError): improve(wf.args())
    for name in ('settings.json','.claude/settings.json','.claude/hooks/pre.sh','CLAUDE.local.md'):
        assert not (wf.project/name).exists()
    assert (wf.project/'AGENTS.md').read_text() == 'bad'


def test_planned_instruction_candidates_are_validated_bound_and_staged_but_not_auto_adopted(wf):
    (wf.project/'app').mkdir(); (wf.project/'mode').write_text('rooted')
    result=improve(wf.args())
    assert result['status'] == 'inconclusive'
    assert result['changed_files'] == []
    assert (Path(result['output'])/'candidate-0/AGENTS.md').read_text() == 'good\n'
    assert (wf.project/'AGENTS.md').read_text() == 'bad'
    validation=json.loads(Path(result['instruction_validation']).read_text())
    assert (validation['status'],validation['scope']['instruction_dirs']) == ('valid',['.'])
    assert (validation['scope']['source'],validation['scope']['task_cwds']) == ('evaluation_manifest_task_cwds',['.'])
    # The root edit drops the active 'bad' line; regression controls in faithful trial workspaces preserved it.
    assert result['instruction_removal']['disposition'] == 'recommended'
    assert result['instruction_removal']['certification'] == 'required'
    from agent_harness_lab.surfaces import adoptable_surfaces
    from agent_harness_lab.sources.instruction_validation import PROPOSER_GUIDANCE
    prompt=' '.join((Path(result['output'])/'preparation/proposer-prompt.txt').read_text().split())
    assert f'modify only these project-owned surfaces in candidate: {adoptable_surfaces()}.' in prompt
    assert ' '.join(PROPOSER_GUIDANCE.split()) in prompt
    assert 'model settings, permissions, hooks or credentials' in prompt


def test_a_plan_for_a_task_cwd_no_evaluated_task_runs_in_is_refused_before_trials(wf):
    # The proposer declares task_cwd app so app/AGENTS.md is governing; the evaluated tasks all run at the root.
    (wf.project/'app').mkdir(); (wf.project/'mode').write_text('nested')
    result=improve(wf.args())
    assert (result['status'],result['reason'],result['instruction_reasons']) == ('blocked','instruction_validation_blocked',['task_cwd_not_evaluated'])
    validation=json.loads(Path(result['instruction_validation']).read_text())
    assert (validation['scope']['source'],validation['scope']['task_cwds']) == ('evaluation_manifest_task_cwds',['.'])
    assert dict(reason='task_cwd_not_evaluated',path='app') in validation['findings']
    assert not (Path(result['output'])/'evaluation-0').exists()
    assert 'recommendation' not in result
    assert not (wf.project/'app/AGENTS.md').exists()
    assert (wf.project/'AGENTS.md').read_text() == 'bad'


@pytest.mark.parametrize('mode', ('unplanned','unrelated'))
def test_nested_instruction_candidate_outside_governing_scope_is_rejected(wf, mode):
    (wf.project/'app').mkdir()
    (wf.project/'mode').write_text(mode)
    with pytest.raises(ValueError, match='outside its governing scope'): improve(wf.args())
    assert not (wf.project/'app/AGENTS.md').exists()
    assert not (wf.project/'other').exists()


def test_unfaithful_removal_controls_withhold_the_recommendation(wf):
    # The project's AGENTS.override.md is not staged, so the baseline trial workspace never held the guidance removed.
    (wf.project/'AGENTS.override.md').write_text('Synthetic override guidance.\n')
    (wf.project/'mode').write_text('ambient')
    result=improve(wf.args())
    assert result['instruction_removal']['disposition'] == 'inconclusive'
    assert (result['status'],result['reason'],result['instruction_removal_reason']) == ('inconclusive','instruction_removal_inconclusive','workspace_surfaces_unverified')
    assert 'recommendation' not in result
    assert not (Path(result['output'])/'change-plan.json').exists()
    assert (wf.project/'AGENTS.override.md').read_text() == 'Synthetic override guidance.\n'


def test_instruction_context_drift_during_evaluation_invalidates_the_recommendation(wf):
    (wf.project/'app').mkdir(); (wf.project/'mode').write_text('drift')
    with pytest.raises(ValueError, match='Instruction context changed during evaluation'): improve(wf.args())
    assert (wf.project/'AGENTS.md').read_text() == 'bad'
    assert (wf.project/'app/AGENTS.md').read_text() == 'drifted\n'
    assert not list((wf.root/'state').rglob('change-plan.json'))


def test_instruction_validation_failure_blocks_before_evaluation(wf):
    (wf.project/'app').mkdir(); (wf.project/'mode').write_text('unrequired')
    result=improve(wf.args())
    assert (result['status'],result['reason']) == ('blocked','instruction_validation_blocked')
    assert result['instruction_reasons'] == ['required_text_not_active']
    assert not (Path(result['output'])/'preparation/evaluation-0').exists()
    assert (wf.project/'AGENTS.md').read_text() == 'bad'


def test_concurrent_source_change_is_preserved(wf):
    (wf.project/'mode').write_text('concurrent')
    with pytest.raises(ValueError): improve(wf.args())
    assert (wf.project/'AGENTS.md').read_text() == 'concurrent'


def test_lock_prevents_overlapping_improvements(wf):
    with project_lock(wf.project):
        with pytest.raises(ValueError):
            with project_lock(wf.project): pass


def test_watch_remembers_unchanged_evidence_across_restarts(wf):
    with patch('agent_harness_lab.workflow._run_improvement',return_value={'status':'no_change'}) as run, patch('builtins.print'):
        watch(wf.args()); watch(wf.args())
        assert run.call_count == 1


def test_no_supported_change_does_not_create_evaluation(wf):
    (wf.project/'mode').write_text('no_change')
    result=improve(wf.args())
    assert result['status'] == 'no_change'
    assert result['reason'] == 'proposer_no_change'
    assert 'No supported defect' not in json.dumps(result)
    assert not (Path(result['output'])/'evaluation-0').exists()


def test_identical_candidate_has_distinct_reason_without_evaluation(wf):
    (wf.project/'mode').write_text('identical')
    result=improve(wf.args())
    assert result['status'] == 'no_change'
    assert result['reason'] == 'candidate_identical_to_baseline'
    assert (wf.project/'AGENTS.md').read_text() == 'bad'
    assert not (Path(result['output'])/'evaluation-0').exists()
    assert json.loads((Path(result['output'])/'workflow.json').read_text())['reason'] == result['reason']


def test_snapshot_does_not_follow_symlinked_discovery_ancestors(wf):
    outside=wf.root/'outside'
    (outside/'skills/private').mkdir(parents=True)
    (outside/'skills/private/SKILL.md').write_text('PRIVATE')
    (wf.project/'.agents').symlink_to(outside, target_is_directory=True)
    original=snapshot(wf.project,wf.root/'snapshot')
    assert set(original) == {'AGENTS.md'}
    assert 'PRIVATE' not in ''.join(p.read_text() for p in (wf.root/'snapshot').rglob('*') if p.is_file())


def test_changed_candidate_is_rejected_before_any_adoption(wf):
    original=snapshot(wf.project,wf.root/'baseline')
    import shutil
    shutil.copytree(wf.root/'baseline',wf.root/'candidate')
    proposed=candidate_states(wf.root/'candidate',original)
    (wf.root/'candidate/AGENTS.md').write_text('unevaluated')
    with pytest.raises(ValueError):
        adopt(wf.project,wf.root/'baseline',wf.root/'candidate',original,proposed)
    assert (wf.project/'AGENTS.md').read_text() == 'bad'


def test_expired_grader_budget_never_launches_a_process(wf):
    from unittest.mock import Mock
    runner=Mock()
    evaluation=wf.root/'evaluation'
    evaluation.mkdir()
    for name in ('task','broken','partial','complete'):
        (evaluation/name).mkdir()
    grader=evaluation/'grade.py'
    grader.write_text('pass')
    task=lambda split:dict(id=split,split=split,fixture='task',grader=[sys.executable,str(grader),'{workspace}'])
    controls=[dict(task=split,state=state,fixture=state) for split in ('development','regression','heldout')
              for state in ('broken','partial','complete')]
    manifest=evaluation/'manifest.json'
    manifest.write_text(json.dumps(dict(tasks=[task(s) for s in ('development','regression','heldout')],
        grader_self_tests=controls,harness_paths=['AGENTS.md'])))
    with pytest.raises(TimeoutError):
        validate_manifest(manifest,None,None,['AGENTS.md'],runner,lambda:0,set())
    runner.execute.assert_not_called()


def test_grader_cannot_be_copied_into_agent_fixture(wf):
    from unittest.mock import Mock
    runner=Mock()
    evaluation=wf.root/'evaluation'
    evaluation.mkdir()
    grader=evaluation/'grade.py'; grader.write_text('pass')
    tasks=[dict(id=s,split=s,fixture='.',grader=[sys.executable,str(grader),'{workspace}'])
           for s in ('development','regression','heldout')]
    controls=[dict(task=s,state=state,fixture='.') for s in ('development','regression','heldout')
              for state in ('broken','partial','complete')]
    manifest=evaluation/'manifest.json'
    manifest.write_text(json.dumps(dict(tasks=tasks,grader_self_tests=controls,harness_paths=['AGENTS.md'])))
    with pytest.raises(ValueError):
        validate_manifest(manifest,None,None,['AGENTS.md'],runner,lambda:10,set())
    runner.execute.assert_not_called()


@pytest.mark.parametrize('cmd', (pytest.param(['--help'], id='root'), pytest.param(['improve', '--help'], id='improve'),
                                 pytest.param(['watch', '--help'], id='watch')))
def test_help_reflects_recommendation_only_boundary(cmd):
    """Help output must not imply automatic behavioral adoption."""
    result = subprocess.run(
        [sys.executable, '-m', 'agent_harness_lab'] + cmd,
        capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{cmd} failed: {result.stderr}"
    out = (result.stdout + result.stderr).lower()
    assert 'adopt supported harness repairs' not in out
    assert 'continuously improve' not in out
    if 'improve' in cmd or 'watch' in cmd:
        assert 'recommend' in out
        assert 'no automatic adoption' in out or 'recommendation-only' in out or 'recommend' in out


def test_audit_focus_hint_reaches_cooperative_proposer_without_source_text(wf, monkeypatch, capsys):
    from agent_harness_lab.audit.cli import main as audit_main

    (wf.project / '.git').mkdir()
    (wf.project / 'AGENTS.md').write_text('SYNTHETIC_PRIVATE_SENTENCE\n')
    (wf.project / 'mode').write_text('no_change')
    monkeypatch.setenv('XDG_STATE_HOME', str(wf.root / 'audit-state'))
    assert audit_main([str(wf.project), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    hint = report['improvement_hint']
    assert hint['status'] == 'hypothesis' and hint['authority'] == 'none'
    args = wf.args()
    args.focus = hint['focus']
    result = improve(args)
    context = json.loads((Path(result['output']) / 'preparation/context.json').read_text())
    assert context['focus'] == hint['focus']
    assert 'SYNTHETIC_PRIVATE_SENTENCE' not in context['focus']
    assert str(wf.project) not in context['focus']
    assert result['status'] == 'no_change'


def test_audit_focus_hint_keeps_instruction_validation_in_force(wf, monkeypatch, capsys):
    from agent_harness_lab.audit.cli import main as audit_main

    (wf.project / '.git').mkdir()
    (wf.project / 'AGENTS.md').write_text('SYNTHETIC_PRIVATE_SENTENCE\n')
    (wf.project / 'mode').write_text('rooted')
    monkeypatch.setenv('XDG_STATE_HOME', str(wf.root / 'audit-state'))
    assert audit_main([str(wf.project), '--json']) == 0
    hint = json.loads(capsys.readouterr().out)['improvement_hint']
    args = wf.args()
    args.focus = hint['focus']
    result = improve(args)
    context = json.loads((Path(result['output']) / 'preparation/context.json').read_text())
    assert context['focus'] == hint['focus']
    assert Path(result['instruction_validation']).is_file()
    assert (wf.project / 'AGENTS.md').read_text() == 'SYNTHETIC_PRIVATE_SENTENCE\n'
    assert result['status'] == 'inconclusive'
