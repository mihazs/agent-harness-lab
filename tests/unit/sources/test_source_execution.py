"""Content-free execution receipts use synthetic identities, never private histories."""
from dataclasses import FrozenInstanceError, replace
from contextlib import redirect_stderr, redirect_stdout
import io
import hashlib
import json
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.execution import (CoverageManifest, ExecutionIdentity, ExecutionMetadata,
    IdentityField, MissingField, RecordRef, ScanBound)
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.snapshot import LocalExport, Snapshot
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.store import CommitUncertain, SourceStore


def metadata_for(source, record):
    ref = RecordRef(record.source_sha256, record.native_id_sha256, record.revision_sha256)
    field = IdentityField('session', 'synthetic.session', digest('session'), 'declared', 'source_declared',
        (ref,), reason='source_declaration', observed_at='2026-01-01T00:00:00Z')
    coverage = CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256,
        source.version, 'synthetic-parser-v1', (ScanBound('records', 100, 1),),
        missing_fields=(MissingField('served_model', 'not_observed'),), reported_completeness='partial')
    return ExecutionMetadata((ExecutionIdentity(ref, (field,)),), coverage)


class TestExecutionContract:
    @pytest.fixture(autouse=True)
    def _contract(self):
        self.source = SourceSpec('local_file', 'ahl-metadata', 'v2', digest('root'), digest('project'),
            digest('scope'), identity_provenance='source_declared_id', enabled=True)
        self.record = EvidenceRecord(self.source.source_key, digest('session'), digest('revision'), digest('content'),
            'behavioral_event', 'native_session', '2026-01-01T00:00:00Z')
        self.metadata = metadata_for(self.source, self.record)
        self.execution, self.coverage = self.metadata.identities[0], self.metadata.coverage
        self.ref, self.field = self.execution.subject, self.execution.fields[0]

    def test_frozen_contracts_round_trip_without_changing_v1_records(self):
        value = json.loads(json.dumps(self.metadata.to_dict()))
        restored = ExecutionMetadata.parse(value)
        assert restored == self.metadata
        assert restored.sha256 == self.metadata.sha256
        restored.validate(self.source, [self.record])
        assert self.record.schema_version == 1
        assert 'execution' not in self.record.to_dict()
        with pytest.raises(FrozenInstanceError): restored.coverage = None

    def test_requested_alias_never_supplies_an_unknown_served_version(self):
        requested = replace(self.field, name='requested_model', namespace='model.alias', value_sha256=digest('requested-alias'))
        execution = replace(self.execution, fields=(requested,))
        summary = replace(self.metadata, identities=(execution,)).summary()
        assert summary['fields']['requested_model']['declared'] == 1
        assert summary['fields']['served_model']['unknown'] == 1

    def test_unknown_is_null_and_conflict_retains_alternatives_without_selecting_one(self):
        unknown = IdentityField('effective_harness', 'harness.snapshot', None, 'unknown', 'unknown', (), reason='no_effective_binding')
        conflict = replace(self.field, value_sha256=None, status='conflicted', conflicts=(digest('a'), digest('b')),
            reason='conflicting_evidence')
        assert conflict.value_sha256 is None
        assert len(conflict.conflicts) == 2
        for value in ({**unknown.to_dict(), 'value_sha256': digest('')},
                      {**conflict.to_dict(), 'value_sha256': digest('a')},
                      {**conflict.to_dict(), 'conflicts': [digest('a'), digest('a')]}):
            with pytest.raises(ValueError): IdentityField.parse(value)

    @pytest.mark.parametrize('provenance', ['native_field', 'controller_observation'])
    def test_claimed_native_or_controller_labels_cannot_import_verified_execution_identity(self, provenance):
        claimed = replace(self.field, status='verified', provenance=provenance, reason='native_metadata')
        metadata = replace(self.metadata, identities=(replace(self.execution, fields=(claimed,)),))
        with pytest.raises(ValueError):
            metadata.validate(self.source, [self.record])

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(source_sha256=digest('foreign')), id='foreign-source'),
        pytest.param(dict(revision_sha256=digest('missing')), id='dangling-revision'),
    ])
    def test_foreign_and_dangling_subject_or_evidence_references_are_rejected(self, changes):
        ref = replace(self.ref, **changes)
        with pytest.raises(ValueError):
            replace(self.metadata, identities=(replace(self.execution, subject=ref),)).validate(self.source, [self.record])
        with pytest.raises(ValueError):
            replace(self.metadata, identities=(replace(self.execution, fields=(replace(self.field, evidence=(ref,)),)),)).validate(self.source, [self.record])

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(expected_scope_sha256=digest('foreign')), id='foreign-expected-scope'),
        pytest.param(dict(observed_scope_sha256=digest('foreign')), id='foreign-observed-scope'),
        pytest.param(dict(adapter_version='other'), id='other-adapter-version'),
        pytest.param(dict(source_sha256=digest('foreign')), id='foreign-source'),
    ])
    def test_declared_coverage_cannot_claim_a_foreign_scope_or_adapter(self, changes):
        with pytest.raises(ValueError):
            replace(self.metadata, coverage=replace(self.coverage, **changes)).validate(self.source, [self.record])

    def test_declared_coverage_cannot_contradict_its_bounds(self):
        with pytest.raises(ValueError): replace(self.coverage, reported_completeness='complete')
        with pytest.raises(ValueError): ScanBound('records', 1, 2)
        with pytest.raises(ValueError): ScanBound('records', True, 1)

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(namespace='/private/path'), id='path-namespace'),
        pytest.param(dict(reason='SYNTHETIC_SECRET'), id='free-text-reason'),
        pytest.param(dict(observed_at='yesterday'), id='bad-observed-at'),
        pytest.param(dict(effective_from='2026-02-01T00:00:00Z', effective_until='2026-01-01T00:00:00Z'), id='inverted-effective-range'),
        pytest.param(dict(name='arbitrary'), id='arbitrary-name'),
    ])
    def test_strict_fields_reject_paths_free_text_bad_times_and_unknown_names(self, changes):
        with pytest.raises(ValueError): replace(self.field, **changes)

    def test_strict_fields_metadata_and_mutable_collections_are_rejected(self):
        with pytest.raises(ValueError): replace(self.field, evidence=[self.ref])
        with pytest.raises(ValueError): ExecutionMetadata.parse({**self.metadata.to_dict(), 'text': 'SYNTHETIC_SECRET'})
        with pytest.raises(ValueError): replace(self.execution, fields=(self.field, self.field))

    def test_large_execution_evidence_and_unlimited_scan_counts_round_trip(self):
        records = tuple(replace(self.record, native_id_sha256=digest(i)) for i in range(1001))
        refs = tuple(RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in records)
        field = replace(self.field, evidence=refs[:9], status='conflicted', value_sha256=None,
            conflicts=tuple(digest(('alternative', i)) for i in range(9)))
        metadata = ExecutionMetadata(tuple(ExecutionIdentity(ref, (field,)) for ref in refs))
        restored = ExecutionMetadata.parse(metadata.to_dict())
        restored.validate(self.source, records)
        assert restored == metadata
        for limit in (None, 2**41):
            bound = ScanBound('records', limit, 2**41)
            assert ScanBound.parse(bound.to_dict()) == bound
        for observed in (-1, True, 1.5):
            with pytest.raises(ValueError): ScanBound('records', None, observed)


