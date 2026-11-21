"""Check an oversized real ledger is refused with an actionable limit reason."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from agent_harness_lab.interop.otel import MAX_TOTAL_BYTES


def test_real_oversized_ledger_reports_the_limit():
    run_name = os.environ.get('REAL_LEDGER_RUN')
    project_name = os.environ.get('REAL_LEDGER_PROJECT')
    if not run_name or not project_name:
        pytest.skip('set REAL_LEDGER_RUN and REAL_LEDGER_PROJECT')
    run = Path(run_name).resolve(strict=True)
    project = Path(project_name).resolve(strict=True)
    invocation = json.loads((run / 'invocation.json').read_text())
    if Path(invocation['project']).resolve(strict=True) != project:
        raise ValueError('recorded run belongs to another project')
    if (run / 'events.v2.jsonl').stat().st_size <= MAX_TOTAL_BYTES:
        pytest.skip('selected real ledger does not exceed the input limit')
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
    before = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-otel-limit-', dir=Path.home() / 'dev/.worktrees') as folder:
        root = Path(folder)
        checkout, output = root / 'checkout', root / 'output'
        subprocess.run([*git, 'worktree', 'add', '--quiet', '--detach', str(checkout), 'HEAD'], check=True)
        try:
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3])
            result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'trace-export', '--format',
                                     'otel-json', '--input', str(run), '--output', str(output)], cwd=checkout,
                                    env=env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 2 and 'input_too_large' in result.stderr
            assert not output.exists()
            assert not subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'])
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == before
