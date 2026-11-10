"""Fixtures that drive a unittest.TestCase helper by hand must fail when its cleanups fail.

Each row runs one real fixture in a pytest subprocess with its helper's setUp (and setUpClass) replaced by a
synthetic one that registers a failing cleanup, so a leaked temp dir, patch or cgroup surfaces as an error.
"""
from pathlib import Path
import os
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
KERNEL = 'tests.integration.evaluation.test_permission_audit.PermissionKernelTests'
REPEATED_KERNEL = 'tests.integration.evaluation.test_repeated_workflow.RepeatedWorkflowKernel'
ACCESS_KERNEL = 'tests.integration.evaluation.test_repeated_access_companion.RepeatedAccessKernel'
KERNELS = [KERNEL, REPEATED_KERNEL, ACCESS_KERNEL]
ROWS = [
    ('tests.integration.evaluation.test_offline_evaluation', 'offline',
     ['tests.integration.evaluation.test_offline_evaluation.OfflineFixture']),
    ('tests.integration.evaluation.test_oracle_history', 'history',
     ['tests.integration.evaluation.test_oracle_history.OracleHistoryTests']),
    ('tests.integration.evaluation.test_oracle_plan', 'oracle',
     ['tests.integration.evaluation.test_oracle_plan.OraclePlanTests']),
    ('tests.integration.evaluation.test_permission_audit', 'case', [KERNEL]),
    ('tests.integration.evaluation.test_permission_audit', 'inference_fixture',
     ['tests.integration.sandbox.test_sandbox_inference.ControllerInferenceTests']),
    ('tests.integration.evaluation.test_permission_audit', 'responses_fixture',
     ['tests.integration.sandbox.test_sandbox_inference.ResponsesEvaluationTests']),
    ('tests.integration.evaluation.test_repeated_workflow', 'admission',
     ['tests.integration.evaluation.test_repeated_workflow.RepeatedWorkflowAdmission']),
    ('tests.integration.evaluation.test_repeated_workflow', 'kernel_case', [REPEATED_KERNEL]),
    ('tests.integration.evaluation.test_repeated_access_companion', 'access',
     ['tests.integration.evaluation.test_repeated_access_companion.RepeatedAccessAdmission']),
    ('tests.integration.evaluation.test_repeated_access_companion', 'kernel_case', [ACCESS_KERNEL]),
    ('tests.integration.sandbox.test_sandbox_construction', 'controlled',
     ['tests.integration.sandbox.test_sandbox_construction.ControlledWorkflowTests']),
    ('tests.integration.sandbox.test_sandbox_graders', 'grader',
     ['tests.integration.sandbox.test_sandbox_graders.SandboxedGraderTests']),
    ('tests.integration.sandbox.test_sandbox_inference', 'controller',
     ['tests.integration.sandbox.test_sandbox_inference.ControllerInferenceTests']),
    ('tests.integration.sandbox.test_sandbox_inference', 'evaluation',
     ['tests.integration.sandbox.test_sandbox_inference.ResponsesEvaluationTests']),
]
TEMPLATE = '''import contextlib, errno, importlib

def fail():
    raise RuntimeError('synthetic per-test cleanup failure')

def set_up(self):
    self.f = None; self.addCleanup(fail)

@contextlib.contextmanager
def busy():
    try: yield None
    finally: raise OSError(errno.EBUSY, 'synthetic leaked cgroup')

for path in {classes!r}:
    module, _, name = path.rpartition('.')
    helper = getattr(importlib.import_module(module), name)
    helper.setUp = set_up
    if path in {kernels!r}:
        helper.setUpClass = classmethod(lambda cls: cls.enterClassContext(busy()))
for path in {wrapped!r}:
    module, _, name = path.rpartition('.')
    helper = getattr(importlib.import_module(module), name)
    helper.setUp = (lambda original: lambda self: (original(self), self.addCleanup(fail)))(helper.setUp)
del helper  # a module-level TestCase would be collected

from {module} import {imports}

def test_synthetic({fixture}):
    pass
'''


