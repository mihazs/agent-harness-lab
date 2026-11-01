"""Synthetic control-file tests are separate from opt-in kernel enforcement tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation.cgroup import Cgroup, delegated
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend


class CgroupContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {name: '' for name in ('memory.max', 'memory.swap.max', 'memory.oom.group',
                     'pids.max', 'cpu.max', 'cgroup.kill', 'cgroup.procs')}
        self.files.update({'cgroup.events': 'populated 0\n', 'memory.events': 'max 0\noom 0\noom_kill 0\n',
                           'pids.events': 'max 0\n', 'cpu.stat': 'usage_usec 100\n'})
        self.removed = []
        mkdir, rmdir = os.mkdir, os.rmdir
        def create(name, *args, **kwargs):
            mkdir(name, *args, **kwargs)
            for key, value in self.files.items():
                (self.root/name/key).write_text(value)
        def remove(name, *args, **kwargs):
            folder = self.root/name
            self.removed.append({p.name: p.read_text() for p in folder.iterdir()})
            for path in folder.iterdir(): path.unlink()
            rmdir(name, *args, **kwargs)
        for target, replacement in (('agent_harness_lab.evaluation.cgroup.delegated', lambda root: self.root),
                                    ('agent_harness_lab.evaluation.cgroup.os.mkdir', create),
                                    ('agent_harness_lab.evaluation.cgroup.os.rmdir', remove)):
            mock = patch(target, side_effect=replacement); mock.start(); self.addCleanup(mock.stop)

    def boundary(self):
        return Cgroup(self.root, Limits(), time.monotonic()+5)

    def backend(self):
        backend = SandboxBackend(sys.executable, cgroup_root=self.root)
        command = patch.object(backend, 'command', side_effect=lambda argv, *args: argv)
        command.start(); self.addCleanup(command.stop)
        return backend

    def test_readback_attach_accounting_and_cleanup(self):
        with self.boundary() as group:
            self.assertEqual(group.read('memory.max'), str(Limits().memory_bytes))
            self.assertEqual(group.read('memory.swap.max'), '0')
            self.assertEqual(group.read('cpu.max'), '100000 100000')
            self.assertEqual(group.read('pids.max'), str(Limits().processes))
            group.attach()
        self.assertEqual(self.removed[0]['cgroup.procs'].strip(), '0')
        self.assertTrue(group.report['empty_after_cleanup'])
        self.assertEqual(group.report['events']['cpu.stat']['usage_usec'], 100)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_controller_or_cleanup_primitive_blocks_before_launch(self):
        for name in ('memory.max', 'cgroup.kill'):
            original = self.files.pop(name)
            with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'resource setup failed'):
                    self.backend().run([sys.executable, '-c', 'pass'], self.root, {}, 'grader')
            launch.assert_not_called()
            self.files[name] = original
        self.assertEqual(len(self.removed), 2)

    def test_readback_mismatch_and_elapsed_setup_do_not_launch(self):
        with patch.object(Cgroup, 'read', return_value='max'):
            with self.assertRaisesRegex(ValueError, 'resource setup failed'): self.boundary().__enter__()
        with self.assertRaises(TimeoutError): Cgroup(self.root, Limits(), 0).__enter__()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_control_is_never_followed(self):
        with self.boundary() as group:
            path = self.root/group.name/'cgroup.procs'
            path.unlink(); path.symlink_to(self.root/'foreign')
            with self.assertRaises(OSError): group.attach()
            path.unlink(); path.write_text('')
        self.assertFalse((self.root/'foreign').exists())

    def test_child_attach_failure_cannot_execute_command(self):
        self.files.pop('cgroup.procs')
        with self.assertRaises(subprocess.SubprocessError):
            self.backend().run([sys.executable, '-c', "open('escaped','w').close()"], self.root, {}, 'grader')
        self.assertFalse((self.root/'escaped').exists())
        self.assertEqual(len(self.removed), 1)

    def test_swallowed_resource_denial_invalidates_zero_exit(self):
        self.files['pids.events'] = 'max 1\n'
        run = self.backend().run([sys.executable, '-c', 'pass'], self.root, {}, 'executor')
        self.assertEqual(run['exit_code'], 0)
        self.assertEqual(run['status'], 'resource_limit')
        self.assertFalse(run['attestation']['automatic_promotion'])
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_cleanup_accounting_or_nonempty_tree_never_attests_success(self):
        with self.assertRaisesRegex(ValueError, 'cleanup/accounting failed'):
            with self.boundary() as group:
                (self.root/group.name/'memory.events').write_text('oom 0\n')
        self.assertIsNone(group.report)
        group = self.boundary().__enter__()
        with patch.object(group, 'counts', return_value={'populated': 1}), \
                patch('agent_harness_lab.evaluation.cgroup.time.monotonic', side_effect=[0, 3]):
            with self.assertRaisesRegex(ValueError, 'cleanup/accounting failed'): group.__exit__()
        self.assertIsNone(group.report)

    def test_launch_failure_and_interruption_cleanup_the_created_group(self):
        for failure in (OSError('synthetic launch failure'), KeyboardInterrupt()):
            with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen', side_effect=failure):
                with self.assertRaises(type(failure)):
                    self.backend().run([sys.executable, '-c', 'pass'], self.root, {}, 'grader')
        self.assertEqual(len(self.removed), 2)


class DelegationTests(unittest.TestCase):
    def test_mount_identity_ownership_and_enabled_controllers_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            mount = Path(folder); root = mount/'delegated'; root.mkdir()
            (root/'cgroup.subtree_control').write_text('cpu memory pids')
            original = Path.read_text
            def read(path, *args, **kwargs):
                if str(path) == '/proc/self/mountinfo':
                    return f'1 0 0:1 / {mount} rw - cgroup2 cgroup rw\n'
                return original(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                self.assertEqual(delegated(root), root)
                with self.assertRaises(ValueError): delegated(mount)
                with patch('agent_harness_lab.evaluation.cgroup.os.geteuid', return_value=0):
                    with self.assertRaises(ValueError): delegated(root)
                (root/'cgroup.subtree_control').write_text('cpu pids')
                with self.assertRaisesRegex(ValueError, 'already be enabled'): delegated(root)

    def test_plain_directory_and_symlink_cannot_become_cgroup_attestations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'link').symlink_to(root, target_is_directory=True)
            for path in (root, root/'link'):
                with self.assertRaises(ValueError): delegated(path)

    def test_missing_delegation_is_explicitly_unsupported_without_worker(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
            backend = SandboxBackend(sys.executable, cgroup_root=folder)
            result = backend.capability()
        self.assertEqual(result['status'], 'unsupported')
        self.assertFalse(result['automatic_promotion'])
        launch.assert_not_called()

    def test_zero_exit_with_resource_denial_is_not_an_available_capability(self):
        backend = SandboxBackend(sys.executable)
        result = dict(exit_code=0, status='resource_limit', inputs_unchanged=True, attestation={'cgroup_v2': None})
        with patch.object(backend, 'run', return_value=result):
            self.assertEqual(backend.capability()['status'], 'unsupported')

    def test_cli_checks_explicit_unavailable_subtree_without_fallback(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from agent_harness_lab.evaluation.cli import main
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()) as out:
            code = main(['sandbox-check', '--cgroup-root', folder])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out.getvalue())['status'], 'unsupported')
