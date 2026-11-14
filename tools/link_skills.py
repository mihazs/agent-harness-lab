#!/usr/bin/env python3
"""Link the owned skill globally, keeping replaced directories as backups."""
import argparse
from pathlib import Path
import tempfile

if not __package__:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_harness_lab.read_only import FrozenProjectError, require_writable

NAMES = ('harness-self-improvement',)
RETIRED = ('harness-evaluation',)  # folded into harness-self-improvement; stale owned links are removed


def install(source, destination, backup_root):
    source, destination, backup_root = Path(source).resolve(), Path(destination), Path(backup_root)
    require_writable(destination)
    require_writable(backup_root)
    pending, stale = [], []
    for name in RETIRED:
        link = destination / name
        if link.is_symlink():
            target = link.resolve()
            # Ours only when it points at the retired skill of this source or of an installer release.
            if target.name == name and (target.parent == source or (
                    target.parent.name == 'skills' and (target.parent.parent / '.release-sha256').is_file())):
                stale.append(link)
    for name in NAMES:
        target, link = source / name, destination / name
        if not (target / 'SKILL.md').is_file():
            raise ValueError(f'missing skill: {name}')
        if link.is_symlink():
            if link.resolve() == target:
                continue
            raise ValueError(f'refusing to replace a foreign symlink: {link}')
        if link.exists() and not link.is_dir():
            raise ValueError(f'refusing to replace a non-directory: {link}')
        pending.append((target, link))
    if not pending and not stale:
        return None
    require_writable(destination)
    destination.mkdir(parents=True, exist_ok=True)
    require_writable(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    require_writable(backup_root)
    backup = Path(tempfile.mkdtemp(prefix='skills-', dir=backup_root))
    moved, installed = [], []
    try:
        for target, link in [*pending, *((None, link) for link in stale)]:
            if link.exists() or link.is_symlink():
                saved = backup / link.name
                require_writable(destination)
                require_writable(backup_root)
                link.rename(saved)
                moved.append((saved, link))
            if target:
                require_writable(destination)
                link.symlink_to(target, target_is_directory=True)
                installed.append(link)
    except BaseException:
        try:
            for link in reversed(installed):
                require_writable(destination)
                link.unlink()
            for saved, link in reversed(moved):
                require_writable(destination)
                require_writable(backup_root)
                saved.rename(link)
        except FrozenProjectError as exc:
            raise FrozenProjectError(f'{exc}; recover original files from backup {backup}') from exc
        raise
    return backup


def main(argv=None, source=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Creates the harness-self-improvement link and removes a stale harness-evaluation link it owns; replaces existing directories only after backup.\n'
        'Foreign symlinks and non-directories are refused; already-current links are unchanged.\n'
        'Prints a text receipt or backup location; does not run agents or install dependencies.\n'
        'Run this project\'s harness-self-improvement once with: ahl skills invoke [PROJECT]; PROJECT defaults to the\n'
        'current directory and installation alone does not start a run.\n'
        'Example: ahl skills --destination /tmp/ahl-skills --backup-root /tmp/ahl-skill-backups')
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--agent', choices=('codex', 'claude'), default='codex',
                        help='Target host: codex uses ~/.agents/skills, claude uses ~/.claude/skills; excludes --destination (default: %(default)s)')
    target.add_argument('--destination', type=Path, help='Explicit skill-link directory instead of the selected host directory; excludes --agent')
    parser.add_argument('--backup-root', type=Path, default=Path.home() / '.local/state/harness-improver/backups', help='Directory for private backups of replaced skill directories (default: ~/.local/state/harness-improver/backups)')
    args = parser.parse_args(argv)
    destination = args.destination or Path.home() / ('.claude/skills' if args.agent == 'claude' else '.agents/skills')
    backup = install(source or Path(__file__).resolve().parents[1] / 'skills', destination, args.backup_root)
    print(f'Linked skills. Previous directories: {backup}' if backup else 'Skill links already current.')


if __name__ == '__main__':
    main()
