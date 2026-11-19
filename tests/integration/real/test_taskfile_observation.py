"""Opt-in checks against existing project Taskfiles and instructions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.audit.taskfile_observation import capture
from agent_harness_lab.sources import harness_inventory


PROJECTS = json.loads(os.environ.get('REAL_TASK_PROJECTS', '[]'))
CODE_ROOT = Path(__file__).resolve().parents[3]
WORKTREES = Path.home()/'dev/.worktrees'


@pytest.fixture(params=PROJECTS or [None], ids=lambda _: 'private-project')
def checkout(request):
    if request.param is None: pytest.skip('set REAL_TASK_PROJECTS to existing local checkouts')
    project = Path(request.param).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ahl-task-real-', dir=WORKTREES) as folder:
        root = Path(folder)
        path = root/'checkout'
        git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(path), 'HEAD'], check=True)
        try:
            instruction = project/'AGENTS.md'
            if not instruction.is_file() or instruction.is_symlink(): pytest.skip('no real root instruction')
            shutil.copyfile(instruction, path/'AGENTS.md')
            yield path, root
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(path)], check=True)


def test_real_taskfile_and_instruction_observation(checkout):
    yaml = pytest.importorskip('yaml')
    project, root = checkout
    taskfile = project/'Taskfile.yml'
    if not taskfile.is_file() or taskfile.is_symlink(): pytest.skip('no real root Taskfile')
    expected = yaml.safe_load(taskfile.read_bytes())['tasks']
    before = subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z'])
    profile = dict(schema_version=1, runtime='codex', version=surfaces.PINS['codex'], task_cwd='.')
    inventory = harness_inventory.capture(project, profile, schema_version=2)
    direct = capture(project, inventory, '.')
    environment = dict(os.environ, XDG_STATE_HOME=str(root/'state'), PYTHONPATH=str(CODE_ROOT), PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-m', 'agent_harness_lab', 'audit', str(project), '--runtime', 'codex',
               '--taskfile-observation', '--json']
    result = subprocess.run(command, env=environment, cwd=project, capture_output=True, text=True, timeout=45)
    assert result.returncode in (0, 3)
    cli = json.loads(result.stdout)['taskfile_observation']
    for report in (direct, cli):
        assert report['status'] == 'observed' and set(report['targets']) == set(expected)
        assert report['mention_coverage'] == 'root_instruction_only'
        assert report['target_coverage'] == report['observed_loading'] == report['runtime_execution'] == 'unknown'
        assert report['publication_allowed'] is False
        assert any(row['status'] == 'lexical_candidate' for row in report['mentions'])
        assert all(row['status'] == ('lexical_candidate' if row['target'] in expected else 'unknown')
                   for row in report['mentions'])
    assert subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z']) == before


def test_real_absent_taskfile_stays_unknown(checkout):
    project, _ = checkout
    if (project/'Taskfile.yml').exists(): pytest.skip('selected checkout has a root Taskfile')
    before = subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z'])
    profile = dict(schema_version=1, runtime='codex', version=surfaces.PINS['codex'], task_cwd='.')
    inventory = harness_inventory.capture(project, profile, schema_version=2)
    report = capture(project, inventory, '.')
    assert report['status'] == report['target_coverage'] == 'unknown'
    assert report['mention_coverage'] == 'unknown'
    assert report['targets'] == report['mentions'] == []
    assert subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z']) == before


def test_real_taskfile_without_runtime_root_instruction(checkout):
    yaml = pytest.importorskip('yaml')
    project, root = checkout
    taskfile = project/'Taskfile.yml'
    if not taskfile.is_file() or taskfile.is_symlink() or (project/'CLAUDE.md').exists():
        pytest.skip('needs a real Taskfile without a root Claude instruction')
    before = subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z'])
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    inventory = harness_inventory.capture(project, profile, schema_version=2)
    direct = capture(project, inventory, '.')
    environment = dict(os.environ, XDG_STATE_HOME=str(root/'state'), PYTHONPATH=str(CODE_ROOT), PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-m', 'agent_harness_lab', 'audit', str(project), '--runtime', 'claude-code',
               '--taskfile-observation', '--json']
    result = subprocess.run(command, env=environment, cwd=project, capture_output=True, text=True, timeout=45)
    assert result.returncode in (0, 3)
    cli = json.loads(result.stdout)['taskfile_observation']
    for report in (direct, cli):
        assert report['status'] == 'observed'
        assert set(report['targets']) == set(yaml.safe_load(taskfile.read_bytes())['tasks'])
        assert report['mention_coverage'] == 'unknown' and report['mentions'] == []
    assert subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain', '-z']) == before
