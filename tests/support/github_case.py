"""Runs GitHubFixture.setUp() as a pytest fixture for plain Test* classes."""
from contextlib import ExitStack
import unittest

import pytest

from tests.unit.sources.test_source_github import GitHubFixture


class GitHubPytestCase(GitHubFixture):
    """Subclass this in a Test* class; GitHubFixture.setUp() and read() may still call the unittest assertions."""
    @pytest.fixture(autouse=True)
    def _github(self):
        cleanups = ExitStack(); self.addCleanup = cleanups.callback
        self.setUp()
        yield
        cleanups.close()

    def __getattr__(self, name):
        if name.startswith('assert'): return getattr(unittest.TestCase(), name)
        raise AttributeError(name)
