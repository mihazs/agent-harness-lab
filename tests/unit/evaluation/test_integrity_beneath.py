"""Synthetic trees: contained reads open each component from its parent and never follow a swapped link."""
from contextlib import ExitStack, contextmanager
import fcntl
import os
from pathlib import Path
import socket
import stat
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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


class Beneath:
    """A contained root and an outside directory holding a same-named file, with swap and leak helpers."""

    def __init__(self, base, cleanups):
        self.cleanups = cleanups
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
        if before is not None: assert sorted(os.listdir(PROC_FD)) == before

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
        assert OUTSIDE not in (value if isinstance(value, bytes) else b'')
        pytest.fail(f'read was not refused: {value!r}')

    def bounded(self, call, seconds=5):
        """Run call on a daemon thread, so a blocking special-file open fails the test instead of hanging it."""
        outcome = []
        def run():
            try: outcome.append((True, call()))
            except BaseException as error: outcome.append((False, error))
        worker = threading.Thread(target=run, daemon=True)
        worker.start(); worker.join(seconds)
        assert not worker.is_alive(), 'opening a special file blocked'
        returned, value = outcome[0]
        if not returned: raise value
        return value

    @contextmanager
    def opened(self):
        """Record every name passed to os.open."""
        real, names = os.open, []
        def recorded(name, flags, *args, **kwargs):
            names.append(os.fsdecode(name)); return real(name, flags, *args, **kwargs)
        with patch.object(integrity.os, 'open', recorded): yield names

    def restore(self, directory):
        if directory.is_symlink(): directory.unlink()
        if directory.with_name(directory.name+'.orig').exists(): directory.with_name(directory.name+'.orig').rename(directory)

    def special_leaves(self):
        """FIFO, Unix socket and, where this process may create one, a character device node."""
        os.mkfifo(self.root/'pipe')
        cwd = os.getcwd(); os.chdir(self.root)  # a relative bind keeps the socket path short
        try:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.cleanups.callback(server.close)
            server.bind('sock')
        finally: os.chdir(cwd)
        names = ['pipe', 'sock']
        try: os.mknod(self.root/'device', stat.S_IFCHR | 0o600, os.makedev(1, 3)); names.append('device')
        except (PermissionError, AttributeError): pass
        return names

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
        assert fired, 'the swap hook never ran'
        assert not outside.intersection(synced), 'fsync reached outside content'
        assert isinstance(error, ValueError), error
        assert str(error) == 'input crosses a symlink'

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
        assert fired, 'the swap hook never ran'
        assert self.outside_files() == before, 'a file outside was created or written'
        assert not outside.intersection(made), 'a directory was created outside'
        assert OUTSIDE not in repr(value).encode()
        assert isinstance(error, ValueError), error
        assert str(error) == message

    def replaced(self, store):
        """Rename an ancestor aside (no link anywhere) and build an empty owner-only replacement store."""
        moved = self.root/'deep.orig'
        def replace():
            (self.root/'deep').rename(moved); store.mkdir(mode=0o700, parents=True)
        return moved/store.relative_to(self.root/'deep'), replace


@pytest.fixture
def tree(tmp_path):
    with ExitStack() as cleanups:
        yield Beneath(tmp_path, cleanups)


TRIGGERS = dict(first_open=lambda name, kwargs: True,
                deep_open=lambda name, kwargs: name == 'deep' and 'dir_fd' in kwargs,
                sub_open=lambda name, kwargs: name == 'sub' and 'dir_fd' in kwargs)
READERS = ('read', 'sha', 'size', 'equal')


