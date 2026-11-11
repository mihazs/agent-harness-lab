"""Suite-wide pytest configuration. Holds only fixtures that every tree shares."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_home(tmp_path_factory, monkeypatch):
    """No test may create or write the real user state directory (limiter lock, quota file)."""
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path_factory.mktemp('xdg-state')))
