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

    def test_trace_records_split_on_line_feed_only(self):
        from run_evaluation import trace_result
        usage={'input_tokens':3}
        for agent, last in (('claude', {'type':'result','subtype':'success','is_error':False,'usage':usage}),
                            ('codex', {'type':'turn.completed','usage':usage})):
            last=json.dumps(last)
            for separator in (' ', ' ', '\x85', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e'):
                with self.subTest(agent=agent, separator=hex(ord(separator))):
                    raw='{"type":"message","text":"a'+separator+'b"}'
                    escaped=json.dumps({'type':'message','text':'a'+separator+'b'})
                    # JSON permits raw U+2028/U+2029/U+0085 in strings but not raw C0 controls.
                    expected=('invalid_trace', None) if separator < ' ' else ('completed', usage)
                    for newline in ('\n', '\r\n'):
                        self.assertEqual(trace_result(dict(timeout=False, exit_code=0, stdout=raw+newline+last+newline), agent), expected)
                        self.assertEqual(trace_result(dict(timeout=False, exit_code=0, stdout=escaped+newline+last+newline), agent), ('completed', usage))
                    joined=json.dumps({'type':'message'})+separator+last
                    self.assertEqual(trace_result(dict(timeout=False, exit_code=0, stdout=joined), agent), ('invalid_trace', None))

    def test_crlf_traces_and_blank_lines_are_unchanged(self):
        from run_evaluation import trace_result
        done=json.dumps({'type':'result','subtype':'success','is_error':False,'usage':{'n':1}})
        for stdout in (done, done+'\r\n', '\r\n'+json.dumps({'type':'message'})+'\r\n\r\n'+done+'\r\n', '  \n'+done+'\n\n'):
            self.assertEqual(trace_result(dict(timeout=False, exit_code=0, stdout=stdout), 'claude'), ('completed', {'n':1}))
        self.assertEqual(trace_result(dict(timeout=False, exit_code=0, stdout=done+'\r\n{'), 'claude'), ('invalid_trace', None))
        self.assertEqual(trace_result(dict(timeout=False, exit_code=0, stdout=''), 'claude'), ('incomplete', None))

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

    def test_changed_grader_invalidates_grade_and_stops_later_trials(self):
        self.grader.write_text("from pathlib import Path; p=Path(__file__); p.write_text(p.read_text()+'\\n# changed')\n")
        result, data = self.run_case()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(data['inputs_unchanged'])
        self.assertEqual([t['status'] for t in data['trials']], ['invalid_input', 'invalid_input'])
        self.assertFalse((self.root/'out/candidate/case/workspace').exists())
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
        from agent_harness_lab.evaluation.integrity import digest
        from agent_harness_lab.evaluation.oracle import identity
        spec=importlib.util.spec_from_file_location('synthetic_runner', RUNNER)
        runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        oracle = self.root/'oracle'; oracle.mkdir()
        grader = oracle/'grade.py'
        grader.write_text(self.grader.read_text()+"import json; print(json.dumps(dict(schema_version=1,valid=True,outcome=True,grader_id='synthetic',grader_version='v1')))\n")
        tasks = [dict(id='case',split='development',fixture='fixture',prompt='ok',
                      grader=[sys.executable,str(grader),'{workspace}'])]
        bundles = {'case':dict(path='oracle',digest=digest(oracle))}
        plan=ExperimentSpec('synthetic', (('case','root','family'),), 2, 13, 4, 2,
                            .05, .1, .05, (('family',1),), runner.digest(self.root/'baseline'),
                            runner.digest(self.root/'candidate'), identity(self.root,tasks,bundles), 'runtime')
        manifest=dict(tasks=tasks,grader_bundles=bundles,
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


class TrialSurfaceTests(unittest.TestCase):
    """Synthetic HOME and CODEX_HOME only; the real user layers are never read or named."""
    def setUp(self):
        from unittest.mock import patch
        import importlib.util
        spec = importlib.util.spec_from_file_location('runner_trial_surfaces', RUNNER)
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.home = self.root / 'synthetic-home'
        (self.home / '.agents/skills/example').mkdir(parents=True)
        self.user_codex = self.root / 'synthetic-user-codex'
        (self.user_codex / 'skills/example').mkdir(parents=True)
        (self.user_codex / 'auth.json').write_text('{"synthetic": "credential"}')
        self.environ = {'HOME': str(self.home), 'CODEX_HOME': str(self.user_codex), 'PATH': '/synthetic/bin'}
        self.trial = self.root / 'out/candidate/case'
        self.workspace = self.trial / 'workspace'
        self.workspace.mkdir(parents=True)
        patcher = patch.dict('os.environ', self.environ, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)

    def assert_no_user_layers(self, argv, environment):
        named = ' '.join([*argv, *environment.values()])
        self.assertNotIn(str(self.home / '.claude'), named)
        self.assertNotIn(str(self.user_codex), named)
        self.assertNotIn(str(self.home / '.codex'), named)

    def test_claude_trial_requests_project_only_settings_strict_mcp_and_no_hooks(self):
        argv, environment = self.runner.trial_command('claude', 'claude', self.workspace / '.harness-trial', (),
                                                      self.trial, self.workspace, {}, self.environ)
        self.assertEqual(argv[argv.index('--setting-sources') + 1], 'project')
        self.assertIn('--strict-mcp-config', argv)
        mcp = Path(argv[argv.index('--mcp-config') + 1])
        self.assertEqual(json.loads(mcp.read_text()), {'mcpServers': {}})
        settings = Path(argv[argv.index('--settings') + 1])
        self.assertEqual(json.loads(settings.read_text()), {'disableAllHooks': True})
        for staged in (mcp, settings):
            self.assertEqual(staged.parent, self.trial)
            self.assertFalse(staged.is_relative_to(self.workspace))
            self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
        self.assert_no_user_layers(argv, environment)

    def test_codex_trial_uses_a_temporary_home_with_only_defaults_and_workspace_trust(self):
        import tomllib
        argv, environment = self.runner.trial_command('codex', 'codex', None, ('example',), self.trial, self.workspace,
                                                      dict(model='synthetic-model', model_reasoning_effort='low'),
                                                      self.environ)
        home = Path(environment['CODEX_HOME'])
        self.assertEqual(home, self.trial / 'codex-home')
        self.assertEqual(sorted(p.name for p in home.iterdir()), ['config.toml'])
        self.assertEqual(tomllib.loads((home / 'config.toml').read_text()),
                         dict(model='synthetic-model', model_reasoning_effort='low',
                              projects={str(self.workspace): dict(trust_level='trusted')}))
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)
        disabled = argv[argv.index('-c', argv.index('-c') + 1) + 1]
        self.assertIn(str(self.home / '.agents/skills/example'), disabled)
        self.assertEqual(argv[-1], '-')
        self.assert_no_user_layers(argv, environment)

    def test_unverified_or_missing_capability_rows_never_claim_isolation(self):
        from unittest.mock import patch
        from dataclasses import replace
        from agent_harness_lab import surfaces
        from agent_harness_lab.evaluation import trial_surfaces
        for runtime in ('claude-code', 'codex'):
            value = trial_surfaces.descriptor(runtime)
            self.assertEqual(value['status'], 'inherited_unverified')
            self.assertFalse(value['observed']); self.assertFalse(value['security_isolation'])
            self.assertTrue(all(row['status'] == 'unverified' for row in value['requests']))
        verified = tuple(replace(row, status='verified') for row in surfaces.CAPABILITIES)
        with patch.object(surfaces, 'CAPABILITIES', verified):
            self.assertEqual(trial_surfaces.descriptor('codex')['status'], 'requested_documented_unobserved')
        without = tuple(row for row in surfaces.CAPABILITIES if row.item != '--setting-sources project CLI flag')
        with patch.object(surfaces, 'CAPABILITIES', without):
            argv, _ = self.runner.trial_command('claude', 'claude', None, (), self.trial, self.workspace, {}, self.environ)
            self.assertNotIn('--setting-sources', argv)
            value = trial_surfaces.descriptor('claude-code')
            self.assertEqual(value['status'], 'inherited_unverified')
            self.assertIn('no_capability_row', [row['status'] for row in value['requests']])
        for runtime in ('claude-code', 'codex'):
            self.assertNotIn('isolated', json.dumps(trial_surfaces.descriptor(runtime)))


class TrialWorkspaceTests(unittest.TestCase):
    """End to end with the fake CLIs and a synthetic HOME/CODEX_HOME."""
    run_case = EvaluationTests.run_case
    use_claude_fake = EvaluationTests.use_claude_fake

    def setUp(self):
        EvaluationTests.setUp(self)
        self.home = self.root / 'synthetic-home'; self.home.mkdir()
        self.user_codex = self.root / 'synthetic-user-codex'; self.user_codex.mkdir()
        (self.user_codex / 'config.toml').write_text('model = "synthetic-model"\nmodel_reasoning_effort = "low"\nsecret = "x"\n')
        self.fake.write_text(self.fake.read_text().replace(
            "prompt=sys.stdin.read()", "import os\nprompt=sys.stdin.read()\npathlib.Path('env.json').write_text(json.dumps(dict(os.environ)))"))

    def run_isolated(self, **options):
        import os
        from unittest.mock import patch
        environ = {**os.environ, 'HOME': str(self.home), 'CODEX_HOME': str(self.user_codex)}
        with patch.dict('os.environ', environ, clear=True):
            return self.run_case(**options)

    def test_nested_harness_file_is_copied_to_the_same_relative_path_and_recorded(self):
        for version in ('baseline', 'candidate'):
            (self.root / version / 'app').mkdir()
            (self.root / version / 'AGENTS.md').write_text(version + ' root')
            (self.root / version / 'app/AGENTS.md').write_text(version + ' app')
        result, data = self.run_isolated(harness_paths=['AGENTS.md', 'app/AGENTS.md', 'app/.claude/rules/absent.md'])
        self.assertEqual(result.returncode, 0, result.stderr)
        for trial in data['trials']:
            workspace = self.root / 'out' / trial['version'] / 'case/workspace'
            self.assertEqual((workspace / 'app/AGENTS.md').read_text(), trial['version'] + ' app')
            self.assertEqual((workspace / 'AGENTS.md').read_text(), trial['version'] + ' root')
            surfaces = trial['workspace_surfaces']
            self.assertEqual([row['path'] for row in surfaces['included']], ['AGENTS.md', 'app/AGENTS.md'])
            self.assertEqual(surfaces['absent'], ['app/.claude/rules/absent.md'])
            self.assertEqual(surfaces['observed_loading'], 'unknown')

    def test_codex_run_records_unverified_isolation_and_uses_only_the_trial_home(self):
        result, data = self.run_isolated()
        self.assertEqual(result.returncode, 0, result.stderr)
        isolation = data['isolation']
        self.assertEqual(isolation['status'], 'inherited_unverified')
        self.assertEqual(isolation['copied_config_keys'], ['model', 'model_reasoning_effort'])
        self.assertFalse(isolation['full_certification'])
        for trial in data['trials']:
            case = self.root / 'out' / trial['version'] / 'case'
            seen = json.loads((case / 'workspace/env.json').read_text())
            self.assertEqual(seen['CODEX_HOME'], str(case / 'codex-home'))
            self.assertNotIn('secret', (case / 'codex-home/config.toml').read_text())

    def test_claude_run_records_unverified_isolation(self):
        self.use_claude_fake()
        result, data = self.run_isolated(agent='claude')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['isolation']['status'], 'inherited_unverified')
        self.assertEqual(data['isolation']['runtime'], 'claude-code')
        self.assertTrue(all(row['requested'] for row in data['isolation']['requests']))