@pytest.mark.parametrize('reader', READERS)
@pytest.mark.parametrize('swapped', ('deep', 'sub'))
@pytest.mark.parametrize('label', tuple(TRIGGERS))
def test_parent_swapped_after_the_check_is_refused_for_every_reader(tree, label, swapped, reader):
    (tree.outside/'sub').mkdir(); (tree.outside/'sub'/'f').write_bytes(OUTSIDE)
    # A directory already held open stays bound to its original inode: inside content, never outside.
    held = {('sub_open', 'deep')}
    directory = tree.root/'deep' if swapped == 'deep' else tree.root/'deep'/'sub'
    call = readers(contained(tree.root, 'deep/sub/f'))[reader]
    try:
        with tree.unleaked(), tree.swapped_on_open(directory, TRIGGERS[label]) as fired:
            try: value = call()
            except (ValueError, OSError) as error: value = error
        assert fired, 'the swap hook never ran'
        if (label, swapped) in held:
            assert value == dict(read=INSIDE, sha=integrity.hashlib.sha256(INSIDE).hexdigest(),
                                 size=len(INSIDE), equal=False)[reader]
        else:
            assert isinstance(value, ValueError), value
            assert str(value) == 'input crosses a symlink'
    finally: tree.restore(directory)


def test_artifact_parent_swapped_after_containment_is_refused(tree):
    real = integrity.contained
    def checked_then_swapped(root, value):
        path = real(root, value); tree.swap(tree.root/'deep'/'sub'); return path
    with patch.object(integrity, 'contained', side_effect=checked_then_swapped):
        error = tree.refused(lambda: artifacts(tree.root, ['deep/sub/f']))
    assert str(error) == 'input crosses a symlink'


def test_artifacts_still_read_contained_regular_files(tree):
    with tree.unleaked():
        assert artifacts(tree.root, ['deep/sub/f']) == {'deep/sub/f': INSIDE}
    assert str(tree.refused(lambda: artifacts(tree.root, ['deep/sub/f'], max_bytes=len(INSIDE)-1))) == 'artifact byte bound exceeded'


@pytest.mark.parametrize(('name', 'message'), [
    pytest.param('pipe', 'artifact must be a regular single-link file', id='pipe'),
    pytest.param('twin', 'artifact must be a regular single-link file', id='twin')])
def test_artifacts_refuse_special_and_linked_files(tree, name, message):
    os.mkfifo(tree.root/'pipe'); (tree.root/'single').write_bytes(INSIDE); os.link(tree.root/'single', tree.root/'twin')
    assert str(tree.bounded(lambda: tree.refused(lambda: artifacts(tree.root, [name])))) == message


REFUSED_INPUTS = dict(leaf=('leaf', 'input crosses a symlink'),
                      parent=('parent/f', 'input crosses a symlink'),
                      hardlink=('deep/sub/f', 'input must be a bounded single-link regular file'),
                      fifo=('pipe', 'input must be a bounded single-link regular file'),
                      directory=('deep', 'input must be a bounded single-link regular file'))


@pytest.mark.parametrize('reader', READERS)
@pytest.mark.parametrize('case', tuple(REFUSED_INPUTS))
def test_links_special_files_and_bounds_are_refused_without_leaks(tree, case, reader):
    (tree.root/'leaf').symlink_to(tree.target)
    (tree.root/'parent').symlink_to(tree.outside, target_is_directory=True)
    os.link(tree.target, tree.root/'twin')
    os.mkfifo(tree.root/'pipe')
    name, message = REFUSED_INPUTS[case]
    call = readers(tree.root/name)[reader]
    assert str(tree.bounded(lambda: tree.refused(call))) == message


@pytest.mark.parametrize('reader', READERS)
def test_reads_over_the_byte_bound_are_refused_without_leaks(tree, reader):
    call = readers(tree.target, len(INSIDE)-1)[reader]
    assert str(tree.refused(call)) == 'input must be a bounded single-link regular file'


def test_missing_input_is_refused_without_leaks(tree):
    assert isinstance(tree.refused(lambda: read_regular(tree.root/'absent', 10)), FileNotFoundError)


def test_fifo_is_refused_without_blocking(tree):
    os.mkfifo(tree.root/'pipe')
    assert str(tree.bounded(lambda: tree.refused(lambda: read_regular(tree.root/'pipe', 10)))) == 'input must be a bounded single-link regular file'


LABELLED_READERS = dict(
    artifact=(lambda root, name: artifacts(root, [name]), 'artifact must be a regular single-link file'),
    read=(lambda root, name: read_regular(root/name, 10), 'input must be a bounded single-link regular file'))


