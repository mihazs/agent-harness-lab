"""Synthetic control-file tests are separate from opt-in kernel enforcement tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.cgroup import Cgroup, delegated
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend


class Contract(SimpleNamespace):
    """Synthetic cgroup control files under tmp_path, with mkdir/rmdir and delegation patched to them."""

    def boundary(self):
        return Cgroup(self.root, Limits(), time.monotonic()+5)

    def backend(self):
        backend = SandboxBackend(sys.executable, cgroup_root=self.root)
        self.stack.enter_context(patch.object(backend, 'command', side_effect=lambda argv, *args: argv))
        return backend


@pytest.fixture
def cg(tmp_path):
    files = {name: '' for name in ('memory.max', 'memory.swap.max', 'memory.oom.group',
             'pids.max', 'cpu.max', 'cgroup.kill', 'cgroup.procs')}
    files.update({'cgroup.events': 'populated 0\n', 'memory.events': 'max 0\noom 0\noom_kill 0\n',
                  'pids.events': 'max 0\n', 'cpu.stat': 'usage_usec 100\n'})
    removed = []
    mkdir, rmdir = os.mkdir, os.rmdir
    def create(name, *args, **kwargs):
        mkdir(name, *args, **kwargs)
        for key, value in files.items():
            (tmp_path/name/key).write_text(value)
    def remove(name, *args, **kwargs):
        folder = tmp_path/name
        removed.append({p.name: p.read_text() for p in folder.iterdir()})
        for path in folder.iterdir(): path.unlink()
        rmdir(name, *args, **kwargs)
    with ExitStack() as stack:
        for target, replacement in (('agent_harness_lab.evaluation.cgroup.delegated', lambda root: tmp_path),
                                    ('agent_harness_lab.evaluation.cgroup.os.mkdir', create),
                                    ('agent_harness_lab.evaluation.cgroup.os.rmdir', remove)):
            stack.enter_context(patch(target, side_effect=replacement))
        yield Contract(root=tmp_path, files=files, removed=removed, stack=stack)


def test_readback_attach_accounting_and_cleanup(cg):
    with cg.boundary() as group:
        assert group.read('memory.max') == str(Limits().memory_bytes)
        assert group.read('memory.swap.max') == '0'
        assert group.read('cpu.max') == '100000 100000'
        assert group.read('pids.max') == str(Limits().processes)
        group.attach()
    assert cg.removed[0]['cgroup.procs'].strip() == '0'
    assert group.report['empty_after_cleanup']
    assert group.report['events']['cpu.stat']['usage_usec'] == 100
    assert list(cg.root.iterdir()) == []


def test_missing_controller_or_cleanup_primitive_blocks_before_launch(cg):
    for name in ('memory.max', 'cgroup.kill'):
        original = cg.files.pop(name)
        with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
            with pytest.raises(ValueError, match='resource setup failed'):
                cg.backend().run([sys.executable, '-c', 'pass'], cg.root, {}, 'grader')
        launch.assert_not_called()
        cg.files[name] = original
    assert len(cg.removed) == 2


def test_storage_memory_ceiling_can_only_tighten_and_requires_readback(cg):
    with cg.boundary() as group:
        for value in (0, -1, True, Limits().memory_bytes+1):
            with pytest.raises(ValueError): group.limit_memory(value)
        def write(name, value): (cg.root/group.name/name).write_text(value)
        with patch.object(group, 'write', side_effect=write): group.limit_memory(65536)
        assert group.read('memory.max') == '65536'
        assert group.controls['memory.max'] == '65536'
        with patch.object(group, 'read', return_value='max'):
            with pytest.raises(ValueError): group.limit_memory(32768)


def test_readback_mismatch_and_elapsed_setup_do_not_launch(cg):
    with patch.object(Cgroup, 'read', return_value='max'):
        with pytest.raises(ValueError, match='resource setup failed'): cg.boundary().__enter__()
    with pytest.raises(TimeoutError): Cgroup(cg.root, Limits(), 0).__enter__()
    assert list(cg.root.iterdir()) == []


def test_symlink_control_is_never_followed(cg):
    with cg.boundary() as group:
        path = cg.root/group.name/'cgroup.procs'
        path.unlink(); path.symlink_to(cg.root/'foreign')
        with pytest.raises(OSError): group.attach()
        path.unlink(); path.write_text('')
    assert not (cg.root/'foreign').exists()


def test_child_attach_failure_cannot_execute_command(cg):
    cg.files.pop('cgroup.procs')
    with pytest.raises(subprocess.SubprocessError):
        cg.backend().run([sys.executable, '-c', "open('escaped','w').close()"], cg.root, {}, 'grader')
    assert not (cg.root/'escaped').exists()
    assert len(cg.removed) == 1


def test_swallowed_resource_denial_invalidates_zero_exit(cg):
    cg.files['pids.events'] = 'max 1\n'
    run = cg.backend().run([sys.executable, '-c', 'pass'], cg.root, {}, 'executor')
    assert run['exit_code'] == 0
    assert run['status'] == 'resource_limit'
    assert not run['attestation']['automatic_promotion']
    assert run['attestation']['cgroup_v2']['empty_after_cleanup']


def test_cleanup_accounting_or_nonempty_tree_never_attests_success(cg):
    with pytest.raises(ValueError, match='cleanup/accounting failed'):
        with cg.boundary() as group:
            (cg.root/group.name/'memory.events').write_text('oom 0\n')
    assert group.report is None
    group = cg.boundary().__enter__()
    with patch.object(group, 'counts', return_value={'populated': 1}), \
            patch('agent_harness_lab.evaluation.cgroup.time.monotonic', side_effect=[0, 3]):
        with pytest.raises(ValueError, match='cleanup/accounting failed'): group.__exit__()
    assert group.report is None


def test_launch_failure_and_interruption_cleanup_the_created_group(cg):
    for failure in (OSError('synthetic launch failure'), KeyboardInterrupt()):
        with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen', side_effect=failure):
            with pytest.raises(type(failure)):
                cg.backend().run([sys.executable, '-c', 'pass'], cg.root, {}, 'grader')
    assert len(cg.removed) == 2


def test_mount_identity_ownership_and_enabled_controllers_are_required(tmp_path):
    mount = tmp_path; root = mount/'delegated'; root.mkdir()
    (root/'cgroup.subtree_control').write_text('cpu memory pids')
    original = Path.read_text
    original_stat = Path.stat
    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return f'1 0 0:1 / {mount} rw - cgroup2 cgroup rw\n'
        return original(path, *args, **kwargs)
    def stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == root:
            fields = list(value); fields[4] = 1000
            return os.stat_result(fields)
        return value
    with patch.object(Path, 'read_text', read), patch.object(Path, 'stat', stat), \
            patch('agent_harness_lab.evaluation.cgroup.os.geteuid', return_value=1000):
        assert delegated(root) == root
        with pytest.raises(ValueError): delegated(mount)
        with patch('agent_harness_lab.evaluation.cgroup.os.geteuid', return_value=0):
            with pytest.raises(ValueError): delegated(root)
        with patch('agent_harness_lab.evaluation.cgroup.os.geteuid', return_value=1001):
            with pytest.raises(ValueError): delegated(root)
        (root/'cgroup.subtree_control').write_text('cpu pids')
        with pytest.raises(ValueError, match='already be enabled'): delegated(root)


def test_plain_directory_and_symlink_cannot_become_cgroup_attestations(tmp_path):
    root = tmp_path
    (root/'link').symlink_to(root, target_is_directory=True)
    for path in (root, root/'link'):
        with pytest.raises(ValueError): delegated(path)


def test_missing_delegation_is_explicitly_unsupported_without_worker(tmp_path):
    folder = str(tmp_path)
    with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
        backend = SandboxBackend(sys.executable, cgroup_root=folder)
        result = backend.capability()
    assert result['status'] == 'unsupported'
    assert not result['automatic_promotion']
    launch.assert_not_called()


def test_zero_exit_with_resource_denial_is_not_an_available_capability():
    backend = SandboxBackend(sys.executable)
    result = dict(exit_code=0, status='resource_limit', inputs_unchanged=True, attestation={'cgroup_v2': None})
    with patch.object(backend, 'run', return_value=result):
        assert backend.capability()['status'] == 'unsupported'


def test_cli_checks_explicit_unavailable_subtree_without_fallback(tmp_path):
    from contextlib import redirect_stdout
    from io import StringIO
    from agent_harness_lab.evaluation.cli import main
    with redirect_stdout(StringIO()) as out:
        code = main(['sandbox-check', '--cgroup-root', str(tmp_path)])
    assert code == 3
    assert json.loads(out.getvalue())['status'] == 'unsupported'
