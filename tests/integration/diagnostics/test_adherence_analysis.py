"""Every analysis reads the project's native captures for ignored instructions; synthetic stores only."""
import json
import re
from contextlib import redirect_stderr, redirect_stdout
import io

import pytest

from agent_harness_lab import cli
from agent_harness_lab.telemetry.claude_discovery import directory_names
from tests.support.native_session_fixture import claude, codex, write

SECRET = 'zz-private-token'
SESSION = '00000000-0000-4000-8000-000000000001'
FORBIDDEN = 'git push --force origin main'


@pytest.fixture
def home(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_text('- Never run `git push --force`.\n')
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('AHL_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path / folder))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'unused-xdg'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return project


def capture(project, steps, *, version=None, session=SESSION):
    rows = claude(project, steps, session=session, **({'version': version} if version else {}))
    return write(project.parent / 'claude/projects' / directory_names(project)[0] / f'{session}.jsonl', rows)


def analyze(project, *extra, name='output'):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(['analyze', str(project), '--output', str(project.parent / name), *extra])
    assert code == 0, err.getvalue()
    folder = project.parent / name
    return folder, json.loads((folder / 'analysis-brief.json').read_text())


def test_analysis_writes_a_redacted_adherence_artifact_and_a_counts_only_brief_section(home):
    capture(home, [('memory', str(home / 'AGENTS.md')), ('user', f'Push {SECRET}.'), ('call', 'Bash', dict(command=FORBIDDEN))])
    folder, brief = analyze(home)
    section = brief['instruction_adherence']
    assert (section['status'], section['findings'], section['mechanisms'], section['sessions']['read']) == (
        'completed', dict(observed_violation=1), dict(forbidden_action_taken=1), 1)
    assert brief['sources']['instruction_adherence'] == 'instruction-adherence.json' == section['artifact']
    artifact = (folder / 'instruction-adherence.json').read_text()
    assert json.loads(artifact)['counts'] == dict(observed_violation=1)
    for text in (artifact, json.dumps(brief)):
        assert SECRET not in text and 'git push' not in text and str(home) not in text
    assert not re.search(r'[0-9a-f]{64}', json.dumps(section))


def test_analysis_of_a_codex_capture_reports_a_possible_violation_as_an_abstention_because_loading_is_unknown(home):
    rows = codex(home, [('user', 'go'), ('exec', FORBIDDEN)], session='codex-thread')
    write(home.parent / 'codex/sessions/rollout.jsonl', rows)
    folder, brief = analyze(home)
    section = brief['instruction_adherence']
    assert (section['status'], section['findings'], section['abstentions']) == ('completed', {}, 1)
    assert json.loads((folder / 'instruction-adherence.json').read_text())['abstentions'][0]['status'] == 'possible_violation'


def test_analysis_of_explicit_events_reports_not_run_and_never_zero_findings(home):
    events = write(home.parent / 'events.jsonl', [dict(case_id='c', event_id='1', activity='user_message', capture_order=0)])
    folder, brief = analyze(home, '--events', str(events))
    assert brief['instruction_adherence'] == dict(authority='descriptive_only', causal_diagnosis=False, status='not_run', reason='no_native_capture_path')
    assert not (folder / 'instruction-adherence.json').exists() and 'instruction_adherence' not in brief['sources']


def test_analysis_reports_the_typed_reason_when_no_native_capture_could_be_read(home):
    capture(home, [('user', 'go'), ('call', 'Bash', dict(command=FORBIDDEN))], version='9.9.9')
    folder, brief = analyze(home)
    assert brief['instruction_adherence']['status'] == 'not_run' and brief['instruction_adherence']['reason'] == 'no_readable_native_session'
    assert brief['instruction_adherence']['skipped'] == dict(unsupported_version=1) and not (folder / 'instruction-adherence.json').exists()


def test_analysis_counts_a_skipped_capture_beside_the_sessions_it_read(home):
    capture(home, [('memory', str(home / 'AGENTS.md')), ('user', 'go'), ('call', 'Bash', dict(command=FORBIDDEN))])
    capture(home, [('user', 'older')], version='9.9.9', session='00000000-0000-4000-8000-000000000002')
    _, brief = analyze(home)
    assert brief['instruction_adherence']['sessions'] == dict(read=1, unknown_lineage=1, skipped=dict(unsupported_version=1))


def test_adherence_artifact_is_identical_across_two_analyses_of_the_same_captures(home):
    capture(home, [('memory', str(home / 'AGENTS.md')), ('user', 'go'), ('call', 'Bash', dict(command=FORBIDDEN))])
    first, _ = analyze(home)
    text = (first / 'instruction-adherence.json').read_bytes()
    second, _ = analyze(home, name='again')
    assert (second / 'instruction-adherence.json').read_bytes() == text != b''