@pytest.mark.parametrize('label', tuple(LABELLED_READERS))
@pytest.mark.parametrize('name', ('pipe', 'sock', 'device'))
def test_special_leaves_are_refused_with_the_label_before_any_open(tree, name, label):
    if name not in tree.special_leaves(): pytest.skip('this process may not create a character device node')
    read, message = LABELLED_READERS[label]
    with tree.opened() as names:
        assert str(tree.bounded(lambda: tree.refused(lambda: read(tree.root, name)))) == message
        assert name not in names


@pytest.mark.parametrize('label', tuple(LABELLED_READERS))
def test_device_leaf_is_refused_before_its_open(tree, label):
    """Simulated where mknod needs privileges: the pre-open lstat reports a character device."""
    (tree.root/'device').write_bytes(INSIDE); real = os.stat
    def as_device(name, *args, **kwargs):
        info = real(name, *args, **kwargs)
        if name != 'device' or 'dir_fd' not in kwargs: return info
        return os.stat_result((stat.S_IFCHR | 0o600, *info[1:]))
    read, message = LABELLED_READERS[label]
    with patch.object(integrity.os, 'stat', as_device), tree.opened() as names:
        assert str(tree.refused(lambda: read(tree.root, 'device'))) == message
        assert 'device' not in names


def test_relative_and_bounded_reads_still_return_contents(tree, monkeypatch):
    with tree.unleaked():
        assert read_regular(tree.target, len(INSIDE)) == INSIDE
        assert regular_size(tree.target, None) == len(INSIDE)
    monkeypatch.chdir(tree.root)
    assert read_regular('deep/sub/f', None) == INSIDE


@pytest.mark.skipif(not (hasattr(os, 'O_PATH') and os.geteuid() != 0), reason='needs O_PATH and permission checks')
def test_search_only_ancestor_is_traversed_like_ordinary_resolution(tree):
    (tree.root/'deep').chmod(0o311); tree.cleanups.callback((tree.root/'deep').chmod, 0o700)
    with tree.unleaked():
        assert read_regular(tree.target, None) == INSIDE


@pytest.mark.parametrize('patched', [
    pytest.param(lambda: patch.object(integrity, '_DIR_FD_OPEN', False), id='_DIR_FD_OPEN'),
    pytest.param(lambda: patch.object(integrity.os, 'O_NOFOLLOW'), id='O_NOFOLLOW')])
def test_platform_without_dir_fd_open_is_refused(tree, patched):
    patched = patched()
    with patched:
        if patched.attribute == 'O_NOFOLLOW': del integrity.os.O_NOFOLLOW
        with pytest.raises(NotImplementedError, match='safe file reads require POSIX'):
            read_regular(tree.target, None)
        with pytest.raises(NotImplementedError, match='safe file reads require POSIX'):
            artifacts(tree.root, ['deep/sub/f'])


def test_bank_revision_tree_sync_refuses_parent_swapped_after_containment(tree):
    tree.assert_fsync_refused((bank_revisions, 'contained'), tree.root/'deep'/'sub',
                              lambda: bank_revisions.sync_tree(tree.root))


def test_lineage_snapshot_sync_refuses_parent_swapped_after_write(tree):
    raw = b'{}\n'; identity = integrity.hashlib.sha256(raw).hexdigest()
    (tree.outside/(identity+'.json')).write_bytes(OUTSIDE); (tree.root/'lineage').mkdir()
    store = lineage.LineageStore(tree.root/'lineage', project_sha256='a'*64, scope_sha256='b'*64)
    tree.assert_fsync_refused((lineage, 'write_bytes'), tree.root/'lineage'/'snapshots',
                              lambda: store.snapshot(raw, identity))


def test_review_evidence_sync_refuses_parent_swapped_after_containment(tree):
    store = regression_review.ReviewStore(tree.root/'store')
    packet, task = dict(files=['sub/f'], trees=[], artifacts=[]), type('Task', (), dict(artifacts=()))
    tree.assert_fsync_refused((regression_review, 'contained'), tree.root/'deep'/'sub',
        lambda: store.sync_evidence(tree.root, tree.root/'deep', packet, task, time.monotonic()+60))


