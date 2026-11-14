"""Content-free edit fingerprints on pinned native captures so edit-then-revert is computable (#766), synthetic captures only."""
import json

import pytest

from tests.integration.telemetry.test_claude_native import ClaudeNativeTests
from tests.integration.telemetry.test_codex_rollout import CodexRolloutTests
from tests.integration.telemetry.test_native_input_fingerprints import HEX16, calls, signal

SECRET = 'SYNTHETIC_SECRET'
PATH = '/synthetic/SYNTHETIC_SECRET_target.txt'
EDIT_FIELDS = ('edit_target_fingerprint', 'edit_before_fingerprint', 'edit_after_fingerprint')


def write(content, path=PATH):
    return 'Write', dict(file_path=path, content=content)


def swap(old, new, path=PATH):
    return 'Edit', dict(file_path=path, old_string=old, new_string=new, replace_all=False)


class TestClaudeEditFingerprints(ClaudeNativeTests):
    @pytest.fixture(autouse=True)
    def _capture(self, tmp_path):
        self.setUp(tmp_path)

    def session(self, *steps):
        self.rows = [self.row('user', 'u0', None, dict(role='user', content=SECRET))]
        for number, (tool, tool_input) in enumerate(steps, 1):
            self.rows.append(self.row('assistant', f'a{number}', f'a{number - 1}' if number > 1 else 'u0', dict(role='assistant', id=f'response-{number}',
                stop_reason='tool_use', content=[dict(type='tool_use', id=f'call-{number}', name=tool, input=tool_input)])))
        self.write()
        return self.capture()[0]

    def test_a_write_returning_to_an_earlier_content_is_an_edit_then_revert(self):
        row = signal(self.session(write('SYNTHETIC_ONE'), write('SYNTHETIC_TWO'), write('SYNTHETIC_ONE')), 'edit_then_revert')
        assert (row['status'], row.get('count')) == ('observed_pattern', 1)
        assert row['competing_explanations'] and row['basis']

    def test_the_reverse_of_an_edit_pair_on_the_same_target_is_an_edit_then_revert(self):
        row = signal(self.session(swap('SYNTHETIC_ONE', 'SYNTHETIC_TWO'), swap('SYNTHETIC_TWO', 'SYNTHETIC_ONE')), 'edit_then_revert')
        assert (row['status'], row.get('count')) == ('observed_pattern', 1)

    @pytest.mark.parametrize('steps', [
        [swap('SYNTHETIC_ONE', 'SYNTHETIC_TWO'), swap('SYNTHETIC_THREE', 'SYNTHETIC_FOUR')],
        [swap('SYNTHETIC_ONE', 'SYNTHETIC_TWO'), swap('SYNTHETIC_TWO', 'SYNTHETIC_ONE', '/synthetic/other.txt')],
        [write('SYNTHETIC_ONE'), write('SYNTHETIC_TWO', '/synthetic/other.txt'), write('SYNTHETIC_ONE')],
    ], ids=['unrelated-edit-pair', 'reverse-on-another-target', 'equal-write-around-another-target'])
    def test_an_unrelated_pair_is_a_zero_that_stays_unverified(self, steps):
        row = signal(self.session(*steps), 'edit_then_revert')
        assert (row['status'], row.get('count')) == ('not_observed_capture_unverified', 0)

    def test_records_carry_hashes_only_and_never_the_path_or_the_text(self):
        records = self.session(write('SYNTHETIC_ONE'), swap('SYNTHETIC_ONE', 'SYNTHETIC_TWO'), write('SYNTHETIC_THREE', '/synthetic/other.txt'))
        first, second, third = calls(records)
        assert set(EDIT_FIELDS) - {'edit_before_fingerprint'} == set(first) & set(EDIT_FIELDS) and set(EDIT_FIELDS) <= set(second)
        assert all(HEX16.match(call[name]) for call in (first, second, third) for name in EDIT_FIELDS if name in call)
        assert first['edit_target_fingerprint'] == second['edit_target_fingerprint'] != third['edit_target_fingerprint']
        assert 'SYNTHETIC_' not in json.dumps(records) and 'synthetic/' not in json.dumps(records)
        assert not any(set(EDIT_FIELDS) & set(r) for r in records if r['activity'] != 'tool_call')

    @pytest.mark.parametrize('step', [
        ('Write', dict(file_path=PATH)),
        ('Write', dict(file_path=PATH, content=3)),
        ('Write', dict(content='SYNTHETIC_ONE')),
        ('Write', 'free text SYNTHETIC_ONE'),
        swap('', 'SYNTHETIC_ONE'),
        swap('SYNTHETIC_ONE', 'SYNTHETIC_ONE'),
        ('MultiEdit', dict(file_path=PATH, edits=[dict(old_string='SYNTHETIC_ONE', new_string='SYNTHETIC_TWO')])),
        ('NotebookEdit', dict(notebook_path=PATH, new_source='SYNTHETIC_ONE')),
        ('Bash', dict(command='echo SYNTHETIC_ONE > target.txt')),
    ], ids=['write-no-content', 'write-non-text-content', 'write-no-path', 'write-text-input', 'edit-creates-file', 'edit-no-change',
            'multi-edit', 'notebook-edit', 'shell'])
    def test_a_call_whose_input_does_not_name_the_whole_state_keeps_the_signal_unknown(self, step):
        records = self.session(step, step)
        assert not any(set(EDIT_FIELDS) & set(call) for call in calls(records))
        row = signal(records, 'edit_then_revert')
        assert row['status'] == 'unknown' and row['missing_prerequisite']

    def test_edit_fingerprints_stay_out_of_duplicate_checks_and_the_metadata_fingerprint(self):
        self.session(write('SYNTHETIC_ONE'))
        before = self.collect()['fingerprint']
        self.rows[1]['message']['content'][0]['input']['content'] = 'SYNTHETIC_CHANGED_BODY'
        self.write()
        assert self.collect()['fingerprint'] == before
        self.rows.append(json.loads(json.dumps(self.rows[1])))
        self.rows[-1]['message']['content'][0]['input']['content'] = 'SYNTHETIC_OTHER_BODY'
        self.write()
        records, summary, _ = self.capture()
        assert summary['duplicates_skipped'] == 1 and len(calls(records)) == 1


class TestCodexEditFingerprints(CodexRolloutTests):
    @pytest.fixture(autouse=True)
    def _rollout(self, tmp_path):
        self.setUp(tmp_path)

    def test_patch_calls_carry_no_edit_fingerprint_and_keep_the_signal_unknown(self):
        for ordinal, patch in ((6, '*** Update File: a\n-SYNTHETIC_ONE\n+SYNTHETIC_TWO'), (7, '*** Update File: a\n-SYNTHETIC_TWO\n+SYNTHETIC_ONE')):
            self.rows.insert(-1, self.row('response_item', dict(type='custom_tool_call', name='apply_patch', call_id=f'call-{ordinal}', input=patch), ordinal))
        for number, row in enumerate(self.rows):
            row['ordinal'] = number
        self.write()
        records = self.capture()[0]
        assert sum(r.get('tool_name') == 'apply_patch' for r in calls(records)) == 2
        assert not any(set(EDIT_FIELDS) & set(r) for r in records)
        assert signal(records, 'edit_then_revert')['status'] == 'unknown'
