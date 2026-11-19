"""Opt-in checks over actual Codex tool calls, without retaining their arguments."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from agent_harness_lab.diagnostics.adherence_native import Skipped, _Codex, read_session
from agent_harness_lab.diagnostics.instruction_adherence import _asked, _question, _user_items, analyze, load_sessions
from agent_harness_lab.telemetry import codex_rollout
from agent_harness_lab.telemetry.session_signals import summarize
from agent_harness_lab.telemetry.schema import message_fingerprint
from agent_harness_lab.sources.schema import digest

WORKTREES = Path.home()/'dev/.worktrees'
CODE_ROOT = Path(__file__).resolve().parents[3]
MAX_SESSION_FILES = 400


def selected_project(meta):
    projects = [Path(item).resolve() for item in json.loads(os.environ.get('REAL_CODEX_PROJECTS', '[]'))]
    project = meta.get('cwd') if isinstance(meta, dict) else None
    return not projects or isinstance(project, str) and any(Path(project) == base or base in Path(project).parents for base in projects)


def check_cli_in_worktree(path, project):
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', project]
    repository = Path(subprocess.check_output([*git, 'rev-parse', '--show-toplevel'], text=True).strip())
    before = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-reply-real-', dir=WORKTREES) as folder:
        root, checkout = Path(folder), Path(folder)/'checkout'
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(checkout), 'HEAD'], check=True)
        try:
            checkout_git = ['git', '-C', str(checkout), 'status', '--porcelain', '--untracked-files=no']
            checkout_before = subprocess.check_output(checkout_git)
            report = root/'report.json'
            env = dict(os.environ, PYTHONPATH=str(CODE_ROOT), PYTHONDONTWRITEBYTECODE='1')
            command = ['unshare', '--user', '--map-root-user', '--mount', 'bash', '-c',
                'set -eu; mount --make-rprivate /; mount --bind "$1" "$2"; exec "${@:3}"',
                '_', str(checkout), str(repository), sys.executable, '-m', 'agent_harness_lab',
                'instruction-adherence', str(path), '--native', 'codex', '--project', project, '--output', str(report)]
            result = subprocess.run(command, env=env, cwd=root, capture_output=True, timeout=60)
            assert result.returncode == 0
            data = json.loads(report.read_text())
            assert data['status'] == 'completed' and data['redaction'] == 'digest_only' and data['publication_allowed'] is False
            assert subprocess.check_output(checkout_git) == checkout_before
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == before


@pytest.mark.parametrize('name', ['request_user_input', 'request_user_input_async'])
def test_real_codex_question_call_is_recognized(name):
    root = os.environ.get('REAL_CODEX_SESSION_ROOT')
    if not root:
        pytest.skip('set REAL_CODEX_SESSION_ROOT to a native session directory')
    for index, path in enumerate(Path(root).rglob('*.jsonl')):
        if index >= MAX_SESSION_FILES: break
        if path.stat().st_size > 8 * 1024 * 1024: continue
        with path.open() as stream:
            try: meta = json.loads(next(stream))['payload']
            except (ValueError, StopIteration, KeyError, TypeError): continue
            if not selected_project(meta): continue
            for line in stream:
                if name not in line: continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get('payload', {})
                if (row.get('type') != 'response_item' or not isinstance(payload, dict)
                        or payload.get('type') not in ('function_call', 'custom_tool_call') or payload.get('name') != name):
                    continue
                reader = _Codex(Path.cwd(), ())
                reader.tool(payload)
                assert len(reader.turns) == 1 and _question(reader.turns[0])
                assert not _asked(reader.turns, 0, len(reader.turns))
                return
    pytest.skip('no matching real tool call in the bounded session set')


def test_real_codex_input_reply_reaches_adherence():
    root = os.environ.get('REAL_CODEX_SESSION_ROOT')
    if not root: pytest.skip('set REAL_CODEX_SESSION_ROOT to a native session directory')
    projects = json.loads(os.environ.get('REAL_CODEX_PROJECTS', '[]'))
    start, end = '<send_user_message_question_reply>', '</send_user_message_question_reply>'
    for index, path in enumerate(Path(root).rglob('*.jsonl')):
        if index >= MAX_SESSION_FILES: break
        if path.stat().st_size > 8 * 1024 * 1024: continue
        answers, reply_ids = [], []
        with path.open() as stream:
            try: meta = json.loads(next(stream))['payload']; project = meta['cwd']
            except (ValueError, StopIteration, KeyError, TypeError): continue
            if not selected_project(meta): continue
            for line_number, line in enumerate(stream, 1):
                if start not in line: continue
                try: row = json.loads(line)
                except ValueError: continue
                payload = row.get('payload', {})
                if not isinstance(payload, dict) or payload.get('type') != 'message' or payload.get('role') != 'user': continue
                for item in payload.get('content', []):
                    text = item.get('text', '') if isinstance(item, dict) else ''
                    if not isinstance(text, str) or not text.startswith(start) or not text.endswith(end): continue
                    try: values = json.loads(text[len(start):-len(end)])
                    except ValueError: continue
                    if isinstance(values, list):
                        answers.extend(x['answer'] for x in values if isinstance(x, dict) and isinstance(x.get('answer'), str))
                        ordinal = row.get('ordinal')
                        reply_ids.append(digest((meta['id'], ('ordinal', ordinal) if ordinal is not None else ('line', line_number))))
        if not answers: continue
        try: session = read_session(path, 'codex', project)
        except Skipped: continue
        normalized = load_sessions({'profile': 'ahl-instruction-sessions-v1', 'sessions': [session]})[0]
        turns = normalized['turns']
        for position, turn in enumerate(turns):
            if turn.get('tool') not in ('request_user_input', 'request_user_input_async'): continue
            for reply in range(position + 1, len(turns)):
                later = turns[reply]
                if later.get('role') != 'user' or not later.get('reply_to_question') or later.get('text') not in answers: continue
                between = [item for item in turns[position:reply] if item['role'] != 'user']
                if _question(between[-1]): continue
                assert _asked(turns, position, reply + 1) is None
                assert all(item['restated'] != reply and reply not in item['signals'].values() for item in _user_items(normalized))
                report = analyze([normalized], [])
                assert report['status'] == 'completed' and report['redaction'] == 'digest_only'
                assert report['sessions']['partial'] == 1 and report['publication_allowed'] is False
                try: events, _, _ = codex_rollout.read(path, project)
                except (OSError, ValueError): continue
                assert any(event['event_id'] in reply_ids and event['activity'] == 'user_message'
                           and event.get('message_fingerprint') == message_fingerprint(later['text'], event['case_id'])
                           for event in events)
                if projects: check_cli_in_worktree(path, project)
                return
    pytest.skip('no usable real request and reply in the bounded session set')


def test_real_async_input_tool_is_preserved_in_telemetry():
    root = os.environ.get('REAL_CODEX_SESSION_ROOT')
    if not root: pytest.skip('set REAL_CODEX_SESSION_ROOT to a native session directory')
    for index, path in enumerate(Path(root).rglob('*.jsonl')):
        if index >= MAX_SESSION_FILES: break
        if path.stat().st_size > 8 * 1024 * 1024: continue
        with path.open() as stream:
            try: meta = json.loads(next(stream))['payload']
            except (ValueError, StopIteration, KeyError, TypeError): continue
            if not selected_project(meta): continue
            if meta.get('cli_version') != codex_rollout.PINNED_VERSION or not isinstance(meta.get('cwd'), str): continue
            found = False
            for line in stream:
                if 'request_user_input_async' not in line: continue
                try: row = json.loads(line)
                except ValueError: continue
                payload = row.get('payload', {})
                if (row.get('type') == 'response_item' and isinstance(payload, dict)
                        and payload.get('type') in ('function_call', 'custom_tool_call')
                        and payload.get('name') == 'request_user_input_async' and payload.get('namespace') in (None, 'functions')):
                    found = True
                    break
            if not found: continue
        try: rows, _, _ = codex_rollout.read(path, meta['cwd'], include_session_signals=True)
        except (OSError, ValueError): continue
        calls = [row for row in rows if row.get('activity') == 'tool_call' and row.get('tool_name') == 'request_user_input_async']
        assert calls and all(type(row.get('signal_input_missing')) is bool and 'arguments' not in row for row in calls)
        assert all(row['unknown_input'] for row in summarize(rows, incomplete=True))
        return
    pytest.skip('no compatible real capture with an asynchronous input call')
