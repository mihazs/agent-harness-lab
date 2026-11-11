import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ('audit', 'analysis', 'mine', 'diagnose', 'evaluate', 'benchmark', 'skills', 'bank', 'experiment', 'experiment-history', 'grader-correction', 'regression', 'regression-review', 'sandbox-check',
            'research-simulate', 'incident-search', 'promotion', 'annotation-plan', 'compare', 'benchmark-report', 'analyze', 'improve', 'watch', 'doctor', 'init', 'sources')


def run_cli(folder, *args):
    # Temporary cwd and explicit source path keep the run away from native project capture.
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    run = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *args], cwd=folder, env=env,
                         capture_output=True, text=True, timeout=30)
    assert os.listdir(folder) == [], f'ahl {" ".join(args)} created files'
    return run


def test_top_level_help_lists_every_command(tmp_path):
    run = run_cli(tmp_path, '--help')
    assert run.returncode == 0, run.stderr
    assert 'usage:' in run.stdout
    for name in COMMANDS:
        assert name in run.stdout


@pytest.mark.parametrize(('args', 'boundaries'), [
    (('--help',), ('behavioral candidates stay recommendations', 'except explicit improve --repair-contracts')),
    (('improve', '--help'), ('behavioral candidates stay recommendations', 'explicit --repair-contracts may apply')),
    (('watch', '--help'), ('watch has no --repair-contracts and applies no candidate',)),
])
def test_help_states_adoption_boundary(tmp_path, args, boundaries):
    run = run_cli(tmp_path, *args)
    assert run.returncode == 0, run.stderr
    assert run.stderr == ''
    output = ' '.join(run.stdout.lower().split())
    assert 'adopt supported harness repairs' not in output
    assert 'continuously improve' not in output
    for boundary in boundaries:
        assert boundary in output


def test_version(tmp_path):
    run = run_cli(tmp_path, '--version')
    assert run.returncode == 0, run.stderr
    assert re.search(r'^\d+\.\d+\.\d+', run.stdout.strip())


@pytest.mark.parametrize('name', [pytest.param(name, id=name) for name in COMMANDS])
def test_subcommand_help(tmp_path, name):
    # benchmark imports the POSIX-only resource module.
    if name == 'benchmark' and importlib.util.find_spec('resource') is None:
        pytest.skip('resource module unavailable on this platform')
    run = run_cli(tmp_path, name, '--help')
    assert run.returncode == 0, f'ahl {name} --help: {run.stderr}'
    assert 'usage:' in run.stdout
    assert '--help' in run.stdout