class ExportCase:
    """Synthetic v2 export and journal under tmp_path, shared by the integration and unlimited-export tests."""
    @pytest.fixture(autouse=True)
    def _export(self, tmp_path):
        self.root = tmp_path
        self.source = SourceSpec('local_file', 'ahl-metadata', 'v2', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest('synthetic-scope'), identity_provenance='source_declared_id', enabled=True)
        self.record = EvidenceRecord(self.source.source_key, digest('session'), digest('revision'), digest('SYNTHETIC_SECRET'),
            'behavioral_event', 'native_session', '2026-01-01T00:00:00Z')
        self.metadata = metadata_for(self.source, self.record)
        self.store = SourceStore(self.root/'cache.json', project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        self.manifest, self.data = self.root/'source.json', self.root/'records.json'
        self.write_export()

    def write_export(self, metadata=None):
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='records.json')))
        self.data.write_text(json.dumps(dict(schema_version=2, records=[self.record.to_dict()],
            execution_metadata=(metadata or self.metadata).to_dict())))

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--cache', str(self.store.path),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256])
        assert 'SYNTHETIC_SECRET' not in output.getvalue()+error.getvalue()
        assert str(self.root) not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def ingest(self, metadata=None, *, before=None, snapshot=None, records=None, source=None):
        return self.store.ingest(source or self.source, [self.record] if records is None else records, cursor_before=before,
            snapshot_sha256=snapshot or digest('same-source-snapshot'), metadata=self.metadata if metadata is None else metadata)