def source(module, fixture, classes, wrapped=()):
    imports = f'kernel_scope, {fixture}' if set(classes) & set(KERNELS) else fixture
    return TEMPLATE.format(module=module, fixture=fixture, imports=imports, classes=classes, kernels=KERNELS,
                           wrapped=list(wrapped))


def pytest_run(root, names):
    # Each row must error, so an inherited PYTEST_ADDOPTS such as CI's --maxfail=1 would hide later rows.
    env = {key: value for key, value in os.environ.items() if key != 'PYTEST_ADDOPTS'}
    return subprocess.run([sys.executable, '-m', 'pytest', '-c', str(REPO/'pyproject.toml'), '--rootdir', str(root),
                           '-q', '-rE', '-p', 'no:cacheprovider', '-p', 'no:xdist', *(str(root/name) for name in names)],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=600)


def test_manually_driven_unittest_cleanup_failures_fail_the_fixture(tmp_path):
    names = []
    for index, (module, fixture, classes) in enumerate(ROWS):
        name = f'test_cleanup_row_{index}_{fixture}.py'; names.append(name)
        (tmp_path/name).write_text(source(module, fixture, classes))
    run = pytest_run(tmp_path, names)
    errors = [line for line in run.stdout.splitlines() if line.startswith('ERROR ')]
    for index, name in enumerate(names):
        found = [line for line in errors if f'{name}::test_synthetic' in line]
        assert found, (ROWS[index], run.stdout[-4000:], run.stderr[-2000:])
    assert f'{len(names)} passed' in run.stdout, run.stdout[-4000:]


@pytest.mark.parametrize('row', [row for row in ROWS if set(row[2]) & set(KERNELS)], ids=lambda row: row[0])
def test_kernel_scope_reports_a_busy_class_cleanup(tmp_path, row):
    module, fixture, classes = row
    (tmp_path/'test_cleanup_kernel_scope.py').write_text(source(module, fixture, classes))
    run = pytest_run(tmp_path, ['test_cleanup_kernel_scope.py'])
    assert 'OSError: [Errno 16] synthetic leaked cgroup' in run.stdout, run.stdout[-4000:]
    assert 'RuntimeError: synthetic per-test cleanup failure' in run.stdout, run.stdout[-4000:]


def test_nested_admission_cleanup_failure_fails_the_bank_authority_fixture(monkeypatch):
    from tests.integration.evaluation import test_oracle_bank_authority
    from contextlib import ExitStack

    def fail():
        raise RuntimeError('synthetic admission cleanup failure')

    def broken(add_cleanup):
        add_cleanup(fail)
        raise ValueError('synthetic setup failure')

    monkeypatch.setattr(test_oracle_bank_authority.admissions, 'RegressionAdmission', broken)
    with pytest.raises(RuntimeError, match='synthetic admission cleanup failure'):
        with ExitStack() as cleanups:
            test_oracle_bank_authority.OracleBankAuthority(cleanups.callback)



@pytest.mark.parametrize('module', [
    'tests.integration.workflow.test_one_call',
    'tests.integration.workflow.test_one_call_boundary',
    'tests.integration.workflow.test_one_call_custody',
])
def test_one_call_fixture_reports_cleanup_failure(tmp_path, module):
    (tmp_path/'test_one_cleanup.py').write_text(
        "from tempfile import TemporaryDirectory\n"
        "import tests.support.one_call as support\n"
        "class BrokenTemporaryDirectory(TemporaryDirectory):\n"
        "    def cleanup(self):\n"
        "        super().cleanup()\n"
        "        raise RuntimeError('synthetic per-test cleanup failure')\n"
        "support.TemporaryDirectory = BrokenTemporaryDirectory\n"
        f"from {module} import one\n"
        "def test_synthetic(one): pass\n")
    run = pytest_run(tmp_path, ['test_one_cleanup.py'])
    assert 'ERROR ' in run.stdout and 'synthetic per-test cleanup failure' in run.stdout, run.stdout[-4000:]
    assert '1 passed' in run.stdout, run.stdout[-4000:]


