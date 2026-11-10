"""Prevent hand-driven unittest cleanup from silently swallowing errors.

Computed attribute names and dynamically resolved aliases remain unverified.
"""
import ast
from collections import Counter
from pathlib import Path
import subprocess
import pytest

REPO = Path(__file__).resolve().parents[3]

# Exceptions identify a reference by file, enclosing scope and expression; counts reject extra calls.
RUNNER_ALLOWLIST = {}
OWNER_ALLOWLIST = {
    ('tests/integration/evaluation/test_construction_access.py', 'TestConstructionAccess.test_real_ledger_v3_derivation_and_frozen_history_survive_canonical_upgrade', 'previous.doCleanups'): (1, 'DerivationHarness ExitStack callback'),
    ('tests/integration/evaluation/test_construction_access.py', 'private_markers', 'harness.doCleanups'): (1, 'ConstructionAccessHarness ExitStack'),
    ('tests/integration/evaluation/test_construction_access.py', 'TestConstructionAccess.harness', 'self.doCleanups'): (1, 'ConstructionAccessHarness ExitStack'),
    ('tests/integration/evaluation/test_construction_consumers.py', 'TestConstructionConsumers.harness', 'self.doCleanups'): (1, 'ConstructionAccessHarness ExitStack'),
    ('tests/integration/evaluation/test_controller_actions.py', 'TestControllerActions.harness', 'self.doCleanups'): (1, 'ControllerActionTests ExitStack'),
    ('tests/integration/evaluation/test_controller_emissions.py', 'TestControllerEmissions.harness', 'self.fixture.doCleanups'): (1, 'ControllerActionTests ExitStack callback'),
    ('tests/integration/evaluation/test_correction_rerun.py', 'TestCorrectionRerun.harness', 'self.doCleanups'): (1, 'CorrectionRerunHarness ExitStack'),
    ('tests/integration/evaluation/test_correction_rerun.py', 'TestCorrectionRerun.test_relocated_old_host_python_selector_normalizes_inside_same_pinned_image', 'relocated.doCleanups'): (1, 'CorrectionRerunHarness ExitStack callback'),
    ('tests/integration/evaluation/test_derivation.py', 'TestDerivation.harness', 'self.doCleanups'): (1, 'DerivationHarness ExitStack'),
    ('tests/integration/evaluation/test_normalized_exports.py', 'ex', 'f.doCleanups'): (1, 'SourceDispatchTests ExitStack'),
    ('tests/integration/evaluation/test_sequential.py', 'TestSequentialOnline._online', 'self.doCleanups'): (1, 'SequentialOnline ExitStack'),
    ('tests/integration/evaluation/test_sequential_kernel.py', 'TestSequentialKernel._sequential', 'self.fixture.doCleanups'): (1, 'SequentialOnline ExitStack'),
    ('tests/integration/evaluation/test_trajectory_assertions.py', 'TestTrajectoryAssertions.harness', 'self.f.doCleanups'): (1, 'ControllerActionTests ExitStack'),
    ('tests/integration/repository/test_fixture_cleanups.py', 'test_claude_journal_cleanup_failure_is_raised', 'journal.doCleanups'): (1, 'ClaudeSourceTests callback-list negative control'),
    ('tests/integration/repository/test_fixture_cleanups.py', 'test_owner_cleanup_failure_propagates_for_each_converted_group', 'fixture.doCleanups'): (2, 'custom owner negative control; custom owner negative-control cleanup'),
    ('tests/integration/repository/test_fixture_cleanups.py', 'test_native_source_owners_run_all_cleanups_and_report_every_failure', 'owner.doCleanups'): (1, 'native source owner multi-failure control'),
    ('tests/integration/repository/test_fixture_cleanups.py', 'test_native_source_cleanup_continues_after_base_exception', 'owner.doCleanups'): (1, 'native source owner base-exception control'),
    ('tests/integration/sources/test_source_claude.py', 'ClaudeSourceTests.doCleanups', 'self.fixture.doCleanups'): (1, 'ClaudeNativeTests callback list'),
    ('tests/integration/sources/test_source_claude.py', 'TestClaudeSource._source', 'self.doCleanups'): (1, 'ClaudeSourceTests callback list'),
    ('tests/integration/sources/test_source_claude_root.py', 'claude_journal', 'journal.doCleanups'): (1, 'ClaudeSourceTests callback list'),
    ('tests/integration/sources/test_source_codex.py', 'NativeSourceTests.doCleanups', 'self.fixture.doCleanups'): (1, 'CodexRolloutTests callback list'),
    ('tests/integration/sources/test_source_dispatch.py', 'TestSourceDispatch._dispatch', 'self.doCleanups'): (1, 'SourceDispatchTests ExitStack'),
    ('tests/integration/sources/test_source_native_compat.py', 'TestNativeCompatibility.test_codex_file_and_root_preview_contracts_match_the_frozen_baseline', 'fixture.doCleanups'): (1, 'NativeSourceTests callback list'),
}
METHODS = {'doCleanups', 'doClassCleanups'}


