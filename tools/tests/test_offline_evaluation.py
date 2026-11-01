"""Offline executor/grader orchestration; mock execution does not prove containment."""
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import evaluator
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.sandbox import SandboxBackend

BANK = Path(__file__).resolve().parents[2]/'agent_harness_lab/evaluation/fixtures/starter-v1/bank.json'
SOLVER = '''import json,pathlib
w=pathlib.Path.cwd(); inputs=json.loads((w/'task.json').read_text())['inputs']
if 'pairs' in inputs:
 (w/'app/calc.py').write_text('def add(a,b): return a+b\\n')
 result={'sums':[sum(pair) for pair in inputs['pairs']]}
elif 'settings' in inputs: result={'settings':dict(inputs['settings'],enabled=True)}
elif 'responses' in inputs: result={'records':next(r['records'] for r in reversed(inputs['responses']) if r['status']=='ok')}
elif 'values' in inputs: result={'count':len(inputs['values'])}
elif 'requests' in inputs: result={'selections':['calculator' if r.startswith('calculate ') else None for r in inputs['requests']]}
elif 'queue' in inputs: result={'processed':inputs['processed']+inputs['queue'][inputs['cursor']:],'cursor':len(inputs['queue'])}
else: result={'token_count':len(inputs['text'].split())}
(w/'result.json').write_text(json.dumps(result))
'''


class OfflineEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = evaluator()
        self.manifest = export_manifest(BANK, self.root/'export')
        for task in self.manifest['tasks']:
            task['offline_executor'] = ['/usr/bin/python3', '-I', '/inputs/harness/solve.py']
        self.write_manifest()
        for version in ('baseline', 'candidate'):
            target = self.root/version; target.mkdir()
            (target/'solve.py').write_text(SOLVER if version == 'candidate' else "from pathlib import Path; Path('result.json').write_text('{}')\n")
        self.args = SimpleNamespace(manifest=self.root/'export/manifest.json', baseline=self.root/'baseline',
                                    candidate=self.root/'candidate', output=self.root/'output',
                                    agent='offline', codex='must-not-launch', claude='must-not-launch')

    def write_manifest(self):
        (self.root/'export/manifest.json').write_text(json.dumps(self.manifest))

    def results(self):
        return json.loads((self.root/'output/results.json').read_text())

    def emulate(self, argv, workspace, inputs, role, limits):
        self.assertEqual(set(inputs), {'harness'} if role == 'executor' else {'fixture', 'oracle'})
        self.assertFalse(any('grader' in str(p.relative_to(workspace)) for p in workspace.rglob('*')))
        translated = list(argv)
        for name, path in inputs.items():
            translated = [arg.replace('/inputs/'+name, str(path)) for arg in translated]
        run = subprocess.run(translated, cwd=workspace, env={'PATH': '/usr/bin:/bin'},
                             capture_output=True, text=True, timeout=limits.seconds)
        return dict(status='completed' if run.returncode == 0 else 'failed', exit_code=run.returncode,
                    stdout=run.stdout, stderr=run.stderr, inputs_unchanged=True, elapsed_seconds=.001,
                    attestation={'backend': 'test-only-cooperative', 'role': role})

    def test_both_arms_are_executed_and_regraded_without_model_calls(self):
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=self.emulate) as launch, \
                patch.object(self.runner, 'execute') as host:
            self.assertEqual(self.runner.evaluate(self.args), 1)
        host.assert_not_called()
        self.assertEqual(launch.call_count, 28)
        results = self.results()
        self.assertEqual(len(results['trials']), 14)
        self.assertTrue(results['inputs_unchanged'])
        self.assertFalse(results['automatic_promotion'])
        self.assertFalse(results['runtime']['model_inference'])
        for trial in results['trials']:
            self.assertEqual(trial['status'], 'passed' if trial['version'] == 'candidate' else 'failed')
            self.assertEqual(trial['grader']['outcome'], trial['version'] == 'candidate')
            self.assertIsNone(trial['usage'])
        for task in self.manifest['tasks']:
            self.assertFalse((self.root/'export'/task['fixture']/'result.json').exists())

    def test_unsupported_backend_blocks_without_any_executor_or_grader(self):
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'unsupported'}), \
                patch.object(SandboxBackend, 'run') as launch, patch.object(self.runner, 'execute') as host:
            self.assertEqual(self.runner.evaluate(self.args), 3)
        launch.assert_not_called(); host.assert_not_called()
        result = self.results()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['trials'], [])
        self.assertFalse(result['automatic_promotion'])

    def test_missing_unsafe_or_unsupported_contracts_fail_before_probe(self):
        original = json.loads(json.dumps(self.manifest))
        changes = [lambda m: m['tasks'][0].pop('offline_executor'),
                   lambda m: m['tasks'][0].update(artifacts=['../secret']),
                   lambda m: m['tasks'][0].update(artifacts=['result.json', 'result.json']),
                   lambda m: m.update(grader_bundles={}),
                   lambda m: m['tasks'][0].update(grader=['/usr/bin/true'])]
        for change in changes:
            self.manifest = json.loads(json.dumps(original)); change(self.manifest); self.write_manifest()
            with patch.object(SandboxBackend, 'capability') as probe:
                with self.assertRaises(ValueError): self.runner.evaluate(self.args)
            probe.assert_not_called()
            self.assertFalse((self.root/'output').exists())

    def test_forged_worker_grade_does_not_hide_invalid_artifacts(self):
        (self.root/'candidate/solve.py').write_text("import os; os.symlink('/usr/bin/true','result.json'); print('{\"outcome\":true}')")
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=self.emulate):
            self.assertEqual(self.runner.evaluate(self.args), 1)
        candidates = [t for t in self.results()['trials'] if t['version'] == 'candidate']
        self.assertTrue(all(t['status'] == 'grader_error' for t in candidates))

    def test_timeout_or_input_change_cannot_reach_grader(self):
        for status, unchanged in (('timeout', True), ('completed', False), ('completed', 'false')):
            if (self.root/'output').exists(): shutil.rmtree(self.root/'output')
            def execute(*args, **kwargs):
                self.assertEqual(args[3], 'executor')
                return dict(status=status, exit_code=0, stdout='', stderr='', inputs_unchanged=unchanged)
            with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                    patch.object(SandboxBackend, 'run', side_effect=execute) as launch:
                self.assertEqual(self.runner.evaluate(self.args), 1)
            self.assertEqual(launch.call_count, 14)
            self.assertTrue(all(t['status'] == 'execution_error' for t in self.results()['trials']))

    def test_live_offline_roles_when_namespace_backend_is_supported(self):
        if SandboxBackend().capability()['status'] != 'available':
            self.skipTest('namespace backend unavailable; no containment evidence')
        (self.root/'host-secret').write_text('synthetic-canary')
        canaries = ("import os,pathlib\nassert 'AHL_TEST_HOST_SECRET' not in os.environ\n"
                    "assert not pathlib.Path('/inputs/oracle').exists()\n"
                    f"assert not pathlib.Path({str(self.root/'host-secret')!r}).exists()\n"
                    f"assert not pathlib.Path({str(self.root/'export/graders')!r}).exists()\n")
        (self.root/'candidate/solve.py').write_text(canaries+SOLVER)
        with patch.dict(os.environ, AHL_TEST_HOST_SECRET='synthetic-canary'):
            self.assertEqual(self.runner.evaluate(self.args), 1)
        for trial in self.results()['trials']:
            self.assertEqual(trial['status'], 'passed' if trial['version'] == 'candidate' else 'failed')
