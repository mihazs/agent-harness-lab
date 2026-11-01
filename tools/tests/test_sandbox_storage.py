"""Controller-side storage protocol and bounded export, without kernel privileges."""
import array
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

from agent_harness_lab.evaluation import workspace
from agent_harness_lab.evaluation import seccomp
from agent_harness_lab.evaluation.integrity import tree
from agent_harness_lab.evaluation.sandbox import SandboxBackend


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'; self.source.mkdir()
        self.private = self.root/'private'; self.private.mkdir(mode=0o700)
        self.backend = SandboxBackend(sys.executable, cgroup_root=self.root, disk_bytes=65536)
        self.group = Mock(controls={'memory.max': '65536'})

    def scratch(self, outputs=()):
        return workspace.PrivateWorkspace(self.backend, self.source, {}, 'executor',
                                          ['/usr/bin/true'], 65536, time.monotonic()+3, outputs)

    def opened(self, outputs=()):
        scratch = self.enterContext(self.scratch(outputs))
        scratch.fd = os.open(self.private, os.O_RDONLY|os.O_DIRECTORY)
        return scratch

    def test_policy_requires_cgroups_and_page_aligned_positive_integer_capacity(self):
        for total in (None, True, 0, -1, 16385, '65536', 1.0):
            with self.subTest(total=total), self.assertRaises(ValueError): workspace.policy(total)
        with self.assertRaises(ValueError): SandboxBackend(disk_bytes=65536)
        for values in ('result', {}, False, ['../escape'], ['a//b'], ['a\0b'], ['same', 'same'], ['a/../b']):
            with self.subTest(values=values), self.assertRaises(ValueError): self.scratch(values)
        with self.assertRaisesRegex(ValueError, 'explicit artifact allowlist'):
            self.backend.run(['/usr/bin/true'], self.source, {}, 'executor')
        with self.assertRaisesRegex(ValueError, 'graders cannot export'):
            self.backend.run(['/usr/bin/true'], self.source, {}, 'grader', artifact_allowlist=['result'])

    def test_handoff_is_framed_verified_and_seeds_before_worker_release(self):
        (self.source/'seed').write_text('synthetic')
        with self.scratch() as scratch:
            sender = scratch.sender.dup()
            errors = []
            def serve():
                try:
                    with sender:
                        descriptor = os.open(self.private, os.O_RDONLY|os.O_DIRECTORY)
                        try:
                            sender.sendmsg([workspace.MAGIC[:2]], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [descriptor]))])
                        finally: os.close(descriptor)
                        sender.sendall(workspace.MAGIC[2:])
                        size = struct.unpack('!I', sender.recv(4, socket.MSG_WAITALL))[0]
                        self.assertEqual(sender.recv(size, socket.MSG_WAITALL), scratch.frame)
                        self.assertEqual((self.private/'seed').read_text(), 'synthetic')
                        sender.sendall(workspace.READY[:3]); sender.sendall(workspace.READY[3:])
                except BaseException as exc: errors.append(exc)
            thread = threading.Thread(target=serve, daemon=True); thread.start()
            capacity = SimpleNamespace(f_flag=0, f_blocks=32768, f_bfree=28672, f_frsize=1)
            with patch.object(workspace.os, 'fstatvfs', return_value=capacity): scratch.start(self.group)
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(scratch.verified)
            self.assertFalse(os.get_inheritable(scratch.fd))
            self.group.limit_memory.assert_called_once_with(65536-4096)

    def test_malformed_missing_extra_or_wrong_type_descriptors_are_closed(self):
        for payload, count, is_file in ((b'invalid', 1, False), (workspace.MAGIC, 0, False),
                                      (workspace.MAGIC, 3, False), (workspace.MAGIC, 1, True)):
            with self.subTest(count=count, is_file=is_file), self.scratch() as scratch:
                target = self.private
                if is_file:
                    target = self.root/'file'; target.write_text('synthetic')
                descriptor = os.open(target, os.O_RDONLY)
                before = set(os.listdir('/proc/self/fd'))
                try:
                    control = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [descriptor]*count))] if count else []
                    scratch.sender.sendmsg([payload], control)
                    with self.assertRaises((OSError, ValueError)): scratch.start(self.group)
                finally: os.close(descriptor)
                self.assertEqual(len(os.listdir('/proc/self/fd')), len(before)-2)

    def test_wrong_capacity_and_expired_startup_never_verify(self):
        with self.scratch() as scratch:
            descriptor = os.open(self.private, os.O_RDONLY|os.O_DIRECTORY)
            try:
                scratch.sender.sendmsg([workspace.MAGIC], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [descriptor]))])
            finally: os.close(descriptor)
            capacity = SimpleNamespace(f_flag=0, f_blocks=1, f_frsize=1)
            with patch.object(workspace.os, 'fstatvfs', return_value=capacity), self.assertRaises(ValueError): scratch.start(self.group)
            self.assertFalse(scratch.verified)
        scratch = self.opened(); scratch.deadline = 0
        with self.assertRaises(TimeoutError): scratch.timeout()

    def test_syscall_policy_is_sealed_and_unsupported_architectures_fail_closed(self):
        descriptor, record = seccomp.sealed_filter()
        try:
            self.assertEqual(os.pread(descriptor, 65536, 0), seccomp.program())
            self.assertEqual(len(record['sha256']), 64)
            with self.assertRaises(PermissionError): os.write(descriptor, b'changed')
            with self.assertRaises(PermissionError): os.ftruncate(descriptor, 0)
        finally: os.close(descriptor)
        with patch.object(seccomp.platform, 'machine', return_value='unsupported'):
            with self.assertRaises(ValueError): seccomp.program()

    def test_existing_hugetlb_backing_mounts_cannot_bypass_flag_filtering(self):
        inventory = b'1 0 0:1 / /data/huge\\040pages rw - hugetlbfs none rw\n'
        for root in ('/data', '/data/huge pages/nested'):
            with patch.object(seccomp.Path, 'open', mock_open(read_data=inventory)):
                with self.assertRaises(ValueError): seccomp.ordinary_filesystems([root])
        with patch.object(seccomp.Path, 'open', mock_open(read_data=inventory)):
            seccomp.ordinary_filesystems(['/ordinary'])

    def test_changed_seed_and_empty_directory_depth_are_bounded(self):
        (self.source/'seed').write_text('synthetic')
        scratch = self.opened()
        with patch.object(workspace, 'read_regular', return_value=b'changed'), self.assertRaises(ValueError): scratch.seed()
        deep = self.source.joinpath(*(['nested']*65)); deep.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'depth bound'): tree(self.source)

    def test_export_validates_all_files_before_writing_any_host_output(self):
        (self.private/'good').write_text('synthetic')
        (self.private/'bad').symlink_to(self.private/'good')
        scratch = self.opened(['good', 'bad'])
        with self.assertRaises(OSError): scratch.extract()
        self.assertEqual(list(self.source.iterdir()), [])
        (self.private/'bad').unlink(); os.mkfifo(self.private/'bad')
        with self.assertRaises(ValueError): scratch.extract()
        (self.private/'bad').unlink(); os.link(self.private/'good', self.private/'bad')
        with self.assertRaises(ValueError): scratch.extract()

    def test_ancestor_link_and_total_export_limit_cannot_escape(self):
        (self.private/'link').symlink_to(self.source, target_is_directory=True)
        scratch = self.opened(['link/file'])
        with self.assertRaises(OSError): scratch.extract()
        (self.private/'one').write_bytes(b'x'*(768*1024))
        (self.private/'two').write_bytes(b'x'*(768*1024))
        scratch.outputs = ('one', 'two')
        with self.assertRaises(ValueError): scratch.extract()
        self.assertEqual(list(self.source.iterdir()), [])
