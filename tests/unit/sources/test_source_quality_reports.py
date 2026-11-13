"""Synthetic SARIF logs; quality findings describe a declared commit, never a task grade or a test result."""
import contextlib
import io
import json
import socket
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import quality_reports
from agent_harness_lab.sources.quality_reports import HostileReport, QualityReportsExport as ReportsExport
from agent_harness_lab.sources.schema import SourceSpec, digest

SHA = '5e'*20


def result(rule, level='error', uri='src/secret_module.py', line=7, **extra):
    value = dict(ruleId=rule, message=dict(text='SYNTHETIC_SECRET'), locations=[dict(physicalLocation=dict(
        artifactLocation=dict(uri=uri), region=dict(startLine=line)))], **extra)
    if level is not None: value['level'] = level
    return value


def sarif(*runs):
    return dict(version='2.1.0', runs=list(runs))


def run(*results, name='synthetic-analyzer', success=True, **extra):
    return dict(tool=dict(driver=dict(name=name, version='1.0')), invocations=[dict(executionSuccessful=success)],
        results=list(results), **extra)


SAMPLE = sarif(run(
    result('SYN001'), result('SYN002', 'warning'), result('SYN003', 'note'), result('SYN004', 'none'),
    result('SYN005', None), result('SYN006', 'catastrophic'), result('SYN007', kind='pass', level='none'),
    result('SYN009', 'error', kind='pass'),
    result('SYN008', suppressions=[dict(kind='inSource', status='rejected')]),
    dict(message=dict(text='x'), level='error'), result('SYN001')))