def test_preservation_receipt_helper_propagates_nested_cleanup_failure(monkeypatch):
    from contextlib import ExitStack
    import unittest
    from tests.support import preservation_receipts

    def fail():
        raise RuntimeError('synthetic nested cleanup failure')

    class BrokenHistory(unittest.TestCase):
        def setUp(self):
            self.addCleanup(fail)

    monkeypatch.setattr(preservation_receipts.legacy, 'OracleHistoryTests', BrokenHistory)
    with pytest.raises(RuntimeError, match='synthetic nested cleanup failure'):
        with ExitStack() as cleanups:
            preservation_receipts.PreservationReceipt(cleanups).fixture()


def test_preservation_history_propagates_nested_cleanup_failure(monkeypatch):
    from contextlib import ExitStack
    from tests.integration.evaluation import test_preservation_history

    def fail():
        raise RuntimeError('synthetic nested cleanup failure')

    def broken(add_cleanup):
        add_cleanup(fail)
        raise ValueError('synthetic setup failure')

    monkeypatch.setattr(test_preservation_history.core.authority, 'OracleBankAuthority', broken)
    with pytest.raises(RuntimeError, match='synthetic nested cleanup failure'):
        with ExitStack() as cleanups:
            test_preservation_history.PreservationHistory(cleanups)


def test_replay_fixture_propagates_cleanup_failure(monkeypatch, tmp_path):
    from contextlib import ExitStack
    from types import SimpleNamespace
    from tests.integration.evaluation import test_regression_replay

    def fail():
        raise RuntimeError('synthetic replay cleanup failure')

    monkeypatch.setattr(test_regression_replay.tempfile, 'TemporaryDirectory',
                        lambda: SimpleNamespace(name=str(tmp_path), cleanup=fail))
    with pytest.raises(RuntimeError, match='synthetic replay cleanup failure'):
        with ExitStack() as cleanups:
            test_regression_replay.RegressionReplay(cleanups.callback)


@pytest.mark.parametrize('authority', [False, True], ids=['review', 'authority'])
def test_review_helpers_propagate_nested_cleanup_failure(monkeypatch, authority):
    from contextlib import ExitStack
    from tests.integration.evaluation import test_regression_review, test_review_authority

    def fail():
        raise RuntimeError('synthetic review cleanup failure')

    def broken(add_cleanup):
        add_cleanup(fail)
        raise ValueError('synthetic setup failure')

    monkeypatch.setattr(test_regression_review.fixtures, 'RegressionReplay', broken)
    helper = test_review_authority.ReviewAuthorityFixture if authority else test_regression_review.ReviewFixture
    with pytest.raises(RuntimeError, match='synthetic review cleanup failure'):
        with ExitStack() as cleanups:
            helper(cleanups.callback)


@pytest.mark.parametrize('module,helper', [
    ('tests.integration.evaluation.test_regression_controls', 'RegressionControl'),
    ('tests.integration.evaluation.test_grader_correction', 'GraderCorrection'),
])
def test_grader_helpers_propagate_cleanup_failure(monkeypatch, tmp_path, module, helper):
    from contextlib import ExitStack
    import importlib
    from types import SimpleNamespace

    target = importlib.import_module(module)
    def fail():
        raise RuntimeError('synthetic grader cleanup failure')

    monkeypatch.setattr(target.tempfile, 'TemporaryDirectory',
                        lambda: SimpleNamespace(name=str(tmp_path), cleanup=fail))
    with pytest.raises(RuntimeError, match='synthetic grader cleanup failure'):
        with ExitStack() as cleanups:
            getattr(target, helper)(cleanups.callback)


def test_requalification_propagates_cleanup_failure(monkeypatch):
    from contextlib import contextmanager
    from tests.integration.evaluation import test_cohort_requalification

    @contextmanager
    def broken():
        try:
            yield None
        finally:
            raise RuntimeError('synthetic requalification cleanup failure')

    monkeypatch.setattr(test_cohort_requalification, 'bank_correction', broken)
    with pytest.raises(RuntimeError, match='synthetic requalification cleanup failure'):
        with test_cohort_requalification.requalification():
            pass
