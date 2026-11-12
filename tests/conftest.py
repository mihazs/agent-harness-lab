"""Suite-wide pytest configuration. Holds only fixtures that every tree shares."""

import os

import pytest


# Every AHL_* variable the product code reads. tests/unit/repository/test_env_isolation.py derives the read set from
# the code and fails when one is missing here; AHL_STATE_HOME and AHL_MEMORY_STORE are paths, the third denies entry.
AHL_AMBIENT_VARIABLES = ('AHL_STATE_HOME', 'AHL_MEMORY_STORE', 'AHL_MANAGED_PREPARATION')


@pytest.fixture(autouse=True, scope='session')
def _session_without_ahl_ambient():
    """Function-scoped fixtures run after module/class-scoped ones, so strip the variables before any of them builds an env."""
    with pytest.MonkeyPatch.context() as patch:
        for name in AHL_AMBIENT_VARIABLES: patch.delenv(name, raising=False)
        yield


@pytest.fixture(autouse=True)
def _isolated_state_home(tmp_path_factory, monkeypatch):
    """No test may create or write the real user state directory (limiter lock, quota file)."""
    for name in AHL_AMBIENT_VARIABLES: monkeypatch.delenv(name, raising=False)  # a test that needs one sets it itself
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path_factory.mktemp('xdg-state')))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path_factory.mktemp('xdg-config')))  # the remembered-runtime file lives here


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Doctor reads the global skill links under HOME; no test may inspect the real home directory."""
    monkeypatch.setenv('npm_config_cache', os.environ.get('npm_config_cache') or os.path.join(os.path.expanduser('~'), '.npm'))  # offline npm tests
    monkeypatch.setenv('HOME', str(tmp_path_factory.mktemp('home')))
