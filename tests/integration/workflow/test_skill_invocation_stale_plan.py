"""A repair whose target changed after staging is invalidated the same way through `ahl improve` and `ahl skills invoke` (#143).

Each case changes the project at one point between staging the baseline and the target write. The repair must stop
before writing, keep the user's bytes and every unrelated file, record no committed deployment, and leave the project
repairable by a fresh run that plans from the bytes now on disk. No model runs and every input is synthetic.
"""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab import harness_change_plan, one_call
from agent_harness_lab.cli import main
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.workflow import identity
from tests.support.one_call import one as one

ROUTES = [pytest.param(['improve'], id='cli'), pytest.param(['skills', 'invoke'], id='skill')]
EDIT = b'user line\n'
SWAPPED = b'swapped after staging\n'


def route(one, prefix):
    """The exit code, the stdout receipt and stderr of one repair run, with the model runner forbidden."""
    output, errors, code = io.StringIO(), io.StringIO(), 0
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
            patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no model')):
        try:
            code = main([*prefix, str(one.project), '--repair-contracts'])
        except SystemExit as stop:
            code = stop.code
    return code, json.loads(output.getvalue()), errors.getvalue()


def edit_skill(one):
    one.skill.write_bytes(b'\xef\xbb\xbf' + one.valid + EDIT)


@contextlib.contextmanager
def user_edits_the_skill_after_staging(one):
    produce = harness_change_plan.produce
    def late(*args, **kwargs):
        edit_skill(one)
        return produce(*args, **kwargs)
    with patch.object(harness_change_plan, 'produce', late):
        yield


@contextlib.contextmanager
def user_adds_a_harness_file_after_staging(one):
    produce = harness_change_plan.produce
    def late(*args, **kwargs):
        (one.project/'CLAUDE.md').write_text('added by the user\n')
        return produce(*args, **kwargs)
    with patch.object(harness_change_plan, 'produce', late):
        yield


@contextlib.contextmanager
def candidate_is_swapped_after_staging(one):
    attach = harness_change_plan.attach
    def late(*args, **kwargs):
        staged = [path for path in one.root.rglob('candidate/.agents/skills/example/SKILL.md') if path.read_bytes() != SWAPPED]
        assert len(staged) == 1  # an earlier run's candidate is already swapped
        staged[0].write_bytes(SWAPPED)
        return attach(*args, **kwargs)
    with patch.object(harness_change_plan, 'attach', late):
        yield


@contextlib.contextmanager
def user_edits_the_skill_after_the_journal_is_prepared(one):
    prepare = Journal.prepare
    def late(self, *args, **kwargs):
        record = prepare(self, *args, **kwargs)
        edit_skill(one)
        return record
    with patch.object(Journal, 'prepare', late):
        yield


@contextlib.contextmanager
def user_edits_the_skill_at_the_final_guard(one):
    check = one_call.check_configuration
    def late(*args, **kwargs):
        check(*args, **kwargs)
        if list(one.skill.parent.glob('SKILL.md.ahl-*')):  # the journal staged its temporary file: the last check before the replace
            edit_skill(one)
    with patch.object(one_call, 'check_configuration', late):
        yield


# case id: (change, exit code and status, the message stderr names or None, the skill bytes the failed run must leave,
# the bytes a fresh run repairs). The final guard runs inside the journal transaction, whose own rule is to report
# a foreign edit it cannot own as requires_intervention and keep it; every earlier check ends as invalid_input.
STAGED = lambda one: b'\xef\xbb\xbf' + one.valid
EDITED = lambda one: b'\xef\xbb\xbf' + one.valid + EDIT
CASES = {
    'edit-after-staging': (user_edits_the_skill_after_staging, (2, 'invalid_input'), 'target changed before application',
                           EDITED, lambda one: one.valid + EDIT),
    'new-harness-file-after-staging': (user_adds_a_harness_file_after_staging, (2, 'invalid_input'), 'frozen plan inventory changed',
                                       STAGED, lambda one: one.valid),
    'candidate-swapped-after-staging': (candidate_is_swapped_after_staging, (2, 'invalid_input'),
                                        'configuration or candidate changed before application', STAGED, lambda one: one.valid),
    'edit-after-journal-prepare': (user_edits_the_skill_after_the_journal_is_prepared, (2, 'invalid_input'),
                                   'target inventory changed during planned application', EDITED, lambda one: one.valid + EDIT),
    'edit-at-final-guard': (user_edits_the_skill_at_the_final_guard, (1, 'requires_intervention'), None,
                            EDITED, lambda one: one.valid + EDIT),
}


def shape(code, receipt):
    """What a caller can read from a failed run, with nothing that names a run directory or a measured time."""
    return (code, receipt['status'], receipt['reason'], receipt['exit_receipt']['stage'], receipt['exit_receipt']['next_step'],
            receipt.get('error_type'), receipt.get('restoration'))


@pytest.mark.parametrize('prefix', ROUTES)
@pytest.mark.parametrize('case', [pytest.param(name, id=name) for name in CASES])
def test_changed_target_invalidates_the_repair_and_keeps_user_work(one, prefix, case):
    change, (expected_code, expected_status), message, kept, repaired = CASES[case]
    dirty = one.project/'dirty.txt'; dirty.write_text('unrelated user change')
    with change(one):
        code, receipt, errors = route(one, prefix)
    assert (code, receipt['status'], receipt['exit_receipt']['code']) == (expected_code, expected_status, expected_code)
    assert message is None or f'ahl: {message}' in errors
    assert one.skill.read_bytes() == kept(one) and dirty.read_text() == 'unrelated user change'
    assert not list(one.skill.parent.glob('SKILL.md.ahl-*'))
    assert not (one.project/'CLAUDE.md').exists() or (one.project/'CLAUDE.md').read_text() == 'added by the user\n'
    assert Journal(one.project, one.root/'state/promotions'/identity(one.project)).active is None
    code, again, _ = route(one, prefix)  # a fresh run plans from the bytes now on disk and applies once
    assert (code, again['status'], again['changed_files']) == (0, 'applied', ['.agents/skills/example/SKILL.md'])
    assert one.skill.read_bytes() == repaired(one) and dirty.read_text() == 'unrelated user change'
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['rollback', again['deployment'], '--project', str(one.project)]) == 0
    assert one.skill.read_bytes() == kept(one) and dirty.read_text() == 'unrelated user change'


@pytest.mark.parametrize('case', [pytest.param(name, id=name) for name in CASES])
def test_both_routes_report_the_same_invalidation(one, case):
    change, _, message, *_ = CASES[case]
    initial, shapes, lines = one.skill.read_bytes(), [], []
    for prefix in (['improve'], ['skills', 'invoke']):
        with change(one):
            code, receipt, errors = route(one, prefix)
        shapes.append(shape(code, receipt))
        lines.append([line for line in errors.splitlines() if message and message in line])
        one.skill.write_bytes(initial)  # restore the fixture for the other route
        (one.project/'CLAUDE.md').unlink(missing_ok=True)
    assert shapes[0] == shapes[1]
    assert lines[0] == lines[1] and (message is None or len(lines[0]) == 1)
