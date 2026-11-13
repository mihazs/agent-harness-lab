"""Synthetic public repair controls for interruption and last-moment custody."""
from contextlib import redirect_stderr
import io
import json
import multiprocessing
import os
import signal
import time
from unittest.mock import patch

import pytest

from agent_harness_lab import config, one_call, workflow
from agent_harness_lab.promotion import journal as transactions
from agent_harness_lab.promotion.journal import Journal
from tests.support.one_call import one as one




def test_forked_inherited_context_cannot_release_the_live_parent_lock(one):
    if not hasattr(os,'fork'):
        pytest.skip('fork unavailable')
    path=one.root/'state/locks'/(workflow.identity(one.project)+'.lock')
    child=False
    pid=None
    reaped=False
    try:
        with workflow.project_lock(one.project):
            token=path.read_bytes()
            pid=os.fork()
            if pid==0:
                child=True
            else:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    waited,status=os.waitpid(pid,os.WNOHANG)
                    if waited:
                        reaped=True
                        assert status == 0
                        break
                    time.sleep(.01)
                assert reaped, 'Inherited child did not terminate'
                assert path.read_bytes() == token
                with pytest.raises(ValueError):
                    with workflow.project_lock(one.project): pass
        if child: os._exit(0)
        assert not path.exists()
        with workflow.project_lock(one.project):
            assert path.read_bytes() != token
    finally:
        if child: os._exit(90)
        if pid and not reaped:
            try: os.kill(pid,signal.SIGKILL)
            except ProcessLookupError: pass
            os.waitpid(pid,0)


def invoke_interrupted(one):
    try:
        return one.invoke('--repair-contracts')
    except KeyboardInterrupt:
        pytest.fail('Public repair leaked interruption instead of persisting its result')


def run_record(one):
    paths = list((one.root/'state/runs').rglob('workflow.json'))
    assert len(paths) == 1
    return json.loads(paths[0].read_bytes())


def assert_unapplied(one, status, *, prepared):
    assert one.skill.read_bytes() == b'\xef\xbb\xbf'+one.valid
    assert one.skill.stat().st_mode & 0o777 == 0o640
    result = run_record(one)
    assert result['status'] == status
    assert result['changed_files'] == []
    records = list((one.root/'state/promotions').rglob('*.json'))
    assert len(records) == int(prepared)
    for path in records:
        record = json.loads(path.read_bytes())
        assert record['status'] == 'prepared'
        assert not (path.parent/'active.json').exists()


def test_configuration_created_during_journal_preparation_prevents_application(one):
    original = Journal.prepare
    def changed(journal, *args, **kwargs):
        record = original(journal, *args, **kwargs)
        settings = config.defaults()
        settings['harness_paths'] = ['.agents/skills/example/SKILL.md']
        config.create(one.project, settings)
        return record
    with patch.object(Journal, 'prepare', changed):
        try:
            code, _ = one.invoke('--repair-contracts')
        except SystemExit as exc:
            code = exc.code
    assert code == 2
    assert_unapplied(one, 'invalid_input', prepared=True)


