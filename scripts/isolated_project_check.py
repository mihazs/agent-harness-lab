#!/usr/bin/env python3
"""Run a command against a temporary detached worktree of a real repository and prove the repository did not change.

    python scripts/isolated_project_check.py REPO [--ref REF] [--patch FILE] [--timeout SECONDS]
        [--memory-cap-gib N] [--receipt FILE] -- COMMAND [ARGS...]

`{worktree}` inside COMMAND is replaced by the temporary worktree path. The command runs with that worktree as its
working directory and a private temporary `AHL_STATE_HOME`. Git hooks are disabled for the checkout, so no project script
runs. Afterwards the worktree is removed and the real checkout is fingerprinted again: any difference is `drift`.

Exit 0 when the command succeeded and nothing drifted, 1 on drift, 2 on a setup problem; otherwise the command's own status.
The receipt names changed files and hashes; it holds no file contents. Keep it private: it describes a real project.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

VERSION = 1
NO_HOOKS = ('-c', 'core.hooksPath=/dev/null')
BUS_VARIABLES = ('XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS')


def _git(repo, *args, check=True):
    done = subprocess.run(['git', *NO_HOOKS, *args], cwd=repo, capture_output=True, text=True)
    if check and done.returncode:
        raise RuntimeError(f'git {" ".join(args)} failed: {done.stderr.strip() or done.stdout.strip()}')
    return done.stdout


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fingerprint(repo):
    """What a command could change in a checkout, as hashes and counts; no file content."""
    repo = Path(repo)
    config = repo / '.git' / 'config'
    worktrees = [line for line in _git(repo, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('worktree ')]
    return {
        'head': _git(repo, 'rev-parse', 'HEAD').strip(),
        'refs': _digest(_git(repo, 'for-each-ref', '--format=%(refname) %(objectname)')),
        'stashes': len(_git(repo, 'stash', 'list').splitlines()),
        'worktrees': len(worktrees),
        'status': _digest(_git(repo, 'status', '--porcelain=v1', '-uall')),
        'index': _digest(_git(repo, 'ls-files', '-s')),
        'config': _digest(config.read_text()) if config.is_file() else None,
    }


def drift(before, after):
    return sorted(key for key in before if before[key] != after.get(key))


def wrap_with_memory_cap(argv, gib, which=shutil.which):
    """Prefix `systemd-run --user --scope` when a cap is requested and the tool exists; otherwise return argv unchanged."""
    if not gib or not which('systemd-run'):
        return list(argv)
    return ['systemd-run', '--user', '--scope', '--collect', '--quiet', '-p', f'MemoryMax={gib}G', '-p', 'MemorySwapMax=0', '--', *argv]


def substitute(argv, worktree):
    return [part.replace('{worktree}', str(worktree)) for part in argv]


def changed_in(worktree):
    status = _git(worktree, 'status', '--porcelain=v1', '-uall')
    return sorted(line[3:] for line in status.splitlines())


def run(repo, command, ref='HEAD', patch=None, timeout=None, memory_cap_gib=None, inherit_home=False, pass_env=(), log_dir=None, which=shutil.which):
    """Execute COMMAND in a temporary worktree of REPO at REF; return the receipt (a dict)."""
    repo = Path(repo).resolve()
    if not (repo / '.git').exists():
        raise RuntimeError(f'{repo} is not a git checkout')
    commit = _git(repo, 'rev-parse', '--verify', f'{ref}^{{commit}}').strip()
    before = fingerprint(repo)
    receipt = {'tool_version': VERSION, 'commit': commit, 'command': list(command), 'timeout_seconds': timeout,
               'memory_cap_gib': memory_cap_gib, 'inherit_home': inherit_home, 'pass_env': sorted(pass_env), 'before': before}
    with tempfile.TemporaryDirectory(prefix='ahl-isolated-') as temporary:
        worktree, state = Path(temporary) / 'worktree', Path(temporary) / 'state'
        state.mkdir()
        _git(repo, 'worktree', 'add', '--detach', '--quiet', str(worktree), commit)
        try:
            if patch:
                _git(worktree, 'apply', str(Path(patch).resolve()))
            argv = wrap_with_memory_cap(substitute(command, worktree), memory_cap_gib, which)
            capped = argv[:1] == ['systemd-run']
            env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'AHL_STATE_HOME': str(state),
                   'REAL_CHECKOUT': str(repo), 'WORKTREE': str(worktree)}
            if inherit_home and 'HOME' in os.environ:  # a real runtime needs its saved login; tests keep a private home
                env['HOME'] = os.environ['HOME']
            else:
                env['HOME'] = str(Path(temporary) / 'home')
                Path(env['HOME']).mkdir()
            # `systemd-run --user` needs the user bus; without these the capped command never starts.
            names = [*pass_env, *(BUS_VARIABLES if capped else ())]
            env.update({name: os.environ[name] for name in names if name in os.environ})
            try:
                done = subprocess.run(argv, cwd=worktree, env=env, capture_output=True, text=True, timeout=timeout)
                receipt.update(command_status=done.returncode, command_timed_out=False,
                               stdout_sha256=_digest(done.stdout), stderr_sha256=_digest(done.stderr))
                if log_dir:  # opt-in and private: the receipt itself keeps hashes only
                    Path(log_dir).mkdir(parents=True, exist_ok=True)
                    (Path(log_dir) / 'stdout.txt').write_text(done.stdout)
                    (Path(log_dir) / 'stderr.txt').write_text(done.stderr)
            except subprocess.TimeoutExpired:
                receipt.update(command_status=None, command_timed_out=True)
            receipt['changed_files'] = changed_in(worktree)
        finally:
            subprocess.run(['git', *NO_HOOKS, 'worktree', 'remove', '--force', str(worktree)], cwd=repo, capture_output=True)
            subprocess.run(['git', *NO_HOOKS, 'worktree', 'prune'], cwd=repo, capture_output=True)
    after = fingerprint(repo)
    differing = drift(before, after)
    receipt.update(after=after, drift=differing, verdict='drift' if differing else 'unchanged')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('repo', type=Path, help='A git checkout whose files must not change')
    parser.add_argument('--ref', default='HEAD', help='Commit to check out in the temporary worktree (default: HEAD)')
    parser.add_argument('--patch', type=Path, help='Apply this patch in the worktree before running the command')
    parser.add_argument('--timeout', type=int, help='Seconds allowed for the command (default: none)')
    parser.add_argument('--memory-cap-gib', type=int, help='Run the command in a systemd user scope with this memory limit')
    parser.add_argument('--inherit-home', action='store_true', help='Pass the real HOME (needed for a runtime login); default: a private empty home')
    parser.add_argument('--pass-env', action='append', default=[], metavar='NAME', help='Pass this host variable through (repeatable)')
    parser.add_argument('--log-dir', type=Path, help='Also write the command stdout.txt and stderr.txt here (private; the receipt keeps hashes only)')
    parser.add_argument('--receipt', type=Path, help='Write the JSON receipt here (default: print it). It records the command line verbatim: never put a secret on it')
    argv = list(sys.argv[1:] if argv is None else argv)
    split = argv.index('--') if '--' in argv else len(argv)  # REMAINDER would swallow options placed before `--`
    args, command = parser.parse_args(argv[:split]), argv[split + 1:]
    if not command:
        parser.error('a command is required after --')
    try:
        receipt = run(args.repo, command, args.ref, args.patch, args.timeout, args.memory_cap_gib, args.inherit_home, args.pass_env, args.log_dir)
    except RuntimeError as error:
        print(f'isolated check could not start: {error}', file=sys.stderr)
        return 2
    text = json.dumps(receipt, indent=2, sort_keys=True)
    if args.receipt:
        args.receipt.write_text(text + '\n')
    else:
        print(text)
    print(f'isolated check: {receipt["verdict"]}' + (f' ({", ".join(receipt["drift"])})' if receipt['drift'] else ''), file=sys.stderr)
    if receipt['drift']:
        return 1
    return 124 if receipt['command_timed_out'] else receipt['command_status']


if __name__ == '__main__':
    sys.exit(main())
