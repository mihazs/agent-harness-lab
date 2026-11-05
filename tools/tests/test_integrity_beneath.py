"""Synthetic trees: contained reads open each component from its parent and never follow a swapped link."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_harness_lab import private_journal
from agent_harness_lab.evaluation import bank_revisions, integrity, regression_review
from agent_harness_lab.evaluation.integrity import (artifacts, contained, read_regular, regular_equal,
                                                    regular_sha256, regular_size)
from agent_harness_lab.sources import lineage

INSIDE, OUTSIDE = b'inside payload', b'outside secret'
PROC_FD = Path('/proc/self/fd')


def readers(path, max_bytes=1024):
    return dict(read=lambda: read_regular(path, max_bytes), sha=lambda: regular_sha256(path, max_bytes),
                size=lambda: regular_size(path, max_bytes), equal=lambda: regular_equal(path, OUTSIDE, max_bytes))


class BeneathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root, self.outside = base/'root', base/'outside'
        (self.root/'deep'/'sub').mkdir(parents=True); self.outside.mkdir()
        (self.root/'deep'/'sub'/'f').write_bytes(INSIDE)
        (self.outside/'f').write_bytes(OUTSIDE)
        self.target = self.root/'deep'/'sub'/'f'

    def swap(self, directory):
        """Replace a directory with a symlink to the outside directory holding a same-named file."""
        directory.rename(directory.with_name(directory.name+'.orig'))
        directory.symlink_to(self.outside, target_is_directory=True)

    @contextmanager
    def unleaked(self):
        before = sorted(os.listdir(PROC_FD)) if PROC_FD.is_dir() else None
        yield
        if before is not None: self.assertEqual(sorted(os.listdir(PROC_FD)), before)

    @contextmanager
    def swapped_on_open(self, directory, trigger):
        """Swap directory just before the first os.open call that trigger accepts."""
        real, fired = os.open, []
        def hooked(name, flags, *args, **kwargs):
            if not fired and trigger(name, kwargs):
                fired.append(name); self.swap(directory)
            return real(name, flags, *args, **kwargs)
        with patch.object(integrity.os, 'open', hooked): yield fired

    def refused(self, call):
        with self.unleaked():
            try: value = call()
            except (ValueError, OSError) as error: return error
        self.assertNotIn(OUTSIDE, value if isinstance(value, bytes) else b'')
        self.fail(f'read was not refused: {value!r}')

    def restore(self, directory):
        if directory.is_symlink(): directory.unlink()
        if directory.with_name(directory.name+'.orig').exists(): directory.with_name(directory.name+'.orig').rename(directory)

    def test_parent_swapped_after_the_check_is_refused_for_every_reader(self):
        (self.outside/'sub').mkdir(); (self.outside/'sub'/'f').write_bytes(OUTSIDE)
        triggers = dict(first_open=lambda name, kwargs: True,
                        deep_open=lambda name, kwargs: name == 'deep' and 'dir_fd' in kwargs,
                        sub_open=lambda name, kwargs: name == 'sub' and 'dir_fd' in kwargs)
        # A directory already held open stays bound to its original inode: inside content, never outside.
        held = {('sub_open', 'deep')}
        for label, trigger in triggers.items():
            for swapped in ('deep', 'sub'):
                directory = self.root/'deep' if swapped == 'deep' else self.root/'deep'/'sub'
                for reader, call in readers(contained(self.root, 'deep/sub/f')).items():
                    with self.subTest(trigger=label, swapped=swapped, reader=reader):
                        try:
                            with self.unleaked(), self.swapped_on_open(directory, trigger) as fired:
                                try: value = call()
                                except (ValueError, OSError) as error: value = error
                            self.assertTrue(fired, 'the swap hook never ran')
                            if (label, swapped) in held:
                                self.assertEqual(value, dict(read=INSIDE, sha=integrity.hashlib.sha256(INSIDE).hexdigest(),
                                                             size=len(INSIDE), equal=False)[reader])
                            else:
                                self.assertIsInstance(value, ValueError, value)
                                self.assertEqual(str(value), 'input crosses a symlink')
                        finally: self.restore(directory)

    def test_artifact_parent_swapped_after_containment_is_refused(self):
        real = integrity.contained
        def checked_then_swapped(root, value):
            path = real(root, value); self.swap(self.root/'deep'/'sub'); return path
        with patch.object(integrity, 'contained', side_effect=checked_then_swapped):
            error = self.refused(lambda: artifacts(self.root, ['deep/sub/f']))
        self.assertEqual(str(error), 'input crosses a symlink')

    def test_artifacts_still_read_contained_regular_files(self):
        with self.unleaked():
            self.assertEqual(artifacts(self.root, ['deep/sub/f']), {'deep/sub/f': INSIDE})
        os.mkfifo(self.root/'pipe'); (self.root/'single').write_bytes(INSIDE); os.link(self.root/'single', self.root/'twin')
        for name, message in (('pipe', 'artifact must be a regular single-link file'),
                              ('twin', 'artifact must be a regular single-link file')):
            with self.subTest(name=name):
                self.assertEqual(str(self.refused(lambda: artifacts(self.root, [name]))), message)
        self.assertEqual(str(self.refused(lambda: artifacts(self.root, ['deep/sub/f'], max_bytes=len(INSIDE)-1))),
                         'artifact byte bound exceeded')

    def test_links_special_files_and_bounds_are_refused_without_leaks(self):
        (self.root/'leaf').symlink_to(self.target)
        (self.root/'parent').symlink_to(self.outside, target_is_directory=True)
        os.link(self.target, self.root/'twin')
        os.mkfifo(self.root/'pipe')
        cases = dict(leaf=(self.root/'leaf', 'input crosses a symlink'),
                     parent=(self.root/'parent'/'f', 'input crosses a symlink'),
                     hardlink=(self.target, 'input must be a bounded single-link regular file'),
                     fifo=(self.root/'pipe', 'input must be a bounded single-link regular file'),
                     directory=(self.root/'deep', 'input must be a bounded single-link regular file'))
        for label, (path, message) in cases.items():
            for reader, call in readers(path).items():
                with self.subTest(case=label, reader=reader):
                    self.assertEqual(str(self.refused(call)), message)
        for reader, call in readers(self.target, len(INSIDE)-1).items():
            with self.subTest(case='byte-bound', reader=reader):
                self.assertEqual(str(self.refused(call)), 'input must be a bounded single-link regular file')
        with self.subTest(case='missing'):
            self.assertIsInstance(self.refused(lambda: read_regular(self.root/'absent', 10)), FileNotFoundError)

    def test_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.root/'pipe'); outcome = []
        worker = threading.Thread(target=lambda: outcome.append(self.refused(lambda: read_regular(self.root/'pipe', 10))),
                                  daemon=True)
        worker.start(); worker.join(5)
        self.assertFalse(worker.is_alive(), 'opening a FIFO blocked')
        self.assertEqual(str(outcome[0]), 'input must be a bounded single-link regular file')

    def test_relative_and_bounded_reads_still_return_contents(self):
        with self.unleaked():
            self.assertEqual(read_regular(self.target, len(INSIDE)), INSIDE)
            self.assertEqual(regular_size(self.target, None), len(INSIDE))
        cwd = os.getcwd(); self.addCleanup(os.chdir, cwd); os.chdir(self.root)
        self.assertEqual(read_regular('deep/sub/f', None), INSIDE)

    @unittest.skipUnless(hasattr(os, 'O_PATH') and os.geteuid() != 0, 'needs O_PATH and permission checks')
    def test_search_only_ancestor_is_traversed_like_ordinary_resolution(self):
        (self.root/'deep').chmod(0o311); self.addCleanup((self.root/'deep').chmod, 0o700)
        with self.unleaked():
            self.assertEqual(read_regular(self.target, None), INSIDE)

    def test_platform_without_dir_fd_open_is_refused(self):
        for patched in (patch.object(integrity, '_DIR_FD_OPEN', False), patch.object(integrity.os, 'O_NOFOLLOW')):
            with self.subTest(patched=patched.attribute), patched:
                if patched.attribute == 'O_NOFOLLOW': del integrity.os.O_NOFOLLOW
                with self.assertRaisesRegex(NotImplementedError, 'safe file reads require POSIX'):
                    read_regular(self.target, None)
                with self.assertRaisesRegex(NotImplementedError, 'safe file reads require POSIX'):
                    artifacts(self.root, ['deep/sub/f'])

    @contextmanager
    def swapped_after(self, step, directory):
        """Swap directory on the first os.open once (module, name) returns, or at once without a step."""
        real_open, real_fsync = os.open, os.fsync
        armed, fired, synced = [] if step else [True], [], []
        def hooked(path, flags, *args, **kwargs):
            if armed and not fired: fired.append(path); self.swap(directory)
            return real_open(path, flags, *args, **kwargs)
        def fsync(descriptor):
            info = os.fstat(descriptor); synced.append((info.st_dev, info.st_ino)); return real_fsync(descriptor)
        with patch.object(integrity.os, 'open', hooked), patch.object(integrity.os, 'fsync', fsync):
            if step is None: yield fired, synced; return
            module, name = step; real_step = getattr(module, name)
            def stepped(*args, **kwargs):
                value = real_step(*args, **kwargs); armed.append(True); return value
            with patch.object(module, name, stepped): yield fired, synced

    def assert_fsync_refused(self, step, directory, call):
        """The swapped parent is refused before any fsync reaches outside content, leaking no descriptor."""
        outside = {(info.st_dev, info.st_ino) for info in map(os.stat, (self.outside, *self.outside.rglob('*')))}
        try:
            with self.unleaked(), self.swapped_after(step, directory) as (fired, synced):
                try: call(); error = None
                except (ValueError, OSError) as caught: error = caught
        finally: self.restore(directory)
        self.assertTrue(fired, 'the swap hook never ran')
        self.assertFalse(outside.intersection(synced), 'fsync reached outside content')
        self.assertIsInstance(error, ValueError, error)
        self.assertEqual(str(error), 'input crosses a symlink')

    def test_bank_revision_tree_sync_refuses_parent_swapped_after_containment(self):
        self.assert_fsync_refused((bank_revisions, 'contained'), self.root/'deep'/'sub',
                                  lambda: bank_revisions.sync_tree(self.root))

    def test_lineage_snapshot_sync_refuses_parent_swapped_after_write(self):
        raw = b'{}\n'; identity = integrity.hashlib.sha256(raw).hexdigest()
        (self.outside/(identity+'.json')).write_bytes(OUTSIDE); (self.root/'lineage').mkdir()
        store = lineage.LineageStore(self.root/'lineage', project_sha256='a'*64, scope_sha256='b'*64)
        self.assert_fsync_refused((lineage, 'write_bytes'), self.root/'lineage'/'snapshots',
                                  lambda: store.snapshot(raw, identity))

    def test_review_evidence_sync_refuses_parent_swapped_after_containment(self):
        store = regression_review.ReviewStore(self.root/'store')
        packet, task = dict(files=['sub/f'], trees=[], artifacts=[]), type('Task', (), dict(artifacts=()))
        self.assert_fsync_refused((regression_review, 'contained'), self.root/'deep'/'sub',
            lambda: store.sync_evidence(self.root, self.root/'deep', packet, task, time.monotonic()+60))

    def test_directory_sync_refuses_ancestor_swapped_before_the_walk(self):
        (self.outside/'sub').mkdir()
        self.assert_fsync_refused(None, self.root/'deep', lambda: private_journal.sync_directory(self.root/'deep'/'sub'))

    def test_directory_leaf_is_opened_without_following_and_only_as_a_directory(self):
        self.assert_fsync_refused(None, self.root/'deep'/'sub',
                                  lambda: private_journal.sync_directory(self.root/'deep'/'sub'))
        with self.unleaked(), self.assertRaises(NotADirectoryError):
            integrity.open_directory_nofollow(self.target)

    def test_sites_still_fsync_their_own_contained_files_and_directories(self):
        real_fsync, synced = os.fsync, []
        def fsync(descriptor):
            info = os.fstat(descriptor); synced.append((info.st_dev, info.st_ino)); return real_fsync(descriptor)
        expected = {(info.st_dev, info.st_ino) for info in map(os.stat, (self.target, self.root/'deep'/'sub', self.root/'deep'))}
        with self.unleaked(), patch.object(integrity.os, 'fsync', fsync):
            bank_revisions.sync_tree(self.root)
            descriptor = integrity.open_directory_nofollow(self.root/'deep')
            try: self.assertTrue(stat.S_ISDIR(os.fstat(descriptor).st_mode)); os.fsync(descriptor)
            finally: os.close(descriptor)
        self.assertTrue(expected.issubset(synced))

    def test_contained_still_refuses_links_and_escapes(self):
        (self.root/'parent').symlink_to(self.outside, target_is_directory=True)
        for value, message in (('parent/f', 'path crosses a symlink'), ('../outside/f', 'unsafe relative path')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                contained(self.root, value)
        self.assertEqual(contained(self.root, 'deep/sub/f'), self.target.absolute())
        self.assertTrue(stat.S_ISREG(os.lstat(contained(self.root, 'deep/sub/f')).st_mode))


if __name__ == '__main__':
    unittest.main()
