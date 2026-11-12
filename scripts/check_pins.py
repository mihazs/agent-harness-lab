#!/usr/bin/env python3
"""Require that each regression pin fails on the code it was written against (non-vacuity guard).

A pin test file declares one or more markers on their own comment lines:

    # fixed-in: <commit> [-k <pytest expression selecting the tests that pin this fix>]

For every marker the file is copied into a temporary Git worktree at the parent of <commit> and run there with the
current Python. The pin is non-vacuous only when pytest reports failing tests; a pin that passes on the unfixed
parent is VACUOUS (exit 1). A pin that cannot run at the parent (collection error, no tests selected, missing
commit, timeout) is INCONCLUSIVE and never counted as passing (exit 3 when nothing is vacuous).
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

MARKER = re.compile(r'^# fixed-in: ([0-9a-f]{7,40})(?: -k (.+))?$', re.M)


def markers(root):
    """Yield (relative file, commit, -k expression or None) for every pin marker under tests/."""
    for path in sorted(Path(root, 'tests').rglob('test_*.py')):
        for found in MARKER.finditer(path.read_text()):
            yield str(path.relative_to(root)), found.group(1), found.group(2)


def check(root, relative, commit, expression=None, timeout=600):
    """Return (verdict, detail): 'non-vacuous', 'VACUOUS' or 'INCONCLUSIVE'."""
    root = Path(root)
    try:
        parent = subprocess.check_output(['git', 'rev-parse', '--verify', '--quiet', commit + '^'], cwd=root, text=True).strip()
    except subprocess.CalledProcessError:
        return 'INCONCLUSIVE', f'commit {commit} or its parent is not in this clone'
    with tempfile.TemporaryDirectory(prefix='ahl-pin-') as temporary:
        tree = Path(temporary) / 'tree'
        subprocess.run(['git', 'worktree', 'add', '--detach', '--quiet', str(tree), parent], cwd=root, check=True)
        try:
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)  # the current pin, against the parent's code
            command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-x', relative, *(['-k', expression] if expression else [])]
            try:
                result = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return 'INCONCLUSIVE', f'timed out after {timeout}s'
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(tree)], cwd=root, capture_output=True)
    tail = (result.stdout.strip().splitlines() or [''])[-1]
    if result.returncode == 1:
        return 'non-vacuous', f'fails on {parent[:10]} ({tail})'
    if result.returncode == 0:
        return 'VACUOUS', f'passes on the unfixed parent {parent[:10]} ({tail})'
    return 'INCONCLUSIVE', f'pytest exit {result.returncode} on {parent[:10]} ({tail})'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Needs full Git history (the fixing commit and its parent). Creates and removes temporary worktrees; never touches your checkout.\n'
        'Exit 0 every pin is non-vacuous, 1 any pin is vacuous, 3 none vacuous but some inconclusive. Example: python scripts/check_pins.py')
    parser.add_argument('files', nargs='*', help='Pin files to check, relative to --root (default: every marked file under tests/)')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1], help='Repository root (default: this checkout)')
    parser.add_argument('--timeout', type=int, default=600, help='Seconds allowed per pin run (default: %(default)s)')
    args = parser.parse_args(argv)
    rows = [row for row in markers(args.root) if not args.files or row[0] in args.files]
    if not rows:
        print('INCONCLUSIVE: no fixed-in markers found')
        return 3
    verdicts = []
    for relative, commit, expression in rows:
        verdict, detail = check(args.root, relative, commit, expression, args.timeout)
        verdicts.append(verdict)
        print(f'{verdict}: {relative} fixed-in {commit}' + (f' -k {expression}' if expression else '') + f': {detail}')
    print(f'{verdicts.count("non-vacuous")} non-vacuous, {verdicts.count("VACUOUS")} vacuous, {verdicts.count("INCONCLUSIVE")} inconclusive')
    return 1 if 'VACUOUS' in verdicts else 3 if 'INCONCLUSIVE' in verdicts else 0


if __name__ == '__main__':
    sys.exit(main())
