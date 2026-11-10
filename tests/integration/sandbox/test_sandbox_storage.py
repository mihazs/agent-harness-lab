"""Controller-side storage protocol and bounded export, without kernel privileges."""
import array
from contextlib import ExitStack
import os
import socket
import struct
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

import pytest

from agent_harness_lab.evaluation import workspace
from agent_harness_lab.evaluation import seccomp
from agent_harness_lab.evaluation.integrity import tree
from agent_harness_lab.evaluation.sandbox import SandboxBackend


class StorageFixture:
    """Controller-side storage builder; test_sandbox_working_state builds it through close()."""
    def __init__(self, root):
        self.stack = ExitStack()
        self.root = root
        self.source = self.root/'source'; self.source.mkdir()
        self.private = self.root/'private'; self.private.mkdir(mode=0o700)
        self.backend = SandboxBackend(sys.executable, cgroup_root=self.root, disk_bytes=65536)
        self.group = Mock(controls={'memory.max': '65536'})

    def close(self):
        self.stack.close()

    def scratch(self, outputs=()):
        return workspace.PrivateWorkspace(self.backend, self.source, {}, 'executor',
                                          ['/usr/bin/true'], 65536, time.monotonic()+3, outputs)

    def opened(self, outputs=()):
        scratch = self.stack.enter_context(self.scratch(outputs))
        scratch.fd = os.open(self.private, os.O_RDONLY|os.O_DIRECTORY)
        return scratch


@pytest.fixture
def storage(tmp_path):
    fixture = StorageFixture(tmp_path)
    yield fixture
    fixture.close()


