"""Independent synthetic controls for the first actual target-mutation boundary."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import config, one_call
from agent_harness_lab.promotion.journal import Journal
from tests.integration.workflow import test_one_call as fixtures


@pytest.fixture
def one():
    fixture = fixtures.OneCallTests(); fixture.setUp()
    yield fixture
    fixture.doCleanups()


def test_new_recommendation_only_config_inside_commit_check_prevents_target_write(one):
    original = Journal._check
    def revoke(journal, record):
        original(journal, record)
        config.create(one.project, config.defaults())
    before = one.skill.read_bytes()
    with patch.object(Journal, '_check', revoke):
        try:
            one.invoke('--repair-contracts')
        except SystemExit:
            pass
    assert one.skill.read_bytes() == before


def test_deadline_elapsed_inside_commit_check_prevents_target_write(one):
    original = Journal._check
    clock = [0]
    def expire(journal, record):
        original(journal, record)
        clock[0] = 2
    before = one.skill.read_bytes()
    with patch.object(Journal, '_check', expire), patch.object(one_call.time, 'monotonic', side_effect=lambda: clock[0]):
        one.invoke('--repair-contracts', '--timeout', '1')
    assert one.skill.read_bytes() == before


def test_revocation_after_first_write_restores_transaction_instead_of_partial_success(one):
    from agent_harness_lab.promotion import journal as transaction
    second = one.project/'.agents/skills/zexample/SKILL.md'
    second.parent.mkdir(parents=True)
    second.write_bytes(b'\xef\xbb\xbf'+one.valid.replace(b'name: example', b'name: zexample'))
    before = {p: p.read_bytes() for p in (one.skill, second)}
    replace_file = transaction.os.replace
    def revoke_after_first(source, target):
        replace_file(source, target)
        if Path(target) == one.skill and not (one.project/'ahl.json').exists():
            config.create(one.project, config.defaults())
    with patch.object(transaction.os, 'replace', revoke_after_first):
        try:
            one.invoke('--repair-contracts')
        except SystemExit:
            pass
    assert {p: p.read_bytes() for p in before} == before


def test_interrupt_after_first_write_restores_transaction_and_records_interruption(one):
    from agent_harness_lab.promotion import journal as transaction
    second = one.project/'.agents/skills/zexample/SKILL.md'
    second.parent.mkdir(parents=True)
    second.write_bytes(b'\xef\xbb\xbf'+one.valid.replace(b'name: example', b'name: zexample'))
    before = {p: p.read_bytes() for p in (one.skill, second)}
    replace_file = transaction.os.replace
    interrupted = False
    def interrupt_after_first(source, target):
        nonlocal interrupted
        replace_file(source, target)
        if Path(target) == one.skill and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
    with patch.object(transaction.os, 'replace', interrupt_after_first):
        code, result = one.invoke('--repair-contracts')
    assert result['status'] == 'interrupted'
    assert {p: p.read_bytes() for p in before} == before


def test_canonical_public_rollback_receipt_cannot_be_forged_public(one):
    from agent_harness_lab.cli import main
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    _, applied = one.invoke('--repair-contracts')
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(['rollback', applied['deployment'], '--project', str(one.project)]) == 0
    value = json.loads(output.getvalue())
    forged = dict(value, publication_allowed=True, provenance=dict(source_kind='synthetic',
        approved_publication=True, source_ref='synthetic:fixture', derived_from_private=False))
    assert not public_export_allowed([forged])


def test_canonical_operation_row_cannot_be_forged_public(one):
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    _, applied = one.invoke('--repair-contracts')
    row = json.loads(Path(applied['plan']).read_bytes())['operations'][0]
    forged = dict(row, publication_allowed=True, provenance=dict(source_kind='synthetic',
        approved_publication=True, source_ref='synthetic:fixture', derived_from_private=False))
    assert not public_export_allowed([forged])


def test_interrupt_before_commit_record_persistence_restores_uncommitted_target(one):
    from agent_harness_lab.promotion import journal as transaction
    persist = transaction.atomic_json
    interrupted = False
    def interrupt_commit(path, value):
        nonlocal interrupted
        if value.get('status') == 'committed' and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return persist(path, value)
    before = one.skill.read_bytes()
    with patch.object(transaction, 'atomic_json', interrupt_commit):
        _, result = one.invoke('--repair-contracts')
    assert result['status'] == 'interrupted'
    assert one.skill.read_bytes() == before