def test_directory_sync_refuses_ancestor_swapped_before_the_walk(tree):
    (tree.outside/'sub').mkdir()
    tree.assert_fsync_refused(None, tree.root/'deep', lambda: private_journal.sync_directory(tree.root/'deep'/'sub'))


def test_directory_leaf_is_opened_without_following_and_only_as_a_directory(tree):
    tree.assert_fsync_refused(None, tree.root/'deep'/'sub',
                              lambda: private_journal.sync_directory(tree.root/'deep'/'sub'))
    with tree.unleaked(), pytest.raises(NotADirectoryError):
        integrity.open_directory_nofollow(tree.target)


def test_sites_still_fsync_their_own_contained_files_and_directories(tree):
    real_fsync, synced = os.fsync, []
    def fsync(descriptor):
        info = os.fstat(descriptor); synced.append((info.st_dev, info.st_ino)); return real_fsync(descriptor)
    expected = {(info.st_dev, info.st_ino) for info in map(os.stat, (tree.target, tree.root/'deep'/'sub', tree.root/'deep'))}
    with tree.unleaked(), patch.object(integrity.os, 'fsync', fsync):
        bank_revisions.sync_tree(tree.root)
        descriptor = integrity.open_directory_nofollow(tree.root/'deep')
        try: assert stat.S_ISDIR(os.fstat(descriptor).st_mode); os.fsync(descriptor)
        finally: os.close(descriptor)
    assert expected.issubset(synced)


def test_open_beneath_creates_locks_and_reports_leaf_errors_unchanged(tree):
    target = tree.root/'deep'/'sub'/'new'
    with tree.unleaked():
        descriptor = integrity.open_beneath(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        try:
            info = os.fstat(descriptor)
            assert stat.S_IMODE(info.st_mode) & 0o077 == 0
            assert not os.get_inheritable(descriptor)
        finally: os.close(descriptor)
        lock = integrity.open_beneath(tree.root/'deep'/'.lock', os.O_RDWR|os.O_CREAT)
        try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally: os.close(lock)
        with pytest.raises(FileExistsError): integrity.open_beneath(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        (tree.root/'link').symlink_to(tree.outside/'f')
        with pytest.raises(FileExistsError): integrity.open_beneath(tree.root/'link', os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        with pytest.raises(OSError): integrity.open_beneath(tree.root/'link', os.O_RDWR|os.O_CREAT)
    assert (tree.outside/'f').read_bytes() == OUTSIDE


def test_journal_write_refuses_parent_swapped_before_the_walk(tree):
    (tree.outside/'sub').mkdir()
    tree.assert_create_refused(None, tree.root/'deep',
                               lambda: private_journal.write_bytes(tree.root/'deep'/'sub'/'new', b'payload'))


def test_journal_lock_refuses_parent_swapped_before_the_walk(tree):
    store = tree.root/'deep'/'sub'/'store'; store.mkdir(mode=0o700)
    (tree.outside/'sub').mkdir(); (tree.outside/'sub'/'store').mkdir(mode=0o700)
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: None)
    def lock():
        with journal.locked(create=True): pass
    tree.assert_create_refused(None, tree.root/'deep', lock)


def test_journal_marker_refuses_parent_swapped_after_the_lock(tree):
    # The outside tree mirrors the store, so a followed link would land the marker there.
    store = tree.root/'deep'/'sub'/'store'; (tree.outside/'sub'/'store').mkdir(parents=True, mode=0o700)
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: None)
    def lock():
        with journal.locked(create=True): pass
    tree.assert_create_refused((private_journal.fcntl, 'flock'), tree.root/'deep', lock)
    assert not (store/'.store.json').exists()


def test_journal_event_refuses_parent_swapped_after_the_lock(tree):
    store = tree.root/'deep'/'sub'/'store'; (tree.outside/'sub'/'store'/'case'/'events').mkdir(parents=True, mode=0o700)
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
    with journal.locked(create=True): (store/'case'/'events').mkdir(parents=True)
    def append():
        with journal.locked(): journal.append(store/'case', [], 'opened', 'open', {})
    # encoded() is the event's bound check, the last step before publication.
    tree.assert_create_refused((private_journal, 'encoded'), tree.root/'deep', append)
    assert list((store/'case'/'events').iterdir()) == []


def test_journal_marker_refuses_ancestor_replaced_by_a_directory_after_the_lock(tree):
    store = tree.root/'deep'/'sub'/'store'
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: None)
    locked, replace = tree.replaced(store)
    real = private_journal.fcntl.flock
    def flock(*args):
        real(*args); replace()
    with tree.unleaked(), patch.object(private_journal.fcntl, 'flock', flock):
        with pytest.raises(ValueError) as caught:
            with journal.locked(create=True): pass
    assert str(caught.value) == 'case store changed while locked'
    assert sorted(os.listdir(store)) == []
    assert not (locked/'.store.json').exists()


