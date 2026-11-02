"""Explicit private metadata context never becomes telemetry or grading evidence."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from .test_source_execution import metadata_for
    from .test_source_graph import graph_for
else:
    from test_source_execution import metadata_for
    from test_source_graph import graph_for

from agent_harness_lab.cli import main
from agent_harness_lab.onboarding import doctor
from agent_harness_lab.sources.context import load
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore


class SourceContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.project = self.root/'project'; self.project.mkdir()
        self.cache = self.root/'SYNTHETIC_SOURCE_PATH'/'journal.json'
        self.source = SourceSpec('api', 'SYNTHETIC_SECRET', 'SYNTHETIC_SECRET', digest('root'), digest('project'), digest('scope'),
            identity_provenance='source_declared_id', enabled=True)
        self.records = tuple(EvidenceRecord(self.source.source_key, digest(n), digest(('revision', n)), digest('SYNTHETIC_SECRET'),
            'contextual_assertion', 'synthetic_claim', '2026-01-01T00:00:00Z') for n in ('original', 'copy'))
        self.metadata = metadata_for(self.source, self.records[0])
        self.metadata = replace(self.metadata, coverage=replace(self.metadata.coverage, parser_version='SYNTHETIC_SECRET',
            limit_reached=True, truncated=True, gaps=('not_captured',)))
        self.graph = graph_for(self.source, self.records)
        self.store = SourceStore(self.cache, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        self.receipt = self.store.ingest(self.source, self.records, snapshot_sha256=digest('raw'), metadata=self.metadata, graph=self.graph)
        self.analysis = dict(analysis=dict(events=0, cases=0, field_coverage={}, findings=[], recurring_sequences=[],
            limitations=[], deferred_mechanisms={}), evaluation_failures=[], fingerprint='synthetic', caveats=[])
        self.collect = patch('agent_harness_lab.workflow.collect', return_value=self.analysis)
        self.collect.start(); self.addCleanup(self.collect.stop)

    def selection(self):
        return ['--source-cache', str(self.cache), '--source-project-sha256', self.source.project_sha256,
            '--source-scope-sha256', self.source.scope_sha256]

    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            try: code = main(list(arguments))
            except SystemExit as error: code = error.code
        for private in ('SYNTHETIC_SECRET', 'SYNTHETIC_SOURCE_PATH', str(self.cache)):
            self.assertNotIn(private, output.getvalue()+errors.getvalue())
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def context(self):
        return load(self.cache, self.source.project_sha256, self.source.scope_sha256)

    def test_inventory_exposes_declared_gaps_identities_and_graph_without_labels_or_authority(self):
        context = self.context(); source = context['sources'][0]
        self.assertEqual(context['status'], 'available'); self.assertTrue(context['cache_read'])
        self.assertFalse(context['source_read']); self.assertFalse(context['network_used'])
        self.assertEqual(context['target_project_binding'], 'unverified'); self.assertIsNone(context['independent_roots'])
        self.assertFalse(context['task_outcomes_verified']); self.assertFalse(context['execution_authorized'])
        self.assertEqual(source['revisions'], 2); self.assertEqual(source['checkpoint_sha256'], self.receipt.cursor_after_sha256)
        coverage = source['declared_coverage']
        self.assertEqual(coverage['claim_basis'], 'source_declaration'); self.assertTrue(coverage['limit_reached']); self.assertTrue(coverage['truncated'])
        self.assertEqual(source['execution_metadata']['fields']['served_model']['unknown'], 1)
        self.assertEqual(source['evidence_graph']['statuses']['candidate'], 1)
        self.assertEqual(source['provider_sha256'], digest('SYNTHETIC_SECRET'))
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(context)); self.assertNotIn(str(self.cache), json.dumps(context))

    def test_doctor_reads_metadata_only_and_does_not_upgrade_profile_readiness(self):
        original = self.cache.read_bytes(); baseline = doctor(self.project, 'offline')
        with patch('http.client.HTTPSConnection', side_effect=AssertionError('no network')), \
             patch('subprocess.Popen', side_effect=AssertionError('no execution')):
            code, result = self.invoke('doctor', str(self.project), '--profile', 'offline', '--json', *self.selection())
        self.assertEqual(code, 0); self.assertEqual(result['status'], baseline['status'])
        check = next(c for c in result['checks'] if c['check_id'] == 'source_context')
        self.assertEqual(check['status'], 'warn'); self.assertFalse(check['required_for_profile'])
        self.assertEqual(result['source_context']['capture_completeness'], 'unknown')
        self.assertEqual(self.cache.read_bytes(), original)

    def test_analyze_keeps_context_separate_from_events_findings_and_private_report(self):
        output = self.root/'analysis'
        original = self.cache.read_bytes()
        with patch('http.client.HTTPSConnection', side_effect=AssertionError('no network')):
            code, result = self.invoke('analyze', str(self.project), '--output', str(output), *self.selection())
        self.assertEqual(code, 0); self.assertEqual(result['findings'], 0)
        analyzed = json.loads((output/'analysis.json').read_text())
        self.assertEqual(analyzed['analysis']['events'], 0); self.assertEqual(analyzed['analysis']['findings'], [])
        self.assertEqual(analyzed['source_context'], result['source_context'])
        report = (output/'report.md').read_text()
        self.assertIn('Scoped source context', report); self.assertIn('unverified', report)
        self.assertNotIn('SYNTHETIC_SECRET', report); self.assertNotIn(str(self.cache), report)
        self.assertEqual((output/'analysis.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache.read_bytes(), original)

    def test_text_doctor_exposes_declared_coverage_without_private_labels(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['doctor', str(self.project), '--profile', 'offline', *self.selection()])
        self.assertEqual(code, 0); self.assertIn('declared coverage: partial', output.getvalue())
        self.assertIn('execution identities: 1', output.getvalue())
        self.assertNotIn('SYNTHETIC_SECRET', output.getvalue()); self.assertNotIn(str(self.cache), output.getvalue())

    def test_missing_journal_is_unknown_not_an_empty_successful_capture(self):
        missing = self.root/'missing'/'journal.json'
        result = load(missing, self.source.project_sha256, self.source.scope_sha256)
        self.assertEqual(result['status'], 'missing'); self.assertFalse(result['cache_read'])
        self.assertEqual(result['capture_completeness'], 'unknown'); self.assertIsNone(result['independent_roots'])
        self.assertFalse(missing.parent.exists())

    def test_disappearing_journal_is_reported_missing_instead_of_empty_available(self):
        with patch('agent_harness_lab.sources.store.read_regular', side_effect=FileNotFoundError('synthetic disappearance')):
            result = self.context()
        self.assertEqual(result['status'], 'missing'); self.assertFalse(result['cache_read'])

    def test_foreign_scope_malformed_cache_and_partial_selection_fail_before_analysis_output(self):
        output = self.root/'rejected-output'
        args = ['analyze', str(self.project), '--output', str(output)]
        self.assertEqual(self.invoke(*args, '--source-cache', str(self.cache))[0], 2)
        self.assertFalse(output.exists())
        self.assertEqual(self.invoke(*args, *self.selection(), '--source-project-sha256', digest('foreign'))[0], 2)
        self.assertFalse(output.exists())
        original = self.cache.read_bytes(); self.cache.write_text('{invalid')
        self.assertEqual(self.invoke(*args, *self.selection())[0], 2)
        self.assertEqual(self.cache.read_text(), '{invalid'); self.assertFalse(output.exists())
        self.cache.write_bytes(original)

    def test_linked_journal_cannot_enter_an_analysis_or_doctor_context(self):
        linked = self.root/'linked'; linked.symlink_to(self.cache)
        with self.assertRaises(ValueError): load(linked, self.source.project_sha256, self.source.scope_sha256)

    def test_legacy_schema_reads_do_not_rewrite_or_invent_graphs(self):
        legacy = SourceStore(self.root/'legacy.json', project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        legacy.ingest(self.source, self.records, snapshot_sha256=digest('legacy'), metadata=self.metadata)
        state = json.loads(legacy.path.read_text()); state['schema_version'] = 3
        for envelope in state['captures'][self.source.source_key].values(): envelope.pop('graph')
        legacy.path.write_text(json.dumps(state)); original = legacy.path.read_bytes()
        result = legacy.inventory()
        self.assertIsNone(result['sources'][0]['evidence_graph']); self.assertEqual(legacy.path.read_bytes(), original)

    def test_declared_complete_coverage_never_becomes_verified_capture_or_target_binding(self):
        coverage = replace(self.metadata.coverage, reported_completeness='complete', limit_reached=False, truncated=False,
            gaps=(), missing_fields=())
        self.store.ingest(self.source, self.records, cursor_before=self.receipt.cursor_after_sha256,
            snapshot_sha256=digest('complete-claim'), metadata=replace(self.metadata, coverage=coverage))
        result = self.context()
        self.assertEqual(result['sources'][0]['declared_coverage']['reported_completeness'], 'complete')
        self.assertEqual(result['capture_completeness'], 'unknown'); self.assertEqual(result['target_project_binding'], 'unverified')
        self.assertFalse(result['task_outcomes_verified'])

    def test_source_and_version_summary_limits_are_explicit(self):
        for number in range(21):
            source = replace(self.source, root_sha256=digest(('other', number)))
            self.store.ingest(source, ())
        result = self.context()
        self.assertEqual(result['source_count'], 22); self.assertEqual(len(result['sources']), 20)
        self.assertEqual(result['omitted_sources'], 2); self.assertTrue(result['truncated'])
        output = io.StringIO()
        with redirect_stdout(output): main(['doctor', str(self.project), '--profile', 'offline', *self.selection()])
        self.assertIn('sources: 22; omitted: 2; truncated: True', output.getvalue())
        versions = SourceStore(self.root/'versions.json', project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        for number in range(10): versions.ingest(replace(self.source, version='v'+str(number)), ())
        result = versions.inventory()
        self.assertEqual(len(result['sources'][0]['adapter_versions_sha256']), 8)
        self.assertEqual(result['sources'][0]['omitted_adapter_versions'], 2); self.assertTrue(result['truncated'])

    def test_no_selection_stays_opt_in_and_unsupported_platform_has_no_unsafe_read(self):
        with patch('agent_harness_lab.config.supported', return_value=False):
            self.assertIsNone(load())
            code, result = self.invoke('analyze', str(self.project), '--output', str(self.root/'unsupported'), *self.selection())
        self.assertEqual(code, 3); self.assertIsNone(result); self.assertFalse((self.root/'unsupported').exists())
