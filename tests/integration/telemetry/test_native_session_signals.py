"""Synthetic native captures emit only content-free session signals."""
import json

import pytest

from agent_harness_lab.telemetry.session_signals_cli import main
from agent_harness_lab.evidence import collect
from agent_harness_lab.telemetry import claude_native, codex_rollout
from agent_harness_lab.telemetry.schema import BODY_DERIVED
from tests.integration.telemetry.test_claude_native import ClaudeNativeTests
from tests.integration.telemetry.test_codex_rollout import CodexRolloutTests, VERSION as CODEX_VERSION


def test_claude_projects_a_private_path_and_command(tmp_path):
    capture = ClaudeNativeTests(); capture.setUp(tmp_path)
    capture.rows[2]['message']['content'][0]['input'] = {'command': 'git diff -- /private/secret.py'}
    capture.rows[3]['message']['content'][0]['input'] = {'file_path': '/private/secret.py'}
    capture.write()
    rows, _, _ = claude_native.read(capture.path, capture.project, include_session_signals=True)
    calls = [row for row in rows if row['activity'] == 'tool_call']
    assert [row.get('command_family') for row in calls] == ['git-diff', None]
    assert [row.get('file_extensions') for row in calls] == [None, ['py']]
    assert '/private/secret' not in json.dumps(rows)


def test_codex_projects_a_private_command(tmp_path):
    capture = CodexRolloutTests(); capture.setUp(tmp_path)
    capture.rows[2]['payload']['arguments'] = json.dumps({'cmd': 'pytest /private/secret.py'})
    capture.write()
    rows, _, _ = codex_rollout.read(capture.path, capture.project, include_session_signals=True)
    call = next(row for row in rows if row['activity'] == 'tool_call')
    assert call['command_family'] == 'pytest'
    assert '/private/secret' not in json.dumps(rows)


def test_codex_projects_multiple_patch_extensions_without_paths(tmp_path):
    capture = CodexRolloutTests(); capture.setUp(tmp_path)
    capture.rows[2]['payload'].update(type='custom_tool_call', name='apply_patch',
        input='*** Begin Patch\n*** Update File: /private/one.py\n*** Add File: /private/two.md\n*** End Patch')
    capture.write()
    rows, _, _ = codex_rollout.read(capture.path, capture.project, include_session_signals=True)
    call = next(row for row in rows if row['activity'] == 'tool_call')
    assert call['file_extensions'] == ['py', 'md'] and '/private/' not in json.dumps(rows)


def test_missing_native_input_is_unknown_in_the_export(tmp_path):
    capture = CodexRolloutTests(); capture.setUp(tmp_path)
    capture.rows[2]['payload'].pop('arguments')
    capture.write()
    output = tmp_path / 'summary.json'
    assert main([str(capture.project), '--capture', str(capture.path), '--format', 'codex', '--output', str(output)]) == 0
    summary = json.loads(output.read_text())[0]
    assert summary['unknown_input'] is True and 'commands' not in summary and 'extensions' not in summary


def test_partial_capture_never_looks_complete(tmp_path):
    capture = CodexRolloutTests(); capture.setUp(tmp_path)
    capture.rows[2]['payload']['arguments'] = json.dumps({'cmd': 'pytest file.py'})
    capture.write()
    with capture.path.open('a') as stream:
        stream.write('{"type":')
    output = tmp_path / 'summary.json'
    assert main([str(capture.project), '--capture', str(capture.path), '--format', 'codex', '--output', str(output)]) == 0
    summary = json.loads(output.read_text())[0]
    assert summary['unknown_input'] is True and summary['commands'] == {'pytest': 1}


@pytest.mark.parametrize('kind', ['claude', 'codex'])
def test_private_body_signal_changes_do_not_change_metadata_fingerprint(kind, tmp_path):
    capture = ClaudeNativeTests() if kind == 'claude' else CodexRolloutTests()
    capture.setUp(tmp_path)
    def change(command):
        if kind == 'claude':
            capture.rows[2]['message']['content'][0]['input'] = {'command': command}
        else:
            capture.rows[2]['payload']['arguments'] = json.dumps({'cmd': command})
        capture.write()
    def fingerprint():
        if kind == 'claude': return capture.collect()['fingerprint']
        return collect(capture.project, events=capture.path, source_format='codex', source_version=CODEX_VERSION)['fingerprint']
    def projected():
        reader = claude_native if kind == 'claude' else codex_rollout
        rows, _, _ = reader.read(capture.path, capture.project, include_session_signals=True)
        return [{key: value for key, value in row.items() if key not in BODY_DERIVED} for row in rows]
    change('pytest /private/first.py'); before, projection = fingerprint(), projected()
    change('git diff /private/second.md')
    assert fingerprint() == before and projected() == projection


@pytest.mark.parametrize('kind', ['claude', 'codex'])
def test_export_is_private_and_does_not_replace_an_existing_file(kind, tmp_path):
    capture = ClaudeNativeTests() if kind == 'claude' else CodexRolloutTests()
    capture.setUp(tmp_path)
    if kind == 'claude':
        capture.rows[2]['message']['content'][0]['input'] = {'command': 'pytest /private/secret.py'}
    else:
        capture.rows[2]['payload']['arguments'] = json.dumps({'cmd': 'pytest /private/secret.py'})
    capture.write()
    output = tmp_path / 'summary.json'
    args = [str(capture.project), '--capture', str(capture.path), '--format', kind, '--output', str(output)]
    assert main(args) == 0
    text = output.read_text()
    assert '/private/secret' not in text and json.loads(text)[0]['commands'] == {'pytest': 1}
    assert output.stat().st_mode & 0o777 == 0o600
    assert main(args) == 2 and output.read_text() == text
