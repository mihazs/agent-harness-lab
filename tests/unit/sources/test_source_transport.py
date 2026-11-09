from dataclasses import replace
import io
import json
from unittest.mock import Mock, patch

import pytest

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


class TestSourceTransport:
    @pytest.fixture(autouse=True)
    def _transport(self):
        self.base = 'https://api.example.invalid/v1/items'
        self.source = SourceSpec('api', 'synthetic', 'v1', digest(self.base), digest('project'),
            digest('scope'), identity_provenance='source_declared_id', auth_mode='configured', enabled=True)
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256,
                          authorization='Bearer SYNTHETIC_SECRET', decode=lambda value, headers: value)
        with patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection') as wire:
            self.wire = wire
            self.connection = self.wire.return_value
            yield

    def collect(self, **limits):
        return JsonAPI(self.base, **limits).pages(self.source, **self.scope)

    def test_authorized_pages_use_get_and_exact_origin_with_no_proxy_or_retry(self):
        self.connection.getresponse.side_effect = [Response(dict(items=[1], next='?page=2')), Response(dict(items=[2], next=None))]
        assert self.collect() == (1, 2)
        assert self.connection.request.call_count == 2
        assert self.connection.request.call_args.args[:2] == ('GET', '/v1/items?page=2')
        assert self.connection.close.call_count == 2
        assert self.wire.call_args.kwargs['context'].check_hostname

    def test_default_collection_exhausts_pages_records_and_bytes_without_a_deadline(self):
        large='synthetic '*1000000
        pages=[]
        for index in range(21):
            items=list(range(index*500,(index+1)*500))
            if index==0: items[0]=large
            pages.append(Response(dict(items=items,next=f'?page={index+2}' if index<20 else None)))
        self.connection.getresponse.side_effect=pages
        with patch('agent_harness_lab.sources.transport.time.monotonic',side_effect=AssertionError('no implicit deadline')):
            captured=self.collect()
        assert len(captured) == 10500
        assert captured[0] == large
        assert captured[-1] == 10499
        assert self.connection.request.call_count == 21
        assert self.wire.call_args.kwargs['timeout'] is None

    def test_long_endpoint_and_explicit_limits_above_former_ceilings(self):
        long_url=self.base+'/'+'synthetic'*1200
        assert JsonAPI(long_url).base == long_url
        self.connection.getresponse.return_value=Response(dict(items=[1],next=None))
        assert self.collect(max_pages=101,max_bytes=9*1024*1024,timeout=301,seconds=601) == (1,)
        assert self.wire.call_args.kwargs['timeout'] > 300

    def test_scope_and_auth_fail_before_connection(self):
        for source in (replace(self.source, enabled=False), replace(self.source, root_sha256=digest('wrong')),
                       replace(self.source, project_sha256=digest('foreign'))):
            with pytest.raises(TransportError): JsonAPI(self.base).pages(source, **self.scope)
        for token in (None, 'Bearer bad\nheader'):
            with pytest.raises(TransportError): JsonAPI(self.base).pages(self.source, **{**self.scope, 'authorization': token})
        self.wire.assert_not_called()

    @pytest.mark.parametrize('target', [
        pytest.param('https://evil.invalid/v1/items', id='cross-origin'),
        pytest.param('http://api.example.invalid/v1/items', id='downgrade'),
        pytest.param('/other', id='path-escape'),
        pytest.param('/v1/items/%2e%2e/private', id='encoded-traversal'),
        pytest.param('/v1/items/%252e%252e/private', id='double-encoded-traversal'),
        pytest.param('/v1/items/%5c..%5cprivate', id='encoded-backslash-traversal'),
        pytest.param('https://user:password@api.example.invalid/v1/items', id='userinfo')])
    def test_cross_origin_downgrade_path_escape_and_encoded_traversal_stop_before_second_request(self, target):
        self.connection.getresponse.side_effect = None
        self.connection.getresponse.return_value = Response(dict(items=[], next=target))
        with pytest.raises(TransportError): self.collect()
        assert self.connection.request.call_count == 1

    def test_http_failures_and_timeout_are_redacted_and_never_retried(self):
        for status in (301, 302, 401, 429, 500):
            self.connection.reset_mock()
            response = Response(b'SYNTHETIC_SECRET', status)
            self.connection.getresponse.return_value = response
            with pytest.raises(TransportError) as error: self.collect()
            assert 'SYNTHETIC_SECRET' not in str(error.value)
            assert self.connection.request.call_count == 1
            assert response.closed
        self.connection.getresponse.side_effect = TimeoutError('SYNTHETIC_SECRET')
        with pytest.raises(TransportError) as error: self.collect()
        assert 'SYNTHETIC_SECRET' not in str(error.value)

    def test_caps_partial_json_encoding_and_duplicate_keys_fail_closed(self):
        cases = [(Response(dict(items=[], next='?page=2')), {'max_pages': 1}),
                 (Response(b'123456'), {'max_bytes': 5}),
                 (Response(b'{}', headers={'Content-Length': '20'}), {}),
                 (Response(b'{"items":[],"items":[],"next":null}'), {}),
                 (Response(b'{"items":['), {}),
                 (Response(b'{}', headers={'Content-Encoding': 'gzip'}), {})]
        for response, limits in cases:
            self.connection.getresponse.return_value = response
            with pytest.raises(TransportError): self.collect(**limits)

    def test_cycle_and_deadline_are_enforced(self):
        self.connection.getresponse.return_value = Response(dict(items=[], next=self.base))
        with pytest.raises(TransportError): self.collect()
        with patch('agent_harness_lab.sources.transport.time.monotonic', side_effect=[0, 2]):
            with pytest.raises(TransportError): self.collect(seconds=1)
        self.connection.getresponse.return_value = Response(dict(items=[], next=None))
        with patch('agent_harness_lab.sources.transport.time.monotonic', side_effect=[0, 0, 0, 0, 2]):
            with pytest.raises(TransportError): self.collect(seconds=1)

    def test_provider_decoder_exceptions_cannot_expose_response_contents(self):
        self.connection.getresponse.return_value = Response(dict(items=[], next=None))
        decoder = Mock(side_effect=RuntimeError('SYNTHETIC_SECRET'))
        with pytest.raises(TransportError) as error:
            JsonAPI(self.base).pages(self.source, **{**self.scope, 'decode': decoder})
        assert 'SYNTHETIC_SECRET' not in str(error.value)

    def test_failed_later_page_cannot_advance_registry_checkpoint(self, tmp_path):
        record = EvidenceRecord(self.source.source_key, digest('native'), digest('revision'), digest('body'),
            'contextual_assertion', 'page', '2026-01-01T00:00:00Z')
        self.connection.getresponse.side_effect = [Response(dict(items=[record.to_dict()], next='?page=2')),
                                                   Response(b'SYNTHETIC_SECRET', 429)]
        collector = Mock()
        collector.read.side_effect = lambda source: self.collect()
        registry = Registry()
        registry.register(Capability('api', 'synthetic', 'v1', ('contextual_assertion',)), collector)
        scope = {k: self.scope[k] for k in ('project_sha256', 'scope_sha256')}
        store = SourceStore(tmp_path/'cache.json', **scope)
        with pytest.raises(TransportError): registry.ingest(self.source, store, **scope)
        assert not store.path.exists()

    def test_malformed_endpoints_and_nonfinite_bounds_are_rejected(self):
        for url in ('http://api.example.invalid/x', 'https://user:secret@api.example.invalid/x',
                    'https://api.example.invalid:bad/x', 'https://api.example.invalid/x#secret'):
            with pytest.raises(TransportError): JsonAPI(url)
        for limits in ({'max_pages': True}, {'max_bytes': 0}, {'timeout': float('nan')}, {'seconds': -1}):
            with pytest.raises(TransportError): JsonAPI(self.base, **limits)
