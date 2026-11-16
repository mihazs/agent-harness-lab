"""Run every command of docs/score-by-capability-area.md against the synthetic project it builds and compare the output (#800).

The how-to builds the project with scripts/build_area_score_example.py, prints each report and shows the exact output. The test
runs the same commands in a temporary directory and compares standard output (or standard error for the one command that
ends with exit code 2) with the page. No model, network, real project or recorded history is used.
"""
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from agent_harness_lab import cli
from agent_harness_lab.evaluation import statistics

ROOT = Path(__file__).resolve().parents[3]
HOWTO = (ROOT / 'docs/score-by-capability-area.md').read_text()
SECTION = HOWTO.split('## Try it on a synthetic project', 1)[1].split('\n## ', 1)[0]
BLOCKS = re.findall(r'```(sh|text)\n(.*?)\n```', SECTION, re.S)
BUILD, *COMMANDS = [body for kind, body in BLOCKS if kind == 'sh']
OUTPUTS = [body for kind, body in BLOCKS if kind == 'text']
EXIT_CODES = (0, 0, 2)


@pytest.fixture
def project(tmp_path):
    build = shlex.split(BUILD.splitlines()[0])
    assert build[:2] == ['python3', 'scripts/build_area_score_example.py'] and build[2] == '--out'
    target = tmp_path / 'example'
    subprocess.run([sys.executable, str(ROOT / build[1]), '--out', str(target)], check=True, capture_output=True)
    assert BUILD.splitlines()[1] == f'cd {build[3]}'
    return target


def test_the_page_pairs_each_command_with_the_output_it_shows():
    assert len(COMMANDS) == len(OUTPUTS) == len(EXIT_CODES) and all(c.startswith('ahl score . --spec experiment.json --records trials.json') for c in COMMANDS)


@pytest.mark.parametrize(('command', 'shown', 'code'), list(zip(COMMANDS, OUTPUTS, EXIT_CODES)), ids=['all-areas', 'one-area', 'undeclared-area'])
def test_each_documented_command_prints_what_the_page_shows(project, monkeypatch, capsys, command, shown, code):
    monkeypatch.chdir(project)  # the page does `cd` into the project, and the command names its files relative to it
    assert cli.main(shlex.split(command)[1:]) == code
    captured = capsys.readouterr()
    assert (captured.out if code == 0 else captured.err).rstrip('\n') == shown
    assert (captured.err if code == 0 else captured.out) == ''


def test_the_four_states_of_the_example_are_the_four_the_page_explains(project, capsys):
    cli.main(['score', str(project), '--spec', str(project / 'experiment.json'), '--records', str(project / 'trials.json'), '--format', 'md'])
    assert re.findall(r'^\| [^|`]+\(`[^|]+ \| (\w+) \|', capsys.readouterr().out, re.M) == ['scored', 'provisional', 'no_evidence', 'blocked']


def test_the_example_writes_nothing_beyond_the_files_the_page_names_and_refuses_an_existing_directory(project):
    assert sorted(path.name for path in project.iterdir()) == ['ahl.json', 'bank', 'experiment.json', 'trials.json']
    again = subprocess.run([sys.executable, str(ROOT / 'scripts/build_area_score_example.py'), '--out', str(project)], capture_output=True, text=True)
    assert (again.returncode, again.stdout) == (2, '') and 'refusing to overwrite' in again.stderr


@pytest.mark.parametrize(('roots', 'half_width'), [(3, '1.57'), (10, '0.86'), (30, '0.50'), (100, '0.27'), (300, '0.16')])
def test_each_interval_half_width_in_the_limits_table_is_the_one_the_statistics_module_computes(roots, half_width):
    row = next(line.strip() for line in HOWTO.splitlines() if re.match(rf'\s*\| {roots} \| ', line))
    assert row == f'| {roots} | {half_width} |'
    assert f'{statistics.radius([1 / roots] * roots, 0.05):.2f}' == half_width


def test_about_120_roots_is_the_least_that_narrows_a_success_rate_to_a_quarter_either_way():
    least = next(n for n in range(2, 1000) if statistics.radius([1 / n] * n, 0.05) <= 0.25)
    assert 115 <= least <= 125 and 'about 120 roots' in ' '.join(HOWTO.split())
