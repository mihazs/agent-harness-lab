"""pytest, or a stand-in when it cannot be imported.

A standard-library-only runner (the unittest-bound files in .agents/skills/pytest-patterns/SKILL.md) imports
helpers such as SOLVER, OfflineFixture or OracleHistoryTests from pytest-style test modules. Those modules
import pytest from here, so the import still succeeds without pytest. The stand-in's `fixture`, `param` and
`mark.*` return a decorated function unchanged, so unittest never collects it, and any other call, such as
`raises`, `fail` or `skip`, raises ModuleNotFoundError instead of passing.
"""
try:
    import pytest
except ModuleNotFoundError:
    class _Unavailable:
        def __init__(self, path='pytest'):
            self._path = path

        def __getattr__(self, name):
            return _Unavailable(f'{self._path}.{name}')

        def __call__(self, *args, **kwargs):
            if self._path in ('pytest.fixture', 'pytest.param') or self._path.startswith('pytest.mark.'):
                if len(args) == 1 and not kwargs and callable(args[0]):
                    return args[0]
                return self
            raise ModuleNotFoundError(f'{self._path} needs pytest, which is not installed', name='pytest')

    pytest = _Unavailable()

__all__ = ['pytest']
