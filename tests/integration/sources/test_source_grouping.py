"""Independent synthetic content controls; similarity never proves causal lineage."""
from contextlib import ExitStack

import pytest

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate
from tests.integration.evaluation import test_evaluation_foundation as foundation


class TestBankContentGrouping:
    @pytest.fixture(autouse=True)
    def _bank(self):
        with ExitStack() as cleanups:
            self.fixture = foundation.TaskBank(cleanups.callback)
            yield

    def test_whitespace_copy_with_distinct_root_and_fixture_hashes_blocks_certification(self):
        f = self.fixture
        for index, content in enumerate(('do not expose SECRET_7', 'do  not\nexpose\tSECRET_7')):
            folder = f.root/f'fixture{index}'; (folder/'value').write_text(content)
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write()
        assert f.tasks[0]['fixture_digest'] != f.tasks[1]['fixture_digest']
        checked = validate(f.path)
        assert not checked['certification_eligible']
        assert checked['content_grouping']['overlaps'] == 1
        assert checked['issues'][0]['status'] == 'content_overlap_unreviewed'

    def test_shared_prompt_with_different_inputs_is_exposure_without_lineage_merge(self):
        import json
        f = self.fixture
        for index in range(2):
            folder = f.root/f'fixture{index}'
            (folder/'task.json').write_text(json.dumps(dict(prompt='Use the declared independent inputs.', inputs=[index])))
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write(); result = validate(f.path)
        assert not result['certification_eligible']
        grouping = result['content_grouping']
        assert grouping['overlaps'] == 1; assert grouping['receipts'][0]['kind'] == 'prompt'
        assert grouping['independent_roots'] is None; assert not grouping['lineage_verified']
        assert not grouping['publication_allowed']
        assert 'Use the declared' not in json.dumps(result)

    @pytest.mark.parametrize('left,right', [pytest.param('allow data', 'do not allow data', id='negated'),
                                            pytest.param('step_a step_b', 'step_b step_a', id='ordered'),
                                            pytest.param('SECRET_7', 'SECRET_8', id='distinct-identifier')])
    def test_unrelated_negated_ordered_or_distinct_identifiers_remain_distinct(self, left, right):
        f = self.fixture
        for index, content in enumerate((left, right)):
            folder = f.root/f'fixture{index}'; (folder/'value').write_text(content)
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write(); result = validate(f.path)
        assert not result['certification_eligible']; assert result['review_declarations_complete']; assert result['content_grouping']['overlaps'] == 0

    def test_revisions_change_with_source_and_policy_and_old_receipts_are_not_reused(self):
        f = self.fixture; f.write()
        first = validate(f.path)['content_grouping']
        (f.root/'fixture0/value').write_text('a new observed revision')
        with pytest.raises(ValueError, match='integrity'): validate(f.path)
        f.tasks[0]['fixture_digest'] = digest(f.root/'fixture0'); f.write()
        second = validate(f.path)['content_grouping']
        assert first['revision_sha256'] != second['revision_sha256']
        f.tasks[0]['partition'] = 'construction'; f.write()
        assert second['revision_sha256'] != validate(f.path)['content_grouping']['revision_sha256']

    def test_metadata_only_validation_never_reads_or_fingerprints_fixture_contents(self):
        from unittest.mock import patch
        f = self.fixture; f.write()
        with patch('agent_harness_lab.evaluation.content_grouping.inspect_bank', side_effect=AssertionError('unexpected content read')):
            result = validate(f.path, verify_contents=False)
        assert result['content_grouping'] is None; assert not result['certification_eligible']

    def test_binary_data_remains_exact_and_explicit_resource_bounds_fail_closed(self):
        from unittest.mock import patch
        f = self.fixture
        for index in range(2):
            folder = f.root/f'fixture{index}'; (folder/'value').write_bytes(bytes([255-index]))
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write(); checked = validate(f.path)
        assert checked['review_declarations_complete']
        assert not checked['certification_eligible']
        assert checked['content_grouping']['overlaps'] == 0
        assert checked['issues'] == []
        from agent_harness_lab.evaluation.content_grouping import fixture_content
        with pytest.raises(ValueError):
            fixture_content(f.root/'fixture0', f.tasks[0]['fixture_digest'], 0)
        with patch('agent_harness_lab.evaluation.content_grouping.digest', return_value='a'*64):
            with pytest.raises(ValueError, match='changed'): validate(f.path)

    def test_receipt_output_keeps_every_exposure(self):
        f = self.fixture
        (f.root/'fixture1/value').write_text(' 0\n')
        f.tasks[1]['fixture_digest'] = digest(f.root/'fixture1'); f.write()
        result = validate(f.path)
        grouping = result['content_grouping']
        assert grouping['overlaps'] == 1; assert not grouping['receipts_truncated']
        assert len(grouping['receipts']) == grouping['overlaps']; assert not result['certification_eligible']

    def test_export_freezes_the_computed_grouping_revision(self, tmp_path):
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        from agent_harness_lab.config import starter
        bank = starter()
        result = export_manifest(bank, tmp_path/'export')
        assert result['grouping_revision_sha256'] == validate(bank)['content_grouping']['revision_sha256']


    def test_json_key_formatting_and_file_modes_do_not_hide_copies_but_array_order_remains(self):
        f = self.fixture
        for index, raw in enumerate(('{"alpha":1,"beta":[2,3]}', '{ "beta" : [2, 3], "alpha" : 1 }')):
            folder = f.root/f'fixture{index}'; (folder/'value').unlink()
            path = folder/'data.json'; path.write_text(raw); path.chmod(0o600 if index else 0o644)
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write(); checked = validate(f.path)
        assert not checked['certification_eligible']
        assert checked['content_grouping']['overlaps'] == 1
        (f.root/'fixture1/data.json').write_text('{"alpha":1,"beta":[3,2]}')
        f.tasks[1]['fixture_digest'] = digest(f.root/'fixture1'); f.write()
        checked = validate(f.path)
        assert checked['review_declarations_complete']
        assert not checked['certification_eligible']
        assert checked['content_grouping']['overlaps'] == 0
        assert checked['issues'] == []


    def test_ambiguous_json_cannot_hide_the_effective_prompt_from_exposure_checks(self):
        f = self.fixture
        for index in range(2):
            folder = f.root/f'fixture{index}'
            (folder/'task.json').write_text('{"prompt":"decoy-'+str(index)+'","prompt":"same effective exposed task","inputs":'+str(index)+'}')
            f.tasks[index]['fixture_digest'] = digest(folder)
        f.write()
        with pytest.raises(ValueError): validate(f.path)


    @pytest.mark.parametrize('raw', [
        pytest.param(b'{"prompt":NaN}', id='nan-prompt'), pytest.param(b'[]', id='array'), pytest.param(b'{"inputs":[]}', id='no-prompt'),
        pytest.param(b'{"prompt":" "}', id='blank-prompt'), pytest.param(b'\xff', id='invalid-utf8'), pytest.param(b'{broken', id='broken')])
    def test_invalid_task_json_cannot_turn_missing_prompt_visibility_into_certification(self, raw):
        f = self.fixture; folder = f.root/'fixture0'
        (folder/'task.json').write_bytes(raw)
        f.tasks[0]['fixture_digest'] = digest(folder); f.write()
        with pytest.raises(ValueError): validate(f.path)


