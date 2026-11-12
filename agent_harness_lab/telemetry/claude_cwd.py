"""Content-free working-directory classes for Claude rows after the launch directory attributed the session.

The launch directory (first attributable row) decides attribution elsewhere. Here each row only gets a class:
project_root | project_subdirectory | registered_worktree_of_project | other_location. Registered worktrees are read
from the repository's own files (never by running git, hooks or any repository-controlled command), bounded, and never
followed through symlinks; whatever cannot be read stays unknown and classifies as other_location.
"""
import os
from pathlib import Path
import stat

from .schema import CWD_CLASSES as CLASSES
MAX_WORKTREES = 256
MAX_FILE_BYTES = 4096
# Merge priority: unreadable metadata outranks a non-repository, which outranks a read listing, which outranks none needed.
LISTING = ('unavailable', 'not_a_repository', 'listed', 'not_needed')


def _read(path):
    """One small regular file, never through a symlink; anything else raises OSError or ValueError."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode): raise OSError('not a regular file')
        data = os.read(descriptor, MAX_FILE_BYTES+1)
    finally: os.close(descriptor)
    if len(data) > MAX_FILE_BYTES: raise ValueError('oversized repository metadata')
    return data.decode('utf-8').strip('\n')


def _resolve(base, value):
    return Path(os.path.normpath(os.path.join(base, value)))


def _linked(common, found):
    try: scanner = os.scandir(common/'worktrees')
    except FileNotFoundError: return  # a repository without linked worktrees
    with scanner as listing:
        for number, entry in enumerate(listing):
            if number >= MAX_WORKTREES: raise ValueError('too many registered worktrees')
            if not entry.is_dir(follow_symlinks=False): continue
            try: pointer = _resolve(entry.path, _read(Path(entry.path)/'gitdir'))
            except FileNotFoundError: continue
            # A registration points at <worktree>/.git; the root is its parent and must not be the filesystem root.
            if pointer.name == '.git' and pointer.parent != Path(pointer.anchor): found.add(pointer.parent)


def registered_worktrees(project):
    """(worktree roots other than project, state): listed, not_a_repository or unavailable (unreadable or malformed metadata)."""
    project, git = Path(project), Path(project)/'.git'
    try: info = os.lstat(git)
    except FileNotFoundError: return frozenset(), 'not_a_repository'
    except OSError: return frozenset(), 'unavailable'
    found = set()
    try:
        if stat.S_ISDIR(info.st_mode): common = git
        elif stat.S_ISREG(info.st_mode):
            pointer = _read(git)
            if not pointer.startswith('gitdir: '): return frozenset(), 'unavailable'
            admin = _resolve(project, pointer[len('gitdir: '):])
            common = _resolve(admin, _read(admin/'commondir'))
            if common.name == '.git': found.add(common.parent)
        else: return frozenset(), 'unavailable'
        _linked(common, found)
    except (OSError, ValueError): return frozenset(), 'unavailable'
    found.discard(project)
    return frozenset(found), 'listed'


class Places:
    """Classifies row directories against one project; reads the repository only when a row leaves the project root."""
    def __init__(self, project):
        self.project, self.worktrees, self.listing = Path(project), None, 'not_needed'

    def _within(self, path, root): return path == root or root in path.parents

    def classify(self, cwd):
        path = Path(cwd)
        if not path.is_absolute() or '..' in path.parts: return 'other_location'
        if path == self.project: return 'project_root'
        if self.worktrees is None: self.worktrees, self.listing = registered_worktrees(self.project)
        if any(self._within(path, root) for root in self.worktrees): return 'registered_worktree_of_project'
        return 'project_subdirectory' if self._within(path, self.project) else 'other_location'

    def report(self, captures):
        """Counts only: one Counter of rows per class for each native capture (a session or subagent writer)."""
        return dict(captures=sum(1 for rows in captures if any(rows[name] for name in CLASSES[1:])),
            rows={name: sum(rows[name] for rows in captures) for name in CLASSES}, worktrees=self.listing)


def merge(reports):
    """Sum the per-source reports; the listing state keeps the least certain one."""
    reports = [r for r in reports if isinstance(r, dict) and 'rows' in r]
    if not reports: return None
    return dict(captures=sum(r['captures'] for r in reports), rows={name: sum(r['rows'][name] for r in reports) for name in CLASSES},
        worktrees=next(state for state in LISTING if any(r['worktrees'] == state for r in reports)))


def from_sources(sources):
    """The merged report of the native Claude captures among evidence sources, or None when none carried one."""
    return merge([(row.get('native_coverage') or {}).get('cwd_drift') for row in sources])