def bare_references(source):
    found = []

    class References(ast.NodeVisitor):
        def __init__(self): self.scope = []

        def scoped(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = scoped

        def visit_Attribute(self, node):
            if node.attr in METHODS:
                found.append(('.'.join(self.scope), ast.unparse(node)))
            self.generic_visit(node)

        def visit_Call(self, node):
            if (isinstance(node.func, ast.Name) and node.func.id == 'getattr' and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value in METHODS):
                found.append(('.'.join(self.scope), ast.unparse(node)))
            self.generic_visit(node)

    References().visit(ast.parse(source))
    return found


def test_test_helpers_never_call_unittest_cleanups_bare():
    found = []
    try:
        checkout=subprocess.run(['git','-C',str(REPO),'rev-parse','--show-toplevel'],
            capture_output=True,text=True,check=False)
    except FileNotFoundError:
        pytest.skip('tracked cleanup guard requires Git checkout metadata')
    if checkout.returncode or Path(checkout.stdout.strip()).resolve()!=REPO.resolve():
        pytest.skip('tracked cleanup guard requires Git checkout metadata')
    paths=subprocess.check_output(['git','-C',str(REPO),'ls-files','-z','--',
        ':(glob)tests/**/*.py',':(glob)skills/**/test_*.py']).split(b'\0')
    for raw in paths:
        if not raw:continue
        path=REPO/Path(raw.decode('utf-8'))
        if path.is_relative_to(REPO/'tests/support'):continue
        relative=path.relative_to(REPO).as_posix()
        found.extend((relative, scope, expression) for scope, expression in bare_references(path.read_text()))
    allowed = RUNNER_ALLOWLIST | OWNER_ALLOWLIST
    assert all(type(count) is int and count > 0 and reason for count, reason in allowed.values())
    expected = Counter({key: count for key, (count, _) in allowed.items()})
    actual = Counter(found)
    assert not expected - actual, f'remove stale cleanup guard exceptions: {expected - actual}'
    assert not actual - expected, f'route unittest cleanup through tests.support.unittest_cleanups: {actual - expected}'


def test_guard_detects_direct_bound_and_reflective_cleanup_references():
    source = "case.doCleanups()\nregister(case.doClassCleanups)\ngetattr(case, 'doCleanups')()\n"
    assert bare_references(source) == [('', 'case.doCleanups'), ('', 'case.doClassCleanups'),
                                       ('', "getattr(case, 'doCleanups')")]


def test_guard_rejects_a_new_bare_call(tmp_path,monkeypatch):
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    tests=tmp_path/'tests';tests.mkdir()
    (tests/'tracked.py').write_text('pass\n')
    subprocess.run(['git','-C',str(tmp_path),'add','tests/tracked.py'],check=True)
    (tests/'untracked.py').write_text('case.doCleanups()\n')
    (tmp_path/'.gitignore').write_text('tests/ignored.py\n')
    (tests/'ignored.py').write_text('case.doClassCleanups()\n')
    monkeypatch.setitem(globals(),'REPO',tmp_path)
    monkeypatch.setitem(globals(),'OWNER_ALLOWLIST',{})
    test_test_helpers_never_call_unittest_cleanups_bare()
    subprocess.run(['git','-C',str(tmp_path),'add','tests/untracked.py'],check=True)
    with pytest.raises(AssertionError,match='route unittest cleanup'):
        test_test_helpers_never_call_unittest_cleanups_bare()


def test_guard_skips_without_checkout_metadata(tmp_path,monkeypatch):
    monkeypatch.setitem(globals(),'REPO',tmp_path)
    with pytest.raises(pytest.skip.Exception,match='requires Git checkout metadata'):
        test_test_helpers_never_call_unittest_cleanups_bare()


def test_guard_exceptions_survive_line_edits_but_reject_extra_or_removed_calls(tmp_path, monkeypatch):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    path = tmp_path/'tests/example.py'
    path.parent.mkdir()
    path.write_text('def cleanup():\n    case.doCleanups()\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'tests/example.py'], check=True)
    monkeypatch.setitem(globals(), 'REPO', tmp_path)
    monkeypatch.setitem(globals(), 'OWNER_ALLOWLIST', {
        ('tests/example.py', 'cleanup', 'case.doCleanups'): (1, 'synthetic owned cleanup')})
    test_test_helpers_never_call_unittest_cleanups_bare()
    path.write_text('# unrelated edit\n\ndef cleanup():\n    case.doCleanups()\n')
    test_test_helpers_never_call_unittest_cleanups_bare()
    path.write_text('def cleanup():\n    case.doCleanups()\n    case.doCleanups()\n')
    with pytest.raises(AssertionError, match='route unittest cleanup'):
        test_test_helpers_never_call_unittest_cleanups_bare()
    path.write_text('def cleanup():\n    pass\n')
    with pytest.raises(AssertionError, match='remove stale cleanup guard exceptions'):
        test_test_helpers_never_call_unittest_cleanups_bare()
