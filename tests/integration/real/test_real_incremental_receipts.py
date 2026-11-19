"""Replay a real first-run candidate through incremental tracking without a model request."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from agent_harness_lab import incremental, workflow


def _safe_file(root, relative):
    path = root / relative
    if not path.is_relative_to(root) or '.git' in path.relative_to(root).parts:
        raise ValueError('unsafe recorded harness path')
    if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(root)):
        raise ValueError('recorded harness path crosses a symlink')
    return path


def test_real_first_candidate_is_recognized_on_later_runs():
    run_name = os.environ.get('REAL_IMPROVEMENT_RUN')
    project_name = os.environ.get('REAL_IMPROVEMENT_PROJECT')
    if not run_name or not project_name:
        pytest.skip('set REAL_IMPROVEMENT_RUN and REAL_IMPROVEMENT_PROJECT')
    run, project = Path(run_name).resolve(strict=True), Path(project_name).resolve(strict=True)
    invocation = json.loads((run/'invocation.json').read_text())
    if Path(invocation['project']).resolve(strict=True) != project:
        raise ValueError('recorded run belongs to another project')
    record = incremental._record(run/'harness-states.json')
    if not record['candidate']:
        pytest.skip('selected real run did not freeze a candidate')
    baseline, candidate = record['baseline'], record['candidate']['states']
    paths = baseline.keys() | candidate.keys()
    if not any(baseline.get(path) != candidate.get(path) for path in paths):
        pytest.skip('selected real candidate did not change a harness file')
    before = run/'preparation/baseline'
    after = run/f"candidate-{record['candidate']['attempt']}"
    for root, states in ((before, baseline), (after, candidate)):
        for name, expected in states.items():
            actual = workflow.file_state(_safe_file(root, name))
            if actual is None or list(actual) != expected:
                pytest.skip('saved real snapshot is incomplete or changed')
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
    original_status = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-incremental-real-', dir=Path.home()/'dev/.worktrees') as folder:
        root = Path(folder); checkout = root/'checkout'
        subprocess.run([*git, 'worktree', 'add', '--quiet', '--detach', str(checkout), 'HEAD'], check=True)
        saved_state = os.environ.get('AHL_STATE_HOME')
        try:
            def materialize(source, states):
                for name in paths:
                    destination = _safe_file(checkout, name)
                    if name not in states:
                        if destination.exists(): destination.unlink()
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(_safe_file(source, name), destination)
                    os.chmod(destination, states[name][1])
            def observed():
                return {name: value for name in paths if (value := workflow.file_state(_safe_file(checkout, name))) is not None}
            args = SimpleNamespace(project=checkout)
            receipt = (run/'workflow.json').read_bytes()
            for disposition, apply in (('ignored', False), ('applied', True)):
                materialize(before, baseline)
                os.environ['AHL_STATE_HOME'] = str(root/('state-'+disposition))
                store = workflow.state_root()/'runs'/workflow.identity(checkout)
                first_out = store/'run-first'; first_out.mkdir(parents=True)
                first = incremental.begin(args, first_out, observed(), record['scope'])
                first_result = {}; incremental.attach(first_result, first, observed())
                assert 'incremental' not in first_result
                proposed = {name: tuple(value) for name, value in candidate.items()}
                incremental.froze(first, record['candidate']['attempt'], proposed)
                (first_out/'workflow.json').write_bytes(receipt)
                if apply: materialize(after, candidate)
                second_out = store/'run-second'; second_out.mkdir()
                second = incremental.begin(args, second_out, observed(), record['scope'])
                result = {}; incremental.attach(result, second, observed())
                assert result['incremental']['previous']['run'] == first_out.name
                assert result['incremental']['disposition'] == disposition
                assert result['incremental']['stages']['trials']['action'] == 'rerun'
        finally:
            if saved_state is None: os.environ.pop('AHL_STATE_HOME', None)
            else: os.environ['AHL_STATE_HOME'] = saved_state
            subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == original_status
