"""Synthetic CI artifact ZIPs; a report is read from one named member in memory, and a hostile archive is quarantined whole."""
import contextlib
import hashlib
import io
import json
import socket
import stat
import struct
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.ci_join import join_ci_evidence
from agent_harness_lab.sources.quality_reports import QualityReportsExport
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.test_results import HostileReport, TestResultsExport
from tests.unit.sources.test_source_ci_join import ActionsCase, HEAD
from tests.unit.sources.test_source_quality_reports import result, run, sarif
from tests.unit.sources.test_source_test_results import JUNIT, PYTEST, TAP

SHA = '7a'*20
SECRET = 'SYNTHETIC_SECRET'
SARIF = json.dumps(sarif(run(result('SYN001'), result('SYN002', 'warning')))).encode()


def artifact(entries, **flags):
    """A deflated ZIP of (name, bytes) pairs; names are stored verbatim, so a hostile one is not sanitized."""
    out = io.BytesIO()
    with ZipFile(out, 'w', compression=ZIP_DEFLATED) as zf:
        for name, data in entries:
            info = ZipInfo(name, (2026, 11, 15, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            for field, value in flags.items(): setattr(info, field, value)
            zf.writestr(info, data)
    return out.getvalue()


def patched(raw, offset, form, value):
    """Rewrite one field of the first central directory entry: 8 flags, 10 method, 24 uncompressed size."""
    buffer, start = bytearray(raw), raw.index(b'PK\x01\x02')
    struct.pack_into(form, buffer, start+offset, value)
    return bytes(buffer)


class ArchiveCase:
    """Declares one provider's selection over archives written under tmp_path."""
    provider, exporter, member, form = 'test-results', TestResultsExport, 'reports/junit.xml', 'junit-xml'

    @pytest.fixture(autouse=True)
    def _root(self, tmp_path):
        self.root = tmp_path

    def declare(self, reports, **extra):
        selection = dict(schema_version=1, commit_sha=SHA, reports=reports, **extra)
        (self.root/'selection.json').write_text(json.dumps(selection))
        self.source = SourceSpec('local_file', self.provider, 'v1', digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = self.exporter(self.manifest)

    def read(self, raw, member=None, **extra):
        (self.root/'ci.zip').write_bytes(raw)
        self.declare([dict(path='ci.zip', format=self.form, member=member or self.member)], **extra)
        return self.connector.read(self.source)

    def items(self):
        return self.connector.results


class TestTestResultArchives(ArchiveCase):
    def test_a_member_is_imported_like_the_plain_report_but_bound_to_the_member_bytes(self):
        self.read(artifact([(self.member, JUNIT)]))
        archived = self.items()
        (self.root/'plain.xml').write_bytes(JUNIT)
        self.declare([dict(path='plain.xml', format='junit-xml')])
        plain = TestResultsExport(self.manifest); plain.read(self.source)
        assert [(r.status, r.reported_outcome) for r in archived] == [(r.status, r.reported_outcome) for r in plain.results]
        assert {r.report_sha256 for r in archived} == {hashlib.sha256(JUNIT).hexdigest()}
        assert {r.report_path_sha256 for r in archived} == {digest(['ci.zip', self.member])}
        assert not {r.report_path_sha256 for r in archived} & {r.report_path_sha256 for r in plain.results}

    def test_an_archive_streamed_with_data_descriptors_is_read_like_any_other(self):
        class Sink(io.RawIOBase):  # not seekable, so ZipFile writes a data descriptor after each entry
            def __init__(self): self.data = bytearray()
            def writable(self): return True
            def write(self, chunk): self.data += chunk; return len(chunk)
        sink = Sink()
        with ZipFile(sink, 'w', compression=ZIP_DEFLATED) as zf: zf.writestr(self.member, JUNIT)
        raw = bytes(sink.data)
        assert struct.unpack_from('<H', raw, 6)[0] & 8
        self.read(raw)
        assert {r.report_sha256 for r in self.items()} == {hashlib.sha256(JUNIT).hexdigest()}

    @pytest.mark.parametrize('form, raw, status', [
        pytest.param('pytest-json', json.dumps(PYTEST).encode(), 'failed', id='pytest-json'),
        pytest.param('tap', TAP, 'skipped', id='tap')])
    def test_every_report_format_can_come_from_a_member(self, form, raw, status):
        self.form = form
        self.read(artifact([(self.member, raw)]))
        assert status in {r.status for r in self.items()}

    def test_two_members_of_one_archive_are_two_reports_with_their_own_identities(self):
        (self.root/'ci.zip').write_bytes(artifact([('a/junit.xml', JUNIT), ('b/junit.xml', JUNIT)]))
        self.declare([dict(path='ci.zip', format='junit-xml', member='a/junit.xml'),
                      dict(path='ci.zip', format='junit-xml', member='b/junit.xml')])
        self.connector.read(self.source)
        assert len({r.report_path_sha256 for r in self.items()}) == 2 and self.connector.summary['reports'] == 2

    def test_a_member_selection_is_flagged_as_unverified_but_a_plain_one_is_not(self):
        self.read(artifact([(self.member, JUNIT)]))
        assert 'archive_origin_unverified' in self.connector.summary['warnings']
        (self.root/'plain.xml').write_bytes(JUNIT)
        self.declare([dict(path='plain.xml', format='junit-xml')])
        self.connector.read(self.source)
        assert 'archive_origin_unverified' not in self.connector.summary['warnings']

    def test_reading_extracts_nothing_and_uses_no_network(self):
        raw = artifact([(self.member, JUNIT)])
        (self.root/'ci.zip').write_bytes(raw)
        before = {p.name for p in self.root.rglob('*')}
        with patch.object(socket, 'socket', side_effect=AssertionError('network')), patch('urllib.request.urlopen', side_effect=AssertionError('fetch')):
            self.read(raw)
        assert {p.name for p in self.root.rglob('*')} - before == {'selection.json', 'source.json'}
        assert self.connector.summary['network_used'] is False and self.connector.summary['scripts_executed'] is False

    @pytest.mark.parametrize('name', [
        pytest.param('../evil.xml', id='parent-traversal'), pytest.param('reports/../../evil.xml', id='nested-traversal'),
        pytest.param('/etc/evil.xml', id='absolute'), pytest.param('reports\\evil.xml', id='backslash'),
        pytest.param('C:evil.xml', id='drive-letter'), pytest.param('reports//evil.xml', id='empty-segment'),
        pytest.param('./evil.xml', id='dot-segment'), pytest.param('evil\x01.xml', id='control-character')])
    def test_an_unsafe_member_name_anywhere_quarantines_the_whole_archive(self, name):
        with pytest.raises(HostileReport): self.read(artifact([(self.member, JUNIT), (name, b'x')]))

    @pytest.mark.parametrize('flags', [
        pytest.param(dict(external_attr=(stat.S_IFLNK | 0o777) << 16), id='symlink'),
        pytest.param(dict(external_attr=(stat.S_IFIFO | 0o644) << 16), id='special-file'),
        pytest.param(dict(extra=struct.pack('<HHQ', 1, 8, 1)), id='zip64-extra-field')])
    def test_linked_special_or_extended_members_are_refused(self, flags):
        with pytest.raises(HostileReport): self.read(artifact([(self.member, JUNIT)], **flags))

    def test_an_encrypted_member_is_refused(self):
        with pytest.raises(HostileReport): self.read(patched(artifact([(self.member, JUNIT)]), 8, '<H', 1))

    @pytest.mark.filterwarnings('ignore:Duplicate name')
    def test_a_duplicate_member_name_is_refused(self):
        with pytest.raises(HostileReport): self.read(artifact([(self.member, JUNIT), (self.member, JUNIT)]))

    def test_an_unsupported_compression_method_is_refused(self):
        with pytest.raises(HostileReport): self.read(patched(artifact([(self.member, JUNIT)]), 10, '<H', 12))

    def test_a_member_larger_than_its_declared_size_is_refused_not_truncated(self):
        with pytest.raises(HostileReport): self.read(patched(artifact([(self.member, JUNIT*50)]), 24, '<L', 10))

    def test_a_member_over_the_report_byte_bound_is_refused_although_the_archive_is_under_it(self):
        raw = artifact([(self.member, b'<testsuite>' + b' '*200000 + b'</testsuite>')])
        assert len(raw) < 4096
        with pytest.raises(HostileReport): self.read(raw, limits=dict(max_report_bytes=4096))

    @pytest.mark.parametrize('raw', [
        pytest.param(b'', id='empty'), pytest.param(JUNIT, id='plain-xml-not-a-zip'),
        pytest.param(artifact([('reports/junit.xml', JUNIT)])[:-30], id='truncated-directory')])
    def test_a_file_that_is_not_a_valid_archive_imports_nothing(self, raw):
        with pytest.raises(HostileReport): self.read(raw)

    def test_a_missing_member_is_an_error_not_an_empty_report(self):
        with pytest.raises(ValueError, match='lacks the declared member'): self.read(artifact([('other.xml', JUNIT)]))

    def test_a_nested_archive_is_never_opened_as_one(self):
        inner = artifact([(self.member, JUNIT)])
        with pytest.raises(ValueError): self.read(artifact([('inner.zip', inner)]), member='inner.zip')

    @pytest.mark.parametrize('member', [
        pytest.param('', id='empty'), pytest.param('../x.xml', id='traversal'), pytest.param('/x.xml', id='absolute'),
        pytest.param('a\\b.xml', id='backslash'), pytest.param(7, id='not-text'), pytest.param(None, id='null')])
    def test_a_malformed_member_declaration_is_refused(self, member):
        (self.root/'ci.zip').write_bytes(artifact([(self.member, JUNIT)]))
        self.declare([dict(path='ci.zip', format='junit-xml', member=member)])
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_an_unknown_declaration_key_and_a_repeated_member_are_refused(self):
        (self.root/'ci.zip').write_bytes(artifact([(self.member, JUNIT)]))
        self.declare([dict(path='ci.zip', format='junit-xml', member=self.member, url='https://attacker.invalid/x')])
        with pytest.raises(ValueError, match='invalid test report declaration'): self.connector.read(self.source)
        self.declare([dict(path='ci.zip', format='junit-xml', member=self.member)]*2)
        with pytest.raises(ValueError, match='duplicate test report path'): self.connector.read(self.source)

    def test_hostile_report_content_inside_a_member_is_refused_by_the_ordinary_bounds(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>&a;</testsuite>'
        with pytest.raises(HostileReport): self.read(artifact([(self.member, bomb)]))

    def test_the_cli_preview_of_an_archive_selection_holds_hashes_only(self):
        self.read(artifact([(self.member, JUNIT.replace(b'test_pass', SECRET.encode()))]))
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', 'preview', '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        text = output.getvalue()+error.getvalue()
        assert code == 0 and json.loads(output.getvalue())['test_results_summary']['reports'] == 1
        assert not [part for part in (SECRET, self.member, 'ci.zip') if part in text]


class TestQualityReportArchives(ArchiveCase):
    provider, exporter, member, form = 'quality-reports', QualityReportsExport, 'results/analysis.sarif', 'sarif'

    def items(self):
        return self.connector.findings

    def test_a_sarif_member_yields_the_same_findings_as_the_plain_log(self):
        self.read(artifact([(self.member, SARIF)]))
        assert [(f.rule_sha256, f.level) for f in self.items()] == [(digest('SYN001'), 'error'), (digest('SYN002'), 'warning')]
        assert {f.report_sha256 for f in self.items()} == {hashlib.sha256(SARIF).hexdigest()}
        assert {f.report_path_sha256 for f in self.items()} == {digest(['ci.zip', self.member])}
        assert 'archive_origin_unverified' in self.connector.summary['warnings']

    def test_a_traversing_name_quarantines_a_sarif_archive_too(self):
        with pytest.raises(HostileReport): self.read(artifact([(self.member, SARIF), ('../evil.sarif', SARIF)]))

    def test_a_member_never_named_in_the_selection_is_not_imported(self):
        self.read(artifact([(self.member, SARIF), ('results/other.sarif', json.dumps(sarif(run(result('SYN999')))).encode())]))
        assert digest('SYN999') not in {f.rule_sha256 for f in self.items()}

    def test_a_duplicate_or_malformed_member_declaration_is_refused(self):
        (self.root/'ci.zip').write_bytes(artifact([(self.member, SARIF)]))
        self.declare([dict(path='ci.zip', format='sarif', member=self.member)]*2)
        with pytest.raises(ValueError, match='duplicate quality report path'): self.connector.read(self.source)
        self.declare([dict(path='ci.zip', format='sarif', member='../x')])
        with pytest.raises(ValueError): self.connector.read(self.source)


class TestArchiveOriginJoin(ActionsCase):
    """The caller names the member too, because one artifact holds several reports from different jobs."""

    def results(self, *members):
        (self.root/'ci.zip').write_bytes(artifact([(member, JUNIT) for member in members]))
        selection = dict(schema_version=1, commit_sha=HEAD, reports=[dict(path='ci.zip', format='junit-xml', member=m) for m in members])
        (self.root/'selection.json').write_text(json.dumps(selection))
        source = SourceSpec('local_file', 'test-results', 'v1', digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'source.json').write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='selection.json')))
        export = TestResultsExport(self.root/'source.json'); export.read(source)
        return export

    def join(self, export, origins):
        return join_ci_evidence(export, self.actions, repository_id=42, commit_sha=HEAD, origins=origins)

    def test_an_origin_naming_the_member_links_that_reports_failed_case_to_its_job(self):
        report = self.join(self.results('a/junit.xml', 'b/junit.xml'), [
            dict(path='ci.zip', member='a/junit.xml', job_id=2401, step=2), dict(path='ci.zip', member='b/junit.xml', job_id=2402)])
        origins = {o['report_path_sha256']: o for o in report['origins']}
        assert origins[digest(['ci.zip', 'a/junit.xml'])]['job_conclusion'] == 'failure'
        assert origins[digest(['ci.zip', 'b/junit.xml'])]['job_conclusion'] == 'success'
        assert {c['origin']['report_path_sha256'] for c in report['failed_cases']} == set(origins)

    def test_an_origin_without_the_member_names_no_selected_report(self):
        with pytest.raises(ValueError, match='unselected report'):
            self.join(self.results('a/junit.xml'), [dict(path='ci.zip', job_id=2401)])

    def test_a_member_origin_for_a_plain_report_names_no_selected_report(self):
        with pytest.raises(ValueError, match='unselected report'):
            self.join(self.results('a/junit.xml'), [dict(path='ci.zip', member='b/junit.xml', job_id=2401)])
