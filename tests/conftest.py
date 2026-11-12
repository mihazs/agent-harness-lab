"""Suite-wide pytest configuration. Holds only fixtures that every tree shares."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_home(tmp_path_factory, monkeypatch):
    """No test may create or write the real user state directory (limiter lock, quota file)."""
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path_factory.mktemp('xdg-state')))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path_factory.mktemp('xdg-config')))  # the remembered-runtime file lives here


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Doctor reads the global skill links under HOME; no test may inspect the real home directory."""
    monkeypatch.setenv('npm_config_cache', os.environ.get('npm_config_cache') or os.path.join(os.path.expanduser('~'), '.npm'))  # offline npm tests
    monkeypatch.setenv('HOME', str(tmp_path_factory.mktemp('home')))
