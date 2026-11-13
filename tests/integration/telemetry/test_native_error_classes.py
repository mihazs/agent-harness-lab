"""Tool-error classes on the pinned native Claude adapter and in the analysis artifacts (#824); synthetic captures only."""
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import io
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.diagnostics import brief
from agent_harness_lab.telemetry.error_catalog import CLASSES, RUNTIMES, UNCLASSIFIED
from tests.integration.telemetry.test_claude_native import VERSION, ClaudeNativeTests
from tests.unit.telemetry.test_error_catalog import MESSAGES

RUNTIME = RUNTIMES[0]
CANARY = 'SYNTHETIC_CANARY'


def results(records): return [r for r in records if r['activity'] == 'tool_result']


class Capture(ClaudeNativeTests):
    """Two tool calls (Bash call-1, Read call-2) whose results the test chooses; every row records the given runtime."""
    def build(self, tmp_path, version=RUNTIME, outcomes=None):
        self.setUp(tmp_path)
        for row in self.rows: row.update({'version': version} if 'version' in row else {})
        self.set(outcomes or {'call-1': MESSAGES['command_exit_nonzero'], 'call-2': None})
        return self

    def set(self, outcomes):
        """call id -> error text (is_error true), or None for a successful result."""
        for row in self.rows:
            for block in (row.get('message') or {}).get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result' and block['tool_use_id'] in outcomes:
                    text = outcomes[block['tool_use_id']]
                    block.update(is_error=text is not None, content=text or 'ok')
        self.write()

    def classes(self): return [r.get('tool_error_class') for r in results(self.capture()[0])]


@pytest.fixture
def capture(tmp_path): return Capture().build(tmp_path)


@pytest.mark.parametrize('expected', MESSAGES)
def test_each_catalog_message_in_a_covered_runtime_is_classified_on_its_error_result(tmp_path, expected):
    # The Bash result row follows the Read result row in capture order.
    cap = Capture().build(tmp_path, outcomes={'call-1': MESSAGES[expected], 'call-2': None})
    records, _, _ = cap.capture()
    errors = [r for r in results(records) if r['execution_status'] == 'error']
    assert [r['tool_error_class'] for r in errors] == [expected]


def test_a_successful_result_carries_no_class(capture):
    ok = [r for r in results(capture.capture()[0]) if r['execution_status'] == 'returned']
    assert ok and not any('tool_error_class' in r for r in ok)


@pytest.mark.parametrize('text', [
    pytest.param(CANARY, id='unrelated_text'),
    pytest.param('Note from the user: ' + MESSAGES['read_before_edit'], id='message_quoted_inside_user_content'),
    pytest.param(MESSAGES['read_before_edit'] + ' ' + CANARY, id='message_followed_by_other_text'),
    pytest.param('', id='empty_error_body'),
])
def test_look_alike_content_in_an_error_result_stays_unclassified(tmp_path, text):
    cap = Capture().build(tmp_path, outcomes={'call-1': text, 'call-2': None})
    assert [c for c in cap.classes() if c is not None] == [UNCLASSIFIED]


@pytest.mark.parametrize('version', ['2.1.316', '2.1.318'])
def test_a_release_the_adapter_reads_but_the_catalog_never_compared_is_unclassified(tmp_path, version):
    cap = Capture().build(tmp_path, version=version, outcomes={'call-1': MESSAGES['read_before_edit'], 'call-2': None})
    assert [c for c in cap.classes() if c is not None] == [UNCLASSIFIED]


def test_error_text_never_reaches_events_or_the_capture_summary(tmp_path):
    cap = Capture().build(tmp_path, outcomes={'call-1': MESSAGES['edit_target_not_found'] + CANARY, 'call-2': None})
    records, summary, _ = cap.capture()
    assert CANARY not in json.dumps((records, summary)) and 'not found in file' not in json.dumps((records, summary))


def test_a_duplicate_line_whose_error_text_differs_is_still_the_same_idempotent_duplicate(capture):
    capture.rows.append(json.loads(json.dumps(next(r for r in capture.rows if r.get('uuid') == 'r1'))))
    capture.rows[-1]['message']['content'][0]['content'] = MESSAGES['read_before_edit']
    capture.write()
    records, summary, _ = capture.capture()
    assert summary['duplicates_skipped'] == 1 and len(results(records)) == 2


def test_the_class_is_left_out_of_the_body_insensitive_metadata_fingerprint(tmp_path):
    (tmp_path / 'a').mkdir(); (tmp_path / 'b').mkdir()
    first = Capture().build(tmp_path / 'a', outcomes={'call-1': MESSAGES['command_exit_nonzero'], 'call-2': None})
    second = Capture().build(tmp_path / 'b', outcomes={'call-1': MESSAGES['read_before_edit'], 'call-2': None})
    assert first.path.read_bytes() != second.path.read_bytes()
    assert first.collect()['fingerprint'] == second.collect()['fingerprint']


def scan(events):
    state = brief.Scan()
    for event in events: state.observe(event)
    return brief.mechanism_summaries(state.result())['mechanisms']


