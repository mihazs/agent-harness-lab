"""Synthetic trees: contained reads open each component from its parent and never follow a swapped link."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab import config, private_journal
from agent_harness_lab.evaluation import bank_revisions, cgroup, image, integrity, regression_review, working_state
from agent_harness_lab.evaluation.integrity import (artifacts, contained, read_regular, regular_equal,
                                                    regular_sha256, regular_size)
from agent_harness_lab.monitoring import state as state_module
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

    def outside_files(self):
        return {path.relative_to(self.outside): path.read_bytes() for path in self.outside.rglob('*') if path.is_file()}

    def assert_create_refused(self, step, directory, call, message='input crosses a symlink'):
        """The swapped parent is refused before anything outside is created, written or read, leaking no descriptor."""
        before, outside = self.outside_files(), {os.stat(self.outside).st_ino}
        outside.update(os.stat(path).st_ino for path in self.outside.rglob('*') if path.is_dir())
        real_mkdir, made = os.mkdir, []
        def mkdir(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            parent = os.fstat(dir_fd) if dir_fd is not None else os.stat(Path(path).absolute().parent)
            made.append(parent.st_ino)
        try:
            with self.unleaked(), patch.object(integrity.os, 'mkdir', mkdir), \
                    self.swapped_after(step, directory) as (fired, _):
                try: value = call(); error = None
                except (ValueError, OSError) as caught: error, value = caught, None
        finally: self.restore(directory)
        self.assertTrue(fired, 'the swap hook never ran')
        self.assertEqual(self.outside_files(), before, 'a file outside was created or written')
        self.assertFalse(outside.intersection(made), 'a directory was created outside')
        self.assertNotIn(OUTSIDE, repr(value).encode())
        self.assertIsInstance(error, ValueError, error)
        self.assertEqual(str(error), message)

    def test_open_beneath_creates_locks_and_reports_leaf_errors_unchanged(self):
        target = self.root/'deep'/'sub'/'new'
        with self.unleaked():
            descriptor = integrity.open_beneath(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            try:
                info = os.fstat(descriptor)
                self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0)
                self.assertFalse(os.get_inheritable(descriptor))
            finally: os.close(descriptor)
            lock = integrity.open_beneath(self.root/'deep'/'.lock', os.O_RDWR|os.O_CREAT)
            try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally: os.close(lock)
            with self.assertRaises(FileExistsError): integrity.open_beneath(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            (self.root/'link').symlink_to(self.outside/'f')
            with self.assertRaises(FileExistsError): integrity.open_beneath(self.root/'link', os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            with self.assertRaises(OSError): integrity.open_beneath(self.root/'link', os.O_RDWR|os.O_CREAT)
        self.assertEqual((self.outside/'f').read_bytes(), OUTSIDE)

    def test_journal_write_refuses_parent_swapped_before_the_walk(self):
        (self.outside/'sub').mkdir()
        self.assert_create_refused(None, self.root/'deep',
                                   lambda: private_journal.write_bytes(self.root/'deep'/'sub'/'new', b'payload'))

    def test_journal_lock_refuses_parent_swapped_before_the_walk(self):
        store = self.root/'deep'/'sub'/'store'; store.mkdir(mode=0o700)
        (self.outside/'sub').mkdir(); (self.outside/'sub'/'store').mkdir(mode=0o700)
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: None)
        def lock():
            with journal.locked(create=True): pass
        self.assert_create_refused(None, self.root/'deep', lock)

    def test_journal_marker_refuses_parent_swapped_after_the_lock(self):
        # The outside tree mirrors the store, so a followed link would land the marker there.
        store = self.root/'deep'/'sub'/'store'; (self.outside/'sub'/'store').mkdir(parents=True, mode=0o700)
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: None)
        def lock():
            with journal.locked(create=True): pass
        self.assert_create_refused((private_journal.fcntl, 'flock'), self.root/'deep', lock)
        self.assertFalse((store/'.store.json').exists())

    def test_journal_event_refuses_parent_swapped_after_the_lock(self):
        store = self.root/'deep'/'sub'/'store'; (self.outside/'sub'/'store'/'case'/'events').mkdir(parents=True, mode=0o700)
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
        with journal.locked(create=True): (store/'case'/'events').mkdir(parents=True)
        def append():
            with journal.locked(): journal.append(store/'case', [], 'opened', 'open', {})
        # encoded() is the event's bound check, the last step before publication.
        self.assert_create_refused((private_journal, 'encoded'), self.root/'deep', append)
        self.assertEqual(list((store/'case'/'events').iterdir()), [])

    def replaced(self, store):
        """Rename an ancestor aside (no link anywhere) and build an empty owner-only replacement store."""
        moved = self.root/'deep.orig'
        def replace():
            (self.root/'deep').rename(moved); store.mkdir(mode=0o700, parents=True)
        return moved/store.relative_to(self.root/'deep'), replace

    def test_journal_marker_refuses_ancestor_replaced_by_a_directory_after_the_lock(self):
        store = self.root/'deep'/'sub'/'store'
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: None)
        locked, replace = self.replaced(store)
        real = private_journal.fcntl.flock
        def flock(*args):
            real(*args); replace()
        with self.unleaked(), patch.object(private_journal.fcntl, 'flock', flock):
            with self.assertRaises(ValueError) as caught:
                with journal.locked(create=True): pass
        self.assertEqual(str(caught.exception), 'case store changed while locked')
        self.assertEqual(sorted(os.listdir(store)), [])
        self.assertFalse((locked/'.store.json').exists())

    def test_journal_event_refuses_ancestor_replaced_by_a_directory_after_the_lock(self):
        store = self.root/'deep'/'sub'/'store'
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
        with journal.locked(create=True): pass
        locked, replace = self.replaced(store)
        real = private_journal.encoded
        def encoded(value):
            raw = real(value); replace(); return raw
        with self.unleaked(), patch.object(private_journal, 'encoded', encoded):
            with self.assertRaises(ValueError) as caught:
                with journal.locked(): journal.append(store/'case', [], 'opened', 'open', {})
        self.assertEqual(str(caught.exception), 'case store changed while locked')
        self.assertEqual(sorted(os.listdir(store)), [])
        self.assertFalse((locked/'case').exists())

    def test_journal_event_lands_in_the_locked_store_when_replaced_during_publication(self):
        # The swap runs after the store check, so only the held descriptor keeps the event in the locked store.
        store, events = self.root/'deep'/'sub'/'store', []
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
        with journal.locked(create=True): pass
        locked, replace = self.replaced(store)
        with self.unleaked():
            with journal.locked(): journal.append(store/'case', events, 'opened', 'open', {}, before_publish=replace)
        self.assertEqual(sorted(os.listdir(store)), [])
        raw = (locked/'case'/'events'/'0000.json').read_bytes()
        self.assertEqual(events[0][1], private_journal.hashlib.sha256(raw).hexdigest())
        self.assertEqual(private_journal.record_bytes(raw)[0], events[0][0])

    def test_journal_event_guard_reaches_the_shared_replace_step_locked_and_unlocked(self):
        # Fault injection (e.g. the native bank-requalification expiry test) hooks _publish_at, so both append paths
        # must hand their guard to it at the replace step.
        store = self.root/'deep'/'sub'/'store'
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
        real, reached = private_journal._publish_at, []
        def publish(parent, name, value, before_replace):
            if value.get('kind') == 'opened':
                reached.append(name)
                def expired(): raise TimeoutError('synthetic expiry at replace')
                before_replace = expired
            return real(parent, name, value, before_replace)
        guard = lambda: None
        with self.unleaked(), patch.object(private_journal, '_publish_at', side_effect=publish):
            with self.assertRaises(TimeoutError):
                with journal.locked(create=True): journal.append(store/'locked', [], 'opened', 'open', {}, before_publish=guard)
            with self.assertRaises(TimeoutError): journal.append(store/'unlocked', [], 'opened', 'open', {}, before_publish=guard)
        self.assertEqual(reached, ['0000.json', '0000.json'])
        for case in ('locked', 'unlocked'): self.assertEqual(os.listdir(store/case/'events'), [])

    def test_journal_publication_keeps_atomic_json_bytes(self):
        store, events = self.root/'deep'/'sub'/'store', []
        journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
        with self.unleaked():
            with journal.locked(create=True): journal.append(store/'case', events, 'opened', 'open', {})
        target = self.root/'state.json'; state_module.atomic_json(target, events[0][0])
        self.assertEqual((store/'case'/'events'/'0000.json').read_bytes(), target.read_bytes())
        self.assertEqual(stat.S_IMODE((store/'case'/'events').stat().st_mode) & 0o077, 0)
        self.assertEqual(sorted(path.name for path in (store/'case'/'events').iterdir()), ['0000.json'])
        self.assertEqual(private_journal.record(store/'.store.json')[0], dict(schema_version=1, format='synthetic-v1'))

    def test_project_configuration_create_refuses_parent_swapped_before_the_walk(self):
        (self.outside/'sub').mkdir(); value, supported = config.defaults(), config.supported()
        # supported() checks os.open against os.supports_dir_fd, which the swap hook's wrapper is not in.
        with patch.object(config, 'supported', lambda: supported):
            self.assert_create_refused(None, self.root/'deep', lambda: config.create(self.root/'deep'/'sub', value))

    def test_working_state_roots_refuse_parent_swapped_after_their_checks(self):
        (self.outside/'sub').mkdir(); (self.outside/'sub'/'f').write_bytes(OUTSIDE)
        workspace, deadline = self.root/'deep'/'sub', time.monotonic()+60
        with self.subTest(site='analysis_snapshot'):
            self.assert_create_refused(None, self.root/'deep', lambda: working_state.analysis_snapshot(workspace, deadline))
        with self.subTest(site='capture'):
            self.assert_create_refused((working_state, 'tree'), self.root/'deep',
                                       lambda: working_state.WorkingState(workspace, deadline))

    def test_working_state_publish_refuses_stage_parent_swapped_after_staging(self):
        (self.outside/'sub').mkdir(); state = working_state.WorkingState(self.root/'deep'/'sub', time.monotonic()+60)
        real_mkdir, swap = os.mkdir, self.swap
        def mirrored(directory):
            # Give the outside tree the stage folder the publication just created, so a followed link would land there.
            for stage in (self.root/'deep'/'sub').glob('.artifact-stage-*'):
                real_mkdir(self.outside/'sub'/stage.name, 0o700); real_mkdir(self.outside/'sub'/stage.name/'artifacts', 0o700)
            swap(directory)
        with patch.object(self, 'swap', mirrored):
            self.assert_create_refused(None, self.root/'deep',
                                       lambda: state.publish(self.root/'deep'/'sub'/'out', time.monotonic()+60))
        self.assertFalse((self.root/'deep'/'sub'/'out').exists())

    def test_runtime_image_scan_refuses_parent_swapped_after_its_checks(self):
        (self.root/'deep'/'sub').chmod(0o700); (self.outside/'sub').mkdir(mode=0o700)
        for name in image.MOUNT_DIRS: (self.outside/'sub'/name).mkdir(parents=True, exist_ok=True)
        self.assert_create_refused(None, self.root/'deep', lambda: image.scan(self.root/'deep'/'sub'))

    def test_cgroup_root_refuses_parent_swapped_after_delegation(self):
        (self.outside/'sub').mkdir()
        group = cgroup.Cgroup(self.root/'deep'/'sub', SimpleNamespace(memory_bytes=1 << 26, processes=8), time.monotonic()+60)
        with patch.object(cgroup, 'delegated', lambda root: Path(root)):
            self.assert_create_refused((cgroup, 'delegated'), self.root/'deep', group.__enter__,
                                       'delegated cgroup v2 resource setup failed')
        self.assertIsNone(group.parent); self.assertIsNone(group.fd)

    def test_contained_still_refuses_links_and_escapes(self):
        (self.root/'parent').symlink_to(self.outside, target_is_directory=True)
        for value, message in (('parent/f', 'path crosses a symlink'), ('../outside/f', 'unsafe relative path')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                contained(self.root, value)
        self.assertEqual(contained(self.root, 'deep/sub/f'), self.target.absolute())
        self.assertTrue(stat.S_ISREG(os.lstat(contained(self.root, 'deep/sub/f')).st_mode))


if __name__ == '__main__':
    unittest.main()
