import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).with_name('run_evaluation.py')


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('baseline', 'candidate', 'fixture'):
            (self.root / name).mkdir()
        (self.root / 'fixture/original').write_text('preserve')
        self.fake = self.root / 'codex'
        self.fake.write_text('''#!/usr/bin/env python3
import json, pathlib, sys, time
if '--version' in sys.argv:
 print('codex-test 1.0'); sys.exit(0)
prompt=sys.stdin.read()
pathlib.Path('prompt.txt').write_text(prompt)
pathlib.Path('artifact').write_text('done')
mode=prompt.splitlines()[-1]
if mode=='timeout': time.sleep(30)
if mode=='process_error': sys.exit(2)
if mode=='malformed': print('bad-json')
elif mode=='missing': print(json.dumps({'type':'turn.started'}))
elif mode=='turn_failed': print(json.dumps({'type':'turn.failed'}))
else: print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
''')
        self.fake.chmod(0o700)
        self.grader = self.root / 'grade.py'
        self.grader.write_text("import pathlib,sys; p=pathlib.Path(sys.argv[1]); assert (p/'artifact').read_text()=='done'; assert (p/'original').read_text()=='preserve'\n")

    def run_case(self, mode='ok', grader=None, timeout=2, preload=True, skills=None, agent='codex', harness_paths=None):
        manifest = dict(tasks=[dict(id='case', split='development', fixture='fixture',
                                   prompt=mode, preload_skills=preload, grader=grader or [sys.executable, str(self.grader), '{workspace}'])],
                        timeout_seconds=timeout, skills=skills or [], harness_paths=harness_paths or [])
        (self.root / 'manifest.json').write_text(json.dumps(manifest))
        result = subprocess.run([sys.executable, str(RUNNER), '--manifest', str(self.root / 'manifest.json'),
                                 '--baseline', str(self.root / 'baseline'), '--candidate', str(self.root / 'candidate'),
                                 '--output', str(self.root / 'out'), '--codex', str(self.fake),
                                 '--agent', agent, '--claude', str(self.fake)], capture_output=True, text=True)
        return result, json.loads((self.root / 'out/results.json').read_text())

    def test_artifacts_are_graded_and_inputs_preserved(self):
        result, data = self.run_case()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([t['status'] for t in data['trials']], ['passed', 'passed'])
        self.assertTrue(data['inputs_unchanged'])
        self.assertFalse((self.root / 'fixture/artifact').exists())
        self.assertEqual((self.root / 'fixture/original').read_text(), 'preserve')
        self.assertNotEqual((self.root/'out/baseline/case/workspace').resolve(),
                            (self.root/'out/candidate/case/workspace').resolve())

    def test_no_preload_preserves_discovery_without_forcing_invocation(self):
        for name in ('baseline', 'candidate'):
            folder = self.root / name / 'example'
            folder.mkdir()
            (folder / 'SKILL.md').write_text('example skill')
        result, data = self.run_case(preload=False, skills=['example'])
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.root / 'out/candidate/case/workspace'
        self.assertEqual((workspace / '.agents/skills/example/SKILL.md').read_text(), 'example skill')
        self.assertNotIn('Load these exact', (workspace / 'prompt.txt').read_text())
        self.assertEqual(data['runtime']['codex_version'], 'codex-test 1.0')
        self.assertTrue(data['trials'][0]['injected_skill_hashes'])

    def use_claude_fake(self):
        self.fake.write_text("""#!/usr/bin/env python3
import json, pathlib, sys
if '--version' in sys.argv:
 print('claude-test 1.0'); sys.exit(0)
assert '--print' in sys.argv and '--verbose' in sys.argv
assert sys.argv[sys.argv.index('--output-format')+1]=='stream-json'
assert '--no-session-persistence' in sys.argv
assert '--dangerously-skip-permissions' not in sys.argv
assert '--permission-mode' not in sys.argv
plugin=pathlib.Path(sys.argv[sys.argv.index('--plugin-dir')+1])
assert json.loads((plugin/'.claude-plugin/plugin.json').read_text())['name']=='harness-trial'
prompt=sys.stdin.read()
pathlib.Path('prompt.txt').write_text(prompt)
pathlib.Path('artifact').write_text('done')
mode=prompt.splitlines()[-1]
if mode=='malformed': print('bad-json')
elif mode=='missing': print(json.dumps({'type':'assistant'}))
else:
 print(json.dumps({'type':'result','subtype':'error_max_turns' if mode=='turn_failed' else 'success',
                   'is_error':mode=='turn_failed','usage':{'output_tokens':7}}))
""")

    def test_claude_artifacts_and_namespaced_skill_discovery(self):
        self.use_claude_fake()
        for name in ('baseline', 'candidate'):
            folder = self.root / name / 'example'
            folder.mkdir()
            (folder / 'SKILL.md').write_text('example skill')
        result, data = self.run_case(agent='claude', preload=False, skills=['example'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['runtime']['agent'], 'claude')
        self.assertEqual(data['runtime']['claude_version'], 'claude-test 1.0')
        for trial in data['trials']:
            self.assertEqual(trial['status'], 'passed')
            self.assertEqual(trial['usage']['output_tokens'], 7)
            workspace = self.root / 'out' / trial['version'] / 'case/workspace'
            self.assertEqual((workspace/'.harness-trial/skills/example/SKILL.md').read_text(), 'example skill')
            self.assertNotIn('Load these exact', (workspace/'prompt.txt').read_text())
        self.assertTrue(data['inputs_unchanged'])

    def test_claude_failed_result_cannot_reach_grader(self):
        self.use_claude_fake()
        _, data = self.run_case('turn_failed', agent='claude')
        self.assertEqual(data['trials'][0]['status'], 'incomplete')
        self.assertNotIn('grader', data['trials'][0])

    def test_claude_rejects_absent_result_even_with_artifact(self):
        self.use_claude_fake()
        _, data = self.run_case('missing', agent='claude')
        self.assertEqual(data['trials'][0]['status'], 'incomplete')
        self.assertNotIn('grader', data['trials'][0])

    def test_claude_success_still_requires_acceptance(self):
        self.use_claude_fake()
        result, data = self.run_case(agent='claude', grader=[sys.executable, '-c', 'raise SystemExit(1)'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['trials'][0]['status'], 'failed')

    def test_claude_missing_binary_is_execution_error(self):
        self.fake.unlink()
        result, data = self.run_case(agent='claude')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['trials'][0]['status'], 'execution_error')
        self.assertNotIn('grader', data['trials'][0])

    def test_claude_errors_and_malformed_streams_are_not_passes(self):
        from run_evaluation import trace_result
        for text, expected in [('not json', 'invalid_trace'),
                (json.dumps({'type':'result','subtype':'success','is_error':False}) + '\n' +
                 json.dumps({'type':'error'}), 'incomplete'),
                ('[]', 'invalid_trace')]:
            self.assertEqual(trace_result(dict(timeout=False, exit_code=0, stdout=text), 'claude')[0], expected)

    def test_claude_result_contract_fails_closed(self):
        from run_evaluation import trace_result
        for event in ({'type':'result','subtype':'success'},
                      {'type':'result','subtype':'success','is_error':True},
                      {'type':'turn.completed'}):
            run=dict(timeout=False, exit_code=0, stdout=json.dumps(event))
            self.assertEqual(trace_result(run, 'claude')[0], 'incomplete')

    def test_harness_context_is_injected_for_each_version(self):
        (self.root/'baseline/AGENTS.md').write_text('baseline instructions')
        (self.root/'candidate/AGENTS.md').write_text('candidate instructions')
        result,data=self.run_case(harness_paths=['AGENTS.md'])
        self.assertEqual(result.returncode,0,result.stderr)
        for version in ('baseline','candidate'):
            self.assertEqual((self.root/'out'/version/'case/workspace/AGENTS.md').read_text(),version+' instructions')

    def test_missing_input_directory_rejected(self):
        from run_evaluation import digest
        with self.assertRaises(ValueError):
            digest(self.root / 'missing')

    def test_changed_grader_invalidates_passing_result(self):
        self.grader.write_text("from pathlib import Path; p=Path(__file__); p.write_text(p.read_text()+'\\n# changed')\n")
        result, data = self.run_case()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(data['inputs_unchanged'])
        self.assertEqual([t['status'] for t in data['trials']], ['passed', 'passed'])
        self.assertIn(str(self.grader), data['grader_files'])

    def test_nonzero_grader_rejects_successful_model(self):
        result, data = self.run_case(grader=[sys.executable, '-c', 'raise SystemExit(1)'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['trials'][0]['status'], 'failed')

    def test_model_process_failure_cannot_pass(self):
        result, data = self.run_case('process_error')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['trials'][0]['status'], 'execution_error')
        self.assertNotIn('grader', data['trials'][0])

    def test_malformed_trace_cannot_pass(self):
        _, data = self.run_case('malformed')
        self.assertEqual(data['trials'][0]['status'], 'invalid_trace')

    def test_missing_completion_cannot_pass(self):
        _, data = self.run_case('missing')
        self.assertEqual(data['trials'][0]['status'], 'incomplete')

    def test_failed_turn_cannot_pass(self):
        _, data = self.run_case('turn_failed')
        self.assertEqual(data['trials'][0]['status'], 'incomplete')

    def test_timeout_is_not_a_pass(self):
        _, data = self.run_case('timeout', timeout=0.1)
        self.assertTrue(data['trials'][0]['execution']['timeout'])
        self.assertEqual(data['trials'][0]['status'], 'execution_error')

    def test_missing_grader_is_setup_failure(self):
        _, data = self.run_case(grader=['/missing/eval-grader'])
        self.assertEqual(data['trials'][0]['status'], 'grader_error')


    def test_repeated_plan_runs_fresh_counterbalanced_trials(self):
        import importlib.util
        from dataclasses import asdict
        sys.path.insert(0, str(RUNNER.parents[3]))
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        spec=importlib.util.spec_from_file_location('synthetic_runner', RUNNER)
        runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        plan=ExperimentSpec('synthetic', (('case','root','family'),), 2, 13, 4, 2,
                            .05, .1, .05, (('family',1),), runner.digest(self.root/'baseline'),
                            runner.digest(self.root/'candidate'), 'grader', 'runtime')
        manifest=dict(tasks=[dict(id='case',split='development',fixture='fixture',prompt='ok',
                                  grader=[sys.executable,str(self.grader),'{workspace}'])],
                      experiment=asdict(plan),max_trials=4,budget_seconds=10,timeout_seconds=2)
        (self.root/'manifest.json').write_text(json.dumps(manifest))
        result=subprocess.run([sys.executable,str(RUNNER),'--manifest',str(self.root/'manifest.json'),
            '--baseline',str(self.root/'baseline'),'--candidate',str(self.root/'candidate'),
            '--output',str(self.root/'out'),'--codex',str(self.fake)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads((self.root/'out/results.json').read_text())
        self.assertEqual(len(data['trials']),4)
        self.assertEqual(len({t['trial_id'] for t in data['trials']}),4)
        self.assertEqual([t['version'] for t in data['trials']],['candidate','baseline','baseline','candidate'])
        for t in data['trials']:
            self.assertTrue((self.root/'out'/t['version']/'case'/str(t['repetition'])/'workspace/artifact').is_file())
        self.assertFalse(data['automatic_promotion'])

    def test_repeated_plan_cannot_override_caller_ceiling(self):
        self.test_repeated_plan_runs_fresh_counterbalanced_trials()
        import shutil
        shutil.rmtree(self.root/'out')
        manifest=json.loads((self.root/'manifest.json').read_text())
        manifest['max_trials']=2
        (self.root/'manifest.json').write_text(json.dumps(manifest))
        result=subprocess.run([sys.executable,str(RUNNER),'--manifest',str(self.root/'manifest.json'),
            '--baseline',str(self.root/'baseline'),'--candidate',str(self.root/'candidate'),
            '--output',str(self.root/'out'),'--codex',str(self.fake)],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('caller trial ceiling',result.stderr)
        self.assertFalse((self.root/'out/baseline').exists())

    def test_nonfinite_limits_are_rejected_before_execution(self):
        for value in (float('nan'), float('inf')):
            with self.subTest(value=value):
                (self.root/'manifest.json').write_text(json.dumps(dict(
                    tasks=[dict(id='case', split='development',fixture='fixture',prompt='ok',grader=['true'])],
                    timeout_seconds=value)))
                result=subprocess.run([sys.executable,str(RUNNER),'--manifest',str(self.root/'manifest.json'),
                    '--baseline',str(self.root/'baseline'),'--candidate',str(self.root/'candidate'),
                    '--output',str(self.root/'out'),'--codex',str(self.fake)],capture_output=True,text=True)
                self.assertEqual(result.returncode,2)
                self.assertFalse((self.root/'out').exists())

if __name__ == '__main__':
    unittest.main()



class InterruptCleanupTests(unittest.TestCase):
    def test_interrupt_reaps_its_owned_process_group(self):
        from unittest.mock import Mock, patch
        import signal
        import importlib.util
        spec = importlib.util.spec_from_file_location("runner_under_test", RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        process = Mock(pid=12345)
        process.poll.return_value = None
        process.communicate.side_effect = [KeyboardInterrupt(), ('', '')]
        with patch.object(runner.subprocess, 'Popen', return_value=process), patch.object(runner.os, 'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):
                runner.execute(['synthetic-child'], Path.cwd(), 10)
        kill.assert_called_once_with(12345, signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)
