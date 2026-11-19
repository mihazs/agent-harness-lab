"""Check early first and incremental runs against a real checkout with no network."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

import pytest


def test_real_incomplete_forge_still_records_incremental_baseline():
    configured = os.environ.get('REAL_EARLY_INCREMENTAL_PROJECT')
    if not configured or sys.platform != 'linux' or not shutil.which('bwrap') or not shutil.which('codex'):
        pytest.skip('requires a real project, Linux bubblewrap and the Codex CLI')
    project = Path(configured).resolve(strict=True)
    repo = Path(__file__).resolve().parents[3]
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
    before = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    revisions = subprocess.check_output([*git, 'log', '-2', '--format=%H', '--', 'AGENTS.md'], text=True).splitlines()
    if len(revisions) < 2:
        pytest.skip('real project has fewer than two tracked instruction versions')
    older = subprocess.check_output([*git, 'show', revisions[1]+':AGENTS.md'])
    with tempfile.TemporaryDirectory(prefix='ahl-early-incremental-', dir=Path.home()/'dev/.worktrees') as folder:
        root = Path(folder); checkout = root/'checkout'; state = root/'state'
        subprocess.run([*git, 'worktree', 'add', '--quiet', '--detach', str(checkout), 'HEAD'], check=True)
        try:
            if older == (checkout/'AGENTS.md').read_bytes():
                pytest.skip('historical instruction version equals current version')
            env = {key: value for key, value in os.environ.items() if key in ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TERM', 'CODEX_HOME')}
            env.update(AHL_STATE_HOME=str(state), XDG_STATE_HOME=str(root/'xdg-state'), TMPDIR='/tmp',
                       PYTHONDONTWRITEBYTECODE='1', GIT_OPTIONAL_LOCKS='0')
            command = [shutil.which('bwrap'), '--die-with-parent', '--unshare-net', '--ro-bind', '/', '/',
                       '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--bind', str(root), str(root), '--chdir', str(repo), '--',
                       sys.executable, '-m', 'agent_harness_lab', 'improve', str(checkout), '--history', '--history-from',
                       str(project), '--agent', 'codex', '--timeout', '100']
            receipts, run_names = [], []
            for index in range(2):
                if index: (checkout/'AGENTS.md').write_bytes(older)
                with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                    process = subprocess.Popen(command, cwd=repo, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
                    try: process.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait()
                        pytest.fail('real improvement preflight did not finish within its limit')
                    stderr.seek(0)
                    diagnostics = stderr.read(65536).decode(errors='replace').lower()
                    categories = [word for word in ('read-only', 'permission denied', 'no such file', 'usage:') if word in diagnostics]
                    stdout.seek(0)
                    try:
                        preview = json.loads(stdout.read(65536))
                        categories += [str(preview.get('status')), str(preview.get('reason')), str(preview.get('error_type'))]
                        categories += sorted(preview)
                    except (ValueError, AttributeError):
                        pass
                assert process.returncode == 3, categories
                paths = sorted((state/'runs').glob('*/run-*/workflow.json'), key=lambda path: path.stat().st_mtime_ns)
                assert len(paths) == index+1
                receipt = json.loads(paths[-1].read_text())
                assert (receipt['status'], receipt['reason']) == ('blocked', 'completed_work_incomplete')
                assert receipt['completed_work']['git_detail'] == 'commit_messages_patches_and_worktree_content'
                assert not receipt['changed_files']
                receipts.append(receipt)
                run_names.append(paths[-1].parent.name)
            assert 'incremental' not in receipts[0]
            later = receipts[1]['incremental']
            assert later['previous']['run'] == run_names[0]
            assert later['previous']['status'] == 'blocked'
            assert later['disposition'] == 'no_candidate'
            assert later['harness']['changed_since_previous_baseline'] is True
            assert later['harness']['edited']['count'] > 0
            assert later['stages']['candidate_preparation']['action'] == 'skipped'
            assert later['stages']['evaluation_construction']['action'] == 'skipped'
            assert later['stages']['trials']['action'] == 'skipped'
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == before
