"""Progress is observable before capture reads and never exposes capture metadata."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab import cli, evidence
from tests.support.typed_exit import typed_stdout


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path
    project = root / 'private-project'
    project.mkdir()
    capture = root / 'private-capture.jsonl'
    capture.write_text(json.dumps(dict(case_id='private-session', event_id='private-event',
                                       activity='tool_call', tool_name='private-tool'))+'\n')
    monkeypatch.setenv('CODEX_HOME', str(root/'codex'))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(root/'claude'))
    return SimpleNamespace(root=root, project=project, capture=capture)


def test_explicit_capture_progress_precedes_read_and_stdout_stays_json(case):
    output, error = io.StringIO(), io.StringIO()
    original = evidence._rows
    def read(*args, **kwargs):
        assert 'capture_processing' in error.getvalue()
        yield from original(*args, **kwargs)
    with redirect_stdout(output), redirect_stderr(error), patch.object(evidence, '_rows', read):
        status = cli.main(['analyze', str(case.project), '--events', str(case.capture),
                           '--output', str(case.root/'result')])
    assert status == 0
    assert json.loads(output.getvalue())['status'] == 'analyzed'
    assert 'events=1' in error.getvalue()
    assert 'eligible=0' in error.getvalue()
    assert 'private-' not in error.getvalue()
    assert str(case.root) not in error.getvalue()


def test_discovery_reports_empty_and_explicit_scan_limit_without_paths(case):
    messages = []
    progress = lambda stage, **counts: messages.append((stage, counts))
    evidence.collect(case.project, progress=progress)
    assert messages[-1][0] == 'evidence_empty'
    for number in range(3):
        path = case.root/'codex'/'sessions'/f'private-{number}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(cwd=str(case.project), type='session_meta'))+'\n')
    messages.clear()
    result = evidence.collect(case.project, max_scan_files=1, progress=progress)
    assert result['caveats']
    assert messages[-1][1]['incomplete'] == 1
    assert max(counts.get('scanned', 0) for _, counts in messages) == 2
    assert 'private-' not in json.dumps(messages)


def test_interrupt_reports_status_without_printing_capture_or_partial_json(case):
    output, error = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(error), patch.object(evidence, '_rows', side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as stopped:
            cli.main(['analyze', str(case.project), '--events', str(case.capture),
                      '--output', str(case.root/'interrupted')])
    assert stopped.value.code == 1
    assert 'interrupted' in error.getvalue()
    assert output.getvalue() == ''
    assert 'private-' not in error.getvalue()


def test_evaluation_outcomes_are_not_labeled_as_empty_evidence(case):
    report = case.project/'artifacts'/'synthetic'/'results.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(dict(trials=[dict(task='synthetic', status='failed')])))
    messages = []
    evidence.collect(case.project, progress=lambda stage, **counts: messages.append((stage, counts)))
    assert messages[-1][0] == 'evidence_incomplete'
    assert messages[-1][1]['evaluation_outcomes'] == 1


def test_repeated_stage_updates_are_throttled_but_stage_changes_are_immediate():
    error = io.StringIO()
    with redirect_stderr(error), patch.object(cli.time, 'perf_counter', side_effect=[0, 0, .2, .3, 1.4]):
        progress = cli._progress_reporter()
        progress('discovery', scanned=0)
        progress('discovery', scanned=1)
        progress('capture_processing', selected=1)
        progress('capture_processing', events=1000)
    lines = error.getvalue().splitlines()
    assert len(lines) == 3
    assert 'scanned=0' in lines[0]
    assert 'selected=1' in lines[1]
    assert 'events=1000' in lines[2]


@pytest.mark.parametrize('changed, expected', [([], 'changed_files=0'), (['private-path'], 'changed_files=1'), (None, None)])
def test_final_improve_progress_counts_changes_without_paths(case, changed, expected):
    result = {'status': 'no_change', 'output': str(case.root/'private-output')}
    if changed is not None: result['changed_files'] = changed
    output, error = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(error), patch('agent_harness_lab.workflow.improve', return_value=result):
        cli.main(['improve', str(case.project)])
    assert json.loads(output.getvalue()) == result
    assert expected in error.getvalue() if expected else 'changed_files=unknown' in error.getvalue()
    assert 'private-path' not in error.getvalue()
    assert json.dumps(result['output']) in error.getvalue()
    assert 'trials_spent=unknown' in error.getvalue()
    assert 'cost=unknown' in error.getvalue()
    from agent_harness_lab.exit_guidance import STATUS_ACTION
    assert f"next={STATUS_ACTION['no_change']}" in error.getvalue()


def test_final_blocked_summary_uses_known_remediation_and_handles_missing_artifacts(case):
    result = {'status': 'blocked', 'reason': 'select_one_runtime_with_agent'}
    output, error = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(error), patch('agent_harness_lab.workflow.improve', return_value=result):
        code = cli.main(['improve', str(case.project)])
    assert code == 3
    assert json.loads(output.getvalue()) == result
    assert 'outcome=blocked' in error.getvalue()
    assert 'artifacts=unavailable' in error.getvalue()
    assert 'changed_files=unknown' in error.getvalue()
    assert '--agent codex or --agent claude' in error.getvalue()
    assert 'remaining_seconds=unknown' in error.getvalue()


@pytest.mark.parametrize('status', ['no_change', 'execution_error', 'blocked'])
def test_measured_budget_reports_spent_and_remaining_for_controller_outcomes(case, status):
    output, error = io.StringIO(), io.StringIO()
    def improve(args):
        args._budget_started = cli.time.monotonic() - 2
        return {'status': status, 'changed_files': []}
    with redirect_stdout(output), redirect_stderr(error), patch('agent_harness_lab.workflow.improve', side_effect=improve):
        cli.main(['improve', str(case.project), '--timeout', '60'])
    measured = re.search(r'budget_spent_seconds=([0-9.]+) remaining_seconds=([0-9.]+)', error.getvalue())
    assert measured is not None
    spent, remaining = map(float, measured.groups())
    assert spent >= 2 and abs(remaining-max(0, 60-spent)) <= .11
    assert 'trials_spent=unknown' in error.getvalue()


def test_budget_timing_normalization_preserves_unknown():
    from agent_harness_lab.entry_parity import normalized_stderr
    result = normalized_stderr('budget_spent_seconds=2.3 remaining_seconds=57.7 trials_spent=unknown', '/run')
    assert result == 'budget_spent_seconds=<seconds> remaining_seconds=<seconds> trials_spent=unknown'


def test_exception_reports_measured_budget_without_claiming_trial_spend(case):
    output, error = io.StringIO(), io.StringIO()
    def fail(args):
        args._budget_started = cli.time.monotonic() - 2
        raise ValueError('synthetic failure')
    with redirect_stdout(output), redirect_stderr(error), patch('agent_harness_lab.workflow.improve', side_effect=fail):
        with pytest.raises(SystemExit) as stopped:
            cli.main(['improve', str(case.project), '--timeout', '60'])
    assert stopped.value.code == 2
    assert typed_stdout(output.getvalue())['status'] == 'invalid_input'
    assert re.search(r'budget_spent_seconds=[0-9.]+ remaining_seconds=[0-9.]+', error.getvalue())
    assert 'trials_spent=unknown' in error.getvalue()
