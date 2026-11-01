"""Independent synthetic content controls; similarity never proves causal lineage."""
import unittest
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate
if __package__:
    from . import test_evaluation_foundation as foundation
else:
    import test_evaluation_foundation as foundation


class BankContentGroupingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.TaskBankTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_whitespace_copy_with_distinct_root_and_fixture_hashes_blocks_certification(self):
        f = self.fixture
        for index, content in enumerate(('do not expose SECRET_7', 'do  not\nexpose\tSECRET_7')):
            folder = f.root/f'fixture{index}'; (folder/'value').write_text(content)
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write()
        self.assertNotEqual(f.tasks[0]['fixture_digest'], f.tasks[1]['fixture_digest'])
        self.assertFalse(validate(f.path)['certification_eligible'])

    def test_shared_prompt_with_different_inputs_is_exposure_without_lineage_merge(self):
        import json
        f = self.fixture
        for index in range(2):
            folder = f.root/f'fixture{index}'
            (folder/'task.json').write_text(json.dumps(dict(prompt='Use the declared independent inputs.', inputs=[index])))
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write(); result = validate(f.path)
        self.assertFalse(result['certification_eligible'])
        grouping = result['content_grouping']
        self.assertEqual(grouping['overlaps'], 1); self.assertEqual(grouping['receipts'][0]['kind'], 'prompt')
        self.assertIsNone(grouping['independent_roots']); self.assertFalse(grouping['lineage_verified'])
        self.assertFalse(grouping['publication_allowed'])
        self.assertNotIn('Use the declared', json.dumps(result))

    def test_unrelated_negated_ordered_or_distinct_identifiers_remain_distinct(self):
        f = self.fixture
        for left, right in (('allow data', 'do not allow data'), ('step_a step_b', 'step_b step_a'), ('SECRET_7', 'SECRET_8')):
            for index, content in enumerate((left, right)):
                folder = f.root/f'fixture{index}'; (folder/'value').write_text(content)
                f.tasks[index]['fixture_digest'] = digest(folder)
            f.write(); result = validate(f.path)
            self.assertTrue(result['certification_eligible']); self.assertEqual(result['content_grouping']['overlaps'], 0)

    def test_revisions_change_with_source_and_policy_and_old_receipts_are_not_reused(self):
        f = self.fixture; f.write()
        first = validate(f.path)['content_grouping']
        (f.root/'fixture0/value').write_text('a new observed revision')
        with self.assertRaisesRegex(ValueError, 'integrity'): validate(f.path)
        f.tasks[0]['fixture_digest'] = digest(f.root/'fixture0'); f.write()
        second = validate(f.path)['content_grouping']
        self.assertNotEqual(first['revision_sha256'], second['revision_sha256'])
        f.tasks[0]['partition'] = 'construction'; f.write()
        self.assertNotEqual(second['revision_sha256'], validate(f.path)['content_grouping']['revision_sha256'])

    def test_metadata_only_validation_never_reads_or_fingerprints_fixture_contents(self):
        from unittest.mock import patch
        f = self.fixture; f.write()
        with patch('agent_harness_lab.evaluation.content_grouping.inspect_bank', side_effect=AssertionError('unexpected content read')):
            result = validate(f.path, verify_contents=False)
        self.assertIsNone(result['content_grouping']); self.assertFalse(result['certification_eligible'])

    def test_binary_data_remains_exact_and_resource_bounds_fail_closed(self):
        from unittest.mock import patch
        f = self.fixture
        for index in range(2):
            folder = f.root/f'fixture{index}'; (folder/'value').write_bytes(bytes([255-index]))
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write(); self.assertTrue(validate(f.path)['certification_eligible'])
        with patch('agent_harness_lab.evaluation.content_grouping.MAX_BANK_BYTES', 1):
            with self.assertRaises(ValueError): validate(f.path)
        with patch('agent_harness_lab.evaluation.content_grouping.digest', return_value='a'*64):
            with self.assertRaisesRegex(ValueError, 'changed'): validate(f.path)

    def test_receipt_output_is_bounded_without_dropping_exposure_counts(self):
        from unittest.mock import patch
        f = self.fixture
        (f.root/'fixture1/value').write_text(' 0\n')
        f.tasks[1]['fixture_digest'] = digest(f.root/'fixture1'); f.write()
        with patch('agent_harness_lab.evaluation.content_grouping.MAX_RECEIPTS', 0):
            result = validate(f.path)
        grouping = result['content_grouping']
        self.assertEqual(grouping['overlaps'], 1); self.assertTrue(grouping['receipts_truncated'])
        self.assertEqual(grouping['receipts'], []); self.assertFalse(result['certification_eligible'])

    def test_export_freezes_the_computed_grouping_revision(self):
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        from agent_harness_lab.config import starter
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            bank = starter()
            result = export_manifest(bank, Path(folder)/'export')
        self.assertEqual(result['grouping_revision_sha256'], validate(bank)['content_grouping']['revision_sha256'])


    def test_json_key_formatting_and_file_modes_do_not_hide_copies_but_array_order_remains(self):
        f = self.fixture
        for index, raw in enumerate(('{"alpha":1,"beta":[2,3]}', '{ "beta" : [2, 3], "alpha" : 1 }')):
            folder = f.root/f'fixture{index}'; (folder/'value').unlink()
            path = folder/'data.json'; path.write_text(raw); path.chmod(0o600 if index else 0o644)
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write(); self.assertFalse(validate(f.path)['certification_eligible'])
        (f.root/'fixture1/data.json').write_text('{"alpha":1,"beta":[3,2]}')
        f.tasks[1]['fixture_digest'] = digest(f.root/'fixture1'); f.write()
        self.assertTrue(validate(f.path)['certification_eligible'])


    def test_ambiguous_json_cannot_hide_the_effective_prompt_from_exposure_checks(self):
        f = self.fixture
        for index in range(2):
            folder = f.root/f'fixture{index}'
            (folder/'task.json').write_text('{"prompt":"decoy-'+str(index)+'","prompt":"same effective exposed task","inputs":'+str(index)+'}')
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write()
        with self.assertRaises(ValueError): validate(f.path)


    def test_invalid_task_json_cannot_turn_missing_prompt_visibility_into_certification(self):
        f = self.fixture; folder = f.root/'fixture0'
        for raw in (b'{"prompt":NaN}', b'[]', b'{"inputs":[]}', b'{"prompt":" "}', b'\xff', b'{broken'):
            with self.subTest(raw=raw):
                (folder/'task.json').write_bytes(raw)
                f.tasks[0]['fixture_digest'] = digest(folder); f.write()
                with self.assertRaises(ValueError): validate(f.path)


