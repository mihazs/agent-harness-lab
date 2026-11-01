"""Rootfs pins are controller-selected, complete, bounded and private."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import image
from agent_harness_lab.evaluation.sandbox import SandboxBackend


class RuntimeImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder/'image'; self.root.mkdir(mode=0o700)
        self.work = self.folder/'work'; self.work.mkdir()
        for name in image.MOUNT_DIRS: (self.root/name).mkdir(parents=True, exist_ok=True)
        self.runtime = self.root/'usr/bin/runtime'; self.runtime.write_text('SYNTHETIC_SECRET')

    def backend(self):
        return SandboxBackend(sys.executable, image_root=self.root, image_sha256=image.scan(self.root)['sha256'])

    def test_pin_covers_contents_membership_permissions_and_link_targets(self):
        original = image.scan(self.root)
        self.runtime.touch()
        self.assertNotEqual(image.scan(self.root)['sha256'], original['sha256'])
        self.assertEqual(image.scan(self.root), image.scan(self.root))
        for mutate in (lambda: self.runtime.write_text('changed'), lambda: self.runtime.chmod(0o755),
                       lambda: (self.root/'extra').write_text('new'), lambda: (self.root/'bin').symlink_to('usr/bin')):
            before = image.scan(self.root)['sha256']; mutate()
            self.assertNotEqual(image.scan(self.root)['sha256'], before)
        (self.root/'bin').unlink(); (self.root/'bin').symlink_to('/usr/bin')
        self.assertEqual(image.scan(self.root)['entries'], 13)

    def test_wrong_or_missing_pin_never_launches(self):
        for arguments in (dict(image_root=self.root), dict(image_sha256='0'*64),
                          dict(image_root=self.root, image_sha256='invalid')):
            with self.assertRaises(ValueError): SandboxBackend(**arguments)
        backend = SandboxBackend(sys.executable, image_root=self.root, image_sha256='0'*64)
        with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
            self.assertEqual(backend.capability()['status'], 'unsupported')
            with self.assertRaises(ValueError): backend.run(['/usr/bin/true'], self.work, {}, 'grader')
        launch.assert_not_called()

    def test_image_replaces_host_runtime_mounts_and_role_data_is_disjoint(self):
        backend = self.backend()
        command = backend.command(['/usr/bin/true'], self.work, {}, 'executor')
        binds = [(command[i+1], command[i+2]) for i, value in enumerate(command) if value == '--ro-bind']
        self.assertEqual(binds, [(str(self.root), '/')])
        self.assertIn('--tmpfs', command)
        self.assertEqual(command[command.index('--remount-ro')+1], '/inputs')
        with self.assertRaises(ValueError): backend.command(['/usr/bin/true'], self.root/'work', {}, 'grader')
        with self.assertRaises(ValueError): backend.command(['/usr/bin/true'], self.work, {'oracle': self.root}, 'grader')

    def test_unsafe_mountpoint_root_symlinks_hardlinks_and_special_files_are_rejected(self):
        (self.folder/'alias').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): image.scan(self.folder/'alias')
        target = self.root/'tmp'; target.rmdir(); target.symlink_to('work')
        with self.assertRaises(ValueError): image.scan(self.root)
        target.unlink(); target.mkdir()
        os.link(self.runtime, self.root/'hardlink')
        with self.assertRaises(ValueError): image.scan(self.root)
        (self.root/'hardlink').unlink()
        os.mkfifo(self.root/'fifo')
        with self.assertRaises(ValueError): image.scan(self.root)
        (self.root/'fifo').unlink()
        (self.root/'escape').symlink_to('../foreign')
        with self.assertRaises(ValueError): image.scan(self.root)

    def test_world_access_privilege_bits_bounds_and_budget_are_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError): image.scan(self.root)
        self.root.chmod(0o700); self.runtime.chmod(0o4755)
        with self.assertRaises(ValueError): image.scan(self.root)
        self.runtime.chmod(0o644)
        for constant in ('MAX_BYTES', 'MAX_ENTRIES'):
            with patch.object(image, constant, 2):
                with self.assertRaises(ValueError): image.scan(self.root)
        with self.assertRaises(TimeoutError): image.scan(self.root, deadline=0)

    def test_changed_file_during_read_is_rejected(self):
        original = os.read
        changed = False
        def read(descriptor, size):
            nonlocal changed
            raw = original(descriptor, size)
            if raw and not changed:
                changed = True; self.runtime.write_text('replacement')
            return raw
        with patch.object(image.os, 'read', side_effect=read):
            with self.assertRaises(ValueError): image.scan(self.root)

    def test_image_change_during_worker_execution_cannot_return_success(self):
        backend = self.backend()
        code = f'from pathlib import Path; Path({str(self.runtime)!r}).write_text("mutated")'
        with patch.object(backend, 'command', side_effect=lambda argv, *args: argv):
            with self.assertRaisesRegex(ValueError, 'pin mismatch'):
                backend.run(['/usr/bin/python3', '-c', code], self.work, {}, 'executor')

    def test_budget_exhaustion_cannot_claim_postflight_integrity(self):
        backend = self.backend(); snapshot = image.scan(self.root)
        with patch.object(backend, 'command', side_effect=lambda argv, *args: argv), \
                patch.object(backend.image, 'verify', side_effect=[snapshot, TimeoutError()]):
            run = backend.run(['/usr/bin/true'], self.work, {}, 'grader')
        self.assertEqual(run['status'], 'timeout')
        self.assertFalse(run['inputs_unchanged'])
        self.assertFalse(run['attestation']['runtime_image']['verified_after'])

    def test_readonly_pin_command_emits_no_paths_or_contents(self):
        with patch.object(sys, 'argv', ['image', str(self.root)]), redirect_stdout(io.StringIO()) as out:
            image.main()
        result = json.loads(out.getvalue())
        self.assertEqual(result['sha256'], image.scan(self.root)['sha256'])
        self.assertNotIn(str(self.root), out.getvalue())
        self.assertNotIn('SYNTHETIC_SECRET', out.getvalue())

    def test_extended_attributes_are_part_of_the_pin(self):
        before = image.scan(self.root)['sha256']
        os.setxattr(self.runtime, 'user.ahl-fixture', b'SYNTHETIC_SECRET')
        after = image.scan(self.root)['sha256']
        self.assertNotEqual(before, after)
        os.setxattr(self.runtime, 'user.ahl-fixture', b'changed')
        self.assertNotEqual(after, image.scan(self.root)['sha256'])

    def test_symlink_metadata_change_during_inspection_is_rejected(self):
        (self.root/'bin').symlink_to('usr/bin')
        original = os.readlink
        def readlink(path, **options):
            target = original(path, **options)
            os.utime(path, None, follow_symlinks=False, **options)
            return target
        with patch.object(image.os, 'readlink', side_effect=readlink):
            with self.assertRaises(ValueError): image.scan(self.root)