class TestStorage:
    @pytest.mark.parametrize('total', [
        pytest.param(None, id='none'), pytest.param(True, id='bool'), pytest.param(0, id='zero'),
        pytest.param(-1, id='negative'), pytest.param(16385, id='not-page-aligned'),
        pytest.param('65536', id='string'), pytest.param(1.0, id='float'),
    ])
    def test_policy_requires_page_aligned_positive_integer_capacity(self, total):
        with pytest.raises(ValueError): workspace.policy(total)

    def test_policy_requires_cgroups(self):
        with pytest.raises(ValueError): SandboxBackend(disk_bytes=65536)

    @pytest.mark.parametrize('values', [
        pytest.param('result', id='string'), pytest.param({}, id='mapping'), pytest.param(False, id='bool'),
        pytest.param(['../escape'], id='parent-escape'), pytest.param(['a//b'], id='empty-segment'),
        pytest.param(['a\0b'], id='nul'), pytest.param(['same', 'same'], id='duplicate'),
        pytest.param(['a/../b'], id='embedded-parent'),
    ])
    def test_policy_rejects_unsafe_output_allowlists(self, storage, values):
        with pytest.raises(ValueError): storage.scratch(values)

    def test_policy_requires_an_explicit_executor_allowlist_and_no_grader_export(self, storage):
        with pytest.raises(ValueError, match='explicit artifact allowlist'):
            storage.backend.run(['/usr/bin/true'], storage.source, {}, 'executor')
        with pytest.raises(ValueError, match='graders cannot export'):
            storage.backend.run(['/usr/bin/true'], storage.source, {}, 'grader', artifact_allowlist=['result'])

    def test_handoff_is_framed_verified_and_seeds_before_worker_release(self, storage):
        (storage.source/'seed').write_text('synthetic')
        with storage.scratch() as scratch:
            sender = scratch.sender.dup()
            errors = []
            def serve():
                try:
                    with sender:
                        descriptor = os.open(storage.private, os.O_RDONLY|os.O_DIRECTORY)
                        try:
                            sender.sendmsg([workspace.MAGIC[:2]], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [descriptor]))])
                        finally: os.close(descriptor)
                        sender.sendall(workspace.MAGIC[2:])
                        size = struct.unpack('!I', sender.recv(4, socket.MSG_WAITALL))[0]
                        assert sender.recv(size, socket.MSG_WAITALL) == scratch.frame
                        assert (storage.private/'seed').read_text() == 'synthetic'
                        sender.sendall(workspace.READY[:3]); sender.sendall(workspace.READY[3:])
                except BaseException as exc: errors.append(exc)
            thread = threading.Thread(target=serve, daemon=True); thread.start()
            capacity = SimpleNamespace(f_flag=0, f_blocks=32768, f_bfree=28672, f_frsize=1)
            with patch.object(workspace.os, 'fstatvfs', return_value=capacity): scratch.start(storage.group)
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert errors == []
            assert scratch.verified
            assert not os.get_inheritable(scratch.fd)
            storage.group.limit_memory.assert_called_once_with(65536-4096)

    @pytest.mark.parametrize('payload,count,is_file', [
        pytest.param(b'invalid', 1, False, id='bad-magic'),
        pytest.param(workspace.MAGIC, 0, False, id='missing-descriptor'),
        pytest.param(workspace.MAGIC, 3, False, id='extra-descriptors'),
        pytest.param(workspace.MAGIC, 1, True, id='wrong-type'),
    ])
    def test_malformed_missing_extra_or_wrong_type_descriptors_are_closed(self, storage, payload, count, is_file):
        with storage.scratch() as scratch:
            target = storage.private
            if is_file:
                target = storage.root/'file'; target.write_text('synthetic')
            descriptor = os.open(target, os.O_RDONLY)
            before = set(os.listdir('/proc/self/fd'))
            try:
                control = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [descriptor]*count))] if count else []
                scratch.sender.sendmsg([payload], control)
                with pytest.raises((OSError, ValueError)): scratch.start(storage.group)
            finally: os.close(descriptor)
            assert len(os.listdir('/proc/self/fd')) == len(before)-2

    def test_wrong_capacity_and_expired_startup_never_verify(self, storage):
        with storage.scratch() as scratch:
            descriptor = os.open(storage.private, os.O_RDONLY|os.O_DIRECTORY)
            try:
                scratch.sender.sendmsg([workspace.MAGIC], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [descriptor]))])
            finally: os.close(descriptor)
            capacity = SimpleNamespace(f_flag=0, f_blocks=1, f_frsize=1)
            with patch.object(workspace.os, 'fstatvfs', return_value=capacity), pytest.raises(ValueError): scratch.start(storage.group)
            assert not scratch.verified
        scratch = storage.opened(); scratch.deadline = 0
        with pytest.raises(TimeoutError): scratch.timeout()

    def test_syscall_policy_is_sealed_and_unsupported_architectures_fail_closed(self):
        descriptor, record = seccomp.sealed_filter()
        try:
            assert os.pread(descriptor, 65536, 0) == seccomp.program()
            assert len(record['sha256']) == 64
            with pytest.raises(PermissionError): os.write(descriptor, b'changed')
            with pytest.raises(PermissionError): os.ftruncate(descriptor, 0)
        finally: os.close(descriptor)
        with patch.object(seccomp.platform, 'machine', return_value='unsupported'):
            with pytest.raises(ValueError): seccomp.program()

    def test_existing_hugetlb_backing_mounts_cannot_bypass_flag_filtering(self):
        inventory = b'1 0 0:1 / /data/huge\\040pages rw - hugetlbfs none rw\n'
        for root in ('/data', '/data/huge pages/nested'):
            with patch.object(seccomp.Path, 'open', mock_open(read_data=inventory)):
                with pytest.raises(ValueError): seccomp.ordinary_filesystems([root])
        with patch.object(seccomp.Path, 'open', mock_open(read_data=inventory)):
            seccomp.ordinary_filesystems(['/ordinary'])

    def test_changed_seed_and_empty_directory_depth_are_bounded(self, storage):
        (storage.source/'seed').write_text('synthetic')
        scratch = storage.opened()
        with patch.object(workspace, 'read_regular', return_value=b'changed'), pytest.raises(ValueError): scratch.seed()
        deep = storage.source.joinpath(*(['nested']*65)); deep.mkdir(parents=True)
        with pytest.raises(ValueError, match='depth bound'): tree(storage.source)

    def test_export_validates_all_files_before_writing_any_host_output(self, storage):
        (storage.private/'good').write_text('synthetic')
        (storage.private/'bad').symlink_to(storage.private/'good')
        scratch = storage.opened(['good', 'bad'])
        with pytest.raises(OSError): scratch.extract()
        assert list(storage.source.iterdir()) == []
        (storage.private/'bad').unlink(); os.mkfifo(storage.private/'bad')
        with pytest.raises(ValueError): scratch.extract()
        (storage.private/'bad').unlink(); os.link(storage.private/'good', storage.private/'bad')
        with pytest.raises(ValueError): scratch.extract()

    def test_ancestor_link_and_total_export_limit_cannot_escape(self, storage):
        (storage.private/'link').symlink_to(storage.source, target_is_directory=True)
        scratch = storage.opened(['link/file'])
        with pytest.raises(OSError): scratch.extract()
        (storage.private/'one').write_bytes(b'x'*(768*1024))
        (storage.private/'two').write_bytes(b'x'*(768*1024))
        scratch.outputs = ('one', 'two')
        with pytest.raises(ValueError): scratch.extract()
        assert list(storage.source.iterdir()) == []