def test_journal_event_refuses_ancestor_replaced_by_a_directory_after_the_lock(tree):
    store = tree.root/'deep'/'sub'/'store'
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
    with journal.locked(create=True): pass
    locked, replace = tree.replaced(store)
    real = private_journal.encoded
    def encoded(value):
        raw = real(value); replace(); return raw
    with tree.unleaked(), patch.object(private_journal, 'encoded', encoded):
        with pytest.raises(ValueError) as caught:
            with journal.locked(): journal.append(store/'case', [], 'opened', 'open', {})
    assert str(caught.value) == 'case store changed while locked'
    assert sorted(os.listdir(store)) == []
    assert not (locked/'case').exists()


def test_journal_event_lands_in_the_locked_store_when_replaced_during_publication(tree):
    # The swap runs after the store check, so only the held descriptor keeps the event in the locked store.
    store, events = tree.root/'deep'/'sub'/'store', []
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
    with journal.locked(create=True): pass
    locked, replace = tree.replaced(store)
    with tree.unleaked():
        with journal.locked(): journal.append(store/'case', events, 'opened', 'open', {}, before_publish=replace)
    assert sorted(os.listdir(store)) == []
    raw = (locked/'case'/'events'/'0000.json').read_bytes()
    assert events[0][1] == private_journal.hashlib.sha256(raw).hexdigest()
    assert private_journal.record_bytes(raw)[0] == events[0][0]


def test_journal_event_guard_reaches_the_shared_replace_step_locked_and_unlocked(tree):
    # Fault injection (e.g. the native bank-requalification expiry test) hooks _publish_at, so both append paths
    # must hand their guard to it at the replace step.
    store = tree.root/'deep'/'sub'/'store'
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
    real, reached = private_journal._publish_at, []
    def publish(parent, name, value, before_replace):
        if value.get('kind') == 'opened':
            reached.append(name)
            def expired(): raise TimeoutError('synthetic expiry at replace')
            before_replace = expired
        return real(parent, name, value, before_replace)
    guard = lambda: None
    with tree.unleaked(), patch.object(private_journal, '_publish_at', side_effect=publish):
        with pytest.raises(TimeoutError):
            with journal.locked(create=True): journal.append(store/'locked', [], 'opened', 'open', {}, before_publish=guard)
        with pytest.raises(TimeoutError): journal.append(store/'unlocked', [], 'opened', 'open', {}, before_publish=guard)
    assert reached == ['0000.json', '0000.json']
    for case in ('locked', 'unlocked'): assert os.listdir(store/case/'events') == []


def test_journal_publication_keeps_atomic_json_bytes(tree):
    store, events = tree.root/'deep'/'sub'/'store', []
    journal = private_journal.PrivateJournal(store, 'synthetic-v1', lambda *args: True)
    with tree.unleaked():
        with journal.locked(create=True): journal.append(store/'case', events, 'opened', 'open', {})
    target = tree.root/'state.json'; state_module.atomic_json(target, events[0][0])
    assert (store/'case'/'events'/'0000.json').read_bytes() == target.read_bytes()
    assert stat.S_IMODE((store/'case'/'events').stat().st_mode) & 0o077 == 0
    assert sorted(path.name for path in (store/'case'/'events').iterdir()) == ['0000.json']
    assert private_journal.record(store/'.store.json')[0] == dict(schema_version=1, format='synthetic-v1')


