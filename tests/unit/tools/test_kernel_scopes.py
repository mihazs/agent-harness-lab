"""Every collected kernel canary gets a bounded scope and failures stop the task."""
from types import SimpleNamespace

import pytest

from scripts import run_kernel_scopes

PREFIX = 'tests/integration/sandbox/test_sandbox_kernel.py::'
COLLECTION = '\n'.join([
    PREFIX + 'TestNamespaceKernel::test_one',
    PREFIX + 'TestRegressionDraftKernel::test_two[a]',
    PREFIX + 'TestRegressionDraftKernel::test_two[b]',
    PREFIX + 'TestRegressionDraftKernel::test_three',
    '4 tests collected in 0.01s',
])


def test_collection_groups_classes_and_splits_only_the_slow_class(tmp_path):
    path = tmp_path / 'test_sandbox_kernel.py'
    assert run_kernel_scopes.selections(COLLECTION, path) == [
        f'{path}::TestNamespaceKernel', f'{path}::TestRegressionDraftKernel::test_two',
        f'{path}::TestRegressionDraftKernel::test_three',
    ]


@pytest.mark.parametrize('output', [COLLECTION.replace('4 tests collected', '5 tests collected'),
                                    COLLECTION.replace('test_one', 'not_a_test'), COLLECTION.replace('4 tests collected', '0 tests collected'),
                                    COLLECTION.replace('test_three', 'test_two[a]')])
def test_incomplete_or_unsupported_collection_is_refused(output, tmp_path):
    with pytest.raises(ValueError):
        run_kernel_scopes.selections(output, tmp_path / 'test_sandbox_kernel.py')


def test_unfiltered_run_uses_bounded_scopes_and_stops_on_failure(monkeypatch, tmp_path):
    commands = []
    def fake(command, **kwargs):
        commands.append(command)
        if '--collect-only' in command:
            return SimpleNamespace(returncode=0, stdout=COLLECTION, stderr='')
        return SimpleNamespace(returncode=143 if len(commands) == 3 else 0)
    monkeypatch.setattr(run_kernel_scopes.subprocess, 'run', fake)
    assert run_kernel_scopes.run(tmp_path, '/python') == 143
    assert len(commands) == 3
    for command in commands:
        assert '--property=RuntimeMaxSec=300' in command and '--property=CPUQuota=40%' in command
        assert '--property=MemoryMax=512M' in command and '--property=TasksMax=64' in command
        assert '--expand-environment=no' in command
    assert commands[0][-2:] == ['--collect-only', '-q']
    assert all(command[-1] == '-v' for command in commands[1:])
    assert any('TestNamespaceKernel' in value for value in commands[1])
    assert any('TestRegressionDraftKernel::test_two' in value for value in commands[2])


def test_filtered_run_keeps_one_scope_and_preserves_filter(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(run_kernel_scopes.subprocess, 'run', lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0))
    assert run_kernel_scopes.run(tmp_path, '/python', 'trajectory and not timeout') == 0
    assert len(commands) == 1 and commands[0][-3:] == ['-k', 'trajectory and not timeout', '-v']


def test_failed_collection_runs_no_scopes(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(run_kernel_scopes.subprocess, 'run', lambda command, **kwargs: commands.append(command) or
                        SimpleNamespace(returncode=5, stdout='', stderr='collection failed'))
    assert run_kernel_scopes.run(tmp_path, '/python') == 5
    assert len(commands) == 1 and '--collect-only' in commands[0]