class TestQualityReportSource:
    @pytest.fixture(autouse=True)
    def _reports(self, tmp_path):
        self.root = tmp_path
        (self.root/'reports').mkdir()
        self.log(SAMPLE)

    def log(self, value, name='reports/scan.sarif'):
        (self.root/name).write_text(value if isinstance(value, str) else json.dumps(value))
        self.declare([dict(path=name, format='sarif')])

    def declare(self, reports, sha=SHA, **extra):
        self.selection = dict(schema_version=1, commit_sha=sha, reports=reports, **extra)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec('local_file', 'quality-reports', 'v1', digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = ReportsExport(self.manifest)

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        assert 'SYNTHETIC_SECRET' not in output.getvalue()+error.getvalue()
        return code, output.getvalue()

    def test_levels_kinds_and_unknowns_stay_separate_and_explicit(self):
        self.connector.read(self.source)
        summary = self.connector.summary
        assert summary['levels'] == dict(error=4, warning=1, note=1, none=2, unknown=3)
        assert summary['kinds'] == dict(fail=9, review=0, open=0, informational=0, notApplicable=0, **{'pass': 2})
        assert summary['suppressed'] == 1
        assert ({'level_not_stated', 'unrecognized_level', 'rule_id_absent', 'suppression_status_not_interpreted',
                 'duplicate_finding_identity', 'conflicting_result_kind', 'commit_binding_declared'} <= set(summary['warnings']))
        assert [f.occurrence for f in self.connector.findings if f.rule_sha256 == digest('SYN001')] == [0, 1]
        assert not {'passed', 'failed', 'skipped'} & set(summary['levels'])

    def test_a_clean_looking_log_is_not_a_clean_bill(self):
        self.log(sarif(run(success=False), run(name='other')))
        self.connector.read(self.source)
        assert ({'no_findings_is_not_clean', 'tool_execution_failure_reported'} <= set(self.connector.summary['warnings']))
        self.log(sarif(run(result('SYN001'), name='silent'), dict(run(), invocations=[])))
        self.connector.read(self.source)
        assert 'tool_execution_success_unstated' in self.connector.summary['warnings']

    def test_findings_are_declared_commit_evidence_never_outcomes_or_public(self):
        snapshot = self.connector.read(self.source)
        summary = self.connector.summary
        assert (summary['commit_sha256'], summary['commit_binding'], summary['commit_verified']) == (digest(SHA), 'caller_declared', False)
        for key in ('task_outcomes_verified', 'verification_authorized', 'execution_authorized', 'scripts_executed',
                    'publication_allowed', 'network_used'):
            assert summary[key] is False
        assert summary['independent_trials'] is None
        assert not public_export_allowed([summary])
        for finding in self.connector.findings:
            assert (finding.commit_binding, finding.commit_verified, finding.outcome_scope) == ('caller_declared', False, 'declared_commit_quality_evidence')
        assert {r.record_type for r in snapshot.records} == {'quality_finding', 'quality_report'}
        for record in snapshot.records:
            assert (record.plane, record.causal_status, record.independence, record.capture_completeness, record.outcome) == (
                'development_artifact', 'unknown', 'unknown', 'partial', None)

    def test_same_finding_at_another_commit_or_report_is_a_different_record(self):
        first = {r.native_id_sha256 for r in self.connector.read(self.source).records}
        self.declare([dict(path='reports/scan.sarif', format='sarif')], sha='6f'*20)
        assert not first & {r.native_id_sha256 for r in self.connector.read(self.source).records}

    def test_report_uris_are_hashed_and_never_opened_or_fetched(self):
        self.log(sarif(run(result('SYN001', uri='https://attacker.invalid/payload'), result('SYN002', uri='file:///etc/passwd'))))
        with patch.object(socket, 'socket', side_effect=AssertionError('network')), patch('urllib.request.urlopen', side_effect=AssertionError('fetch')):
            self.connector.read(self.source)
        assert self.connector.summary['findings'] == 2 and self.connector.summary['network_used'] is False

    def test_external_property_file_references_quarantine_the_report(self):
        self.log(sarif(run(result('SYN001'), externalPropertyFileReferences=dict(
            driver=dict(location=dict(uri='https://attacker.invalid/props.json'))))))
        with pytest.raises(HostileReport): self.connector.read(self.source)

    @pytest.mark.parametrize('raw', [
        pytest.param('{"version":"2.0.0","runs":[]}', id='other-version'),
        pytest.param('{"version":"2.1.0"}', id='no-runs'),
        pytest.param('[]', id='not-an-object'),
        pytest.param('{"version":"2.1.0","runs":[{"tool":{"driver":{}}}]}', id='nameless-driver'),
        pytest.param('{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"x"}},"results":{}}]}', id='results-not-list'),
        pytest.param('{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"x"}},"results":[{"kind":"fatal"}]}]}', id='unknown-kind'),
        pytest.param('{"version":"2.1.0","version":"2.1.0","runs":[]}', id='duplicate-key'),
        pytest.param('{"version":"2.1.0","runs":[NaN]}', id='non-finite'),
        pytest.param('{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"x"}},"results":[{"locations":[{"physicalLocation":{"artifactLocation":{"uri":7}}}]}]}]}', id='numeric-uri'),
        pytest.param('{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"x"}},"invocations":7}]}', id='invocations-not-list'),
        pytest.param('\xff', id='not-json')])
    def test_malformed_logs_are_refused_and_import_nothing(self, raw):
        self.log(raw)
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_structural_and_size_bounds_are_hostile_refusals(self):
        self.log('{"version":"2.1.0","runs":'+'['*70+']'*70+'}')
        with pytest.raises(HostileReport): self.connector.read(self.source)
        self.log(SAMPLE)
        self.declare([dict(path='reports/scan.sarif', format='sarif')], limits=dict(max_findings=3))
        with pytest.raises(HostileReport): self.connector.read(self.source)
        self.declare([dict(path='reports/scan.sarif', format='sarif')], limits=dict(max_report_bytes=64))
        with pytest.raises(ValueError): self.connector.read(self.source)
        self.declare([dict(path='reports/scan.sarif', format='sarif')], limits=dict(max_findings=10**9))
        with pytest.raises(ValueError, match='lower'): self.connector.read(self.source)

    @pytest.mark.parametrize('sha', [
        pytest.param('5e'*10, id='short'), pytest.param('5E'*20, id='uppercase'), pytest.param(None, id='missing')])
    def test_commit_identity_must_be_full_lowercase_hex(self, sha):
        self.declare([dict(path='reports/scan.sarif', format='sarif')], sha=sha)
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_selection_is_scoped_and_unknown_formats_are_refused(self):
        for reports in ([], [dict(path='reports/scan.sarif', format='junit-xml')], [dict(path='../x.sarif', format='sarif')],
                        [dict(path='reports/scan.sarif', format='sarif')]*2):
            self.declare(reports)
            with pytest.raises(ValueError): self.connector.read(self.source)
        self.declare([dict(path='reports/scan.sarif', format='sarif')])
        (self.root/'selection.json').write_text(json.dumps(dict(self.selection, commit_sha='6f'*20)))
        with pytest.raises(ValueError): self.connector.read(self.source)
        (self.root/'reports/link.sarif').symlink_to(self.root/'reports/scan.sarif')
        self.declare([dict(path='reports/link.sarif', format='sarif')])
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_a_report_changed_during_capture_rejects_the_capture(self):
        with patch.object(quality_reports, 'regular_sha256', lambda path, limit: '0'*64):
            with pytest.raises(ValueError, match='changed'): self.connector.read(self.source)

    def test_cli_preview_import_and_reimport_keep_hashes_only(self):
        code, preview = self.invoke('preview')
        assert code == 0
        summary = json.loads(preview)['quality_reports_summary']
        assert (summary['findings'], summary['profile']) == (11, 'ci-quality-reports-v1')
        assert not (self.root/'private').exists()
        code, receipt = self.invoke('import')
        assert code == 0
        assert json.loads(receipt)['imported'] == 12
        raw = (self.root/'private/cache.json').read_text()
        for secret in ('SYNTHETIC_SECRET', 'secret_module', 'SYN001', 'synthetic-analyzer', SHA, 'reports/scan.sarif'):
            assert secret not in raw
        assert json.loads(self.invoke('import')[1])['skipped'] == 12
        before = (self.root/'private/cache.json').read_bytes()
        (self.root/'reports/scan.sarif').write_text('{"version":"2.1.0","version":"2.1.0"}')
        assert self.invoke('import')[0] == 2
        assert (self.root/'private/cache.json').read_bytes() == before
