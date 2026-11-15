"""Test modules live in the tests/ package (#354); a module added anywhere else fails here, synthetic trees only."""
import fnmatch
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# The evaluator's own checks run beside its scripts wherever the skill is installed (references/execution.md);
# sources/test_results.py is the test-report source adapter, not a test module.
EXCEPTIONS = ('skills/harness-self-improvement/scripts/test_*.py', 'agent_harness_lab/sources/test_results.py')


def ignored_directories(root):
    ignore = root/'.gitignore'
    lines = ignore.read_text().splitlines() if ignore.is_file() else []
    return ['.git', *(line.rstrip('/') for line in lines if line.endswith('/'))]


def misplaced_test_modules(root):
    root, skip = Path(root), ignored_directories(Path(root))
    found = []
    for folder, directories, files in os.walk(root):
        directories[:] = [d for d in directories if not any(fnmatch.fnmatch(d, pattern) for pattern in skip)]
        relative = Path(folder).relative_to(root)
        if relative.parts[:1] == ('tests',):
            continue
        found += [(relative/name).as_posix() for name in files if fnmatch.fnmatch(name, 'test_*.py')
                  and not any(fnmatch.fnmatch((relative/name).as_posix(), pattern) for pattern in EXCEPTIONS)]
    return sorted(found)


def packages_without_init(root):
    tests = Path(root)/'tests'
    return sorted(folder.relative_to(root).as_posix() for folder in [tests, *tests.rglob('*')]
                  if folder.is_dir() and folder.name != '__pycache__' and not (folder/'__init__.py').is_file())


def make_tree(root, *paths):
    for path in paths:
        (root/path).parent.mkdir(parents=True, exist_ok=True)
        (root/path).write_text('')
    return root


def test_no_test_module_lives_outside_tests():
    assert misplaced_test_modules(ROOT) == []


def test_every_tests_directory_is_a_package():
    assert packages_without_init(ROOT) == []


@pytest.mark.parametrize('path', [pytest.param('tools/tests/test_new.py', id='old-tools-tests'),
                                  pytest.param('agent_harness_lab/test_inline.py', id='beside-code'),
                                  pytest.param('test_top.py', id='repository-root'),
                                  pytest.param('skills/harness-self-improvement/test_beside_skill.py', id='skill-outside-scripts')])
def test_a_misplaced_test_module_is_reported(tmp_path, path):
    root = make_tree(tmp_path, 'tests/unit/area/test_kept.py', path)
    assert misplaced_test_modules(root) == [path]


@pytest.mark.parametrize('path', [pytest.param('tests/integration/area/test_kept.py', id='integration-tree'),
                                  pytest.param('skills/harness-self-improvement/scripts/test_run_evaluation.py', id='evaluator'),
                                  pytest.param('agent_harness_lab/sources/test_results.py', id='source-adapter'),
                                  pytest.param('dist/archive/tools/tests/test_old.py', id='ignored-build-output'),
                                  pytest.param('tools/tests/helper.py', id='not-a-test-module')])
def test_allowed_locations_are_not_reported(tmp_path, path):
    root = make_tree(tmp_path, path)
    (root/'.gitignore').write_text('dist/\n')
    assert misplaced_test_modules(root) == []


def test_a_tests_directory_without_init_is_reported(tmp_path):
    root = make_tree(tmp_path, 'tests/__init__.py', 'tests/unit/__init__.py', 'tests/unit/area/test_case.py')
    assert packages_without_init(root) == ['tests/unit/area']
