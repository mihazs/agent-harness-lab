"""Opt-in real kernel boundaries; synthetic fixtures, no inference or credentials."""
from contextlib import contextmanager
import errno
import os
import json
import re
import shutil
import subprocess
from pathlib import Path
import selectors
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation.bank_tools import regrade_artifacts
from agent_harness_lab.evaluation.cgroup import Cgroup, delegated
from agent_harness_lab.evaluation.integrity import artifacts, digest
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend
from agent_harness_lab.evaluation.image import MOUNT_DIRS, scan
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

    def run_code(self, code, *, inputs=None, role='executor', limits=Limits(seconds=5), **options):
        return self.backend.run(['/usr/bin/python3', '-c', code], self.work, inputs or {}, role, limits, **options)

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
class PinnedImageKernelTests(OfflineFixture, KernelFixture):
    def test_pinned_image_is_used_by_executor_pristine_grader_and_nested_checks(self):
        rootfs = self.root/'runtime'; rootfs.mkdir(mode=0o700)
        for name in MOUNT_DIRS: (rootfs/name).mkdir(parents=True, exist_ok=True)
        environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
        stdlib = Path(subprocess.check_output(['/usr/bin/python3', '-I', '-c',
            'import sysconfig; print(sysconfig.get_path("stdlib"))'], env=environment, text=True).strip())
        shutil.copytree(stdlib, rootfs/stdlib.relative_to('/'), dirs_exist_ok=True,
            ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'tests', 'ensurepip', 'idlelib', 'tkinter'))
        binaries = [Path(p) for p in ('/usr/bin/python3', '/usr/bin/true', '/usr/bin/bwrap')]
        extensions = list((stdlib/'lib-dynload').glob('*.so'))
        libraries = subprocess.check_output(['ldd', *map(str, binaries+extensions)], env=environment, text=True)
        dependencies = {Path(p) for p in re.findall(r'(/[^\s():]+)', libraries) if Path(p).is_file()}
        for source in binaries+sorted(dependencies):
            target = rootfs/source.relative_to('/'); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (rootfs/'image-marker').write_text('SYNTHETIC_PINNED_IMAGE')
        frozen = scan(rootfs)
        backend = SandboxBackend(cgroup_root=self.cgroup_root, image_root=rootfs, image_sha256=frozen['sha256'], disk_bytes=64*1024*1024)
        scratch = self.root/'scratch'; scratch.mkdir()
        code = '''from pathlib import Path
assert Path('/image-marker').read_text() == 'SYNTHETIC_PINNED_IMAGE'
assert not Path('/etc/os-release').exists()
assert not Path('/inputs/oracle').exists()
try: Path('/image-marker').write_text('changed'); raise AssertionError('image is writable')
except OSError: pass
'''
        run = backend.run(['/usr/bin/python3', '-I', '-c', code], scratch, {}, 'executor', Limits(seconds=5), artifact_allowlist=[])
        self.assert_complete(run)
        self.assertTrue(run['attestation']['runtime_image']['verified_after'])
        self.args.cgroup_root = self.cgroup_root
        self.args.image_root, self.args.image_sha256 = rootfs, frozen['sha256']
        self.args.disk_bytes = 64*1024*1024
        self.assertEqual(self.runner.evaluate(self.args), 1)
        result = self.results()
        self.assertEqual(len(result['trials']), 14)
        for trial in result['trials']:
            self.assertEqual(trial['status'], 'passed' if trial['version'] == 'candidate' else 'failed')
            for execution in (trial['execution'], trial['grader']['execution']):
                self.assertTrue(execution['attestation']['aggregate_disk_bound_verified'])
                attested = execution['attestation']['runtime_image']
                self.assertEqual(attested['sha256'], frozen['sha256'])
                self.assertTrue(attested['verified_before'] and attested['verified_after'])


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class StorageKernelTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root, disk_bytes=64*1024*1024)

    def run_code(self, code, **options):
        options.setdefault('artifact_allowlist', [])
        return super().run_code(code, **options)

    def test_working_state_retains_nonzero_edits_between_workers_and_discards_timeout_changes(self):
        from agent_harness_lab.evaluation.working_state import WorkingState
        (self.work/'empty').mkdir(); (self.work/'seed').write_text('original')
        state = WorkingState(self.work,time.monotonic()+10)
        def execute(code,seconds=5):
            return self.backend.run(['/usr/bin/python3','-c',code],self.work,{},'executor',
                                    Limits(seconds=seconds),working_state=state)
        first = execute("from pathlib import Path; Path('empty').rmdir(); Path('seed').unlink(); Path('result').write_text('edited'); raise SystemExit(1)")
        self.assertEqual(first['status'],'failed'); self.assertEqual(first['exit_code'],1)
        self.assertTrue(first['attestation']['cgroup_v2']['empty_after_cleanup'])
        self.assertIsNotNone(first['attestation']['storage_policy']['extracted'])
        second = execute("from pathlib import Path; assert not Path('empty').exists(); assert not Path('seed').exists(); assert Path('result').read_text()=='edited'; Path('result').write_text('final')")
        self.assert_complete(second)
        before = state.snapshot
        failed = execute("from pathlib import Path; import time; Path('result').write_text('partial'); time.sleep(30)",.5)
        self.assertEqual(failed['status'],'timeout'); self.assertIs(state.snapshot,before)
        self.assertTrue(failed['attestation']['cgroup_v2']['empty_after_cleanup'])
        signaled = execute("from pathlib import Path; import os,signal; Path('result').write_text('killed'); os.kill(os.getpid(),signal.SIGKILL)")
        self.assertTrue(signaled['exit_code']<0 or signaled['exit_code']>=128)
        self.assertIs(state.snapshot,before)
        self.assertIsNone(signaled['attestation']['storage_policy']['extracted'])
        exported = state.export(['result'],self.root/'artifacts',time.monotonic()+5)
        self.assertEqual((exported/'result').read_text(),'final')
        self.assertEqual((self.work/'seed').read_text(),'original'); self.assertTrue((self.work/'empty').is_dir())

    def test_every_writable_volume_enforces_aggregate_capacity_without_leaking_setup_fds(self):
        code = "import errno,os,stat\nfor value in os.listdir('/proc/self/fd'):\n if int(value)>2:\n  try: info=os.fstat(int(value))\n  except OSError: continue\n  assert not stat.S_ISDIR(info.st_mode), 'setup handle leaked'\nfor path,cap in (('/work',32*1024*1024),('/tmp',16*1024*1024),('/dev/shm',16*1024*1024)):\n info=os.statvfs(path)\n assert info.f_blocks*info.f_frsize == cap\n denied=False\n for number in range(128):\n  try:\n   with open(path+'/'+str(number),'wb') as stream: stream.write(b'x'*(512*1024))\n  except OSError as error:\n   assert error.errno == errno.ENOSPC\n   denied=True; break\n assert denied, path+' exceeded aggregate quota'\n assert os.statvfs(path).f_bavail == 0\n for name in os.listdir(path): os.unlink(path+'/'+name)\nprint('all-volumes-bounded')\n"
        run = self.run_code(code)
        self.assert_complete(run)
        self.assertIn('all-volumes-bounded', run['stdout'])
        self.assertTrue(run['attestation']['aggregate_disk_bound_verified'])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_seed_and_only_allowlisted_artifacts_survive_worker_cleanup(self):
        source = self.work/'seed'; source.write_text('original'); source.chmod(0o755)
        (self.work/'empty').mkdir()
        code = "from pathlib import Path\nassert Path('seed').read_text() == 'original'\nassert Path('seed').stat().st_mode & 0o777 == 0o755\nassert Path('empty').is_dir()\nPath('seed').write_text('changed')\nPath('result').write_text('allowed')\nPath('unlisted').write_text('discarded')\n"
        run = self.run_code(code, artifact_allowlist=['seed', 'result'])
        self.assert_complete(run)
        self.assertEqual(source.read_text(), 'changed')
        self.assertEqual(source.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.work/'result').read_text(), 'allowed')
        self.assertFalse((self.work/'unlisted').exists())
        self.assertEqual(run['attestation']['storage_policy']['extracted'], dict(artifacts=2, bytes=14))

    def test_unsafe_or_oversized_outputs_are_rejected_before_export(self):
        for code in ("os.symlink('/etc/passwd','result')",
                     "Path('original').write_text('x'); os.link('original','result')",
                     "os.mkfifo('result')", "Path('dir').mkdir(); os.symlink('dir','result')",
                     "Path('result').write_bytes(b'x'*(1024*1024+1))"):
            with self.subTest(code=code), self.assertRaises((OSError, ValueError)):
                self.run_code('import os; from pathlib import Path; '+code,
                    artifact_allowlist=['result'], limits=Limits(seconds=5, file_bytes=2*1024*1024))
            self.assertEqual(list(self.work.iterdir()), [])

    def test_failure_timeout_and_interruption_do_not_export_and_leave_no_cgroup(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        for suffix, expected in (("raise SystemExit(1)", 'failed'), ("import time; time.sleep(30)", 'timeout')):
            run = self.run_code("from pathlib import Path; Path('result').write_text('untrusted'); "+suffix,
                                artifact_allowlist=['result'], limits=Limits(seconds=.5))
            self.assertEqual(run['status'], expected)
            self.assertFalse((self.work/'result').exists())
        with patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_code('import time; time.sleep(30)')
        self.assertEqual({p.name for p in self.cgroup_root.iterdir() if p.is_dir()}, before)

    def test_memfd_and_new_tmpfs_cannot_bypass_the_aggregate_storage_ceiling(self):
        filler = "import os; f=open('data','wb'); [f.write(b'x'*65536) for _ in range(2048)]"
        new_mount = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--ro-bind', '/', '/',
                     '--proc', '/proc', '--dev', '/dev', '--size', str(128*1024*1024),
                     '--tmpfs', '/tmp', '--chdir', '/tmp', '--', '/usr/bin/python3', '-c', filler]
        attacks = ["import os; fd=os.memfd_create('bypass'); [os.write(fd,b'x'*65536) for _ in range(2048)]",
                   f"import os; os.execv('/usr/bin/bwrap',{new_mount!r})"]
        (self.work/'seed').write_bytes(b'x'*(1024*1024))
        for code in attacks:
            with self.subTest(code=code):
                run = self.run_code(code, limits=Limits(seconds=5, file_bytes=128*1024*1024))
                self.assertEqual(run['status'], 'resource_limit', run)
                controls = run['attestation']['cgroup_v2']
                self.assertEqual(run['attestation']['storage_policy']['controller_seeded_bytes'], 1024*1024)
                self.assertEqual(int(controls['controls']['memory.max']), 63*1024*1024)
                self.assertGreater(controls['events']['memory.events']['oom_kill'], 0)
                self.assertTrue(controls['empty_after_cleanup'])

    def test_capability_verifies_the_storage_policy(self):
        result = self.backend.capability()
        self.assertEqual(result['status'], 'available', result)
        self.assertTrue(result['storage_policy']['verified'])
        self.assertNotIn('no aggregate disk quota', result['limitations'])

    def test_hugetlb_memfd_mapping_and_shared_memory_are_denied(self):
        code = "import ctypes,errno,mmap,os\ntry: os.memfd_create('hugetlb-probe',os.MFD_HUGETLB)\nexcept OSError as error: assert error.errno == errno.EPERM\nelse: raise AssertionError('HugeTLB memfd allowed')\nlibc=ctypes.CDLL(None,use_errno=True)\nlibc.mmap.restype=ctypes.c_void_p\nmapped=libc.mmap(None,2*1024*1024,3,mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS|0x40000,-1,0)\nassert mapped == ctypes.c_void_p(-1).value and ctypes.get_errno() == errno.EPERM\nassert libc.shmget(0,2*1024*1024,0o600|0o1000|0o4000) == -1\nassert ctypes.get_errno() == errno.EPERM\nprint('hugetlb-denied')\n"
        run = self.run_code(code, limits=Limits(seconds=5, file_bytes=4*1024*1024))
        self.assert_complete(run)
        self.assertIn('hugetlb-denied', run['stdout'])
        self.assertEqual(run['attestation']['storage_policy']['hugetlb_policy']['name'], 'seccomp-deny-hugetlb-v1')

    def test_alternate_syscall_numbers_cannot_bypass_the_storage_filter(self):
        code = "import ctypes; ctypes.CDLL(None).syscall(0x4000013f,0,4); raise AssertionError('alternate syscall returned')"
        run = self.run_code(code)
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['stderr'], '')
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_startup_verification_and_tiny_budget_fail_closed_and_cleanup(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        with patch('agent_harness_lab.evaluation.workspace.VERIFY', "raise RuntimeError('synthetic startup failure')"):
            with self.assertRaises(ValueError): self.run_code("raise AssertionError('worker must not start')")
        (self.work/'seed').write_bytes(b'x'*(5*1024*1024))
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root, disk_bytes=16384)
        with self.assertRaises((OSError, ValueError)): self.run_code("raise AssertionError('worker must not start')")
        self.assertEqual((self.work/'seed').stat().st_size, 5*1024*1024)
        self.assertEqual({p.name for p in self.cgroup_root.iterdir() if p.is_dir()}, before)


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
