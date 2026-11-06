"""Independent synthetic controls for the first actual target-mutation boundary."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from agent_harness_lab import config, one_call
from agent_harness_lab.promotion.journal import Journal
from tests.integration.workflow import test_one_call as fixtures


class IndependentCommitBoundaryTests(unittest.TestCase):
    def setUp(self):
        fixtures.OneCallTests.setUp(self)

    invoke = fixtures.OneCallTests.invoke

    def test_new_recommendation_only_config_inside_commit_check_prevents_target_write(self):
        original = Journal._check
        def revoke(journal, record):
            original(journal, record)
            config.create(self.project, config.defaults())
        before = self.skill.read_bytes()
        with patch.object(Journal, '_check', revoke):
            try:
                self.invoke('--repair-contracts')
            except SystemExit:
                pass
        self.assertEqual(self.skill.read_bytes(), before)

    def test_deadline_elapsed_inside_commit_check_prevents_target_write(self):
        original = Journal._check
        clock = [0]
        def expire(journal, record):
            original(journal, record)
            clock[0] = 2
        before = self.skill.read_bytes()
        with patch.object(Journal, '_check', expire), patch.object(one_call.time, 'monotonic', side_effect=lambda: clock[0]):
            self.invoke('--repair-contracts', '--timeout', '1')
        self.assertEqual(self.skill.read_bytes(), before)

    def test_revocation_after_first_write_restores_transaction_instead_of_partial_success(self):
        from agent_harness_lab.promotion import journal as transaction
        second = self.project/'.agents/skills/zexample/SKILL.md'
        second.parent.mkdir(parents=True)
        second.write_bytes(b'\xef\xbb\xbf'+self.valid.replace(b'name: example', b'name: zexample'))
        before = {p: p.read_bytes() for p in (self.skill, second)}
        replace_file = transaction.os.replace
        def revoke_after_first(source, target):
            replace_file(source, target)
            if Path(target) == self.skill and not (self.project/'ahl.json').exists():
                config.create(self.project, config.defaults())
        with patch.object(transaction.os, 'replace', revoke_after_first):
            try:
                self.invoke('--repair-contracts')
            except SystemExit:
                pass
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_interrupt_after_first_write_restores_transaction_and_records_interruption(self):
        from agent_harness_lab.promotion import journal as transaction
        second = self.project/'.agents/skills/zexample/SKILL.md'
        second.parent.mkdir(parents=True)
        second.write_bytes(b'\xef\xbb\xbf'+self.valid.replace(b'name: example', b'name: zexample'))
        before = {p: p.read_bytes() for p in (self.skill, second)}
        replace_file = transaction.os.replace
        interrupted = False
        def interrupt_after_first(source, target):
            nonlocal interrupted
            replace_file(source, target)
            if Path(target) == self.skill and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
        with patch.object(transaction.os, 'replace', interrupt_after_first):
            code, result = self.invoke('--repair-contracts')
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_canonical_public_rollback_receipt_cannot_be_forged_public(self):
        from agent_harness_lab.cli import main
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        _, applied = self.invoke('--repair-contracts')
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['rollback', applied['deployment'], '--project', str(self.project)]), 0)
        value = json.loads(output.getvalue())
        forged = dict(value, publication_allowed=True, provenance=dict(source_kind='synthetic',
            approved_publication=True, source_ref='synthetic:fixture', derived_from_private=False))
        self.assertFalse(public_export_allowed([forged]))

    def test_canonical_operation_row_cannot_be_forged_public(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        _, applied = self.invoke('--repair-contracts')
        row = json.loads(Path(applied['plan']).read_bytes())['operations'][0]
        forged = dict(row, publication_allowed=True, provenance=dict(source_kind='synthetic',
            approved_publication=True, source_ref='synthetic:fixture', derived_from_private=False))
        self.assertFalse(public_export_allowed([forged]))

    def test_interrupt_before_commit_record_persistence_restores_uncommitted_target(self):
        from agent_harness_lab.promotion import journal as transaction
        persist = transaction.atomic_json
        interrupted = False
        def interrupt_commit(path, value):
            nonlocal interrupted
            if value.get('status') == 'committed' and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return persist(path, value)
        before = self.skill.read_bytes()
        with patch.object(transaction, 'atomic_json', interrupt_commit):
            _, result = self.invoke('--repair-contracts')
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(self.skill.read_bytes(), before)
