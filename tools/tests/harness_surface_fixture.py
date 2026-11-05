"""Synthetic harness-surface checkouts for inventory tests; invented content only, no real configuration.

build() writes a checkout with root and nested surface files plus the hostile entries an inventory must refuse:
a file symlink, a directory symlink, a nested repository and a FIFO. Later increments pass extra files (relative
path to text) or drop the hostile entries. tree_state() is a no-follow fingerprint for unchanged-tree checks.
"""
import hashlib
import os
from pathlib import Path
import stat

SECRET = 'SYNTHETIC_SURFACE_CONTENT'
FILES = {
    'AGENTS.md': f'# {SECRET} root agents\n',
    'CLAUDE.md': f'# {SECRET} root claude\n',
    'README.md': 'not a harness surface\n',
    'src/task/AGENTS.md': f'# {SECRET} nested agents\n',
    '.claude/skills/alpha/SKILL.md': f'---\nname: alpha\n---\n{SECRET}\n',
    '.agents/skills/beta/SKILL.md': f'---\nname: beta\n---\n{SECRET}\n',
    '.claude/hooks/check.sh': f'#!/bin/sh\necho {SECRET}\n',
}
# Hostile entries build() adds; each must be refused, never followed or opened.
HOSTILE = {'linked.md': 'symlink', '.claude/skills/linked': 'symlink', 'vendor/nested': 'nested_repository',
           'surface.fifo': 'special_file'}


def build(root, files=None, *, hostile=True, git=True):
    """Create the checkout at root and return it; files are added to (or, with None text, removed from) FILES."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    selected = dict(FILES)
    for name, text in (files or {}).items():
        if text is None: selected.pop(name, None)
        else: selected[name] = text
    for name, text in selected.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
    if (root / '.claude/hooks/check.sh').exists(): (root / '.claude/hooks/check.sh').chmod(0o755)
    if git:
        (root / '.git').mkdir(exist_ok=True); (root / '.git/HEAD').write_text('ref: refs/heads/main\n')
    if hostile:
        (root / 'linked.md').symlink_to('AGENTS.md')
        (root / '.claude/skills').mkdir(parents=True, exist_ok=True)
        (root / '.claude/skills/linked').symlink_to('../../src', target_is_directory=True)
        nested = root / 'vendor/nested'; (nested / '.git').mkdir(parents=True)
        (nested / 'AGENTS.md').write_text(f'{SECRET} nested repository\n')
        os.mkfifo(root / 'surface.fifo')
    return root


def outside(root, name='outside'):
    """A sibling directory with surface-shaped files that must never be read unless declared."""
    folder = Path(root) / name
    (folder / 'skills/gamma').mkdir(parents=True, exist_ok=True)
    (folder / 'AGENTS.md').write_text(f'{SECRET} external agents\n')
    (folder / 'skills/gamma/SKILL.md').write_text(f'---\nname: gamma\n---\n{SECRET}\n')
    (folder / 'config.toml').write_text('[projects."/synthetic"]\ntrust_level = "trusted"\n')
    return folder


def tree_state(root):
    """Sorted (path, type, mode, size, content hash) for every entry, read without following any link."""
    rows = []
    for folder, directories, names in os.walk(root, followlinks=False):
        for name in sorted(directories + names):
            path = Path(folder) / name
            info = path.lstat()
            content = None
            if stat.S_ISREG(info.st_mode): content = hashlib.sha256(path.read_bytes()).hexdigest()
            elif stat.S_ISLNK(info.st_mode): content = os.readlink(path)
            rows.append((str(path.relative_to(root)), stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode),
                         info.st_size if content is not None else None, content))
    return tuple(sorted(rows))
