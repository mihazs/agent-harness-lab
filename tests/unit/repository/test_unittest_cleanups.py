"""tests.support.unittest_cleanups raises the cleanup errors that a hand-driven TestCase would drop."""
import contextlib
import errno
import unittest

import pytest

from tests.support.unittest_cleanups import run_class_cleanups, run_cleanups


def fail(message):
    raise RuntimeError(message)


def test_cleanups_run_lifo_continue_after_a_failure_and_raise_it():
    calls = []
    case = unittest.TestCase()
    case.addCleanup(calls.append, 'first')
    case.addCleanup(fail, 'synthetic cleanup failure')
    case.addCleanup(lambda: case.addCleanup(calls.append, 'nested'))
    with pytest.raises(RuntimeError, match='synthetic cleanup failure'):
        run_cleanups(case)
    assert calls == ['nested', 'first']
    run_cleanups(case)


def test_several_failures_across_cases_raise_one_group_in_order():
    first, second = unittest.TestCase(), unittest.TestCase()
    first.addCleanup(fail, 'first'); second.addCleanup(fail, 'second')
    with pytest.raises(ExceptionGroup) as raised:
        run_cleanups(first, second)
    assert [str(error) for error in raised.value.exceptions] == ['first', 'second']


def test_base_exception_does_not_prevent_other_cases_or_cleanups():
    first, second = unittest.TestCase(), unittest.TestCase()
    calls = []
    first.addCleanup(calls.append, 'remaining')
    first.addCleanup(lambda: (_ for _ in ()).throw(SystemExit('synthetic exit')))
    second.addCleanup(calls.append, 'other case')
    second.addCleanup(fail, 'synthetic failure')
    with pytest.raises(BaseExceptionGroup) as raised:
        run_cleanups(first, second)
    assert calls == ['remaining', 'other case']
    assert [type(error) for error in raised.value.exceptions] == [SystemExit, RuntimeError]
    assert not first._cleanups and not second._cleanups


def test_nested_helper_cleanup_failure_reaches_the_outer_case():
    outer, inner = unittest.TestCase(), unittest.TestCase()
    inner.addCleanup(fail, 'inner'); outer.addCleanup(run_cleanups, inner)
    with pytest.raises(RuntimeError, match='inner'):
        run_cleanups(outer)


def test_a_busy_class_context_exit_is_raised():
    @contextlib.contextmanager
    def busy():
        try:
            yield 'synthetic-root'
        finally:
            raise OSError(errno.EBUSY, 'synthetic leaked cgroup')

    class Scope(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.root = cls.enterClassContext(busy())

    Scope.setUpClass()
    with pytest.raises(OSError, match='synthetic leaked cgroup') as raised:
        run_class_cleanups(Scope)
    assert raised.value.errno == errno.EBUSY


def test_clean_cleanups_raise_nothing():
    class Scope(unittest.TestCase):
        pass
    calls = []
    Scope.addClassCleanup(calls.append, 'class')
    case = Scope(); case.addCleanup(calls.append, 'case')
    run_cleanups(case); run_class_cleanups(Scope)
    assert calls == ['case', 'class']