def test_project_configuration_create_refuses_parent_swapped_before_the_walk(tree):
    (tree.outside/'sub').mkdir(); value, supported = config.defaults(), config.supported()
    # supported() checks os.open against os.supports_dir_fd, which the swap hook's wrapper is not in.
    with patch.object(config, 'supported', lambda: supported):
        tree.assert_create_refused(None, tree.root/'deep', lambda: config.create(tree.root/'deep'/'sub', value))


@pytest.mark.parametrize('site', ('analysis_snapshot', 'capture'))
def test_working_state_roots_refuse_parent_swapped_after_their_checks(tree, site):
    (tree.outside/'sub').mkdir(); (tree.outside/'sub'/'f').write_bytes(OUTSIDE)
    workspace, deadline = tree.root/'deep'/'sub', time.monotonic()+60
    if site == 'analysis_snapshot':
        tree.assert_create_refused(None, tree.root/'deep', lambda: working_state.analysis_snapshot(workspace, deadline))
    else:
        tree.assert_create_refused((working_state, 'tree'), tree.root/'deep',
                                   lambda: working_state.WorkingState(workspace, deadline))


def test_working_state_publish_refuses_stage_parent_swapped_after_staging(tree):
    (tree.outside/'sub').mkdir(); state = working_state.WorkingState(tree.root/'deep'/'sub', time.monotonic()+60)
    real_mkdir, swap = os.mkdir, tree.swap
    def mirrored(directory):
        # Give the outside tree the stage folder the publication just created, so a followed link would land there.
        for stage in (tree.root/'deep'/'sub').glob('.artifact-stage-*'):
            real_mkdir(tree.outside/'sub'/stage.name, 0o700); real_mkdir(tree.outside/'sub'/stage.name/'artifacts', 0o700)
        swap(directory)
    with patch.object(tree, 'swap', mirrored):
        tree.assert_create_refused(None, tree.root/'deep',
                                   lambda: state.publish(tree.root/'deep'/'sub'/'out', time.monotonic()+60))
    assert not (tree.root/'deep'/'sub'/'out').exists()


def test_runtime_image_scan_refuses_parent_swapped_after_its_checks(tree):
    (tree.root/'deep'/'sub').chmod(0o700); (tree.outside/'sub').mkdir(mode=0o700)
    for name in image.MOUNT_DIRS: (tree.outside/'sub'/name).mkdir(parents=True, exist_ok=True)
    tree.assert_create_refused(None, tree.root/'deep', lambda: image.scan(tree.root/'deep'/'sub'))


def test_cgroup_root_refuses_parent_swapped_after_delegation(tree):
    (tree.outside/'sub').mkdir()
    group = cgroup.Cgroup(tree.root/'deep'/'sub', SimpleNamespace(memory_bytes=1 << 26, processes=8), time.monotonic()+60)
    with patch.object(cgroup, 'delegated', lambda root: Path(root)):
        tree.assert_create_refused((cgroup, 'delegated'), tree.root/'deep', group.__enter__,
                                   'delegated cgroup v2 resource setup failed')
    assert group.parent is None; assert group.fd is None


@pytest.mark.parametrize(('value', 'message'), [
    pytest.param('parent/f', 'path crosses a symlink', id='parent/f'),
    pytest.param('../outside/f', 'unsafe relative path', id='../outside/f')])
def test_contained_still_refuses_links_and_escapes(tree, value, message):
    (tree.root/'parent').symlink_to(tree.outside, target_is_directory=True)
    with pytest.raises(ValueError, match=message):
        contained(tree.root, value)


def test_contained_still_returns_the_regular_target(tree):
    assert contained(tree.root, 'deep/sub/f') == tree.target.absolute()
    assert stat.S_ISREG(os.lstat(contained(tree.root, 'deep/sub/f')).st_mode)
