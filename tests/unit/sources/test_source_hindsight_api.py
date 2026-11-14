"""Synthetic Hindsight v1.1.0 REST reads over a fake HTTPS wire: one selected bank, GET only, never a task outcome."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timezone
import io
import json
import os
from unittest.mock import patch

import pytest

from tests.unit.sources.test_source_mem0_api import Wire
from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.hindsight_api import PROVIDER, HindsightRestAPI, HindsightSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

BASE = 'https://hindsight.example.test'
KEY = 'Bearer SYNTHETIC_SECRET_TOKEN'
SECRETS = ('SYNTHETIC_SECRET_TOKEN', 'SYNTHETIC_PRIVATE', 'doc-alpha', 'fixture-bank', 'unit-1')
AT = '2031-04-05T06:07:08Z'
# The reader judges every validity window at its capture instant; one fixed instant keeps these controls deterministic.
CAPTURED = datetime(2031, 5, 1, tzinfo=timezone.utc)
VERSION = ('GET', '/version')
LIVE = '/v1/default/banks/fixture-bank/memories/list?limit={}&offset={}&state=valid'
ARCHIVE = '/v1/default/banks/fixture-bank/memories/list?limit={}&offset={}&state=invalidated'


def unit(identity, body, **fields):
    return dict(dict(id=identity, text=body, context='invented context', date='', fact_type='world', document_id='doc-alpha',
        mentioned_at=AT, occurred_start=None, occurred_end=None, entities='', chunk_id=None, proof_count=1, tags=[], metadata={},
        consolidated_at=None, consolidation_failed_at=None, state='valid', invalidation_reason=None, invalidated_at=None,
        edited_at=None, updated_at=AT, source_memory_ids=[]), **fields)


def page(items, total=None, limit=100, offset=0):
    return dict(items=items, total=len(items) if total is None else total, limit=limit, offset=offset)


class HindsightFixture(Wire):
    @pytest.fixture(autouse=True)
    def _hindsight(self, _wire):
        self.selection = HindsightSelection(BASE, 'fixture-instance', '1.1.0', 'fixture-bank')
        self.source = self.spec(self.selection, auth_mode='configured')
        self.live = [
            unit('unit-1', 'invented office was in Lisbon SYNTHETIC_PRIVATE', occurred_start='2030-01-01T00:00:00Z', occurred_end='2031-03-01T00:00:00Z'),
            unit('unit-2', 'invented office moved to Porto', occurred_start='2031-03-01T00:00:00Z'),
            unit('unit-3', 'invented office will open in Faro', occurred_start='2032-01-01T00:00:00Z'),
            unit('unit-4', 'all tests pass in the invented repo', fact_type='experience', proof_count=99),
            unit('obs-1', 'invented consolidated office history', fact_type='observation', source_memory_ids=['unit-1', 'unit-2', 'unit-9'])]
        self.invalidated = [unit('unit-0', 'invented office was in Evora', state='invalidated', invalidated_at='2031-04-02T00:00:00Z',
            invalidation_reason='invented correction SYNTHETIC_PRIVATE', occurred_start='2030-01-01T00:00:00Z')]
        self.routes.update({VERSION: dict(api_version='1.1.0', features={}), ('GET', LIVE.format(100, 0)): lambda: page(self.live),
            ('GET', ARCHIVE.format(100, 0)): lambda: page(self.invalidated)})
        with patch('agent_harness_lab.sources.hindsight_api.now', return_value=CAPTURED):
            yield

    def spec(self, selection, **changes):
        return SourceSpec('api', selection.provider, 'v1', digest(selection.base_url), selection.project_sha256, selection.sha256,
            identity_provenance='source_declared_id', enabled=True, **changes)

    def assert_read_only(self):
        for method, target, *_ in self.calls:
            assert method == 'GET' and (target == '/version' or target.startswith('/v1/default/banks/fixture-bank/memories/list?')), target

    def read(self, selection=None, source=None, key=KEY, **options):
        selection = selection or self.selection
        client = HindsightRestAPI(selection.base_url, selection, authorization=key, **options)
        snapshot = client.read(source or (self.source if selection is self.selection else self.spec(selection, auth_mode='configured')))
        self.assert_read_only()
        rendered = json.dumps(client.summary)
        for secret in SECRETS: assert secret not in rendered
        return client, snapshot

    @staticmethod
    def claims(client): return [asdict(c) for c in client.claims]

    @staticmethod
    def coverage(client): return [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]


class TestHindsightRestRead(HindsightFixture):
    def test_the_selected_banks_memories_become_separate_claims_with_distinct_times_and_statuses(self):
        client, snapshot = self.read()
        summary = client.summary
        assert (summary['profile'], summary['hindsight_version'], summary['upstream_commit'], summary['reported_release']) == (
            'hindsight-rest-v1', '1.1.0', 'a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5', '1.1.0')
        assert summary['claims'] == dict(retained_document=0, extracted_fact=4, fact_relation=0, consolidated_observation=1, invalidated_fact=1)
        # unit-1 ended before capture, unit-3 starts after it, and the correction is a separate, curated-out claim.
        assert summary['assertions'] == dict(current=3, historical=1, not_yet_valid=1, invalidated=1, undetermined=0, not_applicable=0)
        invalidated = next(c for c in self.claims(client) if c['claim_type'] == 'invalidated_fact')
        assert (invalidated['invalidated_at'], invalidated['valid_from'], invalidated['recorded_at'], invalidated['review_candidate']) == (
            '2031-04-02T00:00:00+00:00', '2030-01-01T00:00:00+00:00', None, True)
        assert snapshot.checkpoint_ready and summary['checkpoint_ready'] and summary['read_methods'] == ['GET']
        assert {r.record_type for r in snapshot.records} == {'hindsight_extracted_fact', 'hindsight_consolidated_observation',
            'hindsight_invalidated_fact', 'hindsight_rest_capture'}
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)
        assert not public_export_allowed([summary])

    def test_only_the_version_and_the_selected_banks_listings_are_ever_requested(self):
        self.read()
        assert self.targets() == [VERSION, ('GET', LIVE.format(100, 0)), ('GET', ARCHIVE.format(100, 0))]
        assert all(call.args == ('hindsight.example.test', 443) for call in self.wire.call_args_list)

    def test_the_credential_goes_only_in_the_authorization_header(self):
        self.read()
        assert all(headers['Authorization'] == KEY and 'X-API-Key' not in headers for *_, headers in self.calls)

    def test_a_retrieval_count_or_a_memory_saying_tests_pass_is_not_a_verified_outcome(self):
        client, snapshot = self.read()
        assert (client.summary['task_outcomes_verified'], client.summary['evidence_of_use'], client.summary['retrieval_scores_used'],
                client.summary['independent_trials']) == (False, False, False, None)
        assert not any(r.outcome for r in snapshot.records)
        assert all((c['independent_case'], c['task_outcome_verified'], c['native_session_verified']) == (False,)*3
                   for c in self.claims(client))

    def test_no_write_recall_reflect_or_discovery_operation_is_requested_and_the_summary_says_so(self):
        client, _ = self.read()
        operations = client.summary['operations']
        assert (operations['retain'], operations['recall'], operations['reflect'], operations['list_banks']) == (
            'refused_write', 'refused_inference', 'refused_generation', 'refused_discovery')
        assert (operations['memories_list'], operations['version']) == ('read_only_get', 'read_only_get')
        assert (client.summary['network_used'], client.summary['server_contacted'], client.summary['bodies_stored']) == (True, True, False)

    def test_an_observation_counts_only_sources_captured_in_the_same_bank_listing(self):
        client, _ = self.read()
        observation = next(c for c in self.claims(client) if c['claim_type'] == 'consolidated_observation')
        assert (observation['source_count'], observation['synthesizes_multiple_origins']) == (2, True)
        assert 'observation_source_unresolved' in client.summary['warnings']

    def test_the_list_view_has_no_creation_time_so_the_recorded_time_stays_unknown(self):
        client, _ = self.read()
        assert all(c['recorded_at'] is None for c in self.claims(client))
        assert 'recorded_at_unavailable' in client.summary['warnings'] and 'documents_not_read' in client.summary['warnings']

    def test_a_second_read_is_identical_and_a_changed_memory_is_a_new_revision(self):
        _, first = self.read()
        _, second = self.read()
        assert first.sha256 == second.sha256
        self.live[3]['text'] = 'a different invented fact'
        _, third = self.read()
        assert {(r.native_id_sha256, r.revision_sha256) for r in third.records} != {(r.native_id_sha256, r.revision_sha256) for r in first.records}
        assert {r.native_id_sha256 for r in third.records} == {r.native_id_sha256 for r in first.records}

    def test_a_malformed_or_unknown_timestamp_stays_undetermined_not_current(self):
        self.live[3]['occurred_start'] = 'not a time'
        self.live[2]['occurred_end'] = '2030-01-01T00:00:00'
        client, _ = self.read()
        assert client.summary['assertions']['undetermined'] == 2 and client.summary['timestamps']['valid_from']['invalid'] == 1
        assert client.summary['timestamps']['valid_until']['invalid'] == 1 and 'invalid_timestamp' in client.summary['warnings']

    def test_an_open_upstream_bound_is_a_placeholder_not_an_instant(self):
        self.live[3]['occurred_end'] = '9999-12-31T23:59:59Z'
        client, _ = self.read()
        assert client.summary['assertions']['current'] == 3 and client.summary['timestamps']['valid_until']['placeholder'] == 1

    @pytest.mark.parametrize('identity', [None, '', '  ', 3, ['x']])
    def test_a_row_without_a_usable_identity_is_rejected_and_never_guessed(self, identity):
        self.live.append(unit(identity, 'invented unnamed fact'))
        client, snapshot = self.read()
        assert client.summary['claims']['extracted_fact'] == 4 and client.summary['rows_rejected']['valid'] == 1
        assert 'row_without_native_identity' in client.summary['warnings'] and snapshot.checkpoint_ready

    def test_a_row_outside_the_pinned_columns_is_counted_not_trusted(self):
        self.live[3]['surprise'] = 1
        client, _ = self.read()
        assert client.summary['rows_with_unknown_fields'] == dict(valid=1, invalidated=0) and 'unknown_row_fields' in client.summary['warnings']

    def test_an_identity_repeated_in_one_listing_means_the_listing_moved_and_never_checkpoints(self):
        self.live.append(unit('unit-4', 'a second invented row'))
        client, snapshot = self.read()
        assert ('live_listing', 'pagination_incomplete', 'listing_moved') in self.coverage(client) and not snapshot.checkpoint_ready

    def test_a_fact_both_live_and_curated_out_is_flagged_not_resolved(self):
        self.invalidated.append(unit('unit-2', 'invented office moved to Porto', state='invalidated'))
        client, _ = self.read()
        assert 'invalidated_fact_still_live' in client.summary['warnings']

    def test_an_unrecognized_fact_type_is_kept_as_unrecognized(self):
        self.live[3]['fact_type'] = 'invented-kind'
        client, _ = self.read()
        assert 'unrecognized_fact_type' in client.summary['warnings']

    def test_pages_are_read_by_offset_until_the_reported_total(self):
        rows = [unit(f'unit-{n}', f'invented fact {n}') for n in range(150)]
        self.routes[('GET', LIVE.format(100, 0))] = page(rows[:100], total=150)
        self.routes[('GET', LIVE.format(100, 100))] = page(rows[100:], total=150, offset=100)
        client, snapshot = self.read()
        assert client.summary['claims']['extracted_fact'] == 150 and snapshot.checkpoint_ready
        assert self.targets()[1:3] == [('GET', LIVE.format(100, 0)), ('GET', LIVE.format(100, 100))]

    def test_a_bound_below_the_total_is_truncated_coverage_that_never_checkpoints(self):
        selection = replace(self.selection, max_memories=2)
        self.routes[('GET', LIVE.format(2, 0))] = page(self.live[:2], total=5, limit=2)
        self.routes[('GET', ARCHIVE.format(2, 0))] = page(self.invalidated, limit=2)
        client, snapshot = self.read(selection)
        assert ('live_listing', 'pagination_incomplete', 'max_memories_reached') in self.coverage(client)
        assert not snapshot.checkpoint_ready and 'listing_truncated' in client.summary['warnings'] and snapshot.metadata.coverage.truncated

    @pytest.mark.parametrize('second, basis', [
        pytest.param(page([unit('unit-150', 'x')], total=151, offset=100), 'total_count_changed', id='total-changed'),
        pytest.param(page([], total=150, offset=100), 'short_page', id='empty-before-total'),
        pytest.param(page([unit('unit-0', 'x')], total=150, offset=100), 'listing_moved', id='row-repeated')])
    def test_a_listing_that_moves_between_pages_is_incomplete_coverage_not_a_guess(self, second, basis):
        self.routes[('GET', LIVE.format(100, 0))] = page([unit(f'unit-{n}', 'x') for n in range(100)], total=150)
        self.routes[('GET', LIVE.format(100, 100))] = second
        client, snapshot = self.read()
        assert ('live_listing', 'pagination_incomplete', basis) in self.coverage(client) and not snapshot.checkpoint_ready

    @pytest.mark.parametrize('bad', [
        pytest.param(dict(items=[], total=0, limit=100, offset=0, extra=1), id='extra-envelope-field'),
        pytest.param([], id='list'),
        pytest.param(dict(items={}, total=0, limit=100, offset=0), id='items-not-list'),
        pytest.param(dict(items=['x'], total=1, limit=100, offset=0), id='row-not-object'),
        pytest.param(dict(items=[], total=True, limit=100, offset=0), id='bool-total'),
        pytest.param(dict(items=[], total=-1, limit=100, offset=0), id='negative-total'),
        pytest.param(dict(items=[], total=0, limit=50, offset=0), id='limit-not-echoed'),
        pytest.param(dict(items=[], total=0, limit=100, offset=7), id='offset-not-echoed'),
        pytest.param(page([unit(f'unit-{n}', 'x') for n in range(101)]), id='more-rows-than-asked'),
        pytest.param(page([unit('unit-1', 'x', state='invalidated')]), id='state-disagrees-with-the-listing')])
    def test_a_malformed_listing_fails_the_whole_capture_without_a_snapshot(self, bad):
        self.routes[('GET', LIVE.format(100, 0))] = bad
        with pytest.raises(TransportError): self.read()


class TestHindsightRestBoundaries(HindsightFixture):
    @pytest.mark.parametrize('answer, basis', [
        pytest.param(dict(api_version='1.2.0', features={}), 'release_mismatch', id='another-release'),
        pytest.param(dict(api_version='', features={}), 'unrecognized', id='empty-release'),
        pytest.param(dict(version='1.1.0'), 'unrecognized', id='not-hindsight-shape'),
        pytest.param(dict(api_version=1), 'unrecognized', id='numeric-release'),
        pytest.param([], 'unrecognized', id='list')])
    def test_an_unsupported_release_is_blocked_before_any_bank_is_read(self, answer, basis):
        self.routes[VERSION] = answer
        client, snapshot = self.read()
        assert self.coverage(client) == [('version', 'unsupported_release', basis)]
        assert self.targets() == [VERSION] and not snapshot.checkpoint_ready and snapshot.records == () and client.summary['claims'] is None
        assert 'capture_blocked_or_incomplete' in client.summary['warnings']

    @pytest.mark.parametrize('status, state', [(401, 'unauthorized'), (403, 'forbidden'), (404, 'not_found'), (429, 'rate_limited')])
    def test_a_denied_version_read_is_blocked_coverage_with_no_bank_read(self, status, state):
        self.routes[VERSION] = Response(b'SYNTHETIC_SECRET_TOKEN', status)
        client, snapshot = self.read()
        assert self.coverage(client) == [('version', state, None)] and self.targets() == [VERSION] and not snapshot.checkpoint_ready

    @pytest.mark.parametrize('status, state', [(401, 'unauthorized'), (403, 'forbidden'), (404, 'not_found'), (429, 'rate_limited')])
    def test_a_denied_bank_listing_is_blocked_coverage_and_the_curated_listing_is_not_asked(self, status, state):
        self.routes[('GET', LIVE.format(100, 0))] = Response(b'SYNTHETIC_SECRET_TOKEN', status)
        client, snapshot = self.read()
        assert self.coverage(client) == [('version', 'captured', None), ('live_listing', state, None)]
        assert self.targets() == [VERSION, ('GET', LIVE.format(100, 0))]
        assert not snapshot.checkpoint_ready and snapshot.records == () and 'listing_unavailable' in client.summary['warnings']

    def test_a_denied_curated_listing_keeps_the_live_claims_but_never_checkpoints(self):
        self.routes[('GET', ARCHIVE.format(100, 0))] = Response(b'SYNTHETIC_SECRET_TOKEN', 403)
        client, snapshot = self.read()
        assert ('invalidated_listing', 'forbidden', None) in self.coverage(client)
        assert client.summary['claims']['extracted_fact'] == 4 and client.summary['claims']['invalidated_fact'] == 0
        assert not snapshot.checkpoint_ready

    def test_a_server_error_or_non_json_answer_fails_the_capture(self):
        self.routes[('GET', LIVE.format(100, 0))] = Response(b'x', 500)
        with pytest.raises(TransportError): self.read()
        self.routes[('GET', LIVE.format(100, 0))] = Response(b'{}', headers={'Content-Type': 'text/html'})
        with pytest.raises(TransportError): self.read()

    def test_a_spent_request_budget_is_partial_coverage(self):
        client, snapshot = self.read(max_pages=2)
        assert ('invalidated_listing', 'budget_exhausted', None) in self.coverage(client)
        assert len(self.calls) == 2 and not snapshot.checkpoint_ready and snapshot.metadata.coverage.limit_reached

    def test_without_a_credential_an_open_server_is_read_without_any_auth_header(self):
        source = self.spec(self.selection)
        HindsightRestAPI(BASE, self.selection).read(source)
        assert all('Authorization' not in headers for *_, headers in self.calls)
        with pytest.raises(TransportError): HindsightRestAPI(BASE, self.selection, authorization=KEY).read(source)

    def test_scope_identity_and_source_must_match_before_any_connection(self):
        other = replace(self.selection, bank_id='other-bank')
        for source in (replace(self.source, enabled=False), replace(self.source, project_sha256=digest('foreign')),
                       replace(self.source, scope_sha256=digest('foreign')), replace(self.source, provider='gitea'),
                       replace(self.source, root_sha256=digest('https://other.example.test')), self.spec(other, auth_mode='configured')):
            with pytest.raises(TransportError): HindsightRestAPI(BASE, self.selection, authorization=KEY).read(source)
        with pytest.raises(TransportError): HindsightRestAPI('https://other.example.test', self.selection)
        with pytest.raises(TransportError): HindsightRestAPI(BASE, self.selection, authorization=KEY).read(self.source, prior={})
        self.wire.assert_not_called()

    def test_another_bank_instance_or_server_is_another_project(self):
        assert len({self.selection.project_sha256, replace(self.selection, bank_id='other-bank').project_sha256,
                    replace(self.selection, instance='other').project_sha256,
                    replace(self.selection, base_url='https://hindsight.example.test/sub').project_sha256}) == 4

    def test_registry_import_is_idempotent_and_a_blocked_capture_commits_nothing(self, tmp_path):
        registry = Registry()
        registry.register(Capability('api', PROVIDER, 'v1', ('contextual_assertion',)), HindsightRestAPI(BASE, self.selection, authorization=KEY))
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope)
        first = registry.ingest(self.source, store, **scope)
        assert first.imported > 0 and first.status == 'complete'
        assert registry.ingest(self.source, store, **scope).imported == 0
        self.routes[VERSION] = dict(api_version='1.2.0', features={})
        before = store.status(self.source)['cursor_sha256']
        with pytest.raises(ValueError, match='not ready for a committed checkpoint'): registry.ingest(self.source, store, **scope)
        assert store.status(self.source)['cursor_sha256'] == before


class TestHindsightSelectionValidation:
    VALID = dict(base_url=BASE, instance='fixture-instance', release='1.1.0', bank_id='fixture-bank')

    @pytest.mark.parametrize('change', [
        pytest.param(dict(bank_id=''), id='no-bank'),
        pytest.param(dict(bank_id='a/b'), id='bank-with-slash'),
        pytest.param(dict(bank_id='a%2fb'), id='bank-percent-encoded'),
        pytest.param(dict(bank_id='a b'), id='bank-with-space'),
        pytest.param(dict(bank_id='a?x=1'), id='bank-with-query'),
        pytest.param(dict(bank_id='../x'), id='bank-traversal'),
        pytest.param(dict(bank_id='*'), id='bank-wildcard'),
        pytest.param(dict(bank_id='a'*129), id='bank-too-long'),
        pytest.param(dict(bank_id=7), id='bank-not-text'),
        pytest.param(dict(base_url='http://hindsight.example.test'), id='plain-http'),
        pytest.param(dict(base_url=BASE+'/'), id='trailing-slash'),
        pytest.param(dict(base_url=BASE+'/v1'), id='route-as-root'),
        pytest.param(dict(base_url='https://user:pw@hindsight.example.test'), id='userinfo'),
        pytest.param(dict(instance=' '), id='blank-instance'),
        pytest.param(dict(release='1.1.1'), id='unpinned-release'),
        pytest.param(dict(max_memories=0), id='no-memories'),
        pytest.param(dict(max_memories=10001), id='above-the-ceiling'),
        pytest.param(dict(max_memories=True), id='bool-count'),
        pytest.param(dict(schema_version=2), id='schema')])
    def test_selection_refuses_what_could_cross_a_bank_boundary_or_guess_a_shape(self, change):
        with pytest.raises(ValueError): HindsightSelection(**{**self.VALID, **change})

    def test_a_colon_in_a_bank_name_is_accepted_and_the_selection_round_trips_through_its_manifest_form(self):
        selection = HindsightSelection(**{**self.VALID, 'bank_id': 'agent::project.v2'})
        assert HindsightSelection.parse(json.loads(json.dumps(selection.to_dict()))) == selection
        with pytest.raises(ValueError): HindsightSelection.parse(dict(selection.to_dict(), extra=1))


@pytest.fixture
def _credentials():
    with patch.dict(os.environ, SYNTHETIC_HINDSIGHT_KEY=KEY):
        yield


class TestHindsightCli(HindsightFixture):
    @pytest.fixture(autouse=True)
    def _cli(self, _hindsight, tmp_path):
        self.root = tmp_path
        self.manifest, self.journal = tmp_path/'hindsight.json', tmp_path/'journal.json'
        self.write()

    def write(self, **changes):
        value = dict(schema_version=1, source=self.source.to_dict(), endpoint=BASE, selection=self.selection.to_dict(), limits=dict(max_pages=40))
        self.manifest.write_text(json.dumps({**value, **changes}))

    def invoke(self, operation, *extra):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--cache', str(self.journal),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256, *extra])
        for secret in (*SECRETS, BASE, str(self.root)): assert secret not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    ENV = ('--authorization-env', 'SYNTHETIC_HINDSIGHT_KEY')

    def test_probe_contacts_nothing_and_preview_import_and_status_use_only_the_named_variable(self, _credentials):
        code, result = self.invoke('probe', *self.ENV)
        assert (code, result['status'], result['network_used']) == (0, 'recognized', False)
        self.wire.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            assert self.invoke('preview', *self.ENV)[0] == 3
        self.wire.assert_not_called()
        code, result = self.invoke('preview', *self.ENV)
        assert code == 0 and result['checkpoint_ready'] and result['network_used']
        assert result['hindsight_summary']['claims']['extracted_fact'] == 4 and 'rate_limit' in result
        assert not self.journal.exists()
        assert self.invoke('import', *self.ENV)[0] == 0
        repeated = self.invoke('import', *self.ENV)
        assert repeated[0] == 0 and repeated[1]['imported'] == 0
        assert self.invoke('status')[0] == 0

    def test_an_unsupported_release_blocks_the_import_and_exits_3_without_touching_the_journal(self, _credentials):
        assert self.invoke('import', *self.ENV)[0] == 0
        original = self.journal.read_bytes()
        self.routes[VERSION] = dict(api_version='1.2.0', features={})
        code, result = self.invoke('import', *self.ENV)
        assert (code, result['status'], result['checkpoint_committed']) == (3, 'blocked', False)
        assert ('version', 'unsupported_release', 'release_mismatch') in self.coverage_of(result)
        assert self.journal.read_bytes() == original

    @staticmethod
    def coverage_of(result): return [(c['resource'], c['state'], c['basis']) for c in result['hindsight_summary']['coverage']]

    def test_a_credential_file_works_and_both_sources_together_are_refused(self, _credentials):
        credential = self.root/'key'
        credential.write_text(KEY+'\n')
        assert self.invoke('preview', '--authorization-file', str(credential))[0] == 0
        assert self.invoke('preview', *self.ENV, '--authorization-file', str(credential))[0] == 2

    @pytest.mark.parametrize('change', [pytest.param(dict(endpoint='https://other.example.test'), id='other-endpoint'),
                                        pytest.param(dict(limits=dict(retries=3)), id='unknown-limit')])
    def test_a_configuration_that_exceeds_its_declared_source_is_rejected(self, change, _credentials):
        self.write(**change)
        assert self.invoke('probe')[0] == 2
        self.wire.assert_not_called()
