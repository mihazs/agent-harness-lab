"""Taskfile runners use pytest; only standard-library-only runners keep unittest, and their files are listed."""
import ast
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TASKFILE = ROOT/'Taskfile.yml'
ADAPTATION = ROOT/'.agents/skills/pytest-patterns/SKILL.md'
MARKDOWN = [ROOT/'README.md', ROOT/'CONTRIBUTING.md', *sorted((ROOT/'docs').rglob('*.md')),
            *sorted((ROOT/'skills').rglob('*.md'))]
# test:evaluation:bank-qualification:wheel runs this task from a venv that cannot install pytest.
STDLIB_TASKS = {'test:evaluation:bank-qualification'}
# `-m unittest discover`, or the bank runner's `unittest.main(module=None) ROOT discover`, with an optional -t.
DISCOVER = re.compile(r'''unittest\b\S*(?: \S+)? discover -s (\S+)(?: -t \S+)? -p ["']?([^\s"']+)''')
# The bank runner appends the checkout to sys.path so an installed wheel or archive keeps precedence.
STDLIB_MAIN = 'unittest.main(module=None)'
# Loads a file the way `python -m unittest discover` does, with pytest unimportable, and reports what pytest
# would also collect from it: module-level test functions and Test classes that are not unittest.TestCase.
NO_PYTEST_PROBE = r'''
import importlib.abc, inspect, json, sys, unittest
class Blocked(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition('.')[0] in {'pytest', '_pytest'}:
            raise ModuleNotFoundError(f'No module named {name!r}', name=name)
sys.meta_path.insert(0, Blocked())
root, start, name = sys.argv[1:]
sys.path.insert(0, root)
def flatten(suite):
    for item in suite:
        yield from flatten(item) if isinstance(item, unittest.TestSuite) else [item]
cases = list(flatten(unittest.defaultTestLoader.discover(start, pattern=name)))
errors = [str(case._exception) for case in cases if hasattr(case, '_exception')]
errors += [str(error) for error in unittest.defaultTestLoader.errors]
modules = [m for m in list(sys.modules.values()) if getattr(m, '__file__', None) and m.__file__.endswith('/' + name)]
pytest_only = [f'{m.__name__}.{key}' for m in modules for key, value in vars(m).items()
               if (key.startswith('test') and inspect.isfunction(value))
               or (key.startswith('Test') and inspect.isclass(value) and not issubclass(value, unittest.TestCase))]
print(json.dumps({'cases': len(cases) - len(errors), 'errors': errors, 'modules': len(modules), 'pytest_only': pytest_only}))
'''


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
    return [(name, line) for name, lines in task_blocks().items() for line in lines
            if f' -m {module} ' in line or (module == 'unittest' and STDLIB_MAIN in line)]


def start_dir(text):
    path = Path(text)
    if path.is_absolute() and text.startswith('/path/to/'):
        path = ROOT/'skills'/text.removeprefix('/path/to/')
    return path if path.is_absolute() else ROOT/path


def probe_without_pytest(path):
    path = Path(path)
    done = subprocess.run([sys.executable, '-I', '-c', NO_PYTEST_PROBE, str(ROOT), str(path.parent), path.name],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


def assert_runs_without_pytest(path):
    report = probe_without_pytest(path)
    assert report['errors'] == [], report['errors']
    assert report['modules'] == 1, f'{path} was not imported by unittest discovery'
    assert report['pytest_only'] == [], f'{path} defines cases only pytest collects: {report["pytest_only"]}'
    assert report['cases'] > 0, f'{path} has no unittest cases'


def unittest_bound():
    commands = [line for _, line in runner_lines('unittest')]
    commands += [line for doc in MARKDOWN for line in doc.read_text().splitlines()]
    found = set()
    for line in commands:
        for start, pattern in DISCOVER.findall(line.replace('{{.ROOT_DIR | shellQuote}}', str(ROOT))):
            folder = start_dir(start)
            assert folder.is_dir(), line
            found |= {p.relative_to(ROOT).as_posix() for p in folder.rglob(pattern) if fnmatch.fnmatch(p.name, pattern)
                      and '__pycache__' not in p.parts}
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


@pytest.mark.parametrize('path', sorted(listed_bound()))
def test_unittest_bound_file_runs_fully_without_pytest(path):
    assert_runs_without_pytest(ROOT/path)


CLEAN = 'import unittest\n\n\nclass Cases(unittest.TestCase):\n    def test_one(self):\n        pass\n'


@pytest.mark.parametrize('extra', ['import pytest\n', 'from _pytest import fixtures\n',
                                   'def test_pytest_only(tmp_path):\n    pass\n',
                                   'class TestPlain:\n    def test_two(self):\n        pass\n'])
def test_pytest_dependence_in_a_unittest_bound_file_is_rejected(tmp_path, extra):
    clean, mutated = tmp_path/'clean'/'test_case.py', tmp_path/'mutated'/'test_case.py'
    for path, text in ((clean, CLEAN), (mutated, extra + CLEAN if 'import' in extra else CLEAN + extra)):
        path.parent.mkdir()
        path.write_text(text)
    assert_runs_without_pytest(clean)
    with pytest.raises(AssertionError):
        assert_runs_without_pytest(mutated)
