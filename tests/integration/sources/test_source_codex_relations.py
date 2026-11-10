"""Synthetic native relationships: exact file receipts do not certify execution."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.integration.sources import test_source_codex as fixtures
from agent_harness_lab.sources.resolve import recheck
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.snapshot import LocalExport


class TestNativeRelation:
    @pytest.fixture(autouse=True)
    def _journal(self, tmp_path):
        self.fixture = fixtures.NativeSourceTests(); self.fixture.setUp(tmp_path)

    def snapshot(self): return self.fixture.reader().read(self.fixture.source)

    def test_cli_native_relationship_recheck_is_read_only_and_keeps_execution_unknown(self):
        f = self.fixture
        assert f.invoke('preview')[1]['evidence_graph']['joins'] == 6
        assert f.invoke('import')[0] == 0; before = f.cache.read_bytes(); original = f.capture.read_bytes()
        code, report = f.invoke('recheck')
        assert code == 0; assert report['statuses']['verified'] == 6
        assert not report['network_used']; assert not report['task_outcomes_verified']
        assert report['independent_roots'] is None; assert not report['historical_interval_verified']
        assert f.cache.read_bytes() == before; assert f.capture.read_bytes() == original
        assert f.invoke('status')[1]['evidence_graph']['statuses']['verified'] == 0
        assert all(field.status == 'declared' for identity in f.store().capture(f.source).identities for field in identity.fields)

    def test_only_native_members_and_one_unambiguous_tool_pair_get_candidates(self):
        snapshot = self.snapshot(); graph = snapshot.graph
        assert all(j.status == 'candidate' and j.basis == 'native_fields' for j in graph.joins)
        assert sum(j.relation == 'native_session_member' for j in graph.joins) == 5
        pair = [j for j in graph.joins if j.relation == 'tool_result_of']; assert len(pair) == 1
        kinds = {(r.source_sha256, r.native_id_sha256, r.revision_sha256): r.record_type for r in snapshot.records}
        assert kinds[pair[0].left.key] == 'codex_tool_result'; assert kinds[pair[0].right.key] == 'codex_tool_call'
        assert set(pair[0].evidence) == {pair[0].left, pair[0].right}

    def test_duplicate_calls_results_and_result_before_call_cannot_prove_a_pair(self):
        f = self.fixture; original = list(f.rows)
        for changed in ([*original, f.fixture.row('response_item', dict(type='function_call', name='exec_command', call_id='SYNTHETIC_SECRET'), 6)],
                [*original, f.fixture.row('response_item', dict(type='function_call_output', call_id='SYNTHETIC_SECRET'), 6)],
                [original[0], original[1], {**original[4], 'ordinal': 2}, {**original[2], 'ordinal': 3}]):
            f.rows = changed; f.write(); snapshot = self.snapshot()
            assert not any(j.relation == 'tool_result_of' for j in snapshot.graph.joins)
            report = recheck(f.source, f.reader(), snapshot.graph)
            assert all(r['receipt']['relation'] != 'tool_result_of' for r in report['results'])

    def test_later_duplicate_native_id_removes_proof_for_previously_unique_pair(self):
        f = self.fixture; assert f.invoke('import')[0] == 0; before = f.cache.read_bytes()
        f.rows.append(f.fixture.row('response_item', dict(type='function_call', name='exec_command', call_id='SYNTHETIC_SECRET'), 6))
        f.write(); code, report = f.invoke('recheck'); assert code == 0
        pair = next(r for r in report['results'] if r['receipt']['relation'] == 'tool_result_of')
        assert pair['receipt']['status'] == 'candidate'
        assert pair['resolution_reason'] == 'relation_unobserved'
        assert report['statuses']['verified'] == 5; assert f.cache.read_bytes() == before

    def test_derived_unmatched_coverage_never_gets_native_membership(self):
        f = self.fixture; f.rows = f.rows[:3]; f.write(); snapshot = self.snapshot()
        derived = next(r for r in snapshot.records if r.provenance == 'unknown')
        assert len(snapshot.graph.joins) == 2
        assert not any(j.left.native_id_sha256 == derived.native_id_sha256 for j in snapshot.graph.joins)

    def test_body_edit_invalidates_exact_old_revision_without_overwriting_checkpoint(self):
        f = self.fixture; assert f.invoke('import')[0] == 0
        before = f.cache.read_bytes(); f.rows[2]['payload']['arguments'] = 'SYNTHETIC_CHANGED_BODY'; f.write()
        code, report = f.invoke('recheck'); assert code == 0
        assert report['statuses']['unlinked'] == 2; assert report['statuses']['verified'] == 4
        assert sum(r['resolution_reason'] == 'stale_revision' for r in report['results']) == 2
        assert f.cache.read_bytes() == before

    def test_existing_ids_do_not_verify_invented_conflicted_or_timed_relationships(self):
        f = self.fixture; graph = self.snapshot().graph
        pair = next(j for j in graph.joins if j.relation == 'tool_result_of')
        forged = replace(pair, right=pair.left)
        conflict = replace(pair, status='conflicted', reason='conflicting_evidence')
        timed = replace(pair, effective_from='2020-01-01T00:00:00Z', effective_until='2021-01-01T00:00:00Z')
        proposed = replace(graph, joins=(forged, conflict, timed))
        report = recheck(f.source, f.reader(), proposed)
        by_sha = {r['claim_sha256']: r for r in report['results']}
        assert by_sha[forged.sha256]['receipt']['status'] == 'candidate'
        assert by_sha[conflict.sha256]['receipt']['status'] == 'conflicted'
        assert by_sha[timed.sha256]['receipt']['status'] == 'verified'
        assert by_sha[timed.sha256]['receipt']['effective_from'] is None
        assert by_sha[timed.sha256]['receipt']['effective_until'] is None

    def test_forged_reader_proof_labels_foreign_scope_and_partial_file_are_blocked(self):
        f = self.fixture; graph = self.snapshot().graph
        class Forged(type(f.reader())):
            def read(self, source): raise AssertionError('never use caller-supplied native proof')
        for connector in (LocalExport(f.manifest), Forged(f.manifest)):
            with pytest.raises(NotImplementedError): recheck(f.source, connector, graph)
        with patch('agent_harness_lab.sources.codex.CodexCapture.read', side_effect=AssertionError('no source read')):
            for proposed in (replace(graph, project_sha256=digest('foreign')),
                    replace(graph, joins=(replace(graph.joins[0], status='verified', reason='native_proof'),))):
                with pytest.raises(ValueError): recheck(f.source, f.reader(), proposed)
        assert f.invoke('import')[0] == 0; before = f.cache.read_bytes()
        f.capture.write_bytes(f.capture.read_bytes().rstrip(b'\n'))
        assert f.invoke('recheck')[0] == 2; assert f.cache.read_bytes() == before
