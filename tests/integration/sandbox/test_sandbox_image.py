"""Rootfs pins are controller-selected, complete, bounded and private."""
from contextlib import redirect_stdout
import io
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import image
from agent_harness_lab.evaluation.sandbox import SandboxBackend


class Image(SimpleNamespace):
    """A pinned synthetic rootfs under tmp_path with a work directory beside it."""

    def backend(self):
        return SandboxBackend(sys.executable, image_root=self.root, image_sha256=image.scan(self.root)['sha256'])


@pytest.fixture
def img(tmp_path):
    root = tmp_path/'image'; root.mkdir(mode=0o700)
    work = tmp_path/'work'; work.mkdir()
    for name in image.MOUNT_DIRS: (root/name).mkdir(parents=True, exist_ok=True)
    runtime = root/'usr/bin/runtime'; runtime.write_text('SYNTHETIC_SECRET')
    return Image(folder=tmp_path, root=root, work=work, runtime=runtime)


def test_pin_covers_contents_membership_permissions_and_link_targets(img):
    original = image.scan(img.root)
    img.runtime.touch()
    assert image.scan(img.root)['sha256'] != original['sha256']
    assert image.scan(img.root) == image.scan(img.root)
    for mutate in (lambda: img.runtime.write_text('changed'), lambda: img.runtime.chmod(0o755),
                   lambda: (img.root/'extra').write_text('new'), lambda: (img.root/'bin').symlink_to('usr/bin')):
        before = image.scan(img.root)['sha256']; mutate()
        assert image.scan(img.root)['sha256'] != before
    (img.root/'bin').unlink(); (img.root/'bin').symlink_to('/usr/bin')
    assert image.scan(img.root)['entries'] == 13


def test_wrong_or_missing_pin_never_launches(img):
    for arguments in (dict(image_root=img.root), dict(image_sha256='0'*64),
                      dict(image_root=img.root, image_sha256='invalid')):
        with pytest.raises(ValueError): SandboxBackend(**arguments)
    backend = SandboxBackend(sys.executable, image_root=img.root, image_sha256='0'*64)
    with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
        assert backend.capability()['status'] == 'unsupported'
        with pytest.raises(ValueError): backend.run(['/usr/bin/true'], img.work, {}, 'grader')
    launch.assert_not_called()


def test_image_replaces_host_runtime_mounts_and_role_data_is_disjoint(img):
    backend = img.backend()
    command = backend.command(['/usr/bin/true'], img.work, {}, 'executor')
    binds = [(command[i+1], command[i+2]) for i, value in enumerate(command) if value == '--ro-bind']
    assert binds == [(str(img.root), '/')]
    assert '--tmpfs' in command
    assert command[command.index('--remount-ro')+1] == '/inputs'
    with pytest.raises(ValueError): backend.command(['/usr/bin/true'], img.root/'work', {}, 'grader')
    with pytest.raises(ValueError): backend.command(['/usr/bin/true'], img.work, {'oracle': img.root}, 'grader')


def test_unsafe_mountpoint_root_symlinks_hardlinks_and_special_files_are_rejected(img):
    (img.folder/'alias').symlink_to(img.root, target_is_directory=True)
    with pytest.raises(ValueError): image.scan(img.folder/'alias')
    target = img.root/'tmp'; target.rmdir(); target.symlink_to('work')
    with pytest.raises(ValueError): image.scan(img.root)
    target.unlink(); target.mkdir()
    os.link(img.runtime, img.root/'hardlink')
    with pytest.raises(ValueError): image.scan(img.root)
    (img.root/'hardlink').unlink()
    os.mkfifo(img.root/'fifo')
    with pytest.raises(ValueError): image.scan(img.root)
    (img.root/'fifo').unlink()
    (img.root/'escape').symlink_to('../foreign')
    with pytest.raises(ValueError): image.scan(img.root)


def test_world_access_privilege_bits_bounds_and_budget_are_rejected(img):
    img.root.chmod(0o755)
    with pytest.raises(ValueError): image.scan(img.root)
    img.root.chmod(0o700); img.runtime.chmod(0o4755)
    with pytest.raises(ValueError): image.scan(img.root)
    img.runtime.chmod(0o644)
    for constant in ('MAX_BYTES', 'MAX_ENTRIES', 'MAX_METADATA'):
        with patch.object(image, constant, 2):
            with pytest.raises(ValueError): image.scan(img.root)
    with patch.object(image, 'MAX_DEPTH', 1):
        with pytest.raises(ValueError, match='depth bound'): image.scan(img.root)
    with pytest.raises(TimeoutError): image.scan(img.root, deadline=0)


def test_changed_file_during_read_is_rejected(img):
    original = os.read
    changed = False
    def read(descriptor, size):
        nonlocal changed
        raw = original(descriptor, size)
        if raw and not changed:
            changed = True; img.runtime.write_text('replacement')
        return raw
    with patch.object(image.os, 'read', side_effect=read):
        with pytest.raises(ValueError): image.scan(img.root)


def test_image_change_during_worker_execution_cannot_return_success(img):
    backend = img.backend()
    code = f'from pathlib import Path; Path({str(img.runtime)!r}).write_text("mutated")'
    with patch.object(backend, 'command', side_effect=lambda argv, *args: argv):
        with pytest.raises(ValueError, match='pin mismatch'):
            backend.run(['/usr/bin/python3', '-c', code], img.work, {}, 'executor')


def test_budget_exhaustion_cannot_claim_postflight_integrity(img):
    backend = img.backend(); snapshot = image.scan(img.root)
    with patch.object(backend, 'command', side_effect=lambda argv, *args: argv), \
            patch.object(backend.image, 'verify', side_effect=[snapshot, TimeoutError()]):
        run = backend.run(['/usr/bin/true'], img.work, {}, 'grader')
    assert run['status'] == 'timeout'
    assert not run['inputs_unchanged']
    assert not run['attestation']['runtime_image']['verified_after']


def test_readonly_pin_command_emits_no_paths_or_contents(img):
    with patch.object(sys, 'argv', ['image', str(img.root)]), redirect_stdout(io.StringIO()) as out:
        image.main()
    result = json.loads(out.getvalue())
    assert result['sha256'] == image.scan(img.root)['sha256']
    assert str(img.root) not in out.getvalue()
    assert 'SYNTHETIC_SECRET' not in out.getvalue()


def test_extended_attributes_are_part_of_the_pin(img):
    before = image.scan(img.root)['sha256']
    os.setxattr(img.runtime, 'user.ahl-fixture', b'SYNTHETIC_SECRET')
    after = image.scan(img.root)['sha256']
    assert before != after
    os.setxattr(img.runtime, 'user.ahl-fixture', b'changed')
    assert after != image.scan(img.root)['sha256']


def test_symlink_metadata_change_during_inspection_is_rejected(img):
    (img.root/'bin').symlink_to('usr/bin')
    original = os.readlink
    def readlink(path, **options):
        target = original(path, **options)
        os.utime(path, None, follow_symlinks=False, **options)
        return target
    with patch.object(image.os, 'readlink', side_effect=readlink):
        with pytest.raises(ValueError): image.scan(img.root)
