"""tests.support.optional_pytest keeps helper modules importable by the standard-library unittest runners."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys

import pytest

from tests.support import optional_pytest

HELPER = Path(optional_pytest.__file__)


class BlockPytest(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition('.')[0] in {'pytest', '_pytest'}:
            raise ModuleNotFoundError(f'No module named {name!r}', name=name)


@pytest.fixture
def standin(monkeypatch):
    """Loads a fresh copy of the helper while pytest cannot be imported."""
    monkeypatch.delitem(sys.modules, 'pytest')
    monkeypatch.setattr(sys, 'meta_path', [BlockPytest(), *sys.meta_path])
    spec = importlib.util.spec_from_file_location('synthetic_optional_pytest', HELPER)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.pytest


def test_real_pytest_is_exported_when_installed():
    assert optional_pytest.pytest is pytest


def test_standin_leaves_decorated_functions_unchanged(standin):
    def case(value):
        return value
    decorators = [standin.fixture, standin.fixture(scope='module'),
                  standin.mark.parametrize('value', [standin.param(1, id='one'), standin.param(2, id='two')]),
                  standin.mark.parametrize('value, other', [(1, 2)]),
                  standin.mark.skipif(True, reason='synthetic')]
    for decorate in decorators:
        assert decorate(case) is case
    assert standin is not pytest


@pytest.mark.parametrize('call', [pytest.param(lambda standin: standin.raises(ValueError), id='raises'),
                                  pytest.param(lambda standin: standin.fail('synthetic'), id='fail'),
                                  pytest.param(lambda standin: standin.skip('synthetic'), id='skip')])
def test_standin_fails_any_runtime_use(standin, call):
    with pytest.raises(ModuleNotFoundError, match='needs pytest, which is not installed'):
        call(standin)
