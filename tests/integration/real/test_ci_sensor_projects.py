"""Opt-in CI sensor regression using existing local checkouts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from agent_harness_lab.audit.feedback_sensors import capture

PROJECTS = json.loads(os.environ.get('REAL_SENSOR_PROJECTS', '[]'))
CODE_ROOT = Path(__file__).resolve().parents[3]
WORKTREES = Path.home()/'dev/.worktrees'


@pytest.fixture(params=PROJECTS or [None], ids=lambda _: 'private-project')
def checkout(request):
    if request.param is None: pytest.skip('set REAL_SENSOR_PROJECTS to existing local checkouts')
    project = Path(request.param).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ahl-ci-sensor-real-', dir=WORKTREES) as folder:
        root = Path(folder)
        path = root/'checkout'
        git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(path), 'HEAD'], check=True)
        try:
            yield path, root
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(path)], check=True)


def test_real_ci_configuration_is_reported_without_modifying_checkout(checkout):
    project, root = checkout
    workflows = project/'.github/workflows'
    expected = {str(path.relative_to(project)) for path in workflows.glob('*')
                if path.is_file() and not path.is_symlink() and path.suffix in ('.yml', '.yaml')}
    if (project/'.gitlab-ci.yml').is_file() and not (project/'.gitlab-ci.yml').is_symlink():
        expected.add('.gitlab-ci.yml')
    if not expected: pytest.skip('selected checkout has no recognized CI configuration')
    before = subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z'])
    direct = capture(project)
    environment = dict(os.environ, XDG_STATE_HOME=str(root/'state'), PYTHONPATH=str(CODE_ROOT), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'audit', str(project), '--runtime', 'codex',
                             '--feedback-sensors', '--json'], cwd=project, env=environment,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode in (0, 3)
    cli = json.loads(result.stdout)['feedback_sensor_candidates']
    for report in (direct, cli):
        found = {row['path'] for row in report['candidates'] if row['category'] == 'ci' and row['stage'] == 'ci'
                 and row['content'] == 'not_read' and row['execution'] == 'unknown'}
        assert expected <= found
        assert report['consistency'] == 'not_checked' and report['publication_allowed'] is False
    assert subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z']) == before
