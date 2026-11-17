"""The opt-in test wrapper must either apply a cap or skip without running tests."""
from types import SimpleNamespace

from scripts import capped_test


def test_capped_command_passes_memory_and_python_as_individual_arguments(monkeypatch):
    called = []
    monkeypatch.setattr(capped_test.shutil, 'which', lambda name: '/usr/bin/systemd-run')
    monkeypatch.setattr(capped_test.subprocess, 'run', lambda args, **kw: called.append((args, kw)) or SimpleNamespace(returncode=7))
    assert capped_test.main(['--memory', '4G', '--python', '/path with spaces/python']) == 7
    assert called == [(['/usr/bin/systemd-run', '--user', '--scope', '--collect', '--quiet',
                       '--property=MemoryMax=4G', '--', 'task', 'test', 'PYTHON=/path with spaces/python'], {'check': False})]


def test_missing_systemd_skips_without_starting_uncapped_tests(monkeypatch, capsys):
    monkeypatch.setattr(capped_test.shutil, 'which', lambda name: None)
    monkeypatch.setattr(capped_test.subprocess, 'run', lambda *args, **kw: (_ for _ in ()).throw(AssertionError('must not run')))
    assert capped_test.main(['--memory', '4G']) == 0
    assert 'Skipping capped tests' in capsys.readouterr().err
