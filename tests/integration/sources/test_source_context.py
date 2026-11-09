"""Explicit private metadata context never becomes telemetry or grading evidence."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from unittest.mock import patch

import pytest

from tests.unit.sources.test_source_execution import metadata_for
from tests.unit.sources.test_source_graph import graph_for

from agent_harness_lab.cli import main
from agent_harness_lab.onboarding import doctor
from agent_harness_lab.sources.context import load
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore


class TestSourceContext:
    @pytest.fixture(autouse=True)
    def _context(self, tmp_path):
        self.root = tmp_path; self.project = self.root/'project'; self.project.mkdir()
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
        with patch('agent_harness_lab.workflow.collect', return_value=self.analysis):
            yield

    def selection(self):
        return ['--source-cache', str(self.cache), '--source-project-sha256', self.source.project_sha256,
            '--source-scope-sha256', self.source.scope_sha256]

    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            try: code = main(list(arguments))
            except SystemExit as error: code = error.code
        for private in ('SYNTHETIC_SECRET', 'SYNTHETIC_SOURCE_PATH', str(self.cache)):
            assert private not in output.getvalue()+errors.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def context(self):
        return load(self.cache, self.source.project_sha256, self.source.scope_sha256)

    def test_inventory_exposes_declared_gaps_identities_and_graph_without_labels_or_authority(self):
        context = self.context(); source = context['sources'][0]
        assert context['status'] == 'available'; assert context['cache_read']
        assert not context['source_read']; assert not context['network_used']
        assert context['target_project_binding'] == 'unverified'; assert context['independent_roots'] is None
        assert not context['task_outcomes_verified']; assert not context['execution_authorized']
        assert source['revisions'] == 2; assert source['checkpoint_sha256'] == self.receipt.cursor_after_sha256
        coverage = source['declared_coverage']
        assert coverage['claim_basis'] == 'source_declaration'; assert coverage['limit_reached']; assert coverage['truncated']
        assert source['execution_metadata']['fields']['served_model']['unknown'] == 1
        assert source['evidence_graph']['statuses']['candidate'] == 1
        assert source['provider_sha256'] == digest('SYNTHETIC_SECRET')
        assert 'SYNTHETIC_SECRET' not in json.dumps(context); assert str(self.cache) not in json.dumps(context)

    def test_doctor_reads_metadata_only_and_does_not_upgrade_profile_readiness(self):
        original = self.cache.read_bytes(); baseline = doctor(self.project, 'offline')
        with patch('http.client.HTTPSConnection', side_effect=AssertionError('no network')), \
             patch('subprocess.Popen', side_effect=AssertionError('no execution')):
            code, result = self.invoke('doctor', str(self.project), '--profile', 'offline', '--json', *self.selection())
        assert code == 0; assert result['status'] == baseline['status']
        check = next(c for c in result['checks'] if c['check_id'] == 'source_context')
        assert check['status'] == 'warn'; assert not check['required_for_profile']
        assert result['source_context']['capture_completeness'] == 'unknown'
        assert self.cache.read_bytes() == original

    def test_analyze_keeps_context_separate_from_events_findings_and_private_report(self):
        output = self.root/'analysis'
        original = self.cache.read_bytes()
        with patch('http.client.HTTPSConnection', side_effect=AssertionError('no network')):
            code, result = self.invoke('analyze', str(self.project), '--output', str(output), *self.selection())
        assert code == 0; assert result['findings'] == 0
        analyzed = json.loads((output/'analysis.json').read_text())
        assert analyzed['analysis']['events'] == 0; assert analyzed['analysis']['findings'] == []
        assert analyzed['source_context'] == result['source_context']
        report = (output/'report.md').read_text()
        assert 'Scoped source context' in report; assert 'unverified' in report
        assert 'SYNTHETIC_SECRET' not in report; assert str(self.cache) not in report
        assert (output/'analysis.json').stat().st_mode & 0o777 == 0o600
        assert self.cache.read_bytes() == original

    def test_text_doctor_exposes_declared_coverage_without_private_labels(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['doctor', str(self.project), '--profile', 'offline', *self.selection()])
        assert code == 0; assert 'declared coverage: partial' in output.getvalue()
        assert 'execution identities: 1' in output.getvalue()
        assert 'SYNTHETIC_SECRET' not in output.getvalue(); assert str(self.cache) not in output.getvalue()

    def test_missing_journal_is_unknown_not_an_empty_successful_capture(self):
        missing = self.root/'missing'/'journal.json'
        result = load(missing, self.source.project_sha256, self.source.scope_sha256)
        assert result['status'] == 'missing'; assert not result['cache_read']
        assert result['capture_completeness'] == 'unknown'; assert result['independent_roots'] is None
        assert not missing.parent.exists()

    def test_disappearing_journal_is_reported_missing_instead_of_empty_available(self):
        with patch('agent_harness_lab.sources.store.read_regular', side_effect=FileNotFoundError('synthetic disappearance')):
            result = self.context()
        assert result['status'] == 'missing'; assert not result['cache_read']

    def test_foreign_scope_malformed_cache_and_partial_selection_fail_before_analysis_output(self):
        output = self.root/'rejected-output'
        args = ['analyze', str(self.project), '--output', str(output)]
        assert self.invoke(*args, '--source-cache', str(self.cache))[0] == 2
        assert not output.exists()
        assert self.invoke(*args, *self.selection(), '--source-project-sha256', digest('foreign'))[0] == 2
        assert not output.exists()
        original = self.cache.read_bytes(); self.cache.write_text('{invalid')
        assert self.invoke(*args, *self.selection())[0] == 2
        assert self.cache.read_text() == '{invalid'; assert not output.exists()
        self.cache.write_bytes(original)

    def test_linked_journal_cannot_enter_an_analysis_or_doctor_context(self):
        linked = self.root/'linked'; linked.symlink_to(self.cache)
        with pytest.raises(ValueError): load(linked, self.source.project_sha256, self.source.scope_sha256)

    def test_legacy_schema_reads_do_not_rewrite_or_invent_graphs(self):
        legacy = SourceStore(self.root/'legacy.json', project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        legacy.ingest(self.source, self.records, snapshot_sha256=digest('legacy'), metadata=self.metadata)
        state = json.loads(legacy.path.read_text()); state['schema_version'] = 3
        for envelope in state['captures'][self.source.source_key].values(): envelope.pop('graph')
        legacy.path.write_text(json.dumps(state)); original = legacy.path.read_bytes()
        result = legacy.inventory()
        assert result['sources'][0]['evidence_graph'] is None; assert legacy.path.read_bytes() == original

    def test_declared_complete_coverage_never_becomes_verified_capture_or_target_binding(self):
        coverage = replace(self.metadata.coverage, reported_completeness='complete', limit_reached=False, truncated=False,
            gaps=(), missing_fields=())
        self.store.ingest(self.source, self.records, cursor_before=self.receipt.cursor_after_sha256,
            snapshot_sha256=digest('complete-claim'), metadata=replace(self.metadata, coverage=coverage))
        result = self.context()
        assert result['sources'][0]['declared_coverage']['reported_completeness'] == 'complete'
        assert result['capture_completeness'] == 'unknown'; assert result['target_project_binding'] == 'unverified'
        assert not result['task_outcomes_verified']

    def test_source_and_version_summaries_are_complete_by_default(self):
        for number in range(21):
            source = replace(self.source, root_sha256=digest(('other', number)))
            self.store.ingest(source, ())
        result = self.context()
        assert result['source_count'] == 22; assert len(result['sources']) == 22
        assert result['omitted_sources'] == 0; assert not result['truncated']
        output = io.StringIO()
        with redirect_stdout(output): main(['doctor', str(self.project), '--profile', 'offline', *self.selection()])
        assert 'sources: 22; omitted: 0; truncated: False' in output.getvalue()
        versions = SourceStore(self.root/'versions.json', project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        for number in range(10): versions.ingest(replace(self.source, version='v'+str(number)), ())
        result = versions.inventory()
        assert len(result['sources'][0]['adapter_versions_sha256']) == 10
        assert result['sources'][0]['omitted_adapter_versions'] == 0; assert not result['truncated']

    def test_no_selection_stays_opt_in_and_unsupported_platform_has_no_unsafe_read(self):
        with patch('agent_harness_lab.config.supported', return_value=False):
            assert load() is None
            code, result = self.invoke('analyze', str(self.project), '--output', str(self.root/'unsupported'), *self.selection())
        assert code == 3; assert result is None; assert not (self.root/'unsupported').exists()
