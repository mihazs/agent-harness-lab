"""A linked worktree has its own path: say that history is not found for it, and read the main checkout's only on request."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

from agent_harness_lab import cli
from agent_harness_lab.exit_guidance import REMEDIATION
from agent_harness_lab.telemetry.claude_native import VERSION

ENVIRONMENT = {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
NOT_FOUND, DIFFERS = 'history_not_found_for_worktree', 'source_project_path_differs'


def git(cwd, *arguments):
    subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', '-c', 'commit.gpgsign=false', *arguments],
        cwd=cwd, check=True, capture_output=True, env={**os.environ, **ENVIRONMENT})


def repository(path):
    path.mkdir()
    git(path, 'init', '-q'); git(path, 'commit', '-q', '--allow-empty', '-m', 'synthetic')
    (path/'AGENTS.md').write_text('synthetic')
    return path.resolve()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as error:
            code = error.code
    return code, out.getvalue(), err.getvalue()


class Store:
    """A synthetic repository with one linked worktree and Codex sessions recorded only under the main checkout's path."""
    def __init__(self, root):
        self.root = root
        self.main = repository(root/'main')
        self.worktree = (root/'linked').resolve()
        git(self.main, 'worktree', 'add', '-q', '--detach', str(self.worktree))
        self.agent = root/'agent'; self.agent.write_text('#!/bin/sh\nexit 1\n'); self.agent.chmod(0o755)

    def session(self, name='recorded', cwd=None):
        path = self.root/'codex/sessions'/f'{name}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        cwd = str(self.main if cwd is None else cwd)
        rows = [dict(type='session_meta', cwd=cwd, payload=dict(id=name, cwd=cwd)),
                dict(type='response_item', payload=dict(type='function_call', name='read', call_id='c1')),
                dict(type='response_item', payload=dict(type='function_call_output', call_id='c1', output=dict(exit_code=0)))]
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def digest(self):
        return [(str(path), hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for path in sorted((self.root/'codex').rglob('*')) if path.is_file()]

    def analyze(self, project, *options):
        code, out, err = run(['analyze', str(project), *options])
        return code, (json.loads(out) if code == 0 else None), err

    def improve(self, project, *options):
        code, out, err = run(['improve', str(project), '--agent', 'codex', '--codex', str(self.agent), '--timeout', '30', *options])
        return code, (json.loads(out) if out.strip() else None), err


@pytest.fixture
def store(tmp_path, monkeypatch):
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('AHL_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'unused-xdg'))
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    for marker in ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED'):
        monkeypatch.delenv(marker, raising=False)
    return Store(tmp_path)


def test_analyze_of_a_linked_worktree_reports_history_not_found_instead_of_an_empty_success(store):
    store.session()
    code, result, err = store.analyze(store.worktree)
    assert code == 0 and result['analysis']['events'] == 0
    assert result['capture_reasons'] == [dict(reason=NOT_FOUND, captures=0)]
    assert json.loads((Path(result['output'])/'workflow.json').read_text())['capture_reasons'] == result['capture_reasons']
    assert err.strip().endswith('Next: ' + REMEDIATION[NOT_FOUND])
    assert '--history-from' in REMEDIATION[NOT_FOUND]
    assert str(store.main) not in json.dumps(result)


@pytest.mark.parametrize('kind', ['main-checkout', 'plain-directory', 'malformed-git-pointer', 'unreadable-common-dir'])
def test_analyze_gives_no_worktree_reason_unless_the_project_is_a_readable_linked_worktree(store, tmp_path, kind):
    if kind == 'main-checkout': project = store.main
    elif kind == 'plain-directory': project = tmp_path/'plain'; project.mkdir()
    elif kind == 'malformed-git-pointer': project = tmp_path/'malformed'; project.mkdir(); (project/'.git').write_text('not a pointer\n')
    else:
        # The administrative directory of the registration is gone: whether the project is a worktree is unknown.
        project = tmp_path/'broken'; project.mkdir()
        (project/'.git').write_text('gitdir: '+str(tmp_path/'missing-admin')+'\n')
    code, result, _ = store.analyze(project)
    assert code == 0 and result['analysis']['events'] == 0
    assert result.get('capture_reasons', []) == []


def test_a_worktree_with_its_own_recorded_session_is_not_reported_as_missing_history(store):
    store.session(cwd=store.worktree)
    code, result, _ = store.analyze(store.worktree)
    assert result['analysis']['events'] > 0 and result.get('capture_reasons', []) == []


def test_history_from_reads_the_main_checkouts_sessions_read_only_and_labels_the_receipt(store):
    store.session()
    before = store.digest()
    code, result, _ = store.analyze(store.worktree, '--history-from', str(store.main))
    assert code == 0 and result['analysis']['events'] > 0
    assert result['capture_reasons'] == [dict(reason=DIFFERS, captures=1)]
    assert store.digest() == before
    assert str(store.main) not in json.dumps(result['capture_reasons'])


def test_history_from_without_sessions_is_labelled_and_never_a_success_claim(store):
    code, result, _ = store.analyze(store.worktree, '--history-from', str(store.main))
    assert code == 0 and result['analysis']['events'] == 0
    assert result['capture_reasons'] == [dict(reason=DIFFERS, captures=0)]
    assert result['analysis']['evidence_state'] == 'empty'


def test_history_from_the_project_itself_is_not_labelled_as_a_different_path(store):
    store.session()
    code, result, _ = store.analyze(store.main, '--history-from', str(store.main))
    assert result['analysis']['events'] > 0 and result.get('capture_reasons', []) == []


@pytest.mark.parametrize('kind', ['unrelated-repository', 'plain-directory', 'missing-directory'])
def test_history_from_refuses_a_directory_that_is_not_the_same_repository(store, tmp_path, kind):
    store.session(cwd=tmp_path/'other')
    if kind == 'unrelated-repository': other = repository(tmp_path/'other')
    elif kind == 'plain-directory': other = tmp_path/'other'; other.mkdir()
    else: other = tmp_path/'other'
    before = store.digest()
    code, out, err = run(['analyze', str(store.worktree), '--history-from', str(other)])
    assert code == 2 and out == '' and 'same repository' in err
    assert store.digest() == before


@pytest.mark.parametrize('capture', [('--events', 'capture.jsonl'), ('--source-root', 'native')], ids=['events', 'source-root'])
def test_history_from_cannot_be_combined_with_an_explicit_capture_input(store, tmp_path, capture):
    option, name = capture
    (tmp_path/name).mkdir() if name == 'native' else (tmp_path/name).write_text('')
    pin = ['--format', 'claude', '--source-version', VERSION] if option == '--source-root' else []
    code, out, err = run(['analyze', str(store.worktree), '--history-from', str(store.main), option, str(tmp_path/name), *pin])
    assert code == 2 and out == '' and 'drop --events/--source-root, or drop --history-from' in err


def test_improve_history_of_a_linked_worktree_stops_with_the_worktree_reason_before_any_model_call(store):
    store.session()
    before = store.digest()
    code, result, _ = store.improve(store.worktree, '--history')
    assert (code, result['status'], result['reason']) == (3, 'blocked', NOT_FOUND)
    assert result['remediation'] == REMEDIATION[NOT_FOUND]
    assert result['history']['capture_reasons'] == [dict(reason=NOT_FOUND, captures=0)]
    assert store.digest() == before


def test_improve_history_with_history_from_reads_the_main_checkouts_sessions_and_labels_the_receipt(store):
    store.session()
    before = store.digest()
    code, result, _ = store.improve(store.worktree, '--history', '--history-from', str(store.main))
    assert result['history']['sessions_captured'] == 1 and result['history']['events'] > 0
    assert result['history']['capture_reasons'] == [dict(reason=DIFFERS, captures=1)]
    assert store.digest() == before


def test_improve_without_history_of_a_linked_worktree_keeps_the_reason_in_the_receipt_and_the_next_step(store):
    store.session()
    code, result, err = store.improve(store.worktree)
    assert result['capture_reasons'] == [dict(reason=NOT_FOUND, captures=0)]
    assert REMEDIATION[NOT_FOUND] in err