class TestTextGrouping:
    def test_normalization_preserves_negation_identifiers_punctuation_case_and_order(self):
        from agent_harness_lab.sources.grouping import text_fingerprint
        base = text_fingerprint('do not expose SECRET_7!')
        assert base.normalized_sha256 == text_fingerprint('do  not\nexpose\tSECRET_7 !').normalized_sha256
        for other in ('do expose SECRET_7!', 'do not expose SECRET_8!', 'do not expose secret_7!', 'expose not do SECRET_7!', 'do not expose SECRET_7?'):
            assert base.normalized_sha256 != text_fingerprint(other).normalized_sha256
        assert text_fingerprint('foo bar').normalized_sha256 != text_fingerprint('foobar').normalized_sha256
        assert text_fingerprint('café').normalized_sha256 == text_fingerprint('cafe\u0301').normalized_sha256

    def test_receipt_roundtrip_and_revision_binding_never_establish_independence(self):
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.grouping import GroupingReceipt, compare, text_fingerprint
        left, right = RecordRef('a'*64,'b'*64,'c'*64), RecordRef('a'*64,'d'*64,'e'*64)
        content = text_fingerprint('same observed words')
        receipt = compare(left, right, content, text_fingerprint('same  observed\nwords'))
        assert GroupingReceipt.parse(receipt.to_dict()) == receipt
        assert receipt.evidence_class == 'heuristic_candidate'; assert receipt.lineage_group_sha256 is None
        assert receipt.reviewer_decision == 'pending'; assert len(receipt.unresolved_collisions) == 2
        different = compare(left, right, content, text_fingerprint('not the same observation'))
        assert different.evidence_class == 'unlinked'
        revised = compare(left, RecordRef('a'*64,'d'*64,'f'*64), content, content, invalidation_history=(receipt.sha256,))
        assert receipt.sha256 != revised.sha256; assert revised.invalidation_history == (receipt.sha256,)

    def test_forged_group_decision_algorithm_and_unbounded_metadata_are_rejected(self):
        from dataclasses import replace
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.grouping import GroupingReceipt, compare, text_fingerprint
        left, right = RecordRef('a'*64,'b'*64,'c'*64), RecordRef('a'*64,'d'*64,'e'*64)
        content = text_fingerprint('words'); receipt = compare(left, right, content, content)
        for fields in (dict(lineage_group_sha256='f'*64), dict(reviewer_decision='independent_case'),
                       dict(evidence_class='verified_native_id'), dict(content_relation='different'),
                       dict(invalidation_history=('f'*64,)*2)):
            with pytest.raises(ValueError): replace(receipt, **fields)
        value = receipt.to_dict(); value['left_content']['algorithm'] = 'unknown-v2'
        with pytest.raises(ValueError): GroupingReceipt.parse(value)
        with pytest.raises(ValueError): compare(left, left, content, content)
        for invalid in (None, b'bytes', '\ud800'):
            with pytest.raises(ValueError): text_fingerprint(invalid)

    def test_fingerprint_algorithm_changes_require_new_comparisons(self):
        from dataclasses import replace
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.grouping import FIXTURE_ALGORITHM, compare, text_fingerprint
        content = text_fingerprint('same tokens')
        with pytest.raises(ValueError, match='algorithms'):
            compare(RecordRef('a'*64,'b'*64,'c'*64), RecordRef('a'*64,'d'*64,'e'*64),
                content, replace(content, algorithm=FIXTURE_ALGORITHM))


