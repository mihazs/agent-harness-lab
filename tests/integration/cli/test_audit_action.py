"""The pull-request audit Action is read-only, secret-free and reproducible (#392); synthetic fixtures only."""
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/audit_action.sh'
WORKFLOWS = [ROOT / '.github/workflows/harness-audit.yml']


def checkout(tmp_path):
    project = tmp_path / 'project'
    shutil.copytree(ROOT / 'examples/flawed-audit', project)
    git = ['git', '-C', str(project), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']
    subprocess.run([*git, 'init', '-q'], check=True)
    subprocess.run([*git, 'add', '-A'], check=True)
    subprocess.run([*git, 'commit', '-qm', 'x'], check=True)
    return project


def tree(project):
    return sorted((p.relative_to(project).as_posix(), p.read_bytes() if p.is_file() else None)
                  for p in project.rglob('*') if '.git' not in p.relative_to(project).parts)


def run(project, tmp_path, runtime='claude-code'):
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'GITHUB_ACTION_PATH': str(ROOT),
           'GITHUB_WORKSPACE': str(project), 'RUNNER_TEMP': str(tmp_path), 'INPUT_RUNTIME': runtime}
    return subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)


def test_run_with_empty_secrets_is_reproducible_and_writes_nothing(tmp_path):
    project = checkout(tmp_path)
    before = tree(project)
    first, second = run(project, tmp_path), run(project, tmp_path)
    assert first.returncode == 0 and first.stdout == second.stdout and first.stderr == ''
    assert first.stdout.splitlines()[0].startswith('::warning file=.claude/settings.json,title=ahl audit enforcement::hook_unknown_event')
    assert 'file=CLAUDE.md' in first.stdout
    assert tree(project) == before and list(tmp_path.glob('ahl-audit.*')) == []


def test_changed_files_limit_the_annotations(tmp_path):
    project = checkout(tmp_path)
    git = ['git', '-C', str(project), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']
    (project / 'CLAUDE.md').write_text('\n\n')
    (project / 'note.txt').write_text('changed')
    subprocess.run([*git, 'add', '-A'], check=True)
    subprocess.run([*git, 'commit', '-qm', 'y'], check=True)
    out = run(project, tmp_path).stdout
    assert 'file=CLAUDE.md' in out and '.claude/settings.json' not in out


def test_workflow_is_pull_request_only_read_only_and_secret_free():
    for path in WORKFLOWS:
        text = path.read_text()
        assert re.search(r'^on:\n  pull_request:\n(?!  \S)', text, re.M)
        assert re.search(r'^permissions:\n  contents: read\n(?!  )', text, re.M)
        assert not re.search(r'pull_request_target|workflow_run|secrets\.|GITHUB_TOKEN', text)
        for use in re.findall(r'uses:\s*(\S+)', text):
            assert use == './' or re.search(r'@[0-9a-f]{40}$', use) or use.startswith('actions/'), use


def test_action_passes_inputs_through_the_environment_only():
    action = (ROOT / 'action.yml').read_text()
    assert re.search(r'^runs:\n  using: composite', action, re.M)
    for run_block in re.findall(r'run:\s*(.+)', action):
        assert '${{' not in run_block
    assert 'secrets.' not in action and 'uses:' not in action
