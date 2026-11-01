from dataclasses import replace
import json
import socket
import unittest
from unittest.mock import patch

from agent_harness_lab.sources.schema import EvidenceRecord, ImportReceipt, SourceSpec
from agent_harness_lab.sources.registry import Capability, Registry


class SourceContractsTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceSpec('local_file', 'synthetic', 'v1', '1'*64, '2'*64, '3'*64)
        self.record = EvidenceRecord(self.source.source_key, '4'*64, '5'*64, '6'*64,
            'contextual_assertion', 'memory-page', '2026-01-01T00:00:00Z')

    def test_roundtrip_all_contracts_and_unknown_evidence_stays_unknown(self):
        receipt = ImportReceipt(self.source.source_key, 0, 0, 0)
        for value in (self.source, self.record, receipt):
            self.assertEqual(type(value).parse(json.loads(json.dumps(value.to_dict()))), value)
            with self.assertRaises(ValueError): type(value).parse({**value.to_dict(), 'body': 'SYNTHETIC_SECRET'})
            with self.assertRaises(ValueError): replace(value, schema_version=True)
        self.assertIsNone(self.record.original_at)
        self.assertEqual(self.record.capture_completeness, 'unknown')
        self.assertEqual(self.record.independence, 'unknown')
        self.assertNotIn('outcome', self.record.to_dict())

    def test_mutable_source_settings_do_not_change_native_identity(self):
        upgraded = replace(self.source, enabled=True, version='v2', auth_mode='configured', identity_provenance='source_declared_id')
        self.assertEqual(upgraded.source_key, self.source.source_key)
        for name in ('root_sha256', 'project_sha256', 'scope_sha256'):
            self.assertNotEqual(replace(self.source, **{name: '7'*64}).source_key, self.source.source_key)

    def test_strict_metadata_rejects_paths_raw_ids_bad_timestamps_and_forged_outcomes(self):
        for changes in ({'native_id_sha256': '/private/session'}, {'content_sha256': 'secret'},
                        {'record_type': '../escape'}, {'original_at': 'yesterday'},
                        {'collected_at': '2026-01-01T00:00:00'}, {'causal_status': 'proven'},
                        {'plane': 'verified_success'}, {'provenance': 'executable_grader'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(self.record, **changes)
        with self.assertRaises(ValueError): replace(self.source, read_only=False)
        with self.assertRaises(ValueError): replace(self.source, enabled=1)
        with self.assertRaises(ValueError): replace(self.source, provider='module:call()')

    def test_planes_keep_context_artifacts_and_grader_references_distinct(self):
        for plane in ('behavioral_event', 'contextual_assertion', 'development_artifact', 'grader_reference'):
            record = replace(self.record, plane=plane)
            self.assertEqual(record.plane, plane)
            self.assertNotIn('outcome', record.to_dict())

    def test_failed_and_partial_receipts_cannot_advance_cursors(self):
        receipt = ImportReceipt(self.source.source_key, 1, 0, 0, '8'*64, '8'*64)
        for changes in ({'cursor_after_sha256': '9'*64}, {'errors': 1, 'status': 'complete'},
                        {'limit_reached': True, 'status': 'complete'}, {'imported': True},
                        {'skipped': -1}, {'errors': 100001}, {'capture_completeness': 'complete'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(receipt, **changes)
        committed = replace(receipt, status='complete', cursor_after_sha256='9'*64)
        self.assertEqual(committed.capture_completeness, 'unknown')

    def test_registry_is_empty_explicit_and_never_opens_sources(self):
        source = replace(self.source, enabled=True, identity_provenance='trusted_git_remote')
        registry = Registry()
        scope = dict(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        with patch('builtins.open', side_effect=AssertionError('no source reads')), \
                patch('subprocess.Popen', side_effect=AssertionError('no processes')), \
                patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            self.assertEqual(registry.probe(source, **scope)['status'], 'unsupported_version')
            registry.register(Capability('local_file', 'synthetic', 'v1', ('contextual_assertion',)))
            report = registry.probe(source, **scope)
            self.assertEqual(report['status'], 'recognized')
            self.assertFalse(report['execution_authorized'])
            self.assertFalse(report['source_read'])
            self.assertEqual(registry.probe(self.source, **scope)['status'], 'disabled')
            self.assertEqual(registry.probe(replace(source, identity_provenance='unverified'), **scope)['status'], 'identity_unverified')
            self.assertEqual(registry.probe(replace(source, version='v2'), **scope)['status'], 'unsupported_version')
            self.assertEqual(registry.probe(source, **{**scope, 'project_sha256': '9'*64})['status'], 'scope_mismatch')
        with self.assertRaises(ValueError): registry.register(Capability('local_file', 'synthetic', 'v1', ('contextual_assertion',)))