def failing(case, ident, tool, error_class=None, **extra):
    base = dict(case_id=case, root_case_id=case)
    call = dict(base, event_id=f'{ident}-call', activity='tool_call', tool_name=tool, call_id=ident)
    result = dict(base, event_id=f'{ident}-result', activity='tool_result', tool_name=tool, call_id=ident, execution_status='error', **extra)
    if error_class is not None: result['tool_error_class'] = error_class
    return [call, result]


def test_mechanism_summaries_count_errors_per_class_per_tool_and_per_case():
    events = [*failing('c1', 'a', 'Edit', 'read_before_edit'), *failing('c1', 'b', 'Edit', 'read_before_edit'),
              *failing('c2', 'c', 'Edit', 'read_before_edit'), *failing('c2', 'd', 'Edit', 'edit_target_not_unique'),
              *failing('c1', 'e', 'Bash', 'command_exit_nonzero')]
    by_tool = {m['tool_class']: m for m in scan(events)}
    assert by_tool['Edit']['errors_by_error_class'] == dict(edit_target_not_unique=1, read_before_edit=3)
    assert by_tool['Edit']['cases_by_error_class'] == dict(edit_target_not_unique=1, read_before_edit=2)
    assert by_tool['Bash']['errors_by_error_class'] == dict(command_exit_nonzero=1)
    assert by_tool['Bash']['cases_by_error_class'] == dict(command_exit_nonzero=1)
    assert by_tool['Edit']['mechanism_id'] == 'Edit/tool_reported_error' and by_tool['Edit']['errors'] == 4


def test_unclassified_and_not_captured_errors_stay_separate_and_nothing_is_guessed():
    events = [*failing('c1', 'a', 'Edit', UNCLASSIFIED), *failing('c1', 'b', 'Edit'), *failing('c2', 'c', 'Edit')]
    (mechanism,) = scan(events)
    assert mechanism['errors_by_error_class'] == dict(unclassified=1, unknown=2)
    assert mechanism['cases_by_error_class'] == dict(unclassified=1, unknown=2)


def test_a_source_that_records_no_class_reports_no_class_counts():
    (mechanism,) = scan([*failing('c1', 'a', 'Edit'), *failing('c2', 'b', 'Edit')])
    assert 'errors_by_error_class' not in mechanism and 'cases_by_error_class' not in mechanism


def test_the_proposer_brief_carries_the_class_counts_and_the_catalog_version_without_references():
    state = brief.Scan()
    for event in [*failing('c1', 'a', 'Edit', 'read_before_edit'), *failing('c2', 'b', 'Edit', UNCLASSIFIED)]: state.observe(event)
    data = dict(analysis=dict(events=4, cases=2, reported_roots=2), sources=[], evaluation_outcomes=[], caveats=[])
    built, _ = brief.build(data, state.result())
    (mechanism,) = built['mechanisms']
    assert mechanism['errors_by_error_class'] == dict(read_before_edit=1, unclassified=1) and 'evidence_refs' not in mechanism
    assert built['tool_error_classes'] == dict(catalog='ahl-tool-error-catalog-v1', runtimes=list(RUNTIMES),
                                               errors=2, classified=1, unclassified=1, not_captured=0)


def test_class_counts_are_bounded_by_the_fixed_vocabulary_not_by_history_size():
    events = [e for number in range(400) for e in failing(f'case-{number}', f'k{number}', 'Edit', CLASSES[number % len(CLASSES)])]
    (mechanism,) = scan(events)
    assert set(mechanism['errors_by_error_class']) == set(CLASSES) and sum(mechanism['errors_by_error_class'].values()) == 400


def test_a_full_analysis_carries_class_counts_and_no_error_text_in_any_artifact(tmp_path):
    texts = {'call-1': MESSAGES['edit_target_not_unique'], 'call-2': MESSAGES['read_before_edit'] + ' ' + CANARY}
    cap = Capture().build(tmp_path, outcomes=texts)
    out = cap.root / 'analysis'
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(['analyze', str(cap.project), '--events', str(cap.path), '--format', 'claude', '--source-version', VERSION, '--output', str(out)])
    assert code == 0
    summaries = json.loads((out / 'mechanism-summaries.json').read_text())['mechanisms']
    total = Counter()
    for mechanism in summaries: total.update(mechanism['errors_by_error_class'])
    assert total == Counter(edit_target_not_unique=1, unclassified=1)
    assert json.loads((out / 'analysis-brief.json').read_text())['tool_error_classes']['classified'] == 1
    ledger = [json.loads(line) for line in (out / 'events.v2.jsonl').read_text().splitlines()]
    assert Counter(r['tool_error_class'] for r in ledger if 'tool_error_class' in r) == Counter(edit_target_not_unique=1, unclassified=1)
    artifacts = [p for p in sorted(out.rglob('*')) if p.is_file()]
    assert artifacts
    for artifact in artifacts:
        body = artifact.read_text()
        for text in (CANARY, 'replace_all is false', 'has not been read yet', 'matches of the string'):
            assert text not in body, (artifact.name, text)
    assert CANARY not in stdout.getvalue() + stderr.getvalue()