def test_deadline_elapsed_during_journal_preparation_prevents_application(one):
    original = Journal.prepare
    now = [0]
    def elapsed(journal, *args, **kwargs):
        record = original(journal, *args, **kwargs)
        now[0] = 2
        return record
    with patch.object(one_call.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(Journal, 'prepare', elapsed):
        code, result = one.invoke('--repair-contracts', '--timeout', '1')
    assert code == 1
    assert result['status'] == 'budget_exhausted'
    assert_unapplied(one, 'budget_exhausted', prepared=True)


def test_interrupt_after_journal_preparation_is_persisted_without_target_writes(one):
    original = Journal.prepare
    def interrupted(journal, *args, **kwargs):
        original(journal, *args, **kwargs)
        raise KeyboardInterrupt()
    with patch.object(Journal, 'prepare', interrupted):
        code, result = invoke_interrupted(one)
    assert code == 1
    assert result['status'] == 'interrupted'
    assert_unapplied(one, 'interrupted', prepared=True)


def test_interrupt_after_snapshot_is_persisted_without_target_writes(one):
    original = workflow.snapshot
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt()
    with patch.object(workflow, 'snapshot', interrupted):
        code, result = invoke_interrupted(one)
    assert code == 1
    assert result['status'] == 'interrupted'
    assert_unapplied(one, 'interrupted', prepared=False)


def test_forked_public_repair_cannot_overlap_the_parent_transaction(one):
    if 'fork' not in multiprocessing.get_all_start_methods():
        pytest.skip('fork unavailable')
    context = multiprocessing.get_context('fork')
    original = Journal.prepare
    before = one.skill.read_bytes()
    def competing(journal, *args, **kwargs):
        reader, writer = context.Pipe(duplex=False)
        def child():
            reader.close()
            stderr = io.StringIO()
            try:
                with patch.object(Journal, 'prepare', original), redirect_stderr(stderr):
                    try:
                        code, _ = one.invoke('--repair-contracts')
                    except SystemExit as exc:
                        code = exc.code
                writer.send(dict(code=code, error=stderr.getvalue(), unchanged=one.skill.read_bytes() == before))
            finally:
                writer.close()
        process = context.Process(target=child)
        process.start()
        writer.close()
        try:
            process.join(timeout=5)
            assert not process.is_alive(), 'Competing public repair did not terminate'
            assert process.exitcode == 0
            assert reader.poll(1), 'Competing repair returned no result'
            outcome = reader.recv()
            assert outcome['code'] == 1  # a busy workspace is a conflict, not a usage error (#837)
            assert 'Another run or stale lock exists' in outcome['error']
            assert outcome['unchanged']
            assert one.skill.read_bytes() == before
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            reader.close()
            process.close()
        return original(journal, *args, **kwargs)
    with patch.object(Journal, 'prepare', competing):
        code, result = one.invoke('--repair-contracts')
    assert code == 0
    assert result['status'] == 'applied'
    assert one.skill.read_bytes() == one.valid
    assert run_record(one)['status'] == 'applied'
    journal = Journal(one.project, result['journal'])
    assert journal.active == result['deployment']
    assert journal.load(journal.active)['status'] == 'committed'
    assert len(list(journal.folder.glob('*.json'))) == 2


def failed_compensation(one, *, foreign=None, interrupt=False, interrupt_after_restore=False):
    second = one.project/'.agents/skills/zsecond/SKILL.md'
    second.parent.mkdir(parents=True)
    raw = b'\xef\xbb\xbf'+one.valid.replace(b'name: example', b'name: zsecond')
    second.write_bytes(raw)
    original = Journal._write_snapshot
    written = []
    def fault(journal, name, current, target, pre_write=None):
        if pre_write is None and interrupt:
            raise KeyboardInterrupt()
        result = original(journal, name, current, target, pre_write)
        if pre_write is None and interrupt_after_restore:
            raise KeyboardInterrupt()
        if pre_write is not None:
            written.append(name)
            if len(written) == 1:
                if foreign is not None:
                    (one.project/name).write_bytes(foreign)
                config.create(one.project, config.defaults())
        return result
    with patch.object(Journal, '_write_snapshot', fault):
        try:
            code, result = one.invoke('--repair-contracts')
        except SystemExit as exc:
            code, result = exc.code, run_record(one)
    assert code == (2 if interrupt_after_restore else 1)
    assert second.read_bytes() == raw
    assert written == ['.agents/skills/example/SKILL.md']
    assert result['status'] == ('invalid_input' if interrupt_after_restore else 'requires_intervention')
    remaining = [] if interrupt_after_restore else written
    assert result['changed_files'] == remaining
    assert run_record(one) == result
    records = list((one.root/'state/promotions').rglob('*.json'))
    assert len(records) == 1
    journal = Journal(one.project, records[0].parent)
    record = journal.load(records[0].stem)
    if not interrupt_after_restore:
        assert result['journal'] == str(journal.folder)
        assert result['deployment'] == record['deployment']
        assert result['restoration'] == 'conflict'
    assert record['status'] == 'conflict'
    assert record['guard_rejected']
    assert record['remaining_changes'] == remaining
    assert record['restoration'] == ('restored' if interrupt_after_restore else 'conflict')
    assert journal.active is None


def test_foreign_edit_during_compensation_is_preserved_and_reported(one):
    foreign = b'SYNTHETIC_INTERVENING_EDIT\n'
    failed_compensation(one, foreign=foreign)
    assert one.skill.read_bytes() == foreign


def test_interrupt_during_compensation_reports_remaining_target_change(one):
    failed_compensation(one, interrupt=True)
    assert one.skill.read_bytes() == one.valid


def test_interrupt_after_compensation_replace_reports_exact_restoration(one):
    failed_compensation(one, interrupt_after_restore=True)
    assert one.skill.read_bytes() == b'\xef\xbb\xbf'+one.valid


def interrupted_persistence(one, boundary):
    code, parent = one.invoke('--repair-contracts')
    assert code == 0
    journal = Journal(one.project, parent['journal'])
    parent_record = journal.folder/(parent['deployment']+'.json')
    parent_bytes = parent_record.read_bytes()
    before = b'\xef\xbb\xbf'+one.valid
    one.skill.write_bytes(before)
    original = transactions.atomic_json
    interrupted = []
    def persist(path, value):
        child = value.get('deployment') != parent['deployment']
        record = child and value.get('status') == 'committed'
        pointer = child and path.name == 'active.json'
        selected = pointer if boundary == 'after_pointer' else record
        if selected:
            interrupted.append(value['deployment'])
            if boundary != 'before_record':
                original(path, value)
            raise KeyboardInterrupt()
        return original(path, value)
    with patch.object(transactions, 'atomic_json', persist):
        code, result = invoke_interrupted(one)
    assert len(interrupted) == 1
    child = interrupted[0]
    record = journal.load(child)
    assert parent_record.read_bytes() == parent_bytes
    assert one.skill.stat().st_mode & 0o777 == 0o640
    output = one.root/'state/runs'/workflow.identity(one.project)
    runs = [json.loads(path.read_bytes()) for path in output.glob('*/workflow.json')]
    assert len(runs) == 2
    assert result in runs
    if boundary == 'after_pointer':
        assert (code, result['status']) == (0, 'applied')
        assert one.skill.read_bytes() == one.valid
        assert journal.active == child
        assert record['status'] == 'committed'
        assert result['deployment'] == child
    else:
        assert (code, result['status']) == (1, 'interrupted')
        assert result['changed_files'] == []
        assert one.skill.read_bytes() == before
        assert journal.active == parent['deployment']
        assert record['status'] == 'conflict'
        assert record['guard_rejected']
        assert record['restoration'] == 'restored'
        assert record['remaining_changes'] == []


def test_interrupt_before_committed_record_preserves_existing_parent(one):
    interrupted_persistence(one, 'before_record')


def test_interrupt_after_committed_record_preserves_existing_parent(one):
    interrupted_persistence(one, 'after_record')


def test_interrupt_after_active_pointer_reports_actual_committed_child(one):
    interrupted_persistence(one, 'after_pointer')
