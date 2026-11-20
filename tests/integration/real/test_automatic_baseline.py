"""Opt-in native acceptance on a checked-out project with no pinned evaluation bank."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

HOME = Path.home()  # read at import: the suite replaces HOME per test, and a live runtime needs its real login
WORKTREES = HOME/'dev/.worktrees'


@pytest.mark.parametrize('entry', [('improve',), ('skills', 'invoke')], ids=['cli', 'skill'])
def test_native_first_use_evaluates_before_proposing(entry):
    source = os.environ.get('AHL_REAL_BASELINE_PROJECT')
    runtime = os.environ.get('AHL_REAL_BASELINE_RUNTIME')
    if not source or runtime not in ('codex', 'claude'):
        pytest.skip('set AHL_REAL_BASELINE_PROJECT and AHL_REAL_BASELINE_RUNTIME for a live session')
    project = Path(source).resolve(strict=True)
    git = ['git', '-c', 'index.threads=1', '-c', 'core.hooksPath=/dev/null', '-C', str(project)]
    original = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-native-baseline-', dir=WORKTREES) as directory:
        root = Path(directory)
        checkout, output = root/'project', root/'output'
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(checkout), 'HEAD'], check=True)
        try:
            settings = checkout/'ahl.json'
            if settings.exists() and json.loads(settings.read_text()).get('bank'):
                pytest.skip('the selected real project has a pinned task bank')
            env = dict(os.environ, HOME=str(HOME), AHL_STATE_HOME=str(root/'state'), PYTHONPATH=str(Path(__file__).resolve().parents[3]))
            result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *entry, str(checkout),
                                     '--agent', runtime, '--scope', 'project', '--output', str(output)],
                                    env=env, cwd=checkout, capture_output=True, text=True, timeout=2800)
            receipt = json.loads((output/'workflow.json').read_text())
            assert receipt.get('loop', {}).get('stop_criterion') != 'no_actionable_mechanism'
            assert (output/'preparation/evaluation-0/manifest.json').is_file(), receipt.get('reason')
            trials = json.loads((output/'evaluation-0/results.json').read_text())['trials']
            assert trials and all(row['version'] == 'baseline' for row in trials)
            facts = (receipt.get('status'), receipt.get('reason'), receipt.get('evaluation_blocker'),
                     sorted({(row['status'], str(row.get('reason'))) for row in trials}))  # typed labels only, no project text
            assert any(row['status'] in ('passed', 'failed') for row in trials), facts
            assert 'candidate_preparation' in result.stderr
            assert result.stderr.index('baseline_evaluation') < result.stderr.index('candidate_preparation')
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == original


def test_recorded_baseline_provides_scoring_task_context():
    source = os.environ.get('AHL_REAL_EVALUATION_RUN')
    if not source:
        pytest.skip('set AHL_REAL_EVALUATION_RUN to an existing private run')
    run = Path(source).resolve(strict=True)
    invocation_path = run/'invocation.json'
    if not invocation_path.is_file():
        invocation_path = run/'preparation/context.json'
    invocation = json.loads(invocation_path.read_text())
    project = Path(invocation['project']).resolve(strict=True)
    manifest = json.loads((run/'preparation/evaluation-0/manifest.json').read_text())
    results = json.loads((run/'evaluation-0/results.json').read_text())
    tasks, trials = manifest['tasks'], results['trials']
    scoring = {task['id'] for task in tasks if task.get('split') in ('development', 'regression')}
    expected = {row['task'] for row in trials if row.get('version') == 'baseline'
                and row.get('status') in ('passed', 'failed') and row.get('task') in scoring}
    if not expected:
        pytest.skip('recorded evaluation has no graded baseline scoring task')
    git = ['git', '-c', 'index.threads=1', '-c', 'core.hooksPath=/dev/null', '-C', str(project)]
    original = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-baseline-evidence-', dir=WORKTREES) as folder:
        checkout = Path(folder)/'project'
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(checkout), 'HEAD'], check=True)
        try:
            from agent_harness_lab.loop.adapters import baseline_observations
            observed = baseline_observations(tasks, trials)
            assert {row['task'] for row in observed} == expected
            assert all(row['prompt'] for row in observed)
            assert not subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'])
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == original
