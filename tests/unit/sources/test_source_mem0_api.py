"""Synthetic Mem0 OSS REST and hosted Platform v3 reads over a fake HTTPS wire: scoped, read-only, never a task outcome."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from unittest.mock import patch

import pytest

from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.mem0_api import (REST_PROVIDER, Mem0PlatformAPI, Mem0PlatformSelection,
    Mem0RestAPI, Mem0Selection)
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

OSS_BASE, PLATFORM_BASE = 'https://mem0.example.test', 'https://api.mem0.example.test'
OSS_KEY, PLATFORM_KEY = 'm0sk_SYNTHETIC_SECRET', 'Token SYNTHETIC_PLATFORM_SECRET'
SECRETS = ('SYNTHETIC_SECRET', 'SYNTHETIC_PLATFORM_SECRET', 'SYNTHETIC_PRIVATE', 'user-aaa', 'mem-edited')
AT = '2031-04-05T06:07:08Z'
JSON = {'Content-Type': 'application/json'}
# Every request the readers may ever send: GET reads, and the one Platform listing whose filter travels in a POST body.
READS = (('GET', '/memories'), ('GET', '/v3/memories/'), ('POST', '/v3/memories/'))


def oss_row(identity, body, **fields):
    return dict(dict(id=identity, memory=body, hash='h', metadata=None, created_at=AT, updated_at=AT, user_id='user-aaa'), **fields)


def oss_event(identity, memory, kind, old, new, **fields):
    return dict(dict(id=identity, memory_id=memory, old_memory=old, new_memory=new, event=kind, created_at=AT, updated_at=AT,
                     is_deleted=kind == 'DELETE'), **fields)


def platform_row(identity, body, **fields):
    return dict(dict(id=identity, memory=body, user_id='user-aaa', metadata=None, categories=['invented'], created_at=AT,
                     updated_at=AT), **fields)


def platform_event(identity, memory, kind, old, new, **fields):
    return dict(dict(id=identity, memory_id=memory, old_memory=old, new_memory=new, event=kind, user_id='user-aaa',
                     created_at=AT, updated_at=AT), **fields)


OSS_LIST = '/memories?show_expired=true&top_k=1000&user_id=user-aaa'
PLATFORM_PAGE = '/v3/memories/?page={}&page_size=100'


class Wire:
    """A patched HTTPS connection: routes are (method, target) pairs, and an unrouted request fails the capture."""

    @pytest.fixture(autouse=True)
    def _wire(self):
        self.routes, self.calls = {}, []

        def request(method, target, body=None, headers=None):
            self.calls.append((method, target, body, dict(headers or {})))

        def respond():
            value = self.routes[self.calls[-1][:2]]
            value = value() if callable(value) else value
            return value if isinstance(value, Response) else Response(value, headers=JSON)
        with patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection') as self.wire:
            self.connection = self.wire.return_value
            self.connection.request.side_effect = request
            self.connection.getresponse.side_effect = respond
            yield
        self.__dict__.clear()

    def targets(self): return [call[:2] for call in self.calls]

    def assert_read_only(self):
        for method, target, *_ in self.calls:
            assert (method, target.split('?')[0]) in READS or (method == 'GET' and target.endswith(('/history', '/history/'))), target


class OssFixture(Wire):
    @pytest.fixture(autouse=True)
    def _oss(self, _wire):
        self.selection = Mem0Selection(OSS_BASE, 'fixture-instance', '2.2.1', dict(user_id='user-aaa'))
        self.source = self.spec(self.selection, auth_mode='configured')
        self.routes.update({
            ('GET', OSS_LIST): dict(results=[oss_row('mem-edited', 'invented fact revised SYNTHETIC_PRIVATE'),
                oss_row('mem-tests', 'all tests pass in the invented repo'), oss_row('mem-other', 'another user fact', user_id='user-bbb')]),
            ('GET', '/memories/mem-edited/history'): [oss_event('h1', 'mem-edited', 'ADD', None, 'invented fact'),
                oss_event('h2', 'mem-edited', 'UPDATE', 'invented fact', 'invented fact revised SYNTHETIC_PRIVATE',
                    updated_at='2031-04-06T00:00:00Z')],
            ('GET', '/memories/mem-tests/history'): [oss_event('h3', 'mem-tests', 'ADD', None, 'all tests pass in the invented repo')]})

    def spec(self, selection, **changes):
        return SourceSpec('api', selection.provider, 'v1', digest(selection.base_url), selection.project_sha256, selection.sha256,
            identity_provenance='source_declared_id', enabled=True, **changes)

    def read(self, selection=None, source=None, key=OSS_KEY, **options):
        selection = selection or self.selection
        client = Mem0RestAPI(selection.base_url, selection, authorization=key, **options)
        snapshot = client.read(source or (self.source if selection is self.selection else self.spec(selection, auth_mode='configured')))
        self.assert_read_only()
        rendered = json.dumps(client.summary)
        for secret in SECRETS: assert secret not in rendered
        return client, snapshot


class TestMem0RestRead(OssFixture):
    def test_scoped_listing_and_history_become_claims_without_other_scopes_or_secrets(self):
        client, snapshot = self.read()
        summary = client.summary
        assert (summary['profile'], summary['surface'], summary['release'], summary['upstream_commit']) == (
            'mem0-oss-rest-v1', 'oss', '2.2.1', '94c3fe9f238f3dbf29c9ce98643bd71eb13077cd')
        assert (summary['claims'], summary['currently_asserted'], summary['excluded']['memories_outside_filter']) == (
            dict(memory=2, history_event=3), 2, 1)
        assert snapshot.checkpoint_ready and summary['checkpoint_ready'] and summary['read_methods'] == ['GET']
        assert (summary['network_used'], summary['server_contacted'], summary['task_outcomes_verified']) == (True, True, False)
        # The other user's memory is never asked for its history, and the capture is one listing plus two history reads.
        assert ('GET', '/memories/mem-other/history') not in self.targets() and len(self.calls) == 3
        assert {r.record_type for r in snapshot.records} == {'mem0_memory', 'mem0_history_event', 'mem0_capture'}
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)
        assert not public_export_allowed([summary])

    def test_the_key_goes_only_in_x_api_key_and_the_listing_names_every_bound(self):
        self.read()
        assert all(headers['X-API-Key'] == OSS_KEY and 'Authorization' not in headers for *_, headers in self.calls)
        assert self.calls[0][1] == OSS_LIST and self.calls[0][2] is None
        assert all(call.args == ('mem0.example.test', 443) for call in self.wire.call_args_list)

    def test_identities_are_the_offline_export_ones_and_a_second_read_is_identical(self):
        _, first = self.read()
        _, second = self.read()
        assert digest(['mem0', 'fixture-instance', 'memory', 'mem-edited']) in {r.native_id_sha256 for r in first.records}
        assert {(r.native_id_sha256, r.revision_sha256) for r in first.records} == {(r.native_id_sha256, r.revision_sha256) for r in second.records}
        assert first.sha256 == second.sha256

    def test_a_memory_that_mentions_a_passing_test_is_not_a_verified_outcome(self):
        client, snapshot = self.read()
        assert client.summary['task_outcomes_verified'] is False and client.summary['evidence_of_use'] is False
        assert 'edits_not_independent' in client.summary['warnings'] and 'run_id_not_task_root' in client.summary['warnings']
        assert not any(r.outcome for r in snapshot.records)

    def test_a_full_listing_may_have_been_cut_by_top_k_and_never_checkpoints(self):
        selection = replace(self.selection, max_memories=3)
        self.routes[('GET', '/memories?show_expired=true&top_k=3&user_id=user-aaa')] = self.routes[('GET', OSS_LIST)]
        client, snapshot = self.read(selection)
        assert ('listing', 'pagination_incomplete', 'top_k_reached') in [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]
        assert not snapshot.checkpoint_ready and 'listing_truncated' in client.summary['warnings']
        assert snapshot.metadata.coverage.truncated

    def test_an_oversized_or_malformed_listing_fails_closed(self):
        selection = replace(self.selection, max_memories=2)
        self.routes[('GET', '/memories?show_expired=true&top_k=2&user_id=user-aaa')] = self.routes[('GET', OSS_LIST)]
        with pytest.raises(TransportError): self.read(selection)
        for bad in (dict(results=[], extra=1), [], dict(results={}), dict(results=['x'])):
            self.routes[('GET', OSS_LIST)] = bad
            with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('status, state', [(401, 'unauthorized'), (403, 'forbidden'), (404, 'not_found'), (429, 'rate_limited')])
    def test_a_denied_listing_is_blocked_coverage_with_no_data_and_no_further_request(self, status, state):
        self.routes[('GET', OSS_LIST)] = Response(b'SYNTHETIC_SECRET', status)
        client, snapshot = self.read()
        assert client.summary['coverage'] == [dict(resource='listing', parent_sha256=None, state=state, basis=None)]
        assert not snapshot.checkpoint_ready and snapshot.records == () and client.summary['claims'] is None
        assert len(self.calls) == 1 and 'listing_unavailable' in client.summary['warnings']

    def test_a_forbidden_history_ends_the_capture_but_a_missing_one_is_only_that_memorys_gap(self):
        self.routes[('GET', '/memories/mem-edited/history')] = Response(b'SYNTHETIC_SECRET', 404)
        client, snapshot = self.read()
        assert [(c['resource'], c['state']) for c in client.summary['coverage']] == [('listing', 'captured'), ('history', 'not_found')]
        assert client.summary['history_captured'] == 1 and not snapshot.checkpoint_ready
        assert 'memory_without_history' in client.summary['warnings']
        self.calls.clear()
        self.routes[('GET', '/memories/mem-edited/history')] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read()
        assert ('history', 'forbidden') in [(c['resource'], c['state']) for c in client.summary['coverage']]
        assert len(self.calls) == 2 and client.summary['history_captured'] == 0 and not snapshot.checkpoint_ready

    def test_a_rate_limit_halts_every_later_request_and_a_spent_budget_is_partial_coverage(self):
        self.routes[('GET', '/memories/mem-edited/history')] = Response(b'SYNTHETIC_SECRET', 429)
        client, snapshot = self.read()
        assert ('history', 'rate_limited') in [(c['resource'], c['state']) for c in client.summary['coverage']]
        assert len(self.calls) == 2 and not snapshot.checkpoint_ready
        self.calls.clear()
        self.routes[('GET', '/memories/mem-edited/history')] = [oss_event('h1', 'mem-edited', 'ADD', None, 'x')]
        client, snapshot = self.read(max_pages=2)
        assert ('history', 'budget_exhausted') in [(c['resource'], c['state']) for c in client.summary['coverage']]
        assert len(self.calls) == 2 and not snapshot.checkpoint_ready and snapshot.metadata.coverage.limit_reached

    @pytest.mark.parametrize('identity', ['../reset', 'a/b', 'a?x=1', 'a%2fb', 'a b', ''])
    def test_a_memory_id_that_is_not_a_plain_token_is_never_interpolated_into_a_path(self, identity):
        self.routes[('GET', OSS_LIST)] = dict(results=[oss_row(identity, 'invented fact')])
        client, snapshot = self.read()
        assert self.targets() == [('GET', OSS_LIST)] and not snapshot.checkpoint_ready
        assert ('history', 'not_observed', 'unsafe_memory_id') in [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]

    def test_history_for_a_memory_outside_the_filter_is_not_requested_even_when_its_row_is_malformed(self):
        self.routes[('GET', OSS_LIST)] = dict(results=[oss_row('mem-other', 'x', user_id='user-bbb'), oss_row('mem-tests', 'y')])
        self.routes[('GET', '/memories/mem-tests/history')] = [oss_event('h3', 'mem-tests', 'ADD', None, 'y')]
        client, snapshot = self.read()
        assert self.targets() == [('GET', OSS_LIST), ('GET', '/memories/mem-tests/history')]
        assert client.summary['excluded']['memories_outside_filter'] == 1 and snapshot.checkpoint_ready

    def test_a_malformed_history_fails_the_whole_capture(self):
        for bad in (dict(a=1), 'x', [{}] * 1001):
            self.routes[('GET', '/memories/mem-tests/history')] = bad
            with pytest.raises(TransportError): self.read()

    def test_a_deleted_memory_stays_evidence_as_a_tombstone_and_leaves_the_asserted_count(self):
        self.routes[('GET', '/memories/mem-tests/history')] = [oss_event('h3', 'mem-tests', 'ADD', None, 'all tests pass in the invented repo'),
            oss_event('h4', 'mem-tests', 'DELETE', 'all tests pass in the invented repo', None, updated_at='2031-04-07T00:00:00Z')]
        client, snapshot = self.read()
        assert client.summary['tombstones'] == 1 and client.summary['currently_asserted'] == 1
        assert any(r.state == 'tombstone' for r in snapshot.records)

    def test_an_expired_memory_is_kept_historical(self):
        self.routes[('GET', OSS_LIST)]['results'][1]['expiration_date'] = '2020-01-01'
        client, _ = self.read()
        assert client.summary['memories']['expired'] == 1 and client.summary['currently_asserted'] == 1

    def test_without_a_credential_an_open_server_is_read_without_any_auth_header(self):
        source = self.spec(self.selection)
        client = Mem0RestAPI(OSS_BASE, self.selection)
        client.read(source)
        assert all('X-API-Key' not in headers and 'Authorization' not in headers for *_, headers in self.calls)
        with pytest.raises(TransportError): Mem0RestAPI(OSS_BASE, self.selection, authorization=OSS_KEY).read(source)

    def test_scope_identity_and_source_must_match_before_any_connection(self):
        other = replace(self.selection, filters=dict(user_id='user-bbb'))
        for source in (replace(self.source, enabled=False), replace(self.source, project_sha256=digest('foreign')),
                       replace(self.source, scope_sha256=digest('foreign')), replace(self.source, provider='gitea'),
                       replace(self.source, root_sha256=digest('https://other.example.test')), self.spec(other, auth_mode='configured')):
            with pytest.raises(TransportError): Mem0RestAPI(OSS_BASE, self.selection, authorization=OSS_KEY).read(source)
        with pytest.raises(TransportError): Mem0RestAPI('https://other.example.test', self.selection)
        with pytest.raises(TransportError): Mem0RestAPI(OSS_BASE, Mem0PlatformSelection(PLATFORM_BASE, 'i', 'v3', dict(user_id='u')))
        with pytest.raises(TransportError): Mem0RestAPI(OSS_BASE, self.selection).read(self.source, prior={})
        self.wire.assert_not_called()

    def test_another_scope_or_instance_is_another_project(self):
        assert len({self.selection.project_sha256, replace(self.selection, filters=dict(user_id='u2')).project_sha256,
                    replace(self.selection, instance='other').project_sha256,
                    replace(self.selection, base_url='https://mem0.example.test/sub').project_sha256}) == 4

    def test_registry_import_is_idempotent_and_a_changed_memory_is_a_new_revision(self, tmp_path):
        registry = Registry()
        registry.register(Capability('api', REST_PROVIDER, 'v1', ('contextual_assertion',)), Mem0RestAPI(OSS_BASE, self.selection, authorization=OSS_KEY))
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope)
        first = registry.ingest(self.source, store, **scope)
        assert first.imported > 0 and first.status == 'complete'
        assert registry.ingest(self.source, store, **scope).imported == 0
        self.routes[('GET', OSS_LIST)]['results'][1]['memory'] = 'a different invented fact'
        self.routes[('GET', '/memories/mem-tests/history')] = [oss_event('h3', 'mem-tests', 'ADD', None, 'all tests pass in the invented repo'),
            oss_event('h5', 'mem-tests', 'UPDATE', 'all tests pass in the invented repo', 'a different invented fact', updated_at='2031-04-08T00:00:00Z')]
        assert registry.ingest(self.source, store, **scope).imported > 0


class TestMem0SelectionValidation:
    BASE = dict(base_url=OSS_BASE, instance='fixture-instance', release='2.2.1', filters=dict(user_id='user-aaa'))

    @pytest.mark.parametrize('change', [
        pytest.param(dict(filters={}), id='no-filter'),
        pytest.param(dict(filters=dict(user_id='*')), id='wildcard-user'),
        pytest.param(dict(filters=dict(user_id='')), id='blank'),
        pytest.param(dict(filters=dict(user_id=' user-aaa')), id='padded'),
        pytest.param(dict(filters=dict(user_id='a', metadata='b')), id='unscoped-key'),
        pytest.param(dict(filters=dict(user_id=dict(gte='a'))), id='operator'),
        pytest.param(dict(filters=dict(app_id='a')), id='platform-only-scope'),
        pytest.param(dict(filters=dict(OR=[dict(user_id='a'), dict(agent_id='b')])), id='or-is-platform-only'),
        pytest.param(dict(base_url='http://mem0.example.test'), id='plain-http'),
        pytest.param(dict(base_url=OSS_BASE+'/'), id='trailing-slash'),
        pytest.param(dict(base_url=OSS_BASE+'/memories'), id='route-as-root'),
        pytest.param(dict(base_url='https://user:pw@mem0.example.test'), id='userinfo'),
        pytest.param(dict(instance=' '), id='blank-instance'),
        pytest.param(dict(release='2.2.2'), id='unpinned-release'),
        pytest.param(dict(show_expired='yes'), id='show-expired-type'),
        pytest.param(dict(max_memories=0), id='no-memories'),
        pytest.param(dict(max_memories=1001), id='above-the-server-top_k'),
        pytest.param(dict(max_memories=True), id='bool-count'),
        pytest.param(dict(schema_version=2), id='schema')])
    def test_oss_selection_refuses_what_could_cross_a_boundary_or_guess_a_shape(self, change):
        with pytest.raises(ValueError): Mem0Selection(**{**self.BASE, **change})

    @pytest.mark.parametrize('change', [
        pytest.param(dict(filters=dict(user_id='*')), id='wildcard'),
        pytest.param(dict(filters=dict(OR=[dict(user_id='*')])), id='wildcard-in-or'),
        pytest.param(dict(filters=dict(OR=[dict(user_id=' a')])), id='padded'),
        pytest.param(dict(filters=dict(OR=[])), id='empty-or'),
        pytest.param(dict(filters=dict(OR=[dict(user_id='a')]*17)), id='too-many-conditions'),
        pytest.param(dict(filters=dict(NOT=[dict(user_id='a')])), id='not'),
        pytest.param(dict(release='v4'), id='unpinned'),
        pytest.param(dict(max_memories=10001), id='over')])
    def test_platform_selection_refuses_wildcards_operators_and_unpinned_versions(self, change):
        with pytest.raises(ValueError): Mem0PlatformSelection(**{**self.BASE, 'release': 'v3', **change})

    def test_platform_selection_accepts_literal_scopes_and_an_or_of_them(self):
        base = {**self.BASE, 'release': 'v3', 'base_url': PLATFORM_BASE}
        assert Mem0PlatformSelection(**base).filters == dict(user_id='user-aaa')
        assert Mem0PlatformSelection(**{**base, 'filters': dict(OR=[dict(user_id='a'), dict(agent_id='b', app_id='c')])})

    def test_selection_round_trips_through_its_manifest_form(self):
        selection = Mem0Selection(**self.BASE)
        assert Mem0Selection.parse(json.loads(json.dumps(selection.to_dict()))) == selection
        with pytest.raises(ValueError): Mem0Selection.parse(dict(selection.to_dict(), extra=1))


class PlatformFixture(Wire):
    @pytest.fixture(autouse=True)
    def _platform(self, _wire):
        self.selection = Mem0PlatformSelection(PLATFORM_BASE, 'fixture-instance', 'v3', dict(user_id='user-aaa'))
        self.source = self.spec(self.selection)
        self.pages = [
            dict(count=3, next=PLATFORM_BASE+'/v3/memories/?page=2&page_size=100', previous=None, results=[
                platform_row('mem-edited', 'invented fact revised SYNTHETIC_PRIVATE', session_id='run-aaa'),
                platform_row('mem-tests', 'all tests pass in the invented repo', session_id='run-aaa')]),
            dict(count=3, next=None, previous=PLATFORM_BASE+'/v3/memories/?page=1&page_size=100', results=[
                platform_row('mem-old', 'invented old fact', replaced_by='mem-edited')])]
        self.routes.update({
            ('POST', PLATFORM_PAGE.format(1)): lambda: self.pages[0], ('POST', PLATFORM_PAGE.format(2)): lambda: self.pages[1],
            ('GET', '/v1/memories/mem-edited/history/'): [platform_event('h1', 'mem-edited', 'ADD', None, 'invented fact'),
                platform_event('h2', 'mem-edited', 'UPDATE', 'invented fact', 'invented fact revised SYNTHETIC_PRIVATE',
                    updated_at='2031-04-06T00:00:00Z')],
            ('GET', '/v1/memories/mem-tests/history/'): [platform_event('h3', 'mem-tests', 'ADD', None, 'all tests pass in the invented repo')],
            ('GET', '/v1/memories/mem-old/history/'): [platform_event('h4', 'mem-old', 'ADD', None, 'invented old fact')]})

    def spec(self, selection, **changes):
        return SourceSpec('api', selection.provider, 'v1', digest(selection.base_url), selection.project_sha256, selection.sha256,
            identity_provenance='source_declared_id', auth_mode='configured', enabled=True, **changes)

    def read(self, selection=None, source=None, key=PLATFORM_KEY, **options):
        selection = selection or self.selection
        client = Mem0PlatformAPI(selection.base_url, selection, authorization=key, **options)
        snapshot = client.read(source or (self.source if selection is self.selection else self.spec(selection)))
        self.assert_read_only()
        rendered = json.dumps(client.summary)
        for secret in SECRETS: assert secret not in rendered
        return client, snapshot


class TestMem0PlatformRead(PlatformFixture):
    def test_pages_are_listed_by_post_with_the_filter_in_the_body_and_history_by_get(self):
        client, snapshot = self.read()
        summary = client.summary
        assert (summary['profile'], summary['surface'], summary['platform_v3'], summary['release']) == (
            'mem0-platform-api-v1', 'platform_v3', 'read_only_api', 'v3')
        assert (summary['claims'], summary['currently_asserted'], summary['memories']['replaced']) == (
            dict(memory=3, history_event=4), 2, 1)
        assert snapshot.checkpoint_ready and summary['read_methods'] == ['GET', 'POST']
        assert self.targets() == [('POST', PLATFORM_PAGE.format(1)), ('POST', PLATFORM_PAGE.format(2)),
            ('GET', '/v1/memories/mem-edited/history/'), ('GET', '/v1/memories/mem-tests/history/'), ('GET', '/v1/memories/mem-old/history/')]
        assert json.loads(self.calls[0][2]) == dict(filters=dict(user_id='user-aaa'), show_expired=True)
        assert all(headers['Authorization'] == PLATFORM_KEY and 'X-API-Key' not in headers for *_, headers in self.calls)
        assert self.calls[0][3]['Content-Type'] == 'application/json' and 'Content-Type' not in self.calls[2][3]
        assert {r.record_type for r in snapshot.records} == {'mem0_platform_memory', 'mem0_platform_history_event', 'mem0_platform_capture'}
        assert not public_export_allowed([summary])

    def test_platform_identities_never_collide_with_the_oss_namespace(self):
        _, snapshot = self.read()
        assert digest(['mem0-platform', 'fixture-instance', 'memory', 'mem-edited']) in {r.native_id_sha256 for r in snapshot.records}
        assert digest(['mem0', 'fixture-instance', 'memory', 'mem-edited']) not in {r.native_id_sha256 for r in snapshot.records}

    def test_a_credential_is_required_and_is_never_replaced_by_an_unconfigured_source(self):
        with pytest.raises(TransportError): Mem0PlatformAPI(PLATFORM_BASE, self.selection).read(self.source)
        with pytest.raises(TransportError): Mem0PlatformAPI(PLATFORM_BASE, self.selection).read(replace(self.source, auth_mode='none'))
        self.wire.assert_not_called()

    def test_a_listing_stopped_by_max_memories_is_truncated_and_never_checkpoints(self):
        client, snapshot = self.read(replace(self.selection, max_memories=2))
        assert ('listing', 'pagination_incomplete', 'max_memories_reached') in [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]
        assert not snapshot.checkpoint_ready and 'listing_truncated' in client.summary['warnings']
        assert ('POST', PLATFORM_PAGE.format(2)) not in self.targets()

    def test_a_total_that_disagrees_with_the_rows_read_never_checkpoints(self):
        self.pages[0]['count'] = self.pages[1]['count'] = 9
        client, snapshot = self.read()
        assert ('listing', 'pagination_incomplete', 'count_mismatch') in [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]
        assert not snapshot.checkpoint_ready and 'listing_count_mismatch' in client.summary['warnings']

    def test_a_denied_second_page_reads_no_history_because_the_credential_is_refused(self):
        self.routes[('POST', PLATFORM_PAGE.format(2))] = Response(b'SYNTHETIC_SECRET', 401)
        client, snapshot = self.read()
        assert self.targets() == [('POST', PLATFORM_PAGE.format(1)), ('POST', PLATFORM_PAGE.format(2))]
        assert not snapshot.checkpoint_ready and client.summary['history_captured'] == 0

    @pytest.mark.parametrize('change', [
        pytest.param(lambda p: p[0].update(next=PLATFORM_BASE+'/v3/memories/?page=3&page_size=100'), id='skipped-page'),
        pytest.param(lambda p: p[0].update(next=PLATFORM_BASE+'/v3/memories/?page=1&page_size=100'), id='repeated-page'),
        pytest.param(lambda p: p[0].update(next='cursor-token'), id='cursor-link'),
        pytest.param(lambda p: p[0].update(next=7), id='non-string-link'),
        pytest.param(lambda p: p[0].update(results=[]), id='empty-page-with-a-link'),
        pytest.param(lambda p: p[0].update(extra=1), id='unknown-envelope-field'),
        pytest.param(lambda p: p[0].update(results={}), id='results-type'),
        pytest.param(lambda p: p[0].update(results=p[0]['results']*60), id='more-rows-than-the-page-size')])
    def test_a_page_that_skips_repeats_or_oversizes_fails_the_capture_and_is_never_followed(self, change):
        change(self.pages)
        with pytest.raises(TransportError): self.read()
        assert all(call[1].split('?')[0] != '/' and 'evil' not in call[1] for call in self.calls)

    def test_a_server_next_link_to_another_origin_is_never_requested(self):
        self.pages[0]['next'] = 'https://evil.example.test/v3/memories/?page=2&page_size=100'
        client, _ = self.read()
        assert all(call.args == ('api.mem0.example.test', 443) for call in self.wire.call_args_list)
        assert self.targets()[:2] == [('POST', PLATFORM_PAGE.format(1)), ('POST', PLATFORM_PAGE.format(2))]
        assert client.summary['claims']['memory'] == 3

    @pytest.mark.parametrize('status, state', [(401, 'unauthorized'), (403, 'forbidden'), (404, 'not_found'), (429, 'rate_limited')])
    def test_a_denied_first_page_is_blocked_coverage_with_no_data_and_a_denied_later_page_is_truncation(self, status, state):
        self.routes[('POST', PLATFORM_PAGE.format(1))] = Response(b'SYNTHETIC_SECRET', status)
        client, snapshot = self.read()
        assert client.summary['coverage'] == [dict(resource='listing', parent_sha256=None, state=state, basis=None)]
        assert not snapshot.checkpoint_ready and snapshot.records == () and len(self.calls) == 1
        self.calls.clear()
        self.routes[('POST', PLATFORM_PAGE.format(1))] = lambda: self.pages[0]
        self.routes[('POST', PLATFORM_PAGE.format(2))] = Response(b'SYNTHETIC_SECRET', status)
        client, snapshot = self.read()
        assert ('listing', 'pagination_incomplete', state) in [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]
        assert not snapshot.checkpoint_ready and 'listing_truncated' in client.summary['warnings']

    def test_a_rate_limited_post_is_never_retried_even_when_a_wait_is_allowed(self):
        self.routes[('POST', PLATFORM_PAGE.format(1))] = Response(b'x', 429, headers={'Retry-After': '1'})
        client, _ = self.read(max_wait=60)
        assert len(self.calls) == 1 and client.summary['coverage'][0]['state'] == 'rate_limited'

    def test_a_memory_in_the_listing_for_another_scope_is_excluded_and_its_history_never_read(self):
        self.pages[1]['results'].append(platform_row('mem-other', 'another user fact', user_id='user-bbb'))
        self.pages[0]['count'] = self.pages[1]['count'] = 4
        client, snapshot = self.read()
        assert ('GET', '/v1/memories/mem-other/history/') not in self.targets()
        assert client.summary['excluded']['memories_outside_filter'] == 1 and snapshot.checkpoint_ready

    def test_an_or_filter_is_sent_whole_and_a_speaker_split_listing_is_matched_row_by_row(self):
        selection = Mem0PlatformSelection(PLATFORM_BASE, 'fixture-instance', 'v3', dict(OR=[dict(user_id='user-aaa'), dict(agent_id='agent-aaa')]))
        self.pages[1]['results'].append(platform_row('mem-agent', 'agent fact', user_id=None, agent_id='agent-aaa'))
        self.pages[0]['count'] = self.pages[1]['count'] = 4
        self.routes[('GET', '/v1/memories/mem-agent/history/')] = [platform_event('h9', 'mem-agent', 'ADD', None, 'agent fact', user_id=None)]
        client, _ = self.read(selection)
        assert json.loads(self.calls[0][2])['filters'] == dict(OR=[dict(user_id='user-aaa'), dict(agent_id='agent-aaa')])
        assert ('GET', '/v1/memories/mem-agent/history/') in self.targets() and client.summary['claims']['memory'] == 4

    def test_an_and_of_user_and_agent_warns_that_speaker_attributed_facts_may_be_missing(self):
        selection = Mem0PlatformSelection(PLATFORM_BASE, 'fixture-instance', 'v3', dict(user_id='user-aaa', agent_id='agent-aaa'))
        self.pages = [dict(count=0, next=None, previous=None, results=[])]
        self.routes[('POST', PLATFORM_PAGE.format(1))] = lambda: self.pages[0]
        client, _ = self.read(selection)
        assert 'and_filter_may_omit_speaker_attributed_facts' in client.summary['warnings']

    def test_an_expired_memory_is_requested_and_kept_historical(self):
        self.pages[0]['results'][1]['expiration_date'] = '2020-01-01'
        client, _ = self.read()
        assert json.loads(self.calls[0][2])['show_expired'] is True and client.summary['memories']['expired'] == 1

    def test_hiding_expired_memories_is_an_explicit_selection(self):
        selection = replace(self.selection, show_expired=False)
        self.read(selection)
        assert json.loads(self.calls[0][2])['show_expired'] is False


@pytest.fixture
def _credentials():
    with patch.dict(os.environ, SYNTHETIC_MEM0_KEY=OSS_KEY, SYNTHETIC_MEM0_PLATFORM=PLATFORM_KEY):
        yield


class TestMem0Cli(OssFixture):
    @pytest.fixture(autouse=True)
    def _cli(self, _oss, tmp_path):
        self.root = tmp_path
        self.manifest, self.journal = tmp_path/'mem0.json', tmp_path/'journal.json'
        self.write()

    def write(self, selection=None, **changes):
        selection = selection or self.selection
        source = self.spec(selection, auth_mode='configured')
        self.current = source
        value = dict(schema_version=1, source=source.to_dict(), endpoint=selection.base_url, selection=selection.to_dict(), limits=dict(max_pages=40))
        self.manifest.write_text(json.dumps({**value, **changes}))

    def invoke(self, operation, *extra):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--cache', str(self.journal),
                '--project-sha256', self.current.project_sha256, '--scope-sha256', self.current.scope_sha256, *extra])
        for secret in (*SECRETS, OSS_BASE, str(self.root)): assert secret not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    ENV = ('--authorization-env', 'SYNTHETIC_MEM0_KEY')

    def test_probe_contacts_nothing_and_preview_import_and_status_use_only_the_named_variable(self, _credentials):
        code, result = self.invoke('probe', *self.ENV)
        assert (code, result['status'], result['network_used']) == (0, 'recognized', False)
        self.wire.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            assert self.invoke('preview', *self.ENV)[0] == 3
        self.wire.assert_not_called()
        code, result = self.invoke('preview', *self.ENV)
        assert code == 0 and result['checkpoint_ready'] and result['network_used']
        assert result['mem0_summary']['claims'] == dict(memory=2, history_event=3) and 'rate_limit' in result
        assert not self.journal.exists()
        assert self.invoke('import', *self.ENV)[0] == 0
        repeated = self.invoke('import', *self.ENV)
        assert repeated[0] == 0 and repeated[1]['imported'] == 0
        assert self.invoke('status')[0] == 0

    def test_a_blocked_import_commits_nothing_and_exits_3(self, _credentials):
        assert self.invoke('import', *self.ENV)[0] == 0
        original = self.journal.read_bytes()
        self.routes[('GET', '/memories/mem-tests/history')] = Response(b'SYNTHETIC_SECRET', 403)
        code, result = self.invoke('import', *self.ENV)
        assert (code, result['status'], result['checkpoint_committed']) == (3, 'blocked', False)
        assert ('history', 'forbidden') in [(c['resource'], c['state']) for c in result['mem0_summary']['coverage']]
        assert self.journal.read_bytes() == original

    def test_a_credential_file_works_and_both_sources_together_are_refused(self, _credentials):
        credential = self.root/'key'
        credential.write_text(OSS_KEY+'\n')
        assert self.invoke('preview', '--authorization-file', str(credential))[0] == 0
        assert self.invoke('preview', *self.ENV, '--authorization-file', str(credential))[0] == 2

    def test_the_platform_configuration_sends_a_token_header_through_the_same_command(self, _credentials):
        selection = Mem0PlatformSelection(PLATFORM_BASE, 'fixture-instance', 'v3', dict(user_id='user-aaa'))
        self.write(selection)
        self.routes[('POST', PLATFORM_PAGE.format(1))] = dict(count=1, next=None, previous=None, results=[platform_row('mem-tests', 'invented fact')])
        self.routes[('GET', '/v1/memories/mem-tests/history/')] = [platform_event('h3', 'mem-tests', 'ADD', None, 'invented fact')]
        code, result = self.invoke('preview', '--authorization-env', 'SYNTHETIC_MEM0_PLATFORM')
        assert code == 0 and result['checkpoint_ready'] and result['mem0_summary']['platform_v3'] == 'read_only_api'
        assert all(headers['Authorization'] == PLATFORM_KEY for *_, headers in self.calls)

    @pytest.mark.parametrize('change', [pytest.param(dict(endpoint='https://other.example.test'), id='other-endpoint'),
                                        pytest.param(dict(limits=dict(retries=3)), id='unknown-limit')])
    def test_a_configuration_that_exceeds_its_declared_source_is_rejected(self, change, _credentials):
        self.write(**change)
        assert self.invoke('probe')[0] == 2
        self.wire.assert_not_called()
