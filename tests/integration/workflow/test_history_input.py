"""Read-only historical-session evidence for improve and its skill route; synthetic stores only."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from agent_harness_lab import cli, entry_parity
from agent_harness_lab.exit_guidance import REMEDIATION

DAY = 86400


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as error:
            code = error.code
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Synthetic project, Codex and Claude session stores, a stub runtime and an isolated state directory."""
    project = tmp_path/'project'; project.mkdir()
    (project/'AGENTS.md').write_text('synthetic')
    agent = tmp_path/'agent'; agent.write_text('#!/bin/sh\nexit 1\n'); agent.chmod(0o755)
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('AHL_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'unused-xdg'))
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    for marker in ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED'):
        monkeypatch.delenv(marker, raising=False)

    class Store:
        root, proj, runtime = tmp_path, project, agent

        def session(self, name, age_days, cwd=None):
            path = tmp_path/'codex/sessions'/f'{name}.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            cwd = str(project if cwd is None else cwd)
            rows = [dict(type='session_meta', cwd=cwd, payload=dict(id=name, cwd=cwd)),
                    dict(type='response_item', payload=dict(type='function_call', name='read', call_id='c1')),
                    dict(type='response_item', payload=dict(type='function_call_output', call_id='c1', output=dict(exit_code=0)))]
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            stamp = os.stat(path).st_mtime - age_days*DAY
            os.utime(path, (stamp, stamp))

        def digest(self):
            entries = []
            for folder in ('codex', 'claude'):
                for path in sorted((tmp_path/folder).rglob('*')):
                    if path.is_file():
                        entries.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns))
            return entries

        def improve(self, *args, prefix=('improve',)):
            code, out, err = run([*prefix, str(project), '--agent', 'codex', '--codex', str(agent), '--timeout', '30', *args])
            return code, json.loads(out), err
    return Store()


def test_state_home_selects_the_run_directory(store):
    store.session('s1', 0)
    code, out, _ = run(['analyze', str(store.proj)])
    output = Path(json.loads(out)['output'])
    assert code == 0
    assert output.is_relative_to(store.root/'state'), output
    assert not (store.root/'unused-xdg').exists() and not (store.root/'home/.local/state').exists()


@pytest.mark.parametrize('prefix', [('improve',), ('skills', 'invoke')])
def test_history_reports_captured_and_unknown_without_touching_sources(store, prefix):
    store.session('recent', 1); store.session('old', 40); store.session('other-project', 1, cwd=store.root/'elsewhere')
    before = store.digest()
    code, result, _ = store.improve('--history', '--since', 'first', prefix=prefix)
    history = json.loads((Path(result['output'])/'analysis.json').read_text())['history']
    assert history['sessions_captured'] == 2 and history['read_only'] and history['scope'] == 'project'
    assert 'capture completeness' in history['unknown'] and 'recurrence' in ' '.join(history['unknown'])
    assert result['history'] == history
    assert store.digest() == before


def test_since_date_excludes_older_sessions(store):
    store.session('recent', 1); store.session('old', 40)
    code, result, _ = store.improve('--history', '--since', '2000-01-01')
    assert result['history']['sessions_captured'] == 2
    cutoff = datetime.fromtimestamp(os.stat(store.root/'codex/sessions/recent.jsonl').st_mtime - 5*DAY, timezone.utc).isoformat()
    code, result, _ = store.improve('--history', '--since', cutoff)
    assert (result['history']['sessions_captured'], result['history']['since']) == (1, cutoff)


@pytest.mark.parametrize('prefix', [('improve',), ('skills', 'invoke')])
def test_unsupported_or_empty_requests_block_with_typed_reason_and_parity(store, prefix):
    store.session('old', 40)
    cases = [(('--history', '--source', 'ai-memory'), 'history_source_ai_memory_unsupported'),
             (('--history', '--since', '2999-01-01'), 'history_no_matching_sessions')]
    for args, reason in cases:
        before = store.digest()
        code, result, _ = store.improve(*args, prefix=prefix)
        assert (code, result['status'], result['reason']) == (3, 'blocked', reason)
        assert result['remediation'] == REMEDIATION[reason]
        assert store.digest() == before


@pytest.mark.parametrize('prefix', [('improve',), ('skills', 'invoke')])
def test_source_narrows_to_one_runtime_and_reports_a_typed_reason_when_it_has_no_captures(store, prefix):
    store.session('codex-one', 1)
    code, result, _ = store.improve('--history', '--source', 'codex', prefix=prefix)
    assert result['history']['sessions_captured'] == 1 and result['history']['source'] == 'codex'
    before = store.digest()
    code, result, _ = store.improve('--history', '--source', 'claude', prefix=prefix)
    assert (code, result['status'], result['reason']) == (3, 'blocked', 'history_runtime_has_no_captures')
    assert result['remediation'] == REMEDIATION['history_runtime_has_no_captures']
    assert result['history']['source'] == 'claude' and result['history']['sessions_captured'] == 0
    assert store.digest() == before


def test_receipts_match_across_entry_routes(store):
    store.session('recent', 1)
    results = []
    for prefix in (('improve',), ('skills', 'invoke')):
        code, result, _ = store.improve('--history', '--source', 'ai-memory', prefix=prefix)
        result.pop('output'); results.append((code, result))
    assert entry_parity.document_differences('workflow.json', results[0][1], results[1][1], '<cli>', '<skill>') == []  # timings may differ
    assert results[0][0] == results[1][0]


@pytest.mark.parametrize('args', [('--since', 'first'), ('--source', 'native'), ('--history', '--since', 'not-a-date'),
                                  ('--history', '--events', 'x.jsonl')])
def test_malformed_history_requests_are_explicit_input_errors(store, args):
    code, out, err = run(['improve', str(store.proj), *args])
    assert (code, out) == (2, '')
    assert '--since' in err or '--history' in err