class TestExecutionIntegration(ExportCase):
    def test_explicit_v2_export_round_trip_preview_import_status_and_legacy_v1_read(self):
        code, preview = self.invoke('preview'); assert code == 0
        assert preview['execution_metadata']['fields']['served_model']['unknown'] == 1
        assert self.invoke('import')[0] == 0
        assert self.invoke('import')[1]['imported'] == 0
        code, status = self.invoke('status'); assert code == 0
        assert status['snapshot_sha256'] == preview['checkpoint_snapshot_sha256']
        assert status['source_snapshot_sha256'] == preview['snapshot_sha256']
        assert status['capture_completeness'] == 'unknown'
        assert status['execution_metadata']['declared_coverage']['reported_completeness'] == 'partial'
        assert self.store.capture(self.source) == self.metadata
        assert 'SYNTHETIC_SECRET' not in self.store.path.read_text()
        assert json.loads(self.store.path.read_text())['schema_version'] == 4
        legacy = replace(self.source, version='v1')
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=legacy.to_dict(), data='records.json')))
        self.data.write_text(json.dumps([self.record.to_dict()]))
        snapshot = LocalExport(self.manifest).read(legacy)
        assert snapshot.metadata is None
        assert snapshot.records == (self.record,)

    def test_unknown_normalized_export_version_is_rejected_before_reading_payload(self):
        unsupported = replace(self.source, version='v4')
        with patch('agent_harness_lab.sources.snapshot.read_regular', side_effect=AssertionError('no payload read')):
            with pytest.raises(ValueError): LocalExport(self.manifest).read(unsupported)

    def test_metadata_only_changes_recurrence_and_idempotency_preserve_all_checkpoints(self):
        first = self.ingest()
        replay = self.ingest(before=first.cursor_after_sha256)
        assert replay.cursor_after_sha256 == first.cursor_after_sha256
        newer = replace(self.metadata, coverage=replace(self.metadata.coverage, parser_version='synthetic-parser-v2'))
        second = self.ingest(newer, before=replay.cursor_after_sha256, records=[])
        third = self.ingest(before=second.cursor_after_sha256)
        cursors = [first.cursor_after_sha256, second.cursor_after_sha256, third.cursor_after_sha256]
        assert len(set(cursors)) == 3
        assert [self.store.capture(self.source, c) for c in cursors] == [self.metadata, newer, self.metadata]
        assert self.store.status(self.source)['revisions'] == 1
        assert self.store.status(self.source)['checkpoints'] == 3

    def test_source_version_upgrade_changes_capture_identity_without_duplicating_native_records(self):
        metadata = replace(self.metadata, coverage=None)
        first = self.ingest(metadata)
        upgraded = replace(self.source, version='v3')
        second = self.ingest(metadata, source=upgraded, before=first.cursor_after_sha256)
        assert first.cursor_after_sha256 != second.cursor_after_sha256
        assert second.imported == 0
        state = json.loads(self.store.path.read_text())
        envelopes = state['captures'][self.source.source_key]
        assert envelopes[first.cursor_after_sha256]['source_spec_sha256'] != envelopes[second.cursor_after_sha256]['source_spec_sha256']
        assert self.store.capture(upgraded, first.cursor_after_sha256) == metadata

    def test_delayed_revision_does_not_rebind_historical_code_or_unknown_served_model(self):
        identity = self.metadata.identities[0]
        code = replace(identity.fields[0], name='code_revision', namespace='synthetic.git', value_sha256=digest('old-code'))
        old = replace(self.metadata, identities=(replace(identity, fields=(code,)),))
        first = self.ingest(old)
        record = replace(self.record, revision_sha256=digest('new-revision'), content_sha256=digest('new-content'))
        newer = metadata_for(self.source, record)
        newer = replace(newer, identities=(replace(newer.identities[0], fields=(replace(code, evidence=(newer.identities[0].subject,), value_sha256=digest('new-code')),)),))
        self.ingest(newer, records=[record], before=first.cursor_after_sha256, snapshot=digest('later-source-snapshot'))
        historical = self.store.capture(self.source, first.cursor_after_sha256)
        assert historical == old
        assert historical.identities[0].fields[0].value_sha256 == digest('old-code')
        assert historical.summary()['fields']['served_model']['unknown'] == 1
        assert historical.identities[0].fields[0].observed_at == '2026-01-01T00:00:00Z'

    @pytest.mark.parametrize('version', [pytest.param(1, id='v1'), pytest.param(2, id='v2')])
    def test_v1_and_v2_journals_migrate_only_after_success_and_keep_existing_history(self, version):
        self.store = SourceStore(self.root/f'legacy-{version}.json', project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        seed = self.store.ingest(self.source, [self.record], snapshot_sha256=digest('legacy-source'))
        state = json.loads(self.store.path.read_text()); state['schema_version'] = version; state.pop('captures')
        if version == 1: state.pop('snapshots')
        self.store.path.write_text(json.dumps(state)); original = self.store.path.read_bytes()
        self.store.status(self.source)
        assert self.store.capture(self.source) is None
        assert self.store.path.read_bytes() == original
        invalid = replace(self.metadata, coverage=replace(self.metadata.coverage, expected_scope_sha256=digest('foreign')))
        with pytest.raises(ValueError): self.ingest(invalid, before=seed.cursor_after_sha256)
        assert self.store.path.read_bytes() == original
        self.ingest(before=seed.cursor_after_sha256)
        migrated = json.loads(self.store.path.read_text())
        assert migrated['schema_version'] == 4
        assert migrated['records'] == state['records']
        assert migrated['cursors'][self.source.source_key][0] == seed.cursor_after_sha256

    def test_verified_labels_are_rejected_through_export_registry_and_direct_store(self):
        identity = self.metadata.identities[0]
        field = replace(identity.fields[0], status='verified', provenance='native_field')
        invalid = replace(self.metadata, identities=(replace(identity, fields=(field,)),))
        self.write_export(invalid)
        assert self.invoke('import')[0] == 2
        assert not self.store.path.exists()
        with pytest.raises(ValueError): self.ingest(invalid)
        assert not self.store.path.exists()
        collector = Mock()
        collector.read.return_value = Snapshot(digest('snapshot'), (self.record,), invalid)
        registry = Registry(); registry.register(Capability('local_file', 'ahl-metadata', 'v2', ('behavioral_event',)), collector)
        with pytest.raises(ValueError):
            registry.preview(self.source, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)

    @pytest.mark.parametrize('kind', ['content', 'source', 'cursor'])
    def test_capture_envelope_tampering_is_rejected_without_rewriting_the_journal(self, kind):
        receipt = self.ingest()
        state = json.loads(self.store.path.read_bytes()); captures = state['captures'][self.source.source_key]
        envelope = captures[receipt.cursor_after_sha256]
        if kind == 'content': envelope['metadata']['identities'][0]['fields'][0]['value_sha256'] = digest('changed')
        elif kind == 'source': envelope['source_spec_sha256'] = digest('foreign')
        else: captures[digest('not-a-checkpoint')] = captures.pop(receipt.cursor_after_sha256)
        self.store.path.write_text(json.dumps(state)); before = self.store.path.read_bytes()
        with pytest.raises(ValueError): self.store.status(self.source)
        assert self.store.path.read_bytes() == before

    def test_empty_metadata_preserves_snapshot_identity_and_metadata_requires_snapshot_mode(self):
        empty = ExecutionMetadata(())
        snapshot = digest('unwrapped-snapshot')
        self.ingest(empty, snapshot=snapshot)
        assert self.store.status(self.source)['snapshot_sha256'] == snapshot
        assert self.store.capture(self.source) is None
        before = self.store.path.read_bytes()
        with pytest.raises(ValueError): self.store.ingest(self.source, [self.record], metadata=self.metadata)
        assert self.store.path.read_bytes() == before

    def test_ambiguous_commit_keeps_records_and_execution_metadata_in_one_transaction(self):
        from agent_harness_lab.monitoring.state import atomic_json
        def commit_then_error(path, state):
            atomic_json(path, state)
            raise OSError('synthetic acknowledgement loss')
        with patch('agent_harness_lab.sources.store.atomic_json', side_effect=commit_then_error):
            with pytest.raises(CommitUncertain): self.ingest()
        assert self.store.status(self.source)['revisions'] == 1
        assert self.store.capture(self.source) == self.metadata


class TestUnlimitedExport(ExportCase):
    def test_complete_large_export_keeps_all_records_past_old_byte_and_count_caps(self):
        records = tuple(replace(self.record, native_id_sha256=digest(i), record_type='record.'+'x'*400)
            for i in range(10001))
        self.data.write_text(json.dumps(dict(schema_version=2, records=[r.to_dict() for r in records],
            execution_metadata=ExecutionMetadata(()).to_dict())))
        assert self.data.stat().st_size > 8*1024*1024
        snapshot = LocalExport(self.manifest).read(self.source)
        assert snapshot.records == records
        assert snapshot.sha256 == hashlib.sha256(self.data.read_bytes()).hexdigest()
