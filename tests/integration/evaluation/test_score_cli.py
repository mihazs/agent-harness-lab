"""`ahl score` through the CLI: a read-only per-area report whose bad inputs exit 2 with a typed reason (#797)."""
import json
import subprocess
import sys

import pytest

from agent_harness_lab import cli
from tests.support import score_fixture as fixture
from tests.support.area_bank import REGISTRY, TAGS

DOCS = dict(id='docs', label='Docs', description='Write and correct documentation.')
REVIEW = dict(id='review', label='Review', description='Find defects in a change.')


@pytest.fixture
def four_states(tmp_path):
    """Every state at once: ux-ui has an ungraded task, docs has no task and the one review task is unreviewed."""
    root = fixture.project(tmp_path, registry=[*REGISTRY, DOCS, REVIEW], tags={**TAGS, 'resumption': ['review']}, reviewed=list(TAGS))
    spec = fixture.experiment(root)
    return root, *fixture.write(root, spec, fixture.records(spec, ungraded=('skill-routing',)))


def score(capsys, root, spec, records, *options):
    code = cli.main(['score', str(root), '--spec', str(spec), '--records', str(records), *options])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_json_report_shows_all_four_evidence_states_for_a_synthetic_bank(four_states, capsys):
    code, out, err = score(capsys, *four_states)
    assert (code, err) == (0, '')
    assert {row['id']: row['state'] for row in json.loads(out)['areas']} == {
        'coding': 'scored', 'ux-ui': 'provisional', 'docs': 'no_evidence', 'review': 'blocked'}


def test_area_narrows_the_report_to_the_named_areas(four_states, capsys):
    code, out, _ = score(capsys, *four_states, '--area', 'ux-ui', '--area', 'docs')
    assert (code, [row['id'] for row in json.loads(out)['areas']]) == (0, ['ux-ui', 'docs'])


def test_the_markdown_report_lists_every_area_with_its_state(four_states, capsys):
    code, out, _ = score(capsys, *four_states, '--format', 'md')
    rows = {line.split(' | ')[0].strip('| '): line.split(' | ')[1] for line in out.splitlines() if line.startswith('| ') and '`' in line}
    assert (code, rows) == (0, {'Coding (`coding`)': 'scored', 'UX/UI (`ux-ui`)': 'provisional', 'Docs (`docs`)': 'no_evidence',
                                'Review (`review`)': 'blocked'})


def test_the_report_is_identical_on_every_run_and_writes_nothing(four_states, capsys):
    root = four_states[0]
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob('*'))
    first, second = score(capsys, *four_states), score(capsys, *four_states)
    assert first == second and sorted(path.relative_to(root).as_posix() for path in root.rglob('*')) == before


def test_an_undeclared_area_exits_2_with_the_typed_reason_and_prints_no_report(four_states, capsys):
    code, out, err = score(capsys, *four_states, '--area', 'design')
    assert (code, out) == (2, '') and 'score_area_undeclared' in err and '--area[0]' in err and 'design' not in err.replace('score_area_undeclared', '')


def test_a_repeated_area_exits_2_with_the_duplicate_reason(four_states, capsys):
    code, out, err = score(capsys, *four_states, '--area', 'coding', '--area', 'coding')
    assert (code, out) == (2, '') and 'area_duplicate' in err and '--area[1]' in err


def test_a_bank_that_changed_since_ahl_json_pinned_it_exits_2(four_states, capsys):
    root = four_states[0]
    bank = root / 'bank/bank.json'
    bank.write_text(bank.read_text() + '\n')
    code, out, err = score(capsys, *four_states)
    assert (code, out) == (2, '') and 'digest' in err


def test_a_task_tagged_with_an_area_ahl_json_does_not_declare_exits_2_with_the_task_position(tmp_path, capsys):
    root = fixture.project(tmp_path, registry=REGISTRY[:1], tags=TAGS)
    spec = fixture.experiment(root)
    code, out, err = score(capsys, root, *fixture.write(root, spec, fixture.records(spec)))
    assert (code, out) == (2, '') and 'task_area_undeclared' in err and 'tasks[2].areas[0]' in err


@pytest.mark.parametrize('content, why', [
    pytest.param('{"a": 1}', 'records must be a JSON array', id='object-not-array'),
    pytest.param('[{"trial_id": "t"}]', 'argument', id='record-with-missing-fields'),
    pytest.param('not json', 'Expecting value', id='not-json'),
])
def test_unusable_records_exit_2_without_a_report(four_states, capsys, content, why):
    root, spec, records = four_states
    records.write_text(content)
    code, out, err = score(capsys, root, spec, records)
    assert (code, out) == (2, '') and why in err


def test_a_project_without_ahl_json_exits_2(tmp_path, capsys):
    root = fixture.project(tmp_path)
    spec = fixture.experiment(root)
    paths = fixture.write(root, spec, fixture.records(spec))
    (root / 'ahl.json').unlink()
    code, out, err = score(capsys, root, *paths)
    assert (code, out) == (2, '') and err.startswith('ahl score:')


def test_the_installed_command_prints_the_report_from_a_new_process(four_states):
    root, spec, records = four_states
    done = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'score', str(root), '--spec', str(spec), '--records', str(records), '--area', 'coding'],
                          capture_output=True, text=True)
    assert done.returncode == 0 and json.loads(done.stdout)['areas'][0]['state'] == 'scored'
