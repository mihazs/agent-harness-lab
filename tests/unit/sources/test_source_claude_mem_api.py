"""Synthetic Claude-Mem worker list reads over a fake HTTPS wire: scoped, read-only, never a task outcome."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime
import io
import json
import os
from unittest.mock import patch

import pytest

from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.claude_mem_api import PROVIDER, ClaudeMemSelection, ClaudeMemWorkerAPI
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

BASE, INSTANCE, PROJECT = 'https://claude-mem.example.test', 'fixture-instance', 'invented-project'
KEY = 'Bearer SYNTHETIC_WORKER_SECRET'
SECRETS = ('SYNTHETIC_WORKER_SECRET', 'SYNTHETIC_PRIVATE', 'content-s1', 'mem-s1', PROJECT, INSTANCE, 'claude-mem.example.test')
AT = '2031-04-05T06:07:08+00:00'
EPOCH = int(datetime.fromisoformat(AT).timestamp()*1000)
JSON = {'Content-Type': 'application/json; charset=utf-8'}
LISTS = ('/api/observations', '/api/summaries', '/api/prompts')
# Every request the reader may ever send: GET reads of the version and the three pinned list routes.
READS = {('GET', route) for route in ('/api/version', *LISTS)}


def observation(identity, **fields):
    return dict(dict(id=identity, memory_session_id='mem-s1', content_session_id='content-s1', project=PROJECT,
        merged_into_project=None, platform_source='claude', type='discovery', title='Invented title', subtitle=None,
        narrative='invented narrative SYNTHETIC_PRIVATE', text=None, facts='[]', concepts='[]', files_read='[]',
        files_modified='[]', prompt_number=1, created_at=AT, created_at_epoch=EPOCH), **fields)


def summary(identity, **fields):
    return dict(dict(id=identity, session_id='content-s1', platform_source='claude', request='fix the invented bug',
        investigated='x', learned='y', completed='all tests pass in the invented repo', next_steps=None, notes=None,
        project=PROJECT, merged_into_project=None, created_at=AT, created_at_epoch=EPOCH), **fields)


def prompt(identity, **fields):
    return dict(dict(id=identity, content_session_id='content-s1', project=PROJECT, platform_source='claude',
        prompt_number=1, prompt_text='invented request SYNTHETIC_PRIVATE', created_at=AT, created_at_epoch=EPOCH), **fields)


def page(items, offset=0, limit=100, more=False):
    return dict(items=items, hasMore=more, offset=offset, limit=limit)


def target(route, offset=0, limit=100, platform=None):
    query = f'limit={limit}&offset={offset}' + ('' if platform is None else f'&platformSource={platform}') + f'&project={PROJECT}'
    return ('GET', f'{route}?{query}')


class Wire:
    """A patched HTTPS connection: routes are (method, target) pairs, and an unrouted request fails the capture."""

    @pytest.fixture(autouse=True)
    def _wire(self):
        self.routes, self.calls = {}, []

        def request(method, path, body=None, headers=None):
            self.calls.append((method, path, body, dict(headers or {})))

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
        for method, path, *_ in self.calls: assert (method, path.split('?')[0]) in READS, path


class Fixture(Wire):
    @pytest.fixture(autouse=True)
    def _worker(self, _wire):
        self.selection = ClaudeMemSelection(BASE, INSTANCE, '15.9.0', PROJECT)
        self.source = self.spec(self.selection, auth_mode='configured')
        self.routes.update({
            ('GET', '/api/version'): dict(version='15.9.0'),
            target('/api/observations'): page([observation(1), observation(2, type='bugfix')]),
            target('/api/summaries'): page([summary(7)]),
            target('/api/prompts'): page([prompt(3)])})

    def spec(self, selection, **changes):
        return SourceSpec('api', selection.provider, 'v1', digest(selection.base_url), selection.project_sha256, selection.sha256,
            identity_provenance='source_declared_id', enabled=True, **changes)

    def read(self, selection=None, source=None, key=KEY, **options):
        selection = selection or self.selection
        client = ClaudeMemWorkerAPI(selection.base_url, selection, authorization=key, **options)
        snapshot = client.read(source or (self.source if selection is self.selection else self.spec(selection, auth_mode='configured')))
        self.assert_read_only()
        rendered = json.dumps(client.summary)
        for secret in SECRETS: assert secret not in rendered
        return client, snapshot


class TestClaudeMemWorkerRead(Fixture):
    def test_one_project_becomes_separate_claims_without_bodies_ids_or_secrets(self):
        client, snapshot = self.read()
        summary = client.summary
        assert (summary['profile'], summary['claude_mem_version'], summary['upstream_commit']) == (
            'claude-mem-worker-v1', '15.9.0', 'd0968e5d136abc34dcfe1b78acf3726fa84978d8')
        assert summary['claims'] == dict(observation=2, generated_summary=1, user_prompt=1)
        assert summary['timestamps'] == dict(native=4, missing=0, invalid=0, conflicting=0)
        assert {r.record_type for r in snapshot.records} == {'claude_mem_observation', 'claude_mem_generated_summary',
            'claude_mem_user_prompt', 'claude_mem_capture'}
        assert snapshot.checkpoint_ready and summary['checkpoint_ready'] and summary['read_methods'] == ['GET']
        assert (summary['network_used'], summary['server_contacted'], summary['worker_started'], summary['database_opened'],
                summary['bodies_stored']) == (True, True, False, False, False)
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)
        assert not public_export_allowed([summary])
        # One version read and one request per list: nothing else is asked of the worker.
        assert self.targets() == [('GET', '/api/version'), target('/api/observations'), target('/api/summaries'), target('/api/prompts')]

    def test_a_generated_summary_is_its_own_claim_and_a_completed_looking_text_is_not_an_outcome(self):
        client, snapshot = self.read()
        assert client.summary['task_outcomes_verified'] is False and client.summary['evidence_of_use'] is False
        assert client.summary['independent_trials'] is None and 'summaries_not_independent' in client.summary['warnings']
        assert 'search_hits_not_applicable' in client.summary['warnings']
        assert not any(r.outcome for r in snapshot.records)
        claims = {c.claim_type: c for c in client.claims}
        assert claims['generated_summary'].synthesizes_multiple_origins is True and claims['observation'].synthesizes_multiple_origins is False
        assert all(not (c.independent_case or c.evidence_of_use or c.task_outcome_verified or c.native_session_verified) for c in client.claims)
        assert 'search_hit' not in claims

    def test_identities_are_the_offline_export_ones_and_a_second_read_is_identical(self):
        _, first = self.read()
        _, second = self.read()
        assert digest(['claude-mem', INSTANCE, 'observation', 1, 0]) in {r.native_id_sha256 for r in first.records}
        assert digest(['claude-mem', INSTANCE, 'generated_summary', 7, 0]) in {r.native_id_sha256 for r in first.records}
        assert {(r.native_id_sha256, r.revision_sha256) for r in first.records} == {(r.native_id_sha256, r.revision_sha256) for r in second.records}
        assert first.sha256 == second.sha256

    def test_an_edited_body_keeps_the_native_id_and_makes_a_new_revision(self):
        _, first = self.read()
        self.routes[target('/api/observations')] = page([observation(1, narrative='an edited invented narrative'), observation(2, type='bugfix')])
        _, second = self.read()
        old, new = ({r.native_id_sha256: r.revision_sha256 for r in snap.records} for snap in (first, second))
        changed = digest(['claude-mem', INSTANCE, 'observation', 1, 0])
        capture = next(r.native_id_sha256 for r in first.records if r.record_type == 'claude_mem_capture')
        assert old.keys() == new.keys() and {k for k in old if old[k] != new[k]} == {changed, capture} and first.sha256 != second.sha256

    def test_a_change_to_an_unmodelled_field_is_still_a_new_capture(self):
        _, first = self.read()
        self.routes[target('/api/prompts')] = page([prompt(3, brand_new_field='x')])
        client, second = self.read()
        assert first.sha256 != second.sha256 and client.summary['rows_with_unknown_fields']['prompts'] == 1
        assert 'unknown_row_fields' in client.summary['warnings'] and 'brand_new_field' not in json.dumps(client.summary)

    def test_a_row_for_another_project_is_excluded_counted_and_never_a_claim(self):
        self.routes[target('/api/observations')] = page([observation(1), observation(2, project='other-project'),
            observation(4, project='someone-elses', merged_into_project=PROJECT.upper())])
        client, snapshot = self.read()
        assert client.summary['claims']['observation'] == 2
        assert client.summary['excluded']['rows_outside_selection'] == dict(observations=1, summaries=0, prompts=0)
        assert 'row_outside_selection' in client.summary['warnings']
        assert digest(['claude-mem', INSTANCE, 'observation', 2, 0]) not in {r.native_id_sha256 for r in snapshot.records}
        assert digest(['claude-mem', INSTANCE, 'observation', 4, 0]) in {r.native_id_sha256 for r in snapshot.records}

    def test_a_row_without_a_project_cannot_be_confirmed_in_scope_and_is_excluded(self):
        self.routes[target('/api/prompts')] = page([prompt(3, project=None)])
        client, _ = self.read()
        assert client.summary['claims']['user_prompt'] == 0 and client.summary['excluded']['rows_outside_selection']['prompts'] == 1

    def test_a_platform_selection_is_sent_and_a_row_of_another_platform_is_excluded(self):
        selection = replace(self.selection, platform_source='codex')
        for route, rows in zip(LISTS, ([observation(1, platform_source='codex'), observation(2)], [summary(7, platform_source='codex')], [])):
            self.routes[target(route, platform='codex')] = page(rows)
        client, _ = self.read(selection)
        assert client.summary['claims'] == dict(observation=1, generated_summary=1, user_prompt=0)
        assert client.summary['excluded']['rows_outside_selection']['observations'] == 1
        assert all('platformSource=codex' in path for _, path, *_ in self.calls if path.startswith(LISTS))

    @pytest.mark.parametrize('row, warning', [
        pytest.param(dict(id=0), 'row_without_native_identity', id='zero-id'),
        pytest.param(dict(id=True), 'row_without_native_identity', id='bool-id'),
        pytest.param(dict(id='1'), 'row_without_native_identity', id='string-id'),
        pytest.param(dict(memory_session_id=None), 'row_without_native_identity', id='no-session'),
        pytest.param(dict(memory_session_id='  '), 'row_without_native_identity', id='blank-session')])
    def test_a_row_without_a_native_identity_is_rejected_and_counted(self, row, warning):
        self.routes[target('/api/observations')] = page([observation(1, **row)])
        client, snapshot = self.read()
        assert client.summary['rows_rejected']['observations'] == 1 and client.summary['claims']['observation'] == 0
        assert warning in client.summary['warnings'] and snapshot.checkpoint_ready

    @pytest.mark.parametrize('fields, status', [
        pytest.param(dict(created_at=None, created_at_epoch=None), 'missing', id='missing'),
        pytest.param(dict(created_at='2031-04-05T06:07:08', created_at_epoch=None), 'invalid', id='naive'),
        pytest.param(dict(created_at='not a time', created_at_epoch=None), 'invalid', id='unparseable'),
        pytest.param(dict(created_at=AT, created_at_epoch=EPOCH+60000), 'conflicting', id='disagreeing')])
    def test_an_unknown_timestamp_is_never_guessed(self, fields, status):
        self.routes[target('/api/observations')] = page([observation(1, **fields)])
        client, _ = self.read()
        assert client.summary['timestamps'][status] == 1 and status+'_timestamp' in client.summary['warnings']
        assert client.claims[0].original_at is None and client.claims[0].timestamp_status == status

    def test_an_unrecognized_type_and_platform_become_labels_not_free_text(self):
        self.routes[target('/api/observations')] = page([observation(1, type='SYNTHETIC_PRIVATE', platform_source='SYNTHETIC_PRIVATE')])
        client, _ = self.read()
        assert (client.claims[0].detail, client.claims[0].platform_source) == ('unrecognized', 'other')
        assert {'unrecognized_observation_type', 'unrecognized_platform_source'} <= set(client.summary['warnings'])

    def test_the_credential_goes_only_in_authorization_and_none_means_no_header(self):
        self.read()
        assert all(headers['Authorization'] == KEY and 'X-API-Key' not in headers for *_, headers in self.calls)
        assert all(call.args == ('claude-mem.example.test', 443) for call in self.wire.call_args_list)
        self.calls.clear()
        self.read(source=self.spec(self.selection), key=None)
        assert all('Authorization' not in headers for *_, headers in self.calls)

    def test_a_configured_source_without_a_credential_never_connects(self):
        with pytest.raises(TransportError): self.read(key=None)
        self.wire.assert_not_called()

    def test_scope_identity_and_source_must_match_before_any_connection(self):
        for source in (replace(self.source, enabled=False), replace(self.source, root_sha256=digest('wrong')),
                       replace(self.source, project_sha256=digest('foreign')), replace(self.source, scope_sha256=digest('foreign')),
                       replace(self.source, provider='mem0-oss-rest'), replace(self.source, version='v2')):
            with pytest.raises(TransportError): self.read(source=source)
        self.wire.assert_not_called()

    def test_a_prior_capture_is_never_reconciled_against_absences(self):
        client = ClaudeMemWorkerAPI(BASE, self.selection, authorization=KEY)
        with pytest.raises(TransportError): client.read(self.source, prior=object())
        self.wire.assert_not_called()

    def test_another_project_platform_or_instance_is_another_scope(self):
        base = self.selection
        scopes = {base.project_sha256, replace(base, project='other-project').project_sha256,
                  replace(base, platform_source='codex').project_sha256, replace(base, instance='other-instance').project_sha256,
                  replace(base, base_url='https://other.example.test').project_sha256}
        assert len(scopes) == 5
        # The worker matches a project without regard to ASCII case, so the same project in another case is the same scope.
        assert replace(base, project=PROJECT.upper()).project_sha256 == base.project_sha256

    def test_registry_import_is_idempotent_and_a_changed_memory_is_a_new_revision(self, tmp_path):
        registry = Registry()
        registry.register(Capability('api', PROVIDER, 'v1', ('contextual_assertion',)), ClaudeMemWorkerAPI(BASE, self.selection, authorization=KEY))
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope)
        first = registry.ingest(self.source, store, **scope)
        assert first.imported > 0 and first.status == 'complete'
        assert registry.ingest(self.source, store, **scope).imported == 0
        self.routes[target('/api/summaries')] = page([summary(7, completed='a different invented outcome')])
        assert registry.ingest(self.source, store, **scope).imported > 0


class TestClaudeMemWorkerPaging(Fixture):
    def test_a_long_list_is_read_in_pages_of_one_hundred_until_the_worker_says_it_ends(self):
        self.routes[target('/api/observations')] = page([observation(i) for i in range(1, 101)], more=True)
        self.routes[target('/api/observations', offset=100)] = page([observation(i) for i in range(101, 151)], offset=100)
        client, snapshot = self.read()
        assert client.summary['claims']['observation'] == 150 and snapshot.checkpoint_ready
        assert [t for t in self.targets() if t[1].startswith('/api/observations')] == [target('/api/observations'), target('/api/observations', offset=100)]

    def test_a_list_cut_by_max_rows_asks_only_for_what_remains_and_never_checkpoints(self):
        selection = replace(self.selection, max_rows=150)
        self.routes[target('/api/observations')] = page([observation(i) for i in range(1, 101)], more=True)
        self.routes[target('/api/observations', offset=100, limit=50)] = page([observation(i) for i in range(101, 151)], offset=100, limit=50, more=True)
        client, snapshot = self.read(selection)
        assert client.summary['claims']['observation'] == 150
        assert ('observations', 'pagination_incomplete', 'max_rows_reached') in [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]
        assert not snapshot.checkpoint_ready and 'listing_truncated' in client.summary['warnings'] and snapshot.metadata.coverage.truncated

    def test_a_list_that_ends_exactly_at_max_rows_is_complete_only_when_the_worker_says_so(self):
        selection = replace(self.selection, max_rows=2)
        for route in LISTS[1:]: self.routes[target(route, limit=2)] = page([], limit=2)
        self.routes[target('/api/observations', limit=2)] = page([observation(1), observation(2)], limit=2)
        _, snapshot = self.read(selection)
        assert snapshot.checkpoint_ready
        self.routes[target('/api/observations', limit=2)] = page([observation(1), observation(2)], limit=2, more=True)
        _, snapshot = self.read(selection)
        assert not snapshot.checkpoint_ready

    def test_a_row_listed_twice_because_the_worker_moved_under_the_reader_fails_the_capture(self):
        self.routes[target('/api/observations')] = page([observation(i) for i in range(1, 101)], more=True)
        self.routes[target('/api/observations', offset=100)] = page([observation(100)], offset=100)
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('bad', [
        pytest.param(dict(extra=1), id='unknown-envelope-field'),
        pytest.param(dict(items={}), id='items-not-a-list'),
        pytest.param(dict(items=['x']), id='row-not-an-object'),
        pytest.param(dict(hasMore='yes'), id='more-not-a-bool'),
        pytest.param(dict(hasMore=1), id='more-is-an-int'),
        pytest.param(dict(offset=5), id='offset-not-echoed'),
        pytest.param(dict(offset=False), id='offset-bool'),
        pytest.param(dict(limit=20), id='limit-clamped-by-the-worker'),
        pytest.param(dict(items=[observation(1)], hasMore=True), id='more-after-a-short-page'),
        pytest.param(dict(items=[], hasMore=True), id='empty-page-that-claims-more'),
        pytest.param(dict(items=[observation(i) for i in range(1, 102)]), id='page-over-the-requested-size'),
        pytest.param(dict(items=[observation(1, **{f'f{i}': 1 for i in range(200)})]), id='row-with-too-many-fields')])
    def test_a_malformed_page_fails_the_capture_and_commits_nothing(self, bad):
        self.routes[target('/api/observations')] = {**page([observation(1)]), **bad}
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('raw', [
        pytest.param([], id='bare-list'),
        pytest.param(b'{"items":[{"id":NaN}],"hasMore":false,"offset":0,"limit":100}', id='nan-in-a-row'),
        pytest.param(b'{"items":[],"items":[],"hasMore":false,"offset":0,"limit":100}', id='duplicate-key'),
        pytest.param(b'not json', id='not-json')])
    def test_a_response_that_is_not_the_pinned_envelope_fails_the_capture(self, raw):
        self.routes[target('/api/observations')] = Response(raw, headers=JSON)
        with pytest.raises(TransportError): self.read()

    def test_a_non_json_content_type_fails_the_capture(self):
        self.routes[target('/api/observations')] = Response(page([]), headers={'Content-Type': 'text/html'})
        with pytest.raises(TransportError): self.read()


class TestClaudeMemWorkerCoverage(Fixture):
    @pytest.mark.parametrize('status, state', [(401, 'unauthorized'), (403, 'forbidden'), (429, 'rate_limited')])
    def test_a_denied_first_list_is_blocked_coverage_with_no_data_and_no_further_request(self, status, state):
        self.routes[target('/api/observations')] = Response(b'SYNTHETIC_WORKER_SECRET', status)
        client, snapshot = self.read()
        assert client.summary['coverage'] == [dict(resource='observations', parent_sha256=None, state=state, basis=None)]
        assert not snapshot.checkpoint_ready and client.summary['claims'] == dict(observation=0, generated_summary=0, user_prompt=0)
        assert self.targets() == [('GET', '/api/version'), target('/api/observations')] and 'capture_blocked_or_incomplete' in client.summary['warnings']

    @pytest.mark.parametrize('status, state', [(401, 'unauthorized'), (403, 'forbidden'), (429, 'rate_limited')])
    def test_a_denied_version_read_ends_the_capture_before_any_list_is_requested(self, status, state):
        self.routes[('GET', '/api/version')] = Response(b'SYNTHETIC_WORKER_SECRET', status)
        client, snapshot = self.read()
        assert client.summary['coverage'] == [dict(resource='version', parent_sha256=None, state=state, basis=None)]
        assert not snapshot.checkpoint_ready and self.targets() == [('GET', '/api/version')]

    def test_a_missing_list_route_is_only_that_lists_gap_and_blocks_the_checkpoint(self):
        self.routes[target('/api/summaries')] = Response(b'SYNTHETIC_WORKER_SECRET', 404)
        client, snapshot = self.read()
        assert [(c['resource'], c['state']) for c in client.summary['coverage']] == [
            ('observations', 'captured'), ('summaries', 'not_found'), ('prompts', 'captured')]
        assert client.summary['claims'] == dict(observation=2, generated_summary=0, user_prompt=1) and not snapshot.checkpoint_ready

    def test_a_forbidden_later_list_ends_the_capture_and_keeps_what_was_read_as_partial(self):
        self.routes[target('/api/summaries')] = Response(b'SYNTHETIC_WORKER_SECRET', 403)
        client, snapshot = self.read()
        assert [(c['resource'], c['state']) for c in client.summary['coverage']] == [('observations', 'captured'), ('summaries', 'forbidden')]
        assert target('/api/prompts') not in self.targets() and not snapshot.checkpoint_ready
        assert client.summary['claims']['observation'] == 2 and client.summary['capture_completeness'] == 'partial'

    def test_a_denied_second_page_is_truncation_not_an_empty_end(self):
        self.routes[target('/api/observations')] = page([observation(i) for i in range(1, 101)], more=True)
        self.routes[target('/api/observations', offset=100)] = Response(b'SYNTHETIC_WORKER_SECRET', 403)
        client, snapshot = self.read()
        assert ('observations', 'pagination_incomplete', 'forbidden') in [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]
        assert not snapshot.checkpoint_ready and snapshot.metadata.coverage.truncated

    def test_a_spent_request_budget_is_partial_coverage_never_a_guess(self):
        client, snapshot = self.read(max_pages=2)
        assert client.summary['requests'] == 2 and not snapshot.checkpoint_ready
        assert [c['state'] for c in client.summary['coverage']] == ['captured', 'budget_exhausted']
        assert snapshot.metadata.coverage.limit_reached and client.summary['claims']['user_prompt'] == 0

    def test_a_rate_limited_list_halts_every_later_request(self):
        self.routes[target('/api/summaries')] = Response(b'SYNTHETIC_WORKER_SECRET', 429)
        client, _ = self.read()
        assert [(c['resource'], c['state']) for c in client.summary['coverage']] == [('observations', 'captured'), ('summaries', 'rate_limited')]
        assert target('/api/prompts') not in self.targets()


class TestClaudeMemWorkerVersion(Fixture):
    def test_a_worker_that_reports_the_selected_release_is_attested_by_the_worker(self):
        client, _ = self.read()
        assert client.summary['version_attestation'] == 'worker_reported'
        assert (client.summary['upstream_shape'], client.summary['compatibility']) == ('source_inferred', 'unverified')

    @pytest.mark.parametrize('reported', [pytest.param('15.9.1', id='newer'), pytest.param('v15.9.0', id='prefixed'), pytest.param('14.0.0', id='older')])
    def test_a_worker_of_another_release_is_refused_before_any_list_is_read(self, reported):
        self.routes[('GET', '/api/version')] = dict(version=reported)
        with pytest.raises(TransportError): self.read()
        assert self.targets() == [('GET', '/api/version')]

    def test_a_worker_without_a_version_route_is_read_on_the_callers_declaration_with_a_warning(self):
        self.routes[('GET', '/api/version')] = Response(b'SYNTHETIC_WORKER_SECRET', 404)
        client, snapshot = self.read()
        assert client.summary['version_attestation'] == 'caller_declared' and 'worker_version_unreported' in client.summary['warnings']
        assert snapshot.checkpoint_ready

    @pytest.mark.parametrize('value', [pytest.param(dict(version=15), id='number'), pytest.param(dict(), id='absent'),
                                       pytest.param(dict(version='15.9.0 beta!'), id='unsafe-text'), pytest.param([], id='list')])
    def test_an_unrecognizable_version_answer_is_not_read_as_a_match(self, value):
        self.routes[('GET', '/api/version')] = value
        client, _ = self.read()
        assert client.summary['version_attestation'] == 'caller_declared' and 'worker_version_unrecognized' in client.summary['warnings']


class TestClaudeMemSelectionValidation:
    VALID = dict(base_url=BASE, instance=INSTANCE, release='15.9.0', project=PROJECT)

    @pytest.mark.parametrize('change', [
        pytest.param(dict(base_url='http://claude-mem.example.test'), id='plain-http'),
        pytest.param(dict(base_url='http://127.0.0.1:37777'), id='loopback-http'),
        pytest.param(dict(base_url=BASE+'/'), id='trailing-slash'),
        pytest.param(dict(base_url=BASE+'/api'), id='route-as-root'),
        pytest.param(dict(base_url='https://user:pw@claude-mem.example.test'), id='userinfo'),
        pytest.param(dict(base_url=BASE+'?x=1'), id='query'),
        pytest.param(dict(instance=' '), id='blank-instance'),
        pytest.param(dict(instance=' padded'), id='padded-instance'),
        pytest.param(dict(project=''), id='no-project'),
        pytest.param(dict(project=' '), id='blank-project'),
        pytest.param(dict(project=' padded'), id='padded-project'),
        pytest.param(dict(project='p'*201), id='oversized-project'),
        pytest.param(dict(release='15.9.1'), id='unpinned-release'),
        pytest.param(dict(platform_source='SYNTHETIC'), id='unknown-platform'),
        pytest.param(dict(platform_source='cl aude'), id='unsafe-platform'),
        pytest.param(dict(max_rows=0), id='no-rows'),
        pytest.param(dict(max_rows=10001), id='above-the-ceiling'),
        pytest.param(dict(max_rows=True), id='bool-count'),
        pytest.param(dict(schema_version=2), id='schema')])
    def test_selection_refuses_what_could_cross_a_boundary_or_guess_a_shape(self, change):
        with pytest.raises(ValueError): ClaudeMemSelection(**{**self.VALID, **change})

    def test_selection_accepts_a_sub_path_root_and_every_platform_label(self):
        assert ClaudeMemSelection(**{**self.VALID, 'base_url': BASE+'/claude-mem'}).root == ('claude-mem.example.test', 443, '/claude-mem')
        for platform in ('claude', 'codex', 'cursor', 'kimi', 'pi', 'antigravity-cli'):
            assert ClaudeMemSelection(**self.VALID, platform_source=platform).platform_source == platform

    def test_selection_round_trips_through_its_manifest_form(self):
        selection = ClaudeMemSelection(**self.VALID)
        assert ClaudeMemSelection.parse(json.loads(json.dumps(selection.to_dict()))) == selection
        with pytest.raises(ValueError): ClaudeMemSelection.parse(dict(selection.to_dict(), extra=1))
        with pytest.raises(ValueError): ClaudeMemSelection.parse({k: v for k, v in selection.to_dict().items() if k != 'project'})

    def test_a_reader_refuses_an_endpoint_other_than_its_selection_and_a_foreign_selection_object(self):
        selection = ClaudeMemSelection(**self.VALID)
        with pytest.raises(TransportError): ClaudeMemWorkerAPI('https://other.example.test', selection)
        with pytest.raises(TransportError): ClaudeMemWorkerAPI(BASE, selection.to_dict())


@pytest.fixture
def _credentials():
    with patch.dict(os.environ, SYNTHETIC_WORKER_KEY=KEY):
        yield


class TestClaudeMemWorkerCli(Fixture):
    @pytest.fixture(autouse=True)
    def _cli(self, _worker, tmp_path):
        self.root = tmp_path
        self.manifest, self.journal = tmp_path/'worker.json', tmp_path/'journal.json'
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
        for secret in (*SECRETS, BASE, str(self.root)): assert secret not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    ENV = ('--authorization-env', 'SYNTHETIC_WORKER_KEY')

    def test_probe_contacts_nothing_and_preview_import_and_status_use_only_the_named_variable(self, _credentials):
        code, result = self.invoke('probe', *self.ENV)
        assert (code, result['status'], result['network_used']) == (0, 'recognized', False)
        self.wire.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            assert self.invoke('preview', *self.ENV)[0] == 3
        self.wire.assert_not_called()
        code, result = self.invoke('preview', *self.ENV)
        assert code == 0 and result['checkpoint_ready'] and result['network_used']
        assert result['claude_mem_summary']['claims'] == dict(observation=2, generated_summary=1, user_prompt=1) and 'rate_limit' in result
        assert not self.journal.exists()
        assert self.invoke('import', *self.ENV)[0] == 0
        repeated = self.invoke('import', *self.ENV)
        assert repeated[0] == 0 and repeated[1]['imported'] == 0
        assert self.invoke('status')[0] == 0

    def test_a_blocked_import_commits_nothing_and_exits_3(self, _credentials):
        assert self.invoke('import', *self.ENV)[0] == 0
        original = self.journal.read_bytes()
        self.routes[target('/api/prompts')] = Response(b'SYNTHETIC_WORKER_SECRET', 403)
        code, result = self.invoke('import', *self.ENV)
        assert (code, result['status'], result['checkpoint_committed']) == (3, 'blocked', False)
        assert ('prompts', 'forbidden') in [(c['resource'], c['state']) for c in result['claude_mem_summary']['coverage']]
        assert self.journal.read_bytes() == original

    def test_a_credential_file_works_and_both_sources_together_are_refused(self, _credentials):
        credential = self.root/'key'
        credential.write_text(KEY+'\n')
        assert self.invoke('preview', '--authorization-file', str(credential))[0] == 0
        assert self.invoke('preview', *self.ENV, '--authorization-file', str(credential))[0] == 2

    def test_a_worker_of_another_release_exits_2_and_imports_nothing(self, _credentials):
        self.routes[('GET', '/api/version')] = dict(version='15.9.1')
        assert self.invoke('import', *self.ENV)[0] == 2
        assert not self.journal.exists()

    @pytest.mark.parametrize('change', [pytest.param(dict(endpoint='https://other.example.test'), id='other-endpoint'),
                                        pytest.param(dict(limits=dict(retries=3)), id='unknown-limit')])
    def test_a_configuration_that_exceeds_its_declared_source_is_rejected(self, change, _credentials):
        self.write(**change)
        assert self.invoke('probe')[0] == 2
        self.wire.assert_not_called()
