"""Synthetic Mem0 Platform v3 exports: paged get-all listings and history stay scoped contextual claims, never outcomes."""
import contextlib
import copy
import io
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.hardened import HostileReport
from agent_harness_lab.sources.mem0 import Mem0Export, Mem0PlatformClaim, Mem0PlatformExport
from agent_harness_lab.sources.schema import SourceSpec, digest

AT = '2031-04-05T06:07:08Z'
COMMIT = 'f1791daa86d469249c9b002f62644d58a1eb96a0'


def item(identity, body, **fields):
    return dict(dict(id=identity, memory=body, user_id='user-aaa', app_id='app-aaa', metadata=None, categories=['invented'],
                     created_at=AT, updated_at=AT, synthesized=False), **fields)


def event(identity, memory, kind, old, new, **fields):
    return dict(dict(id=identity, memory_id=memory, old_memory=old, new_memory=new, event=kind, user_id='user-aaa',
                     input=[dict(role='user', content='invented SYNTHETIC_SECRET_TOKEN')], created_at=AT, updated_at=AT), **fields)


PAGES = [
    dict(count=4, next='https://invented.example/v3/memories/?page=2&page_size=2', previous=None, results=[
        item('mem-edited', 'invented fact revised', session_id='run-aaa'),
        item('mem-tests', 'all tests pass in the invented repo', session_id='run-aaa')]),
    dict(count=4, next=None, previous='https://invented.example/v3/memories/?page=1&page_size=2', results=[
        item('mem-old', 'invented old fact', replaced_by='mem-edited'),
        item('mem-other', 'another user invented fact', user_id='user-bbb')])]
EXPORT = dict(
    exported_at='2031-06-01T00:00:00Z', api_version='v3', filters=dict(user_id='user-aaa'), show_expired=True, pages=PAGES,
    history={
        'mem-edited': [event('h1', 'mem-edited', 'ADD', None, 'invented fact'),
                       event('h2', 'mem-edited', 'UPDATE', 'invented fact', 'invented fact revised', updated_at='2031-04-06T00:00:00Z')],
        'mem-tests': [event('h3', 'mem-tests', 'ADD', None, 'all tests pass in the invented repo')],
        'mem-old': [event('h4', 'mem-old', 'ADD', None, 'invented old fact')],
        'mem-gone': [event('h5', 'mem-gone', 'ADD', None, 'invented deleted fact'),
                     event('h6', 'mem-gone', 'DELETE', 'invented deleted fact', None, updated_at='2031-04-07T00:00:00Z')]})


