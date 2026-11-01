"""Synthetic native relationships: exact file receipts do not certify execution."""
from dataclasses import replace
import unittest
from unittest.mock import patch
if __package__:
    from . import test_source_codex as fixtures
else:
    import test_source_codex as fixtures
from agent_harness_lab.sources.resolve import recheck
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.snapshot import LocalExport


class NativeRelationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NativeSourceTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def snapshot(self): return self.fixture.reader().read(self.fixture.source)

    def test_cli_native_relationship_recheck_is_read_only_and_keeps_execution_unknown(self):
        f = self.fixture
        self.assertEqual(f.invoke('preview')[1]['evidence_graph']['joins'], 6)
        self.assertEqual(f.invoke('import')[0], 0); before = f.cache.read_bytes(); original = f.capture.read_bytes()
        code, report = f.invoke('recheck')
        self.assertEqual(code, 0); self.assertEqual(report['statuses']['verified'], 6)
        self.assertFalse(report['network_used']); self.assertFalse(report['task_outcomes_verified'])
        self.assertIsNone(report['independent_roots']); self.assertFalse(report['historical_interval_verified'])
        self.assertEqual(f.cache.read_bytes(), before); self.assertEqual(f.capture.read_bytes(), original)
        self.assertEqual(f.invoke('status')[1]['evidence_graph']['statuses']['verified'], 0)
        self.assertTrue(all(field.status == 'declared' for identity in f.store().capture(f.source).identities for field in identity.fields))

    def test_only_native_members_and_one_unambiguous_tool_pair_get_candidates(self):
        snapshot = self.snapshot(); graph = snapshot.graph
        self.assertTrue(all(j.status == 'candidate' and j.basis == 'native_fields' for j in graph.joins))
        self.assertEqual(sum(j.relation == 'native_session_member' for j in graph.joins), 5)
        pair = [j for j in graph.joins if j.relation == 'tool_result_of']; self.assertEqual(len(pair), 1)
        kinds = {(r.source_sha256, r.native_id_sha256, r.revision_sha256): r.record_type for r in snapshot.records}
        self.assertEqual(kinds[pair[0].left.key], 'codex_tool_result'); self.assertEqual(kinds[pair[0].right.key], 'codex_tool_call')
        self.assertEqual(set(pair[0].evidence), {pair[0].left, pair[0].right})

    def test_duplicate_calls_results_and_result_before_call_cannot_prove_a_pair(self):
        f = self.fixture; original = list(f.rows)
        for changed in ([*original, f.fixture.row('response_item', dict(type='function_call', name='exec_command', call_id='SYNTHETIC_SECRET'), 6)],
                [*original, f.fixture.row('response_item', dict(type='function_call_output', call_id='SYNTHETIC_SECRET'), 6)],
                [original[0], original[1], {**original[4], 'ordinal': 2}, {**original[2], 'ordinal': 3}]):
            f.rows = changed; f.write(); snapshot = self.snapshot()
            self.assertFalse(any(j.relation == 'tool_result_of' for j in snapshot.graph.joins))
            report = recheck(f.source, f.reader(), snapshot.graph)
            self.assertTrue(all(r['receipt']['relation'] != 'tool_result_of' for r in report['results']))

    def test_later_duplicate_native_id_removes_proof_for_previously_unique_pair(self):
        f = self.fixture; self.assertEqual(f.invoke('import')[0], 0); before = f.cache.read_bytes()
        f.rows.append(f.fixture.row('response_item', dict(type='function_call', name='exec_command', call_id='SYNTHETIC_SECRET'), 6))
        f.write(); code, report = f.invoke('recheck'); self.assertEqual(code, 0)
        pair = next(r for r in report['results'] if r['receipt']['relation'] == 'tool_result_of')
        self.assertEqual(pair['receipt']['status'], 'candidate')
        self.assertEqual(pair['resolution_reason'], 'relation_unobserved')
        self.assertEqual(report['statuses']['verified'], 5); self.assertEqual(f.cache.read_bytes(), before)

    def test_derived_unmatched_coverage_never_gets_native_membership(self):
        f = self.fixture; f.rows = f.rows[:3]; f.write(); snapshot = self.snapshot()
        derived = next(r for r in snapshot.records if r.provenance == 'unknown')
        self.assertEqual(len(snapshot.graph.joins), 2)
        self.assertFalse(any(j.left.native_id_sha256 == derived.native_id_sha256 for j in snapshot.graph.joins))

    def test_body_edit_invalidates_exact_old_revision_without_overwriting_checkpoint(self):
        f = self.fixture; self.assertEqual(f.invoke('import')[0], 0)
        before = f.cache.read_bytes(); f.rows[2]['payload']['arguments'] = 'SYNTHETIC_CHANGED_BODY'; f.write()
        code, report = f.invoke('recheck'); self.assertEqual(code, 0)
        self.assertEqual(report['statuses']['unlinked'], 2); self.assertEqual(report['statuses']['verified'], 4)
        self.assertEqual(sum(r['resolution_reason'] == 'stale_revision' for r in report['results']), 2)
        self.assertEqual(f.cache.read_bytes(), before)

    def test_existing_ids_do_not_verify_invented_conflicted_or_timed_relationships(self):
        f = self.fixture; graph = self.snapshot().graph
        pair = next(j for j in graph.joins if j.relation == 'tool_result_of')
        forged = replace(pair, right=pair.left)
        conflict = replace(pair, status='conflicted', reason='conflicting_evidence')
        timed = replace(pair, effective_from='2020-01-01T00:00:00Z', effective_until='2021-01-01T00:00:00Z')
        proposed = replace(graph, joins=(forged, conflict, timed))
        report = recheck(f.source, f.reader(), proposed)
        by_sha = {r['claim_sha256']: r for r in report['results']}
        self.assertEqual(by_sha[forged.sha256]['receipt']['status'], 'candidate')
        self.assertEqual(by_sha[conflict.sha256]['receipt']['status'], 'conflicted')
        self.assertEqual(by_sha[timed.sha256]['receipt']['status'], 'verified')
        self.assertIsNone(by_sha[timed.sha256]['receipt']['effective_from'])
        self.assertIsNone(by_sha[timed.sha256]['receipt']['effective_until'])

    def test_forged_reader_proof_labels_foreign_scope_and_partial_file_are_blocked(self):
        f = self.fixture; graph = self.snapshot().graph
        class Forged(type(f.reader())):
            def read(self, source): raise AssertionError('never use caller-supplied native proof')
        for connector in (LocalExport(f.manifest), Forged(f.manifest)):
            with self.assertRaises(NotImplementedError): recheck(f.source, connector, graph)
        with patch('agent_harness_lab.sources.codex.CodexCapture.read', side_effect=AssertionError('no source read')):
            for proposed in (replace(graph, project_sha256=digest('foreign')),
                    replace(graph, joins=(replace(graph.joins[0], status='verified', reason='native_proof'),))):
                with self.assertRaises(ValueError): recheck(f.source, f.reader(), proposed)
        self.assertEqual(f.invoke('import')[0], 0); before = f.cache.read_bytes()
        f.capture.write_bytes(f.capture.read_bytes().rstrip(b'\n'))
        self.assertEqual(f.invoke('recheck')[0], 2); self.assertEqual(f.cache.read_bytes(), before)
