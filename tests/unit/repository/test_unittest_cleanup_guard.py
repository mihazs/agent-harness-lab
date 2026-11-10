"""Prevent hand-driven unittest cleanup from silently swallowing errors."""
import ast
import os
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[3]

# Exact (relative path, line, method) exceptions for real TestCases are reserved
# for cleanup owned by the unittest runner; each entry must explain why.
RUNNER_ALLOWLIST = {}
# These are not unittest methods: their owners use ExitStack or a callback list
# and propagate failures directly. Exact locations prevent new callers slipping in.
OWNER_ALLOWLIST = {
    ('tests/integration/evaluation/test_construction_access.py', 531, 'doCleanups'): 'DerivationHarness ExitStack callback',
    ('tests/integration/evaluation/test_construction_access.py', 579, 'doCleanups'): 'ConstructionAccessHarness ExitStack',
    ('tests/integration/evaluation/test_construction_access.py', 136, 'doCleanups'): 'ConstructionAccessHarness ExitStack',
    ('tests/integration/evaluation/test_construction_consumers.py', 22, 'doCleanups'): 'ConstructionAccessHarness ExitStack',
    ('tests/integration/evaluation/test_controller_actions.py', 49, 'doCleanups'): 'ControllerActionTests ExitStack',
    ('tests/integration/evaluation/test_controller_emissions.py', 20, 'doCleanups'): 'ControllerActionTests ExitStack callback',
    ('tests/integration/evaluation/test_correction_rerun.py', 97, 'doCleanups'): 'CorrectionRerunHarness ExitStack',
    ('tests/integration/evaluation/test_correction_rerun.py', 118, 'doCleanups'): 'CorrectionRerunHarness ExitStack callback',
    ('tests/integration/evaluation/test_derivation.py', 134, 'doCleanups'): 'DerivationHarness ExitStack',
    ('tests/integration/evaluation/test_normalized_exports.py', 57, 'doCleanups'): 'SourceDispatchTests ExitStack',
    ('tests/integration/evaluation/test_sequential.py', 315, 'doCleanups'): 'SequentialOnline ExitStack',
    ('tests/integration/evaluation/test_sequential_kernel.py', 34, 'doCleanups'): 'SequentialOnline ExitStack',
    ('tests/integration/evaluation/test_trajectory_assertions.py', 59, 'doCleanups'): 'ControllerActionTests ExitStack',
    ('tests/integration/repository/test_fixture_cleanups.py', 365, 'doCleanups'): 'ClaudeSourceTests callback-list negative control',
    ('tests/integration/repository/test_fixture_cleanups.py', 416, 'doCleanups'): 'custom owner negative control',
    ('tests/integration/repository/test_fixture_cleanups.py', 417, 'doCleanups'): 'custom owner negative-control cleanup',
    ('tests/integration/repository/test_fixture_cleanups.py', 432, 'doCleanups'): 'native source owner multi-failure control',
    ('tests/integration/repository/test_fixture_cleanups.py', 450, 'doCleanups'): 'native source owner base-exception control',
    ('tests/integration/sources/test_source_claude.py', 30, 'doCleanups'): 'ClaudeNativeTests callback list',
    ('tests/integration/sources/test_source_claude.py', 64, 'doCleanups'): 'ClaudeSourceTests callback list',
    ('tests/integration/sources/test_source_claude_root.py', 23, 'doCleanups'): 'ClaudeSourceTests callback list',
    ('tests/integration/sources/test_source_codex.py', 54, 'doCleanups'): 'CodexRolloutTests callback list',
    ('tests/integration/sources/test_source_dispatch.py', 55, 'doCleanups'): 'SourceDispatchTests ExitStack',
    ('tests/integration/sources/test_source_native_compat.py', 40, 'doCleanups'): 'NativeSourceTests callback list',
}
METHODS = {'doCleanups', 'doClassCleanups'}


def bare_references(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in METHODS:
            yield node.lineno, node.attr
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'getattr'
              and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value in METHODS):
            yield node.lineno, node.args[1].value


def test_test_helpers_never_call_unittest_cleanups_bare():
    found = []
    for root, directories, files in os.walk(REPO):
        directories[:] = [name for name in directories if name not in
                          {'.git', '.venv', '.pytest_cache', '__pycache__', 'node_modules', 'artifacts', 'build', 'dist'}]
        for name in files:
            if not name.endswith('.py'): continue
            path = Path(root)/name
            if path.is_relative_to(REPO/'tests/support'): continue
            relative = path.relative_to(REPO).as_posix()
            found.extend((relative, line, name) for line, name in bare_references(path.read_text()))
    allowed=RUNNER_ALLOWLIST | OWNER_ALLOWLIST
    assert all(allowed.values()), 'every exception needs a cleanup ownership explanation'
    assert len(found)==len(set(found)), 'multiple cleanup references at one allowlisted location are unsupported'
    assert set(allowed) <= set(found), 'remove stale cleanup guard exceptions'
    assert not set(found) - set(allowed), f'route unittest cleanup through tests.support.unittest_cleanups: {sorted(set(found) - set(allowed))}'


def test_guard_detects_direct_bound_and_reflective_cleanup_references():
    source = "case.doCleanups()\nregister(case.doClassCleanups)\ngetattr(case, 'doCleanups')()\n"
    assert list(bare_references(source)) == [(1, 'doCleanups'), (2, 'doClassCleanups'), (3, 'doCleanups')]


def test_guard_rejects_a_new_bare_call(tmp_path,monkeypatch):
    (tmp_path/'new_test.py').write_text('case.doCleanups()\n')
    monkeypatch.setitem(globals(),'REPO',tmp_path)
    monkeypatch.setitem(globals(),'OWNER_ALLOWLIST',{})
    with pytest.raises(AssertionError,match='route unittest cleanup'):
        test_test_helpers_never_call_unittest_cleanups_bare()