class TestUnlimitedFingerprint:
    def test_large_text_and_complete_history_preserve_exact_and_normalized_hashes(self):
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.grouping import GroupingReceipt, compare, text_fingerprint
        text = 'x ' * 524289
        content = text_fingerprint(text)
        assert content.tokens == 524289
        history = tuple(str(i).zfill(64) for i in range(65))
        receipt = compare(RecordRef('a'*64,'b'*64,'c'*64), RecordRef('a'*64,'d'*64,'e'*64),
            content, content, invalidation_history=history)
        assert GroupingReceipt.parse(receipt.to_dict()) == receipt
        assert receipt.content_relation == 'exact'


class TestCompleteExposure:
    def test_fixture_content_has_no_implicit_tree_or_file_byte_ceiling(self, tmp_path):
        from agent_harness_lab.evaluation.content_grouping import fixture_content
        limits = dict(max_files=None, max_bytes=None, max_entries=None, max_depth=None)
        root = tmp_path
        for index in range(1001): (root/str(index)).write_bytes(b'')
        raw = b'x'*(11*1024*1024)
        (root/'large').write_bytes(raw)
        pin = digest(root, **limits)
        content, prompt, used = fixture_content(root, pin)
        assert content.exact_sha256 == pin
        assert content.tokens == 1
        assert prompt is None
        assert used == len(raw)
        with pytest.raises(ValueError): fixture_content(root, pin, 1)

    def test_all_overlap_receipts_survive_past_the_old_receipt_ceiling(self, tmp_path):
        from types import SimpleNamespace
        from agent_harness_lab.evaluation.content_grouping import inspect_bank
        root = tmp_path; fixture = root/'fixture'; fixture.mkdir()
        (fixture/'value').write_text('shared synthetic content')
        pin = digest(fixture)
        tasks = [SimpleNamespace(task_id=str(i), fixture='fixture', fixture_digest=pin,
            partition='development') for i in range(130)]
        report, issues = inspect_bank(root, tasks, 'a'*64)
        assert report['overlaps'] == 129
        assert len(report['receipts']) == 129
        assert len(issues) == 129
        assert not report['receipts_truncated']
        assert report['independent_roots'] is None
