import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
   (candidate/'AGENTS.md').write_text('worse' if mode=='refine' and c['attempt']==0 else 'good\n')
   if mode=='escape': (candidate/'settings.json').write_text('forbidden')
   if mode=='concurrent': (project/'AGENTS.md').write_text('concurrent')
   (evaluation/'proposal.json').write_text(json.dumps({'status':'candidate','mechanisms':['synthetic'],'falsifiers':['artifact check']}))
 else:
  assert 'candidate' not in c
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
 good=p('AGENTS.md').read_text().strip()=='good'
 p('artifact').write_text('done' if good or phase.endswith('regression') else 'wrong')
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
'''


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'AGENTS.md').write_text('bad')
        self.agent = self.root / 'agent'
        self.agent.write_text(FAKE)
        self.agent.chmod(0o700)
        self.state = patch('agent_harness_lab.workflow.state_root', return_value=self.root/'state')
        self.state.start()
        self.addCleanup(self.state.stop)
        self.collect = patch('agent_harness_lab.workflow.collect', return_value=dict(
            analysis=dict(events=0,cases=0,field_coverage={},findings=[],recurring_sequences=[],
                          limitations=[],deferred_mechanisms={}),evaluation_failures=[],fingerprint='one',caveats=[]))
        self.collect.start()
        self.addCleanup(self.collect.stop)

    def args(self):
        return argparse.Namespace(project=self.project,events=None,format='normalized',output=None,
            agent='codex',codex=str(self.agent),claude='missing',timeout=30,interval=1,once=True,focus=None)

    def test_cooperative_evaluation_recommends_without_adoption(self):
        result=improve(self.args())
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual((self.project/'AGENTS.md').read_text(),'bad')
        self.assertEqual(result['changed_files'],[])
        self.assertIn('role_isolation',result['blocked_prerequisites'])
        data=json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
        self.assertTrue(data['inputs_unchanged'])
        self.assertIn('failed',[r['status'] for r in data['trials'] if r['version']=='baseline'])

    def test_optional_focus_is_not_required_for_analysis(self):
        with patch('builtins.print'):
            self.assertEqual(main(['analyze', str(self.project)]),0)

    def test_tie_keeps_original(self):
        (self.project/'AGENTS.md').write_text('good')
        self.assertEqual(improve(self.args())['status'],'no_change')
        self.assertEqual((self.project/'AGENTS.md').read_text(),'good')

    def test_refines_once_using_new_heldout_inputs(self):
        (self.project/'mode').write_text('refine')
        result=improve(self.args())
        self.assertEqual(result['status'],'inconclusive')
        self.assertTrue((Path(result['output'])/'evaluation-1/results.json').is_file())
        feedback=json.loads((Path(result['output'])/'feedback-0.json').read_text())
        self.assertTrue(all(t['split']!='heldout' for t in feedback['trials']))

    def test_out_of_scope_candidate_is_never_applied(self):
        (self.project/'mode').write_text('escape')
        with self.assertRaises(ValueError): improve(self.args())
        self.assertFalse((self.project/'settings.json').exists())
        self.assertEqual((self.project/'AGENTS.md').read_text(),'bad')

    def test_concurrent_source_change_is_preserved(self):
        (self.project/'mode').write_text('concurrent')
        with self.assertRaises(ValueError): improve(self.args())
        self.assertEqual((self.project/'AGENTS.md').read_text(),'concurrent')

    def test_lock_prevents_overlapping_improvements(self):
        with project_lock(self.project):
            with self.assertRaises(ValueError):
                with project_lock(self.project): pass

    def test_watch_remembers_unchanged_evidence_across_restarts(self):
        with patch('agent_harness_lab.workflow._run_improvement',return_value={'status':'no_change'}) as run, patch('builtins.print'):
            watch(self.args()); watch(self.args())
            self.assertEqual(run.call_count,1)

    def test_no_supported_change_does_not_create_evaluation(self):
        (self.project/'mode').write_text('no_change')
        result=improve(self.args())
        self.assertEqual(result['status'],'no_change')
        self.assertFalse((Path(result['output'])/'evaluation-0').exists())

    def test_snapshot_does_not_follow_symlinked_discovery_ancestors(self):
        outside=self.root/'outside'
        (outside/'skills/private').mkdir(parents=True)
        (outside/'skills/private/SKILL.md').write_text('PRIVATE')
        (self.project/'.agents').symlink_to(outside, target_is_directory=True)
        original=snapshot(self.project,self.root/'snapshot')
        self.assertEqual(set(original),{'AGENTS.md'})
        self.assertNotIn('PRIVATE',''.join(p.read_text() for p in (self.root/'snapshot').rglob('*') if p.is_file()))

    def test_changed_candidate_is_rejected_before_any_adoption(self):
        original=snapshot(self.project,self.root/'baseline')
        import shutil
        shutil.copytree(self.root/'baseline',self.root/'candidate')
        proposed=candidate_states(self.root/'candidate',original)
        (self.root/'candidate/AGENTS.md').write_text('unevaluated')
        with self.assertRaises(ValueError):
            adopt(self.project,self.root/'baseline',self.root/'candidate',original,proposed)
        self.assertEqual((self.project/'AGENTS.md').read_text(),'bad')

    def test_expired_grader_budget_never_launches_a_process(self):
        from unittest.mock import Mock
        runner=Mock()
        evaluation=self.root/'evaluation'
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
        with self.assertRaises(TimeoutError):
            validate_manifest(manifest,None,None,['AGENTS.md'],runner,lambda:0,set())
        runner.execute.assert_not_called()

    def test_grader_cannot_be_copied_into_agent_fixture(self):
        from unittest.mock import Mock
        runner=Mock()
        evaluation=self.root/'evaluation'
        evaluation.mkdir()
        grader=evaluation/'grade.py'; grader.write_text('pass')
        tasks=[dict(id=s,split=s,fixture='.',grader=[sys.executable,str(grader),'{workspace}'])
               for s in ('development','regression','heldout')]
        controls=[dict(task=s,state=state,fixture='.') for s in ('development','regression','heldout')
                  for state in ('broken','partial','complete')]
        manifest=evaluation/'manifest.json'
        manifest.write_text(json.dumps(dict(tasks=tasks,grader_self_tests=controls,harness_paths=['AGENTS.md'])))
        with self.assertRaises(ValueError):
            validate_manifest(manifest,None,None,['AGENTS.md'],runner,lambda:10,set())
        runner.execute.assert_not_called()
