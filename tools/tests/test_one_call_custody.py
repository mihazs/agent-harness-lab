"""Synthetic public repair controls for interruption and last-moment custody."""
from contextlib import redirect_stderr
import io
import json
import multiprocessing
import unittest
from unittest.mock import patch

from agent_harness_lab import config, one_call, workflow
from agent_harness_lab.promotion import journal as transactions
from agent_harness_lab.promotion.journal import Journal
if __package__:
    from . import test_one_call as fixtures
else:
    import test_one_call as fixtures


class OneCallCustodyTests(unittest.TestCase):
    def setUp(self):
        fixtures.OneCallTests.setUp(self)

    invoke = fixtures.OneCallTests.invoke

    def invoke_interrupted(self):
        try:
            return self.invoke('--repair-contracts')
        except KeyboardInterrupt:
            self.fail('Public repair leaked interruption instead of persisting its result')

    def run_record(self):
        paths = list((self.root/'state/runs').rglob('workflow.json'))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_bytes())

    def assert_unapplied(self, status, *, prepared):
        self.assertEqual(self.skill.read_bytes(), b'\xef\xbb\xbf'+self.valid)
        self.assertEqual(self.skill.stat().st_mode & 0o777, 0o640)
        result = self.run_record()
        self.assertEqual(result['status'], status)
        self.assertEqual(result['changed_files'], [])
        records = list((self.root/'state/promotions').rglob('*.json'))
        self.assertEqual(len(records), int(prepared))
        for path in records:
            record = json.loads(path.read_bytes())
            self.assertEqual(record['status'], 'prepared')
            self.assertFalse((path.parent/'active.json').exists())

    def test_configuration_created_during_journal_preparation_prevents_application(self):
        original = Journal.prepare
        def changed(journal, *args, **kwargs):
            record = original(journal, *args, **kwargs)
            settings = config.defaults()
            settings['harness_paths'] = ['.agents/skills/example/SKILL.md']
            config.create(self.project, settings)
            return record
        with patch.object(Journal, 'prepare', changed):
            try:
                code, _ = self.invoke('--repair-contracts')
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 2)
        self.assert_unapplied('invalid_input', prepared=True)

    def test_deadline_elapsed_during_journal_preparation_prevents_application(self):
        original = Journal.prepare
        now = [0]
        def elapsed(journal, *args, **kwargs):
            record = original(journal, *args, **kwargs)
            now[0] = 2
            return record
        with patch.object(one_call.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(Journal, 'prepare', elapsed):
            code, result = self.invoke('--repair-contracts', '--timeout', '1')
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assert_unapplied('budget_exhausted', prepared=True)

    def test_interrupt_after_journal_preparation_is_persisted_without_target_writes(self):
        original = Journal.prepare
        def interrupted(journal, *args, **kwargs):
            original(journal, *args, **kwargs)
            raise KeyboardInterrupt()
        with patch.object(Journal, 'prepare', interrupted):
            code, result = self.invoke_interrupted()
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'interrupted')
        self.assert_unapplied('interrupted', prepared=True)

    def test_interrupt_after_snapshot_is_persisted_without_target_writes(self):
        original = workflow.snapshot
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt()
        with patch.object(workflow, 'snapshot', interrupted):
            code, result = self.invoke_interrupted()
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'interrupted')
        self.assert_unapplied('interrupted', prepared=False)

    def test_forked_public_repair_cannot_overlap_the_parent_transaction(self):
        if 'fork' not in multiprocessing.get_all_start_methods():
            self.skipTest('fork unavailable')
        context = multiprocessing.get_context('fork')
        original = Journal.prepare
        before = self.skill.read_bytes()
        def competing(journal, *args, **kwargs):
            reader, writer = context.Pipe(duplex=False)
            def child():
                reader.close()
                stderr = io.StringIO()
                try:
                    with patch.object(Journal, 'prepare', original), redirect_stderr(stderr):
                        try:
                            code, _ = self.invoke('--repair-contracts')
                        except SystemExit as exc:
                            code = exc.code
                    writer.send(dict(code=code, error=stderr.getvalue(), unchanged=self.skill.read_bytes() == before))
                finally:
                    writer.close()
            process = context.Process(target=child)
            process.start()
            writer.close()
            try:
                process.join(timeout=5)
                self.assertFalse(process.is_alive(), 'Competing public repair did not terminate')
                self.assertEqual(process.exitcode, 0)
                self.assertTrue(reader.poll(1), 'Competing repair returned no result')
                outcome = reader.recv()
                self.assertEqual(outcome['code'], 2)
                self.assertIn('Another run or stale lock exists', outcome['error'])
                self.assertTrue(outcome['unchanged'])
                self.assertEqual(self.skill.read_bytes(), before)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                reader.close()
                process.close()
            return original(journal, *args, **kwargs)
        with patch.object(Journal, 'prepare', competing):
            code, result = self.invoke('--repair-contracts')
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(self.skill.read_bytes(), self.valid)
        self.assertEqual(self.run_record()['status'], 'applied')
        journal = Journal(self.project, result['journal'])
        self.assertEqual(journal.active, result['deployment'])
        self.assertEqual(journal.load(journal.active)['status'], 'committed')
        self.assertEqual(len(list(journal.folder.glob('*.json'))), 2)

    def failed_compensation(self, *, foreign=None, interrupt=False, interrupt_after_restore=False):
        second = self.project/'.agents/skills/zsecond/SKILL.md'
        second.parent.mkdir(parents=True)
        raw = b'\xef\xbb\xbf'+self.valid.replace(b'name: example', b'name: zsecond')
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
                        (self.project/name).write_bytes(foreign)
                    config.create(self.project, config.defaults())
            return result
        with patch.object(Journal, '_write_snapshot', fault):
            try:
                code, result = self.invoke('--repair-contracts')
            except SystemExit as exc:
                code, result = exc.code, self.run_record()
        self.assertEqual(code, 2 if interrupt_after_restore else 1)
        self.assertEqual(second.read_bytes(), raw)
        self.assertEqual(written, ['.agents/skills/example/SKILL.md'])
        self.assertEqual(result['status'], 'invalid_input' if interrupt_after_restore else 'requires_intervention')
        remaining = [] if interrupt_after_restore else written
        self.assertEqual(result['changed_files'], remaining)
        self.assertEqual(self.run_record(), result)
        records = list((self.root/'state/promotions').rglob('*.json'))
        self.assertEqual(len(records), 1)
        journal = Journal(self.project, records[0].parent)
        record = journal.load(records[0].stem)
        if not interrupt_after_restore:
            self.assertEqual(result['journal'], str(journal.folder))
            self.assertEqual(result['deployment'], record['deployment'])
            self.assertEqual(result['restoration'], 'conflict')
        self.assertEqual(record['status'], 'conflict')
        self.assertTrue(record['guard_rejected'])
        self.assertEqual(record['remaining_changes'], remaining)
        self.assertEqual(record['restoration'], 'restored' if interrupt_after_restore else 'conflict')
        self.assertIsNone(journal.active)

    def test_foreign_edit_during_compensation_is_preserved_and_reported(self):
        foreign = b'SYNTHETIC_INTERVENING_EDIT\n'
        self.failed_compensation(foreign=foreign)
        self.assertEqual(self.skill.read_bytes(), foreign)

    def test_interrupt_during_compensation_reports_remaining_target_change(self):
        self.failed_compensation(interrupt=True)
        self.assertEqual(self.skill.read_bytes(), self.valid)

    def test_interrupt_after_compensation_replace_reports_exact_restoration(self):
        self.failed_compensation(interrupt_after_restore=True)
        self.assertEqual(self.skill.read_bytes(), b'\xef\xbb\xbf'+self.valid)

    def interrupted_persistence(self, boundary):
        code, parent = self.invoke('--repair-contracts')
        self.assertEqual(code, 0)
        journal = Journal(self.project, parent['journal'])
        parent_record = journal.folder/(parent['deployment']+'.json')
        parent_bytes = parent_record.read_bytes()
        before = b'\xef\xbb\xbf'+self.valid
        self.skill.write_bytes(before)
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
            code, result = self.invoke_interrupted()
        self.assertEqual(len(interrupted), 1)
        child = interrupted[0]
        record = journal.load(child)
        self.assertEqual(parent_record.read_bytes(), parent_bytes)
        self.assertEqual(self.skill.stat().st_mode & 0o777, 0o640)
        output = self.root/'state/runs'/workflow.identity(self.project)
        runs = [json.loads(path.read_bytes()) for path in output.glob('*/workflow.json')]
        self.assertEqual(len(runs), 2)
        self.assertIn(result, runs)
        if boundary == 'after_pointer':
            self.assertEqual((code, result['status']), (0, 'applied'))
            self.assertEqual(self.skill.read_bytes(), self.valid)
            self.assertEqual(journal.active, child)
            self.assertEqual(record['status'], 'committed')
            self.assertEqual(result['deployment'], child)
        else:
            self.assertEqual((code, result['status']), (1, 'interrupted'))
            self.assertEqual(result['changed_files'], [])
            self.assertEqual(self.skill.read_bytes(), before)
            self.assertEqual(journal.active, parent['deployment'])
            self.assertEqual(record['status'], 'conflict')
            self.assertTrue(record['guard_rejected'])
            self.assertEqual(record['restoration'], 'restored')
            self.assertEqual(record['remaining_changes'], [])

    def test_interrupt_before_committed_record_preserves_existing_parent(self):
        self.interrupted_persistence('before_record')

    def test_interrupt_after_committed_record_preserves_existing_parent(self):
        self.interrupted_persistence('after_record')

    def test_interrupt_after_active_pointer_reports_actual_committed_child(self):
        self.interrupted_persistence('after_pointer')
