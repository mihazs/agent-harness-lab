"""Taskfile runners use pytest; only standard-library-only runners keep unittest, and their files are listed."""
import ast
import fnmatch
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TASKFILE = ROOT/'Taskfile.yml'
ADAPTATION = ROOT/'.agents/skills/pytest-patterns/SKILL.md'
MARKDOWN = [ROOT/'README.md', ROOT/'CONTRIBUTING.md', *sorted((ROOT/'docs').rglob('*.md')),
            *sorted((ROOT/'skills').rglob('*.md'))]
# test:evaluation:bank-qualification:wheel runs this task from a venv that cannot install pytest.
STDLIB_TASKS = {'test:evaluation:bank-qualification'}
DISCOVER = re.compile(r'''-m unittest discover -s (\S+) -p ["']?([^\s"']+)''')


def task_blocks():
    blocks, name = {}, None
    for line in TASKFILE.read_text().splitlines():
        found = re.match(r'^  ([\w:.-]+):\s*$', line)
        if found:
            name = found.group(1)
        elif name and line.startswith('    '):
            blocks.setdefault(name, []).append(line)
    return blocks


def runner_lines(module):
    return [(name, line) for name, lines in task_blocks().items() for line in lines if f' -m {module} ' in line]


def start_dir(text):
    path = Path(text)
    if path.is_absolute() and text.startswith('/path/to/'):
        path = ROOT/'skills'/text.removeprefix('/path/to/')
    return path if path.is_absolute() else ROOT/path


def unittest_bound():
    commands = [line for _, line in runner_lines('unittest')]
    commands += [line for doc in MARKDOWN for line in doc.read_text().splitlines()]
    found = set()
    for line in commands:
        for start, pattern in DISCOVER.findall(line.replace('{{.ROOT_DIR | shellQuote}}', str(ROOT))):
            folder = start_dir(start)
            assert folder.is_dir(), line
            found |= {p.relative_to(ROOT).as_posix() for p in folder.iterdir() if fnmatch.fnmatch(p.name, pattern)}
    return found


def listed_bound():
    section = ADAPTATION.read_text().split('- **Unittest-bound files.**', 1)
    assert len(section) == 2, 'the adaptation section lacks the unittest-bound files bullet'
    bullet = re.split(r'\n- \*\*', section[1], maxsplit=1)[0]
    return set(re.findall(r'`([\w./-]+\.py)`', bullet))


def test_only_standard_library_tasks_run_unittest():
    assert {name for name, _ in runner_lines('unittest')} == STDLIB_TASKS


def test_standard_library_task_is_reached_from_an_index_free_wheel_venv():
    wheel = '\n'.join(task_blocks()['test:evaluation:bank-qualification:wheel'])
    assert '-m pip install --no-index --no-deps' in wheel
    assert '- task: test:evaluation:bank-qualification\n' in wheel + '\n'
    assert 'setup:test' not in wheel


@pytest.mark.parametrize('line', [pytest.param(line, id=f'{name}-{index}')
                                  for index, (name, line) in enumerate(runner_lines('pytest'))])
def test_pytest_runner_has_no_unittest_selection_flags(line):
    assert ' discover ' not in line and ' -s ' not in line
    assert not re.search(r' -p (?!no:)', line)
    assert line.count(' -k ') <= 1


def test_unittest_bound_files_are_listed_in_the_adaptation():
    assert listed_bound() == unittest_bound()


@pytest.mark.parametrize('path', sorted(listed_bound()))
def test_unittest_bound_file_keeps_a_testcase_class(path):
    tree = ast.parse((ROOT/path).read_text())
    bases = [ast.unparse(base) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) for base in node.bases]
    assert 'unittest.TestCase' in bases
