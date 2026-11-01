from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.transport import JsonAPI, TransportError
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.store import SourceStore


class Response(io.BytesIO):
    def __init__(self, value, status=200, headers=None):
        super().__init__(json.dumps(value).encode() if not isinstance(value, bytes) else value)
        self.status, self.headers = status, headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def getheaders(self):
        return list(self.headers.items())


class SourceTransportTests(unittest.TestCase):
    def setUp(self):
        self.base = 'https://api.example.invalid/v1/items'
        self.source = SourceSpec('api', 'synthetic', 'v1', digest(self.base), digest('project'),
            digest('scope'), identity_provenance='source_declared_id', auth_mode='configured', enabled=True)
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256,
                          authorization='Bearer SYNTHETIC_SECRET', decode=lambda value, headers: value)
        wire = patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection')
        self.wire = wire.start()
        self.addCleanup(wire.stop)
        self.connection = self.wire.return_value

    def collect(self, **limits):
        return JsonAPI(self.base, **limits).pages(self.source, **self.scope)

    def test_authorized_pages_use_get_and_exact_origin_with_no_proxy_or_retry(self):
        self.connection.getresponse.side_effect = [Response(dict(items=[1], next='?page=2')), Response(dict(items=[2], next=None))]
        self.assertEqual(self.collect(), (1, 2))
        self.assertEqual(self.connection.request.call_count, 2)
        self.assertEqual(self.connection.request.call_args.args[:2], ('GET', '/v1/items?page=2'))
        self.assertEqual(self.connection.close.call_count, 2)
        self.assertTrue(self.wire.call_args.kwargs['context'].check_hostname)

    def test_scope_and_auth_fail_before_connection(self):
        for source in (replace(self.source, enabled=False), replace(self.source, root_sha256=digest('wrong')),
                       replace(self.source, project_sha256=digest('foreign'))):
            with self.assertRaises(TransportError): JsonAPI(self.base).pages(source, **self.scope)
        for token in (None, 'Bearer bad\nheader'):
            with self.assertRaises(TransportError): JsonAPI(self.base).pages(self.source, **{**self.scope, 'authorization': token})
        self.wire.assert_not_called()

    def test_cross_origin_downgrade_path_escape_and_encoded_traversal_stop_before_second_request(self):
        for target in ('https://evil.invalid/v1/items', 'http://api.example.invalid/v1/items',
                       '/other', '/v1/items/%2e%2e/private', '/v1/items/%252e%252e/private',
                       '/v1/items/%5c..%5cprivate',
                       'https://user:password@api.example.invalid/v1/items'):
            with self.subTest(target=target):
                self.connection.reset_mock()
                self.connection.getresponse.side_effect = None
                self.connection.getresponse.return_value = Response(dict(items=[], next=target))
                with self.assertRaises(TransportError): self.collect()
                self.assertEqual(self.connection.request.call_count, 1)

    def test_http_failures_and_timeout_are_redacted_and_never_retried(self):
        for status in (301, 302, 401, 429, 500):
            self.connection.reset_mock()
            response = Response(b'SYNTHETIC_SECRET', status)
            self.connection.getresponse.return_value = response
            with self.assertRaises(TransportError) as error: self.collect()
            self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
            self.assertEqual(self.connection.request.call_count, 1)
            self.assertTrue(response.closed)
        self.connection.getresponse.side_effect = TimeoutError('SYNTHETIC_SECRET')
        with self.assertRaises(TransportError) as error: self.collect()
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))

    def test_caps_partial_json_encoding_and_duplicate_keys_fail_closed(self):
        cases = [(Response(dict(items=[], next='?page=2')), {'max_pages': 1}),
                 (Response(b'123456'), {'max_bytes': 5}),
                 (Response(b'{}', headers={'Content-Length': '20'}), {}),
                 (Response(b'{"items":[],"items":[],"next":null}'), {}),
                 (Response(b'{"items":['), {}),
                 (Response(b'{}', headers={'Content-Encoding': 'gzip'}), {})]
        for response, limits in cases:
            self.connection.getresponse.return_value = response
            with self.assertRaises(TransportError): self.collect(**limits)

    def test_cycle_and_deadline_are_enforced(self):
        self.connection.getresponse.return_value = Response(dict(items=[], next=self.base))
        with self.assertRaises(TransportError): self.collect()
        with patch('agent_harness_lab.sources.transport.time.monotonic', side_effect=[0, 2]):
            with self.assertRaises(TransportError): self.collect(seconds=1)
        self.connection.getresponse.return_value = Response(dict(items=[], next=None))
        with patch('agent_harness_lab.sources.transport.time.monotonic', side_effect=[0, 0, 0, 0, 2]):
            with self.assertRaises(TransportError): self.collect(seconds=1)

    def test_provider_decoder_exceptions_cannot_expose_response_contents(self):
        self.connection.getresponse.return_value = Response(dict(items=[], next=None))
        decoder = Mock(side_effect=RuntimeError('SYNTHETIC_SECRET'))
        with self.assertRaises(TransportError) as error:
            JsonAPI(self.base).pages(self.source, **{**self.scope, 'decode': decoder})
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))

    def test_failed_later_page_cannot_advance_registry_checkpoint(self):
        record = EvidenceRecord(self.source.source_key, digest('native'), digest('revision'), digest('body'),
            'contextual_assertion', 'page', '2026-01-01T00:00:00Z')
        self.connection.getresponse.side_effect = [Response(dict(items=[record.to_dict()], next='?page=2')),
                                                   Response(b'SYNTHETIC_SECRET', 429)]
        collector = Mock()
        collector.read.side_effect = lambda source: self.collect()
        registry = Registry()
        registry.register(Capability('api', 'synthetic', 'v1', ('contextual_assertion',)), collector)
        with tempfile.TemporaryDirectory() as folder:
            scope = {k: self.scope[k] for k in ('project_sha256', 'scope_sha256')}
            store = SourceStore(Path(folder)/'cache.json', **scope)
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertFalse(store.path.exists())

    def test_malformed_endpoints_and_nonfinite_bounds_are_rejected(self):
        for url in ('http://api.example.invalid/x', 'https://user:secret@api.example.invalid/x',
                    'https://api.example.invalid:bad/x', 'https://api.example.invalid/x#secret'):
            with self.assertRaises(TransportError): JsonAPI(url)
        for limits in ({'max_pages': True}, {'max_bytes': 0}, {'timeout': float('nan')}, {'seconds': -1}):
            with self.assertRaises(TransportError): JsonAPI(self.base, **limits)
