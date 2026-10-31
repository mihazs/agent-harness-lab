#!/usr/bin/env python3
"""Link the two owned skills globally, keeping replaced directories as backups."""
import argparse
from pathlib import Path
import tempfile

NAMES = ('harness-evaluation', 'harness-self-improvement')


def install(source, destination, backup_root):
    source, destination = Path(source).resolve(), Path(destination)
    pending = []
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
    if not pending:
        return None
    destination.mkdir(parents=True, exist_ok=True)
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix='skills-', dir=backup_root))
    moved, installed = [], []
    try:
        for target, link in pending:
            if link.exists():
                saved = backup / link.name
                link.rename(saved)
                moved.append((saved, link))
            link.symlink_to(target, target_is_directory=True)
            installed.append(link)
    except BaseException:
        for link in reversed(installed):
            link.unlink()
        for saved, link in reversed(moved):
            saved.rename(link)
        raise
    return backup


def main(argv=None, source=None):
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--agent', choices=('codex', 'claude'), default='codex',
                        help='Skill discovery directory (default: codex)')
    target.add_argument('--destination', type=Path, help='Custom skill directory')
    parser.add_argument('--backup-root', type=Path, default=Path.home() / '.local/state/harness-improver/backups')
    args = parser.parse_args(argv)
    destination = args.destination or Path.home() / ('.claude/skills' if args.agent == 'claude' else '.agents/skills')
    backup = install(source or Path(__file__).resolve().parents[1] / 'skills', destination, args.backup_root)
    print(f'Linked skills. Previous directories: {backup}' if backup else 'Skill links already current.')


if __name__ == '__main__':
    main()
