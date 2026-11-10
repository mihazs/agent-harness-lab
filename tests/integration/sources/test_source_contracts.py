from dataclasses import replace
import json
import socket
from unittest.mock import patch

import pytest

from agent_harness_lab.sources.schema import EvidenceRecord, ImportReceipt, SourceSpec
from agent_harness_lab.sources.registry import Capability, Registry


class TestSourceContracts:
    @pytest.fixture(autouse=True)
    def _contracts(self):
        self.source = SourceSpec('local_file', 'synthetic', 'v1', '1'*64, '2'*64, '3'*64)
        self.record = EvidenceRecord(self.source.source_key, '4'*64, '5'*64, '6'*64,
            'contextual_assertion', 'memory-page', '2026-01-01T00:00:00Z')

    def test_roundtrip_all_contracts_and_unknown_evidence_stays_unknown(self):
        receipt = ImportReceipt(self.source.source_key, 0, 0, 0)
        for value in (self.source, self.record, receipt):
            assert type(value).parse(json.loads(json.dumps(value.to_dict()))) == value
            with pytest.raises(ValueError): type(value).parse({**value.to_dict(), 'body': 'SYNTHETIC_SECRET'})
            with pytest.raises(ValueError): replace(value, schema_version=True)
        assert self.record.original_at is None
        assert self.record.capture_completeness == 'unknown'
        assert self.record.independence == 'unknown'
        assert self.record.trust_boundary == 'untrusted_data'
        assert 'outcome' not in self.record.to_dict()

    def test_mutable_source_settings_do_not_change_native_identity(self):
        upgraded = replace(self.source, enabled=True, version='v2', auth_mode='configured', identity_provenance='source_declared_id')
        assert upgraded.source_key == self.source.source_key
        for name in ('root_sha256', 'project_sha256', 'scope_sha256'):
            assert replace(self.source, **{name: '7'*64}).source_key != self.source.source_key

    @pytest.mark.parametrize('changes', [
        pytest.param({'native_id_sha256': '/private/session'}, id='path-native-id'),
        pytest.param({'content_sha256': 'secret'}, id='raw-content-digest'),
        pytest.param({'record_type': '../escape'}, id='escaping-record-type'),
        pytest.param({'original_at': 'yesterday'}, id='bad-original-at'),
        pytest.param({'collected_at': '2026-01-01T00:00:00'}, id='naive-collected-at'),
        pytest.param({'causal_status': 'proven'}, id='forged-causal-status'),
        pytest.param({'plane': 'verified_success'}, id='forged-plane'),
        pytest.param({'provenance': 'executable_grader'}, id='forged-provenance'),
        pytest.param({'trust_boundary': 'trusted_instruction'}, id='forged-trust-boundary'),
    ])
    def test_strict_record_metadata_rejects_paths_raw_ids_bad_timestamps_and_forged_outcomes(self, changes):
        with pytest.raises(ValueError): replace(self.record, **changes)

    def test_strict_source_metadata_rejects_writable_and_executable_declarations(self):
        with pytest.raises(ValueError): replace(self.source, read_only=False)
        with pytest.raises(ValueError): replace(self.source, enabled=1)
        with pytest.raises(ValueError): replace(self.source, provider='module:call()')

    def test_planes_keep_context_artifacts_and_grader_references_distinct(self):
        for plane in ('behavioral_event', 'contextual_assertion', 'development_artifact', 'grader_reference'):
            record = replace(self.record, plane=plane)
            assert record.plane == plane
            assert 'outcome' not in record.to_dict()

    @pytest.mark.parametrize('changes', [
        pytest.param({'cursor_after_sha256': '9'*64}, id='cursor-after-failure'),
        pytest.param({'errors': 1, 'status': 'complete'}, id='complete-with-errors'),
        pytest.param({'limit_reached': True, 'status': 'complete'}, id='complete-at-limit'),
        pytest.param({'imported': True}, id='boolean-imported'),
        pytest.param({'skipped': -1}, id='negative-skipped'),
        pytest.param({'errors': True}, id='boolean-errors'),
        pytest.param({'capture_completeness': 'complete'}, id='claimed-complete-capture'),
    ])
    def test_failed_and_partial_receipts_cannot_advance_cursors(self, changes):
        receipt = ImportReceipt(self.source.source_key, 1, 0, 0, '8'*64, '8'*64)
        with pytest.raises(ValueError): replace(receipt, **changes)

    def test_committed_receipt_keeps_capture_completeness_unknown(self):
        receipt = ImportReceipt(self.source.source_key, 1, 0, 0, '8'*64, '8'*64)
        committed = replace(receipt, status='complete', cursor_after_sha256='9'*64)
        assert committed.capture_completeness == 'unknown'

    def test_registry_is_empty_explicit_and_never_opens_sources(self):
        source = replace(self.source, enabled=True, identity_provenance='trusted_git_remote')
        registry = Registry()
        scope = dict(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        with patch('builtins.open', side_effect=AssertionError('no source reads')), \
                patch('subprocess.Popen', side_effect=AssertionError('no processes')), \
                patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            assert registry.probe(source, **scope)['status'] == 'unsupported_version'
            registry.register(Capability('local_file', 'synthetic', 'v1', ('contextual_assertion',)))
            report = registry.probe(source, **scope)
            assert report['status'] == 'recognized'
            assert not report['execution_authorized']
            assert not report['source_read']
            assert registry.probe(self.source, **scope)['status'] == 'disabled'
            assert registry.probe(replace(source, identity_provenance='unverified'), **scope)['status'] == 'identity_unverified'
            assert registry.probe(replace(source, version='v2'), **scope)['status'] == 'unsupported_version'
            assert registry.probe(source, **{**scope, 'project_sha256': '9'*64})['status'] == 'scope_mismatch'
        with pytest.raises(ValueError): registry.register(Capability('local_file', 'synthetic', 'v1', ('contextual_assertion',)))


class TestUnlimitedContract:
    def test_large_import_counts_and_structurally_valid_metadata_are_preserved(self):
        from agent_harness_lab.sources.schema import label, timestamp
        receipt = ImportReceipt('a'*64, 100001, 2**41, 100001)
        assert ImportReceipt.parse(receipt.to_dict()) == receipt
        label('native.'+'x'*1000)
        timestamp('2026-01-01T00:00:00.'+'1'*100+'+00:00')
        with pytest.raises(ValueError): label('private/path')
        with pytest.raises(ValueError): timestamp('2026-01-01T00:00:00')