class TestMem0PlatformSource:
    @pytest.fixture(autouse=True)
    def _export(self, tmp_path):
        self.root = tmp_path
        (self.root/'exports').mkdir()
        self.write(EXPORT)

    def write(self, value, **selection):
        (self.root/'exports/mem0.json').write_text(json.dumps(value))
        self.selection = dict(dict(schema_version=1, api_version='v3', instance='fixture-instance', export='exports/mem0.json'),
                              **selection)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec('local_file', 'mem0-platform-export', 'v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = Mem0PlatformExport(self.manifest)

    def read(self, value=None, **selection):
        self.write(EXPORT if value is None else value, **selection)
        return self.connector.read(self.source)

    def memory(self, identity):
        native = digest(['mem0-platform', 'fixture-instance', 'memory', identity])
        return [c for c in self.connector.claims if c.memory_native_id_sha256 == native]

    def test_pages_become_scoped_claims_with_separate_schema_and_ids(self):
        snapshot = self.read()
        summary = self.connector.summary
        assert (summary['profile'], summary['surface'], summary['api_version'], summary['upstream_commit']) == (
            'mem0-platform-export-v1', 'platform_v3', 'v3', COMMIT)
        assert (summary['claims'], summary['currently_asserted'], summary['excluded']['memories_outside_filter']) == (
            dict(memory=3, history_event=4), 2, 1)
        assert {r.record_type for r in snapshot.records} == {
            'mem0_platform_memory', 'mem0_platform_history_event', 'mem0_platform_export'}
        edited = self.memory('mem-edited')
        assert [(c.event, c.status, c.currently_asserted) for c in edited] == [
            ('ADD', 'superseded', False), ('UPDATE', 'current', True), (None, 'asserted', True)]
        fact = edited[-1]
        assert isinstance(fact, Mem0PlatformClaim) and fact.app_sha256 == digest(['mem0-platform', 'fixture-instance', 'app', 'app-aaa'])
        assert fact.run_sha256 == digest(['mem0-platform', 'fixture-instance', 'run', 'run-aaa'])
        assert fact.categories_sha256 == digest(['invented'])
        # Platform identities never collide with the OSS namespace, and re-reading is deterministic.
        first = [c.to_dict() for c in self.connector.claims]
        self.read()
        assert [c.to_dict() for c in self.connector.claims] == first
        assert digest(['mem0', 'fixture-instance', 'memory', 'mem-edited']) != edited[-1].native_id_sha256

    def test_replaced_and_deleted_memories_stay_in_evidence_but_leave_the_asserted_count(self):
        self.read()
        (head, old), = [self.memory('mem-old')]
        assert (old.status, old.currently_asserted, head.status) == ('replaced', False, 'historical')
        assert old.replaced_by_sha256 == digest(['mem0-platform', 'fixture-instance', 'memory', 'mem-edited'])
        assert self.connector.summary['memories']['replaced'] == 1
        # Unlisted history is excluded by default, and kept as an unverified tombstone only on request.
        assert self.memory('mem-gone') == []
        self.read(unlisted_history='include_unverified')
        add, delete = self.memory('mem-gone')
        assert (delete.status, delete.tombstone, delete.scope_status) == ('tombstone', True, 'unlisted_unverified')

    def test_expired_memory_is_historical_and_a_passing_test_memory_is_not_an_outcome(self):
        value = copy.deepcopy(EXPORT)
        value['pages'][0]['results'][1]['expiration_date'] = '2031-05-01'
        self.read(value)
        head, fact = self.memory('mem-tests')
        assert (fact.status, fact.currently_asserted, head.status) == ('expired', False, 'historical')
        fact = self.memory('mem-edited')[-1]
        assert (fact.task_outcome_verified, fact.independent_case, fact.run_is_task_root) == (False,)*3
        summary = self.connector.summary
        assert (summary['task_outcomes_verified'], summary['network_used'], summary['server_contacted']) == (False,)*3
        assert not public_export_allowed([summary])
        with pytest.raises(ValueError): Mem0PlatformClaim(**dict(fact.to_dict(), task_outcome_verified=True))
        with pytest.raises(ValueError): Mem0PlatformClaim(**dict(fact.to_dict(), status='replaced'))

    def test_pagination_stops_safely_and_incomplete_listings_are_flagged(self):
        truncated = copy.deepcopy(EXPORT)
        truncated['pages'][1]['next'] = 'https://invented.example/v3/memories/?page=3'
        truncated['pages'][1]['count'] = 9
        self.read(truncated)
        assert {'listing_truncated', 'listing_count_mismatch'} <= set(self.connector.summary['warnings'])
        # A page repeated by a retry cannot double a memory, and the page count is bounded before mapping.
        repeated = dict(EXPORT, pages=[PAGES[0], PAGES[0], PAGES[1]])
        self.read(repeated)
        assert 'duplicate_memory_id' in self.connector.summary['warnings']
        assert self.connector.summary['rows_rejected']['memories'] == 2
        with pytest.raises(HostileReport, match='page bound'):
            self.read(EXPORT, limits=dict(max_pages=1))

    @pytest.mark.parametrize('value', [
        pytest.param(dict(EXPORT, pages=[]), id='no-pages'), pytest.param(dict(EXPORT, pages={'results': []}), id='object'),
        pytest.param(dict(EXPORT, pages=[dict(results=[], page=1)]), id='unknown-page-field'),
        pytest.param(dict(EXPORT, filters={}), id='no-filter'),         pytest.param(dict(EXPORT, filters=dict(project_id='p')), id='unsupported-scope'),
        pytest.param(dict(EXPORT, filters=dict(AND=[dict(user_id='u')], extra=1)), id='mixed-operator'),
        pytest.param(dict(EXPORT, api_version='v2'), id='version-mismatch')])
    def test_malformed_shapes_filters_and_versions_are_refused(self, value):
        with pytest.raises(ValueError): self.read(value)

    def test_run_filter_uses_session_id_and_or_filters_import_each_branch(self):
        self.read(dict(EXPORT, filters=dict(run_id='run-aaa')))
        assert (self.memory('mem-old'), len(self.memory('mem-tests'))) == ([], 2)
        self.read(dict(EXPORT, filters=dict(OR=[dict(user_id='user-aaa'), dict(user_id='user-bbb')])))
        assert self.connector.summary['claims']['memory'] == 4
        assert self.connector.summary['filter_mode'] == 'or'
        # An AND of user and agent can hide speaker-attributed facts, so it is warned rather than trusted.
        self.read(dict(EXPORT, filters=dict(user_id='user-aaa', agent_id='agent-aaa')))
        assert 'and_filter_may_omit_speaker_attributed_facts' in self.connector.summary['warnings']
        assert self.connector.summary['claims']['memory'] == 0
        # A wildcard is only a literal value here, never an all-tenant enumeration: nothing matches it.
        self.read(dict(EXPORT, filters=dict(user_id='*')))
        assert self.connector.summary['claims']['memory'] == 0

    def test_history_rows_naming_another_scope_are_dropped(self):
        value = copy.deepcopy(EXPORT)
        value['history']['mem-tests'].append(event('h9', 'mem-tests', 'UPDATE', 'x', 'y', user_id='user-bbb'))
        self.read(value)
        assert len(self.memory('mem-tests')) == 2
        assert 'history_row_outside_filter_scope' in self.connector.summary['warnings']

    def test_oss_and_platform_sources_do_not_read_each_others_selections(self):
        with pytest.raises(ValueError, match='source version'):
            Mem0Export(self.manifest).read(self.source)
        self.write(EXPORT, mem0_version='2.2.1')
        with pytest.raises(ValueError, match='selection descriptor'): self.connector.read(self.source)

    def test_cli_import_keeps_hashes_only(self):
        output, error = io.StringIO(), io.StringIO()
        arguments = ['--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                     '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')]
        for operation in ('preview', 'import'):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                assert main(['sources', operation, *arguments]) == 0
        text = output.getvalue()+error.getvalue()+(self.root/'private/cache.json').read_text()
        for secret in ('SYNTHETIC_SECRET', 'user-aaa', 'app-aaa', 'run-aaa', 'mem-edited', 'invented', 'exports/mem0.json'):
            assert secret not in text
        assert '"mem0_summary"' in text