class TextGroupingTests(unittest.TestCase):
    def test_normalization_preserves_negation_identifiers_punctuation_case_and_order(self):
        from agent_harness_lab.sources.grouping import text_fingerprint
        base = text_fingerprint('do not expose SECRET_7!')
        self.assertEqual(base.normalized_sha256, text_fingerprint('do  not\nexpose\tSECRET_7 !').normalized_sha256)
        for other in ('do expose SECRET_7!', 'do not expose SECRET_8!', 'do not expose secret_7!', 'expose not do SECRET_7!', 'do not expose SECRET_7?'):
            self.assertNotEqual(base.normalized_sha256, text_fingerprint(other).normalized_sha256)
        self.assertNotEqual(text_fingerprint('foo bar').normalized_sha256, text_fingerprint('foobar').normalized_sha256)
        self.assertEqual(text_fingerprint('café').normalized_sha256, text_fingerprint('cafe\u0301').normalized_sha256)

    def test_receipt_roundtrip_and_revision_binding_never_establish_independence(self):
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.grouping import GroupingReceipt, compare, text_fingerprint
        left, right = RecordRef('a'*64,'b'*64,'c'*64), RecordRef('a'*64,'d'*64,'e'*64)
        content = text_fingerprint('same observed words')
        receipt = compare(left, right, content, text_fingerprint('same  observed\nwords'))
        self.assertEqual(GroupingReceipt.parse(receipt.to_dict()), receipt)
        self.assertEqual(receipt.evidence_class, 'heuristic_candidate'); self.assertIsNone(receipt.lineage_group_sha256)
        self.assertEqual(receipt.reviewer_decision, 'pending'); self.assertEqual(len(receipt.unresolved_collisions), 2)
        different = compare(left, right, content, text_fingerprint('not the same observation'))
        self.assertEqual(different.evidence_class, 'unlinked')
        revised = compare(left, RecordRef('a'*64,'d'*64,'f'*64), content, content, invalidation_history=(receipt.sha256,))
        self.assertNotEqual(receipt.sha256, revised.sha256); self.assertEqual(revised.invalidation_history, (receipt.sha256,))

    def test_forged_group_decision_algorithm_and_unbounded_metadata_are_rejected(self):
        from dataclasses import replace
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.grouping import GroupingReceipt, compare, text_fingerprint
        left, right = RecordRef('a'*64,'b'*64,'c'*64), RecordRef('a'*64,'d'*64,'e'*64)
        content = text_fingerprint('words'); receipt = compare(left, right, content, content)
        for fields in (dict(lineage_group_sha256='f'*64), dict(reviewer_decision='independent_case'),
                       dict(evidence_class='verified_native_id'), dict(content_relation='different'),
                       dict(invalidation_history=('f'*64,)*2), dict(invalidation_history=tuple(str(i).zfill(64) for i in range(65)))):
            with self.assertRaises(ValueError): replace(receipt, **fields)
        value = receipt.to_dict(); value['left_content']['algorithm'] = 'unknown-v2'
        with self.assertRaises(ValueError): GroupingReceipt.parse(value)
        with self.assertRaises(ValueError): compare(left, left, content, content)
        for invalid in (None, b'bytes', '\ud800', 'x'*(1024*1024+1), 'x '*100001):
            with self.assertRaises(ValueError): text_fingerprint(invalid)

    def test_fingerprint_algorithm_changes_require_new_comparisons(self):
        from dataclasses import replace
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.grouping import FIXTURE_ALGORITHM, compare, text_fingerprint
        content = text_fingerprint('same tokens')
        with self.assertRaisesRegex(ValueError, 'algorithms'):
            compare(RecordRef('a'*64,'b'*64,'c'*64), RecordRef('a'*64,'d'*64,'e'*64),
                content, replace(content, algorithm=FIXTURE_ALGORITHM))
