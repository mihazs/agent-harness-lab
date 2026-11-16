"""`ahl score --sessions`: recorded sessions are placed as advisory context beside the scored rows, never scored (#799)."""
import json

import pytest

from agent_harness_lab import cli
from tests.support import score_fixture as fixture
from tests.support import session_signals as signals
from tests.support.area_bank import REGISTRY, TAGS

DOCS = dict(id='docs', label='Docs', description='Write and correct documentation.')
REVIEW = dict(id='review', label='Review', description='Find defects in a change.')


@pytest.fixture
def project(tmp_path):
    """Four declared areas; docs has no experiment task, so its scored row is no_evidence."""
    root = fixture.project(tmp_path, registry=[*REGISTRY, DOCS, REVIEW], tags=TAGS)
    spec = fixture.experiment(root)
    return root, *fixture.write(root, spec, fixture.records(spec))


def score(capsys, project, *options):
    root, spec, records = project
    code = cli.main(['score', str(root), '--spec', str(spec), '--records', str(records), *options])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def every_kind(root):
    return signals.write(root, signals.sessions(*signals.BY_AREA.values(), signals.AMBIGUOUS, signals.EMPTY))


def test_a_report_without_sessions_has_no_advisory_section(project, capsys):
    code, out, _ = score(capsys, project)
    assert code == 0 and 'advisory_sessions' not in json.loads(out)


def test_each_built_in_area_and_the_unplaceable_sessions_are_reported_as_advisory(project, capsys):
    code, out, err = score(capsys, project, '--sessions', str(every_kind(project[0])))
    section = json.loads(out)['advisory_sessions']
    assert (code, err) == (0, '')
    assert {row['id']: row['sessions'] for row in section['areas']} == dict(coding=1, **{'ux-ui': 1}, docs=1, review=1)
    assert {row['session']: (row['area'], row['reason']) for row in section['rows']} == {
        's-coding': ('coding', None), 's-ux-ui': ('ux-ui', None), 's-docs': ('docs', None), 's-review': ('review', None),
        's-ambiguous': (None, 'ambiguous'), 's-empty': (None, 'no_signal')}


def test_sessions_never_change_a_scored_row_or_the_overall_figure(project, capsys):
    plain = json.loads(score(capsys, project)[1])
    with_sessions = json.loads(score(capsys, project, '--sessions', str(every_kind(project[0])))[1])
    assert {key: value for key, value in with_sessions.items() if key != 'advisory_sessions'} == plain


def test_an_area_with_sessions_but_no_experiment_task_stays_no_evidence(project, capsys):
    result = json.loads(score(capsys, project, '--sessions', str(every_kind(project[0])))[1])
    docs = next(row for row in result['areas'] if row['id'] == 'docs')
    assert (docs['state'], docs['success']) == ('no_evidence', None)
    assert next(row for row in result['advisory_sessions']['areas'] if row['id'] == 'docs')['state'] == 'advisory'


def test_the_advisory_section_never_carries_a_scored_state(project, capsys):
    section = json.loads(score(capsys, project, '--sessions', str(every_kind(project[0])))[1])['advisory_sessions']
    assert 'scored' not in json.dumps(section)


def test_the_markdown_report_separates_the_advisory_table_from_the_scored_table(project, capsys):
    out = score(capsys, project, '--sessions', str(every_kind(project[0])), '--format', 'md')[1]
    scored, advisory = out.split('## Advisory: recorded sessions (observed only, never scored)')
    assert '| scored |' in scored and 'scored' not in advisory.replace('never scored', '').replace('`scored`', '')
    assert '| Unclassified | unclassified | 2 (no_signal 1, below_minimum_signal 0, ambiguous 1, area_not_declared 0) |' in advisory


def test_an_empty_sessions_file_reports_zero_sessions(project, capsys):
    path = signals.write(project[0], [])
    code, out, _ = score(capsys, project, '--sessions', str(path))
    assert (code, json.loads(out)['advisory_sessions']['recorded']) == (0, 0)


def test_area_narrows_the_advisory_rows_but_not_the_placement(project, capsys):
    code, out, _ = score(capsys, project, '--sessions', str(every_kind(project[0])), '--area', 'ux-ui')
    section = json.loads(out)['advisory_sessions']
    assert (code, [row['id'] for row in section['areas']], section['not_selected'], section['recorded']) == (0, ['ux-ui'], 3, 6)


def test_the_report_with_sessions_is_identical_on_every_run(project, capsys):
    options = ('--sessions', str(every_kind(project[0])))
    assert score(capsys, project, *options) == score(capsys, project, *options)


@pytest.mark.parametrize('content, why', [
    pytest.param('{"a": 1}', 'sessions must be a JSON array', id='object-not-array'),
    pytest.param('[{"session": "s", "extensions": {"py": 0}}]', 'sessions[0].extensions', id='zero-count'),
    pytest.param('[{"session": "s"}, {"session": "s"}]', 'sessions[1].session', id='repeated-session'),
    pytest.param('not json', 'Expecting value', id='not-json'),
])
def test_unusable_sessions_exit_2_without_a_report(project, capsys, content, why):
    path = project[0] / 'sessions.json'
    path.write_text(content)
    code, out, err = score(capsys, project, '--sessions', str(path))
    assert (code, out) == (2, '') and why in err


def test_a_missing_sessions_file_exits_2_without_a_report(project, capsys):
    code, out, err = score(capsys, project, '--sessions', str(project[0] / 'absent.json'))
    assert (code, out) == (2, '') and err.startswith('ahl score:')
