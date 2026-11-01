"""Opt-in real kernel boundaries; synthetic fixtures, no inference or credentials."""
from contextlib import contextmanager
import os
import json
from pathlib import Path
import selectors
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation.bank_tools import regrade_artifacts
from agent_harness_lab.evaluation.cgroup import delegated
from agent_harness_lab.evaluation.integrity import artifacts, digest
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend
from test_offline_evaluation import OfflineFixture

ENABLED = bool(os.environ.get('AHL_TEST_CGROUP_ROOT')) or os.environ.get('AHL_TEST_SYSTEMD_SCOPE') == '1'


@contextmanager
def kernel_subtree():
    explicit = os.environ.get('AHL_TEST_CGROUP_ROOT')
    if explicit:
        yield delegated(explicit)
        return
    lines = Path('/proc/self/cgroup').read_text().splitlines()
    if len(lines) != 1 or not lines[0].startswith('0::/'):
        raise ValueError('kernel canaries require unified cgroups')
    root = Path('/sys/fs/cgroup')/lines[0][4:]
    if (not root.name.startswith('ahl-sandbox-test-') or not root.name.endswith('.scope')
            or root.stat().st_uid != os.geteuid()
            or (root/'cgroup.procs').read_text().split() != [str(os.getpid())]):
        raise ValueError('canaries require their own explicitly delegated foreground scope')
    controller = root/'controller'
    controller.mkdir(mode=0o700)
    try:
        (controller/'cgroup.procs').write_text(str(os.getpid()))
        # Only this disposable test scope changes; production never enables controllers.
        (root/'cgroup.subtree_control').write_text('+cpu +memory +pids')
        yield delegated(root)
    finally:
        (root/'cgroup.subtree_control').write_text('-cpu -memory -pids')
        (root/'cgroup.procs').write_text(str(os.getpid()))
        controller.rmdir()


class KernelFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected and not Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()):
            raise AssertionError('kernel canaries imported the wrong package layout')
        cls.cgroup_root = cls.enterClassContext(kernel_subtree())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root/'work'; self.work.mkdir()
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root)

    def run_code(self, code, *, inputs=None, role='executor', limits=Limits(seconds=5)):
        return self.backend.run(['/usr/bin/python3', '-c', code], self.work, inputs or {}, role, limits)

    def assert_complete(self, run):
        self.assertEqual(run['status'], 'completed', run['stderr'])
        self.assertEqual(run['exit_code'], 0, run['stderr'])
        self.assertTrue(run['inputs_unchanged'])
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])
        self.assertFalse(run['attestation']['automatic_promotion'])


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class NamespaceKernelTests(KernelFixture):
    def test_executable_artifact_cannot_read_the_grader_reference(self):
        bundle = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1/graders/localization'
        expected = json.loads((bundle/'expected.json').read_text())
        malicious = '''import json,pathlib
paths=[pathlib.Path('/inputs/oracle/expected.json')]
paths += [p/'root/inputs/oracle/expected.json' for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()]
for path in paths:
 try: reference=json.loads(path.read_text())
 except OSError: continue
 print(json.dumps(reference['python_checks']['outputs'])); raise SystemExit(0)
raise RuntimeError('oracle is unavailable')
'''
        code = f'''import pathlib
pathlib.Path('app').mkdir()
pathlib.Path('app/calc.py').write_text({malicious!r})
pathlib.Path('original.txt').write_text('preserve\\n')
pathlib.Path('result.json').write_text({json.dumps(expected['result'])!r})
'''
        self.assert_complete(self.run_code(code))
        grade = regrade_artifacts(self.work, ['app/calc.py', 'original.txt', 'result.json'], bundle,
                                 digest(bundle), self.backend, limits=Limits(seconds=5))
        self.assertEqual(grade['status'], 'failed')
        self.assertIs(grade['outcome'], False)

    def test_capability_with_real_aggregate_controls(self):
        result = self.backend.capability()
        self.assertEqual(result['status'], 'available', result)
        self.assertIsNotNone(result['cgroup_v2'])
        self.assertFalse(result['automatic_promotion'])

    def test_roles_cannot_read_forbidden_files_or_mutate_readonly_inputs(self):
        (self.root/'forbidden').write_text('SYNTHETIC_FORBIDDEN')
        harness, fixture, oracle = (self.root/name for name in ('harness', 'fixture', 'oracle'))
        for path in (harness, fixture, oracle):
            path.mkdir(); (path/'marker').write_text('SYNTHETIC_INPUT')
        with tempfile.TemporaryDirectory(prefix='ahl-canary-', dir=Path.home()) as home:
            secret = Path(home)/'canary'; secret.write_text('SYNTHETIC_HOME')
            for role in ('proposer', 'evaluator_author', 'executor', 'grader'):
                inputs = {'fixture': fixture, 'oracle': oracle} if role == 'grader' else {'harness': harness, 'fixture': fixture}
                code = f'''import os,pathlib
assert 'AHL_TEST_SECRET' not in os.environ
for value in {list(map(str, (secret, self.root/'forbidden', oracle, self.root)))!r}:
 assert not pathlib.Path(value).exists()
assert pathlib.Path('/inputs/oracle').exists() is {role == 'grader'!r}
for name in {list(inputs)!r}:
 path=pathlib.Path('/inputs')/name/'marker'
 assert path.read_text() == 'SYNTHETIC_INPUT'
 try: path.write_text('mutated'); raise AssertionError('readonly input was writable')
 except OSError: pass
 try: path.chmod(0o777); raise AssertionError('readonly input chmod succeeded')
 except OSError: pass
'''
                with self.subTest(role=role), patch.dict(os.environ, AHL_TEST_SECRET='SYNTHETIC_ENV'):
                    self.assert_complete(self.run_code(code, inputs=inputs, role=role))

    def test_network_namespace_cannot_connect_to_host_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            port = listener.getsockname()[1]
            code = f"import socket; s=socket.socket(); s.settimeout(.5); assert s.connect_ex(('127.0.0.1',{port})) != 0"
            self.assert_complete(self.run_code(code, role='grader'))

    def test_worker_links_do_not_escape_bounded_artifact_extraction(self):
        code = f'''import os,pathlib
os.symlink({str(self.root/'forbidden')!r},'symlink')
try: pathlib.Path('symlink').read_bytes(); raise AssertionError('host path was readable')
except OSError: pass
pathlib.Path('original').write_text('synthetic')
os.link('original','hardlink')
'''
        self.assert_complete(self.run_code(code))
        for name in ('symlink', 'hardlink', '../forbidden'):
            with self.assertRaises(ValueError): artifacts(self.work, [name])

    def test_forged_passing_artifact_is_regraded_by_pristine_oracle(self):
        bundle = self.root/'oracle'; bundle.mkdir()
        (bundle/'expected.json').write_text('42')
        (bundle/'grade.py.txt').write_text('''import json,pathlib,sys
outcome=json.loads((pathlib.Path(sys.argv[1])/'result.json').read_text()) == json.loads(pathlib.Path(sys.argv[2]).read_text())
print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic',grader_version='1')))
sys.exit(0 if outcome else 1)
''')
        self.assert_complete(self.run_code("from pathlib import Path; Path('result.json').write_text('{\"outcome\":true}'); print('passed')"))
        grade = regrade_artifacts(self.work, ['result.json'], bundle, digest(bundle), self.backend, limits=Limits(seconds=5))
        self.assertEqual(grade['status'], 'failed')
        self.assertIs(grade['outcome'], False)

    def test_output_timeout_and_interruption_leave_no_worker_group(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        flood = self.run_code("print('x'*100000)", limits=Limits(seconds=3, output_bytes=1024))
        self.assertEqual(flood['status'], 'output_limit')
        timeout = self.run_code('import time; time.sleep(30)', limits=Limits(seconds=.5))
        self.assertEqual(timeout['status'], 'timeout')
        with patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_code('import time; time.sleep(30)')
        self.assertEqual({p.name for p in self.cgroup_root.iterdir() if p.is_dir()}, before)


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class OfflineKernelTests(OfflineFixture, KernelFixture):
    def test_both_arms_of_all_starter_tasks_use_real_executor_and_pristine_grader(self):
        self.args.cgroup_root = self.cgroup_root
        self.assertEqual(self.runner.evaluate(self.args), 1)
        result = self.results()
        self.assertEqual(len(result['trials']), 14)
        self.assertTrue(result['inputs_unchanged'])
        self.assertFalse(result['automatic_promotion'])
        for trial in result['trials']:
            self.assertEqual(trial['status'], 'passed' if trial['version'] == 'candidate' else 'failed')
            self.assertIs(trial['grader']['outcome'], trial['version'] == 'candidate')
            for run in (trial['execution'], trial['grader']['execution']):
                self.assertEqual(run['attestation']['backend'], 'bubblewrap-offline-v1')
                self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class ResourceKernelTests(KernelFixture):
    def setUp(self):
        super().setUp()
        # Test cgroup enforcement independently from namespace containment.
        command = patch.object(self.backend, 'command', side_effect=lambda argv, *args: argv)
        command.start(); self.addCleanup(command.stop)

    def test_aggregate_memory_exhaustion_is_recorded_and_tree_emptied(self):
        code = '''import os,time
for _ in range(2):
 if os.fork() == 0:
  buffer=bytearray(80*1024*1024)
  for offset in range(0,len(buffer),4096): buffer[offset]=1
  time.sleep(30)
  os._exit(0)
time.sleep(30)
'''
        run = self.run_code(code, limits=Limits(seconds=5, memory_bytes=128*1024*1024, processes=16))
        self.assertGreater(run['attestation']['cgroup_v2']['events']['memory.events']['oom_kill'], 0)
        self.assertEqual(run['status'], 'resource_limit')
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_fork_limit_and_cleanup_include_children_that_create_sessions(self):
        code = '''import os,time
pids=[]
for _ in range(20):
 try: pid=os.fork()
 except OSError: break
 if pid == 0:
  os.setsid(); os.close(1); os.close(2); time.sleep(30); os._exit(0)
 pids.append(pid)
print(*pids,flush=True)
'''
        run = self.run_code(code, limits=Limits(seconds=5, processes=6))
        self.assertEqual(run['status'], 'resource_limit')
        self.assertGreater(run['attestation']['cgroup_v2']['events']['pids.events']['max'], 0)
        for pid in run['stdout'].split():
            path = Path('/proc')/pid/'stat'
            if path.exists():
                try: self.assertEqual(path.read_text().split(') ')[1].split()[0], 'Z')
                except FileNotFoundError: pass
