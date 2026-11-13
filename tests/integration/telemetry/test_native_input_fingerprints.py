"""Content-free tool-call input fingerprints on the pinned native adapters (#666), synthetic captures only."""
import json
import re

import pytest

from agent_harness_lab.diagnostics import brief
from agent_harness_lab.telemetry.schema import input_fingerprint
from tests.integration.telemetry.test_claude_native import ClaudeNativeTests
from tests.integration.telemetry.test_codex_rollout import CodexRolloutTests

SECRET = 'SYNTHETIC_SECRET'
HEX16 = re.compile(r'[0-9a-f]{16}\Z')


def calls(records):
    return [r for r in records if r['activity'] == 'tool_call']


def signal(records, name):
    scan = brief.Scan()
    for record in records:
        scan.observe(record)
    return next(row for row in brief._signals(scan.result()['signals']) if row['id'] == name)


def test_input_fingerprint_is_canonical_case_salted_and_content_free():
    first = input_fingerprint(dict(command='ls', cwd='x'), 'case-a')
    assert HEX16.match(first)
    assert first == input_fingerprint(dict(cwd='x', command='ls'), 'case-a') == input_fingerprint('{ "cwd": "x",\n"command": "ls" }', 'case-a')
    assert first != input_fingerprint(dict(command='ls', cwd='y'), 'case-a') != input_fingerprint(dict(command='ls', cwd='x'), 'case-b')
    assert input_fingerprint('not json at all', 'case-a') == input_fingerprint('not json at all', 'case-a') != input_fingerprint('not json', 'case-a')
    assert input_fingerprint(dict(value=float('nan')), 'case-a') is None
    assert input_fingerprint(object(), 'case-a') is None


class TestClaudeInputFingerprints(ClaudeNativeTests):
    @pytest.fixture(autouse=True)
    def _capture(self, tmp_path):
        self.setUp(tmp_path)

    def retry(self, command=SECRET):
        self.rows.insert(3, self.row('assistant', 'a3', 'a1', dict(role='assistant', id='response-3', model=SECRET, usage=dict(input_tokens=1, output_tokens=1),
            stop_reason='tool_use', content=[dict(type='tool_use', id='call-3', name='Bash', input=dict(command=command))])))
        self.write()

    def test_tool_calls_carry_a_fingerprint_and_no_input_text(self):
        records, _, _ = self.capture()
        shell, read = calls(records)
        assert HEX16.match(shell['input_fingerprint']) and HEX16.match(read['input_fingerprint'])
        assert shell['input_fingerprint'] != read['input_fingerprint']
        assert not any(SECRET in json.dumps(r) for r in records)
        assert not any('input_fingerprint' in r for r in records if r['activity'] != 'tool_call')

    def test_a_repeated_identical_call_is_a_same_call_retry_and_a_changed_input_is_not(self):
        records, _, _ = self.capture()
        assert signal(records, 'same_call_retries')['status'] == 'not_observed_capture_unverified'
        self.retry()
        row = signal(self.capture()[0], 'same_call_retries')
        assert (row['status'], row['count']) == ('observed_pattern', 1) and row['competing_explanations']
        self.retry('SYNTHETIC_OTHER_INPUT')
        assert signal(self.capture()[0], 'same_call_retries')['count'] == 0

    def test_a_missing_or_unusable_input_leaves_the_fingerprint_absent(self):
        del self.rows[2]['message']['content'][0]['input']
        self.rows[3]['message']['content'][0]['input'] = 'free text is still hashed, never stored'
        self.write()
        shell, read = calls(self.capture()[0])
        assert 'input_fingerprint' not in shell and HEX16.match(read['input_fingerprint'])

    def test_duplicate_uuids_stay_body_insensitive(self):
        self.rows.append(json.loads(json.dumps(self.rows[2])))
        self.rows[-1]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'
        self.write()
        records, summary, _ = self.capture()
        assert summary['duplicates_skipped'] == 1 and len(calls(records)) == 2


class TestCodexInputFingerprints(CodexRolloutTests):
    @pytest.fixture(autouse=True)
    def _rollout(self, tmp_path):
        self.setUp(tmp_path)

    def call(self, ordinal, kind, **fields):
        return self.row('response_item', dict(type=kind, call_id=f'call-{ordinal}', **fields), ordinal)

    def test_each_pinned_call_item_hashes_its_own_input_field(self):
        self.rows[2]['payload']['arguments'] = json.dumps(dict(cmd='ls', workdir='x'))
        self.rows[-1:-1] = [self.call(6, 'custom_tool_call', name='apply_patch', input='*** Begin Patch'),
                            self.call(7, 'local_shell_call', action=dict(type='exec', command=['ls'])),
                            self.call(8, 'function_call', name='exec_command', arguments=json.dumps(dict(workdir='x', cmd='ls'))),
                            self.call(9, 'function_call', name='exec_command')]
        for number, row in enumerate(self.rows):
            row['ordinal'] = number
        self.write()
        first, patch, shell, reordered, bare = calls(self.capture()[0])
        assert all(HEX16.match(r['input_fingerprint']) for r in (first, patch, shell, reordered))
        assert first['input_fingerprint'] == reordered['input_fingerprint'] != patch['input_fingerprint'] != shell['input_fingerprint']
        assert 'input_fingerprint' not in bare

    def test_input_text_is_never_stored_and_a_repeat_is_a_same_call_retry(self):
        records, _, _ = self.capture()
        assert not any(SECRET in json.dumps(r) for r in records)
        assert signal(records, 'same_call_retries')['status'] == 'not_observed_capture_unverified'
        self.rows.insert(-1, self.call(6, 'function_call', name='exec_command', arguments=SECRET))
        for number, row in enumerate(self.rows):
            row['ordinal'] = number
        self.write()
        assert signal(self.capture()[0], 'same_call_retries')['count'] == 1
