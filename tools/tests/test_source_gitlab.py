"""Offline GitLab REST v4 fixtures; synthetic hosts, groups, users and projects only, never the network."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import inspect
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from .test_source_transport import Response
else:
    from test_source_transport import Response

from agent_harness_lab.cli import main
from agent_harness_lab.sources import forge, gitlab, transport
from agent_harness_lab.sources.gitlab import PROVIDER, SOURCE_VERSION, GitLabAPI, GitLabConfigured, GitLabSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

HOST = 'gitlab.example.test'
PATH = 'synthetic-group/synthetic-subgroup/synthetic-project'
ENCODED = 'synthetic-group%2Fsynthetic-subgroup%2Fsynthetic-project'
PLANES = ('contextual_assertion', 'development_artifact')
QUERY = 'order_by=created_at&sort=asc&per_page=100'
SECRETS = ('SYNTHETIC_PRIVATE', 'SYNTHETIC_SECRET', 'synthetic-group', 'synthetic-contributor', 'feature/private-branch')


def notes(target, page=1): return f'{target}/notes?page={page}&{QUERY}'


class GitLabFixture:
    def setUp(self):
        self.base = 'https://'+HOST
        self.selection = GitLabSelection(self.base, PATH, ('approvals', 'issues', 'merge_requests', 'notes'), (7,), (7,))
        self.source = self.spec(self.selection)
        self.stamp = '2026-01-01T00:00:00Z'
        self.actor = dict(id=99, username='synthetic-user')
        self.routes = {}
        self.build(42, PATH)
        self.root = '/api/v4/projects/42'
        self.issue_path, self.mr_path = self.root+'/issues/7', self.root+'/merge_requests/7'
        self.project = self.routes['/api/v4/projects/'+ENCODED]
        self.issue, self.mr = self.routes[self.issue_path], self.routes[self.mr_path]
        self.note = self.routes[notes(self.issue_path)][0]
        self.approvals = self.routes[self.mr_path+'/approvals']
        self.headers = {'Content-Type': 'application/json'}
        wire = patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection')
        self.wire = wire.start(); self.addCleanup(wire.stop)
        self.connection = self.wire.return_value
        self.targets, self.hosts = [], []

        def request(method, target, body=None, headers=None):
            self.assertEqual(method, 'GET'); self.assertIsNone(body)
            self.targets.append(target); self.hosts.append(self.wire.call_args.args[0])
        self.connection.request.side_effect = request
        self.connection.getresponse.side_effect = self.respond

    def spec(self, selection, **changes):
        return SourceSpec('api', PROVIDER, SOURCE_VERSION, digest(selection.base_url), selection.project_sha256,
            selection.sha256, identity_provenance='source_declared_id', enabled=True, **changes)

    def build(self, project_id, path, prefix='', host=HOST):
        """Synthetic routes for one project; IIDs and global IDs deliberately repeat across projects and hosts."""
        api, root = f'{prefix}/api/v4', f'{prefix}/api/v4/projects/{project_id}'
        project = dict(id=project_id, path_with_namespace=path, visibility='public', default_branch='main',
            forked_from_project=None)

        def common(native):
            return dict(id=native, iid=7, project_id=project_id, state='opened', title='SYNTHETIC_PRIVATE_TITLE',
                description='SYNTHETIC_PRIVATE_BODY', user_notes_count=1, created_at=self.stamp, updated_at=self.stamp,
                closed_at=None, author=self.actor, labels=['SYNTHETIC_PRIVATE_LABEL'], milestone=None)

        def note(native, kind, owner, system=False):
            return dict(id=native, body='SYNTHETIC_PRIVATE_BODY', system=system, noteable_type=kind, noteable_id=owner,
                noteable_iid=7, project_id=project_id, internal=False, resolvable=False, created_at=self.stamp,
                updated_at=self.stamp, author=self.actor)
        routes = {
            api+'/version': dict(version='17.0.0-ee', revision='0123abcd'),
            api+'/projects/'+path.replace('/', '%2F'): project, root: project,
            root+'/issues/7': dict(common(700), confidential=True),
            notes(root+'/issues/7'): [note(7001, 'Issue', 700), note(7002, 'Issue', 700, True)],
            root+'/merge_requests/7': dict(common(900), source_project_id=project_id, target_project_id=project_id,
                source_branch='feature/private-branch', target_branch='main', sha='b'*40, merge_commit_sha=None,
                squash_commit_sha=None, draft=False, merged_at=None, merged_by=None,
                diff_refs=dict(base_sha='a'*40, head_sha='b'*40, start_sha='a'*40)),
            notes(root+'/merge_requests/7'): [note(9001, 'MergeRequest', 900), note(9002, 'MergeRequest', 900, True)],
            root+'/merge_requests/7/approvals': dict(id=900, iid=7, project_id=project_id, approved=True,
                approvals_required=1, approvals_left=0, approved_by=[dict(user=self.actor)])}
        self.routes.update(routes if host == HOST else {(host, target): value for target, value in routes.items()})

    def respond(self):
        value = self.routes.get((self.hosts[-1], self.targets[-1])) or self.routes[self.targets[-1]]
        if callable(value): value = value()
        if isinstance(value, Response): return value
        headers = dict(self.headers)
        if isinstance(value, list): headers.update({'X-Total': str(len(value)), 'X-Page': '1', 'X-Next-Page': ''})
        return Response(value, headers=headers)

    def read(self, selection=None, source=None, **options):
        selection = selection or self.selection
        client = GitLabAPI(selection.base_url, selection, **options)
        snapshot = client.read(source or (self.source if selection is self.selection else self.spec(selection)))
        rendered = json.dumps(client.summary)
        for secret in SECRETS: self.assertNotIn(secret, rendered)
        return client, snapshot

    def nodes(self, client, kind): return [n for n in client.summary['nodes'] if n['kind'] == kind]

    def states(self, client): return [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]

    def scope(self, source=None):
        source = source or self.source
        return dict(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)

    def registry(self, client=None):
        registry = Registry()
        registry.register(Capability('api', PROVIDER, SOURCE_VERSION, PLANES), client or GitLabAPI(self.base, self.selection))
        return registry

    def page(self, rows, **headers):
        return Response(rows, headers={**self.headers, **headers})


class GitLabSourceTests(GitLabFixture, unittest.TestCase):
    def test_capture_keeps_issues_merge_requests_notes_system_notes_and_approvals_without_grading(self):
        client, snapshot = self.read()
        self.assertEqual({r.record_type for r in snapshot.records}, {'gitlab_instance', 'gitlab_project', 'gitlab_issue',
            'gitlab_merge_request', 'gitlab_note', 'gitlab_system_note', 'gitlab_approvals'})
        self.assertTrue(snapshot.checkpoint_ready)
        self.assertTrue(all(state == 'captured' for _, state, _ in self.states(client)))
        self.assertEqual((client.summary['server_version'], client.summary['server_version_state']), ('17.0.0-ee', 'reported'))
        self.assertEqual((len(self.nodes(client, 'gitlab_note')), len(self.nodes(client, 'gitlab_system_note'))), (2, 2))
        self.assertEqual(client.summary['system_notes'], 2)
        self.assertTrue(all(n['system'] for n in self.nodes(client, 'gitlab_system_note')))
        self.assertFalse(any(n['system'] for n in self.nodes(client, 'gitlab_note')))
        relations = {relation for relation, _, _ in client.summary['relations']}
        self.assertEqual(relations, {'conversation_on', 'system_event_on', 'approval_state_on'})
        mr = self.nodes(client, 'gitlab_merge_request')[0]
        self.assertEqual((mr['state'], mr['draft'], mr['source_project_state']), ('opened', False, 'same_project'))
        self.assertEqual((mr['head_sha256'], mr['base_sha256']), (digest('b'*40), digest('a'*40)))
        self.assertEqual((mr['merge_commit_sha256'], mr['squash_commit_sha256'], mr['merged_at']), (None, None, None))
        self.assertEqual(self.nodes(client, 'gitlab_issue')[0]['confidential'], True)
        self.assertEqual(self.nodes(client, 'gitlab_issue')[0]['body_sha256'], digest('SYNTHETIC_PRIVATE_BODY'))
        approvals = self.nodes(client, 'gitlab_approvals')[0]
        self.assertEqual((approvals['approved'], approvals['approvals_left'], len(approvals['approvers_sha256'])), (True, 0, 1))
        self.assertFalse(client.summary['task_outcomes_verified'])
        self.assertIn('merge_closure_approval_not_task_outcome', client.summary['warnings'])
        self.assertTrue(all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records))
        self.assertEqual(snapshot.metadata.coverage.reported_completeness, 'partial')
        self.assertEqual(self.wire.call_args.args, (HOST, 443))
        self.assertEqual(self.targets[:2], ['/api/v4/version', '/api/v4/projects/'+ENCODED])
        self.assertTrue(all(target.startswith(self.root) for target in self.targets[2:]))

    def test_merged_squashed_and_approved_merge_request_still_claims_no_task_outcome(self):
        self.mr.update(state='merged', merged_at=self.stamp, closed_at=None, merge_commit_sha='c'*40,
            squash_commit_sha='d'*40, merged_by=self.actor)
        self.issue['state'] = 'closed'
        client, snapshot = self.read()
        mr = self.nodes(client, 'gitlab_merge_request')[0]
        self.assertEqual((mr['state'], mr['merged_at']), ('merged', self.stamp))
        self.assertEqual((mr['merge_commit_sha256'], mr['squash_commit_sha256']), (digest('c'*40), digest('d'*40)))
        self.assertIn('merge_and_squash_shas_not_ancestry', client.summary['warnings'])
        self.assertFalse(client.summary['task_outcomes_verified'])
        self.assertTrue(all(r.causal_status == 'unknown' for r in snapshot.records))

    def test_offset_pagination_follows_next_page_link_and_total(self):
        rows = [dict(self.note, id=10000+index) for index in range(101)]
        self.issue['user_notes_count'] = 101
        link = (f'<{self.base}{self.issue_path}/notes?id=42&noteable_id=7&order_by=created_at&page=2&per_page=100&sort=asc>; rel="next", '
            f'<{self.base}{self.issue_path}/notes?id=42&noteable_id=7&order_by=created_at&page=1&per_page=100&sort=asc>; rel="first"')
        self.routes[notes(self.issue_path)] = self.page(rows[:100], **{'X-Total': '101', 'X-Page': '1', 'X-Per-Page': '100',
            'X-Next-Page': '2', 'Link': link})
        self.routes[notes(self.issue_path, 2)] = self.page(rows[100:], **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        client, snapshot = self.read()
        self.assertEqual(len(self.nodes(client, 'gitlab_note')), 102)
        self.assertTrue(snapshot.checkpoint_ready)
        self.assertIn(notes(self.issue_path, 2), self.targets)
        # Without X-Total (large listings), an explicit empty X-Next-Page still marks the last page.
        self.routes[notes(self.issue_path)] = self.page(rows[:100], **{'X-Next-Page': '2'})
        self.routes[notes(self.issue_path, 2)] = self.page(rows[100:], **{'X-Next-Page': ''})
        self.assertTrue(self.read()[1].checkpoint_ready)

    def test_unpaged_responses_are_bounded_and_budgets_default_when_unset(self):
        for rows, accepted in ((transport.MAX_UNPAGED_ROWS, True), (transport.MAX_UNPAGED_ROWS+1, False)):
            with self.subTest(rows=rows):
                self.routes[notes(self.issue_path)] = [dict(self.note, id=10000+index) for index in range(rows)]
                self.issue['user_notes_count'] = rows
                if accepted:
                    self.assertTrue(self.read()[1].checkpoint_ready)
                else:
                    with self.assertRaises(TransportError): self.read()
        client = GitLabAPI(self.base, self.selection, max_pages=3, max_bytes=None)
        self.assertEqual((client.transport.max_pages, client.transport.max_bytes, client.transport.seconds),
            (3, transport.DEFAULT_LIMITS['max_bytes'], transport.DEFAULT_LIMITS['seconds']))

    def test_incomplete_pagination_is_visible_coverage_and_never_checkpoints(self):
        full = [dict(self.note, id=10000+index) for index in range(100)]
        cases = (('total_count_mismatch', lambda: self.page([self.note], **{'X-Total': '3', 'X-Next-Page': ''}), {}),
            ('page_size_unverified', lambda: self.page(full), {}),
            ('declared_count_mismatch', [self.note], dict(user_notes_count=2)),
            ('total_count_changed', lambda: self.page(full, **{'X-Total': '150', 'X-Next-Page': '2'}), {}),
            ('budget_exhausted', lambda: self.page(full, **{'X-Total': '101', 'X-Next-Page': '2'}), {}))
        for basis, response, issue in cases:
            with self.subTest(basis=basis), tempfile.TemporaryDirectory() as folder:
                self.routes[notes(self.issue_path)] = response
                self.routes[notes(self.issue_path, 2)] = lambda: self.page(full[:1], **{'X-Total': '151', 'X-Next-Page': ''})
                self.issue.update({'user_notes_count': 1, **issue})
                options = dict(max_pages=4) if basis == 'budget_exhausted' else {}
                client, snapshot = self.read(**options)
                self.assertIn(('notes', 'pagination_incomplete', basis), self.states(client))
                self.assertFalse(snapshot.checkpoint_ready)
                self.assertTrue(snapshot.metadata.coverage.truncated)
                self.assertEqual(snapshot.metadata.coverage.limit_reached, basis == 'budget_exhausted')
                self.assertIn('capture_blocked_or_incomplete', client.summary['warnings'])
                store = SourceStore(Path(folder)/'journal.json', **self.scope())
                with self.assertRaises(ValueError):
                    self.registry(GitLabAPI(self.base, self.selection, **options)).ingest(self.source, store, **self.scope())
                self.assertFalse(store.path.exists())

    def test_budget_spent_during_a_response_is_coverage_with_capped_bytes(self):
        full = [dict(self.note, id=10000+index) for index in range(100)]
        last = {'X-Total': '101', 'X-Next-Page': ''}
        clock = [0.0]

        def large(): return self.page([dict(self.note, id=20000, body='SYNTHETIC_PRIVATE_BODY'*10000)], **last)

        def late():
            clock[0] += 1000
            return self.page([dict(self.note, id=20000)], **last)

        def stalled(): raise TimeoutError()
        # The second page spends the byte budget mid-body, outlives the deadline, or times out on a deadline-capped socket.
        for following, options in ((large, dict(max_bytes=100_000)), (late, dict(seconds=55)), (stalled, dict(seconds=55))):
            with self.subTest(following=following.__name__), patch.object(transport.time, 'monotonic', lambda: clock[0]):
                self.routes[notes(self.issue_path)] = lambda: self.page(full, **{'X-Total': '101', 'X-Next-Page': '2'})
                self.routes[notes(self.issue_path, 2)] = following
                self.issue['user_notes_count'] = 101
                client, snapshot = self.read(**options)
                self.assertIn(('notes', 'pagination_incomplete', 'budget_exhausted'), self.states(client))
                self.assertIn(notes(self.issue_path, 2), self.targets)
                self.assertFalse(snapshot.checkpoint_ready)
                self.assertTrue(snapshot.metadata.coverage.limit_reached)
                bound = next(b for b in snapshot.metadata.coverage.scan_bounds if b.dimension == 'bytes')
                self.assertLessEqual(bound.observed, bound.limit)
                self.assertIn('capture_blocked_or_incomplete', client.summary['warnings'])

    def test_oversized_single_response_fails_closed_before_parsing(self):
        parsed, loads = [], transport.json.loads

        def spy(body, **options):
            parsed.append(len(body)); return loads(body, **options)
        self.routes[notes(self.issue_path)] = lambda: self.page(b'['+b' '*transport.MAX_RESPONSE_BYTES+b']',
            **{'X-Total': '0', 'X-Next-Page': ''})
        self.issue['user_notes_count'] = 0
        with patch.object(transport.json, 'loads', spy), self.assertRaises(TransportError): self.read()
        self.assertTrue(parsed)
        self.assertLessEqual(max(parsed), transport.MAX_RESPONSE_BYTES)

    def test_unsafe_links_and_disagreeing_page_headers_fail_closed(self):
        collection = f'{self.base}{self.issue_path}/notes'
        full = [dict(self.note, id=10000+index) for index in range(100)]
        for target in ('https://other.example.test'+self.issue_path+'/notes?page=2&'+QUERY,
                f'{self.base}{self.mr_path}/notes?page=2&{QUERY}', f'{collection}?page=3&{QUERY}',
                f'{collection}?page=2&{QUERY}&private_token=secret', f'{collection}?page=2&order_by=created_at&sort=asc&per_page=20',
                f'{collection}?page=2&{QUERY}&id=43', f'{collection}?page=2&{QUERY}&noteable_id=8'):
            with self.subTest(target=target):
                self.routes[notes(self.issue_path)] = self.page(full, **{'X-Next-Page': '2', 'Link': f'<{target}>; rel="next"'})
                with self.assertRaises(TransportError): self.read()
        for headers in ({'X-Next-Page': '3'}, {'X-Next-Page': 'two'}, {'X-Next-Page': '', 'Link': f'<{collection}?page=2&{QUERY}>; rel="next"'},
                {'X-Page': '2', 'X-Next-Page': ''}, {'X-Per-Page': '20', 'X-Next-Page': ''}):
            with self.subTest(headers=headers):
                self.routes[notes(self.issue_path)] = self.page(full, **headers)
                with self.assertRaises(TransportError): self.read()

    def test_iid_and_global_id_stay_distinct_identities(self):
        client, snapshot = self.read()
        issue, mr = self.nodes(client, 'gitlab_issue')[0], self.nodes(client, 'gitlab_merge_request')[0]
        # Issues and merge requests have separate IID sequences; the same IID is two resources.
        self.assertEqual(issue['iid_sha256'], mr['iid_sha256'])
        self.assertNotEqual(issue['native_id_sha256'], mr['native_id_sha256'])
        self.assertNotEqual(issue['id_sha256'], issue['iid_sha256'])
        self.assertNotIn(issue['id_sha256'], {r.native_id_sha256 for r in snapshot.records})
        # A note naming the IID instead of its parent's global ID belongs to another resource.
        self.note['noteable_id'] = 7
        with self.assertRaises(TransportError): self.read()
        self.note['noteable_id'] = 700
        self.issue['id'] = 7
        self.routes[notes(self.issue_path)] = [dict(self.note, noteable_id=7), self.routes[notes(self.issue_path)][1] | dict(noteable_id=7)]
        moved, _ = self.read()
        self.assertNotEqual(self.nodes(moved, 'gitlab_issue')[0]['id_sha256'], issue['id_sha256'])
        self.assertEqual(self.nodes(moved, 'gitlab_issue')[0]['native_id_sha256'], issue['native_id_sha256'])
        self.approvals['id'] = 901
        with self.assertRaises(TransportError): self.read()

    def test_global_ids_are_typed_by_resource_and_booleans_are_not_integers(self):
        # Issues, merge requests and milestones have separate global ID sequences; equal numbers are different resources.
        self.issue['milestone'] = dict(id=self.issue['id'])
        client, _ = self.read()
        issue = self.nodes(client, 'gitlab_issue')[0]
        self.assertNotEqual(issue['id_sha256'], issue['milestone_sha256'])
        self.assertNotEqual(client._native('Issue', 5), client._native('MergeRequest', 5))
        from agent_harness_lab.sources.gitlab import absent_or
        self.assertEqual((absent_or(True, 1), absent_or(None, 1), absent_or(1, 1)), (False, True, True))
        for change in (dict(noteable_iid=True), dict(project_id=True)):
            with self.subTest(change=change), patch.dict(self.note, change), self.assertRaises(TransportError): self.read()
        self.approvals.update(approved_by=[dict(user=None), dict(user=dict(id=55))])
        client, _ = self.read()
        approvals = self.nodes(client, 'gitlab_approvals')[0]
        self.assertEqual((len(approvals['approvers_sha256']), approvals['approvers_unidentified']), (1, 1))
        self.note.pop('internal', None); self.note['confidential'] = True
        client, _ = self.read()
        self.assertTrue(any(n.get('internal') is True for n in self.nodes(client, 'gitlab_note')))

    def test_same_iids_on_other_projects_and_hosts_stay_distinct(self):
        self.build(43, 'synthetic-group/other-project')
        self.build(42, PATH, prefix='/gitlab')
        self.build(42, PATH, host='mirror.'+HOST)
        selections = (self.selection, replace(self.selection, project='synthetic-group/other-project'),
            replace(self.selection, base_url=self.base+'/gitlab'), replace(self.selection, base_url='https://mirror.'+HOST))
        seen, projects = set(), set()
        for selection in selections:
            client, snapshot = self.read(selection)
            native = {r.native_id_sha256 for r in snapshot.records if r.record_type not in ('gitlab_instance',)}
            self.assertEqual(len(native), 8)
            self.assertFalse(native & seen)
            seen |= native; projects.add(client.summary['project_sha256'])
        self.assertEqual(len(projects), 4)
        self.assertIn(('mirror.'+HOST, 443), [call.args for call in self.wire.call_args_list])
        self.assertIn('/gitlab/api/v4/projects/42/issues/7', self.targets)
        # Selecting the same project by numeric ID changes the declared scope but not the native identities.
        by_id = replace(self.selection, project=42)
        self.assertNotEqual(by_id.project_sha256, self.selection.project_sha256)
        first = {r.native_id_sha256 for r in self.read()[1].records}
        start = len(self.targets)
        self.assertEqual({r.native_id_sha256 for r in self.read(by_id)[1].records}, first)
        self.assertEqual(self.targets[start+1], '/api/v4/projects/42')

    def test_fork_merge_request_keeps_source_project_identity(self):
        self.mr['source_project_id'] = 77
        client, _ = self.read()
        mr = self.nodes(client, 'gitlab_merge_request')[0]
        self.assertEqual(mr['source_project_state'], 'other_project')
        self.assertEqual(mr['source_project_sha256'], self.selection.project_identity(77))
        self.assertEqual(mr['target_project_sha256'], client.summary['project_sha256'])
        self.assertNotEqual(mr['source_project_sha256'], mr['target_project_sha256'])
        self.mr['target_project_id'] = 77
        with self.assertRaises(TransportError): self.read()
        self.mr['target_project_id'] = 42
        self.project['forked_from_project'] = dict(id=5)
        self.assertEqual(self.nodes(self.read()[0], 'gitlab_project')[0]['forked_from_sha256'], self.selection.project_identity(5))

    def test_nested_group_paths_encode_once_and_escapes_are_refused(self):
        self.assertEqual(self.selection.selector, ENCODED)
        for path in ('synthetic-group/../other', 'synthetic-group/%2E%2E/other', 'synthetic-group%2Fother',
                'synthetic-group/./other', 'synthetic-group//other', '/synthetic-group/other', 'synthetic-group/other/',
                'synthetic-group\\other', 'single', 'synthetic-group/other.git', 'synthetic-group/other.', 'group/a..b',
                'synthetic-group/-other', 'synthetic-group/other?x=1', 'synthetic-group/oth er', '/'.join(['g']*22), 0, True, '42'):
            with self.subTest(path=path), self.assertRaises(ValueError): replace(self.selection, project=path)
        self.project['path_with_namespace'] = 'synthetic-group/other-project'
        with self.assertRaises(TransportError): self.read()
        self.project['path_with_namespace'] = PATH.upper()
        self.assertTrue(self.read()[1].checkpoint_ready)
        self.wire.reset_mock()

    def test_missing_approvals_endpoint_and_version_are_explicit_capability_gaps(self):
        self.routes['/api/v4/version'] = Response(b'{}', 401)
        self.routes[self.mr_path+'/approvals'] = Response(b'SYNTHETIC_SECRET', 404)
        client, snapshot = self.read()
        self.assertIn(('approvals', 'unsupported_endpoint', 'approvals_not_found'), self.states(client))
        self.assertEqual((client.summary['server_version'], client.summary['server_version_state']), ('unknown', 'unauthorized'))
        self.assertFalse(self.nodes(client, 'gitlab_approvals'))
        self.assertIn('unsupported_version', snapshot.metadata.coverage.gaps)
        self.assertTrue({'approvals_endpoint_unsupported', 'server_version_unknown'} <= set(client.summary['warnings']))
        self.assertTrue(snapshot.checkpoint_ready)
        self.routes[self.mr_path+'/approvals'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read()
        self.assertIn(('approvals', 'forbidden', None), self.states(client))
        self.assertFalse(snapshot.checkpoint_ready)

    def test_permission_failures_and_rate_limits_are_coverage_states_never_success(self):
        cases = (('/api/v4/projects/'+ENCODED, 401, ('project', 'unauthorized', None)),
            ('/api/v4/projects/'+ENCODED, 403, ('project', 'forbidden', None)),
            ('/api/v4/projects/'+ENCODED, 404, ('project', 'not_found', None)),
            (notes(self.issue_path), 403, ('notes', 'forbidden', None)),
            (self.mr_path, 404, ('merge_request', 'not_found', None)),
            (self.issue_path, 429, ('issue', 'rate_limited', None)))
        for target, status, expected in cases:
            with self.subTest(status=status, target=target), tempfile.TemporaryDirectory() as folder:
                original = self.routes[target]; self.routes[target] = Response(b'SYNTHETIC_SECRET', status)
                self.targets.clear()
                client, snapshot = self.read()
                self.assertIn(expected, self.states(client))
                self.assertFalse(snapshot.checkpoint_ready)
                self.assertIn('not_observed', snapshot.metadata.coverage.gaps)
                if status == 429:
                    self.assertEqual(self.targets[-1], target)
                    self.assertFalse(self.nodes(client, 'gitlab_issue'))
                    self.assertTrue(all(state == 'rate_limited' for _, state, _ in self.states(client)[1:]))
                store = SourceStore(Path(folder)/'journal.json', **self.scope())
                with self.assertRaises(ValueError): self.registry().ingest(self.source, store, **self.scope())
                self.assertFalse(store.path.exists())
                self.routes[target] = original
        self.routes[self.issue_path] = Response(b'SYNTHETIC_SECRET', 500)
        with self.assertRaises(TransportError) as error: self.read()
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))

    def test_non_https_and_non_canonical_instances_are_refused_before_connecting(self):
        for url in ('http://'+HOST, f'https://{HOST}/', f'https://{HOST}/api/v4', f'https://user@{HOST}',
                f'https://{HOST}?private_token=secret', 'https://GitLab.example.test', f'https://{HOST}:443', f'https://{HOST}/a/../b'):
            with self.subTest(url=url), self.assertRaises(ValueError): replace(self.selection, base_url=url)
        with self.assertRaises(TransportError): GitLabAPI('https://other.example.test', self.selection)
        for change in (dict(capture=('issues',)), dict(capture=('merge_requests', 'issues')), dict(issues=(7, 7)),
                dict(issues=(0,)), dict(capture=('approvals', 'issues', 'notes'), merge_requests=()),
                dict(capture=('issues', 'merge_requests', 'pipelines')), dict(schema_version=2)):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(self.selection, **change)
        self.assertEqual(GitLabSelection.parse(json.loads(json.dumps(self.selection.to_dict()))), self.selection)
        self.assertEqual(GitLabSelection.parse(json.loads(json.dumps(replace(self.selection, project=42).to_dict()))).project, 42)
        for source in (replace(self.source, enabled=False), replace(self.source, scope_sha256=digest('foreign')),
                replace(self.source, root_sha256=digest('https://other.example.test')), replace(self.source, version='v3'),
                replace(self.source, provider='gitea')):
            with self.assertRaises(TransportError): self.read(source=source)
        self.wire.assert_not_called()

    def test_reader_issues_only_get_requests_and_carries_no_mutation_verbs(self):
        self.read()
        self.assertTrue(self.connection.request.call_args_list)
        self.assertTrue(all(call.args[0] == 'GET' and call.kwargs.get('body') is None for call in self.connection.request.call_args_list))
        for module in (gitlab, forge):
            text = inspect.getsource(module)
            for verb in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'", 'body='):
                self.assertNotIn(verb, text)
        self.assertTrue(all(target.startswith('/api/v4/') for target in self.targets))
        self.assertFalse(any('/projects?' in target or target.endswith('/projects') or '/groups' in target for target in self.targets))

    def test_repeat_import_is_idempotent_and_updates_add_native_revisions(self):
        registry = self.registry()
        with tempfile.TemporaryDirectory() as folder:
            store = SourceStore(Path(folder)/'journal.json', **self.scope())
            first = registry.ingest(self.source, store, **self.scope())
            self.assertGreater(first.imported, 0)
            again = registry.ingest(self.source, store, **self.scope())
            self.assertEqual((again.imported, again.cursor_after_sha256), (0, first.cursor_after_sha256))
            self.note['body'] = 'SYNTHETIC_PRIVATE_EDITED'
            self.assertEqual(registry.ingest(self.source, store, **self.scope()).imported, 1)
            self.mr['sha'] = 'e'*40
            self.approvals.update(approved=False, approvals_left=1, approved_by=[])
            changed = registry.ingest(self.source, store, **self.scope())
            self.assertEqual(changed.imported, 2)
            self.assertNotEqual(changed.cursor_after_sha256, first.cursor_after_sha256)
            text = store.path.read_text()
            for secret in SECRETS: self.assertNotIn(secret, text)

    def test_identity_parent_and_scope_conflicts_fail_closed(self):
        # The readback by numeric ID must name the project the path resolved to.
        self.routes[self.root] = lambda: dict(self.project, id=43)
        with self.assertRaises(TransportError): self.read()
        self.routes[self.root] = lambda: dict(self.project, path_with_namespace='synthetic-group/renamed-project')
        with self.assertRaises(TransportError): self.read()
        self.routes[self.root] = self.project
        for change in (dict(noteable_type='MergeRequest'), dict(project_id=43), dict(noteable_iid=8), dict(system='no')):
            with self.subTest(change=change), patch.dict(self.note, change), self.assertRaises(TransportError): self.read()
        for change in (dict(project_id=43), dict(iid=8), dict(state='SYNTHETIC_STATE')):
            with self.subTest(change=change), patch.dict(self.issue, change), self.assertRaises(TransportError): self.read()
        self.project['visibility'] = 'private'
        with self.assertRaises(TransportError): self.read()


class GitLabConfigurationTests(GitLabFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        self.manifest, self.journal, self.credential = self.dir/'gitlab.json', self.dir/'journal.json', self.dir/'authorization'
        self.source = self.spec(self.selection, auth_mode='configured')
        self.write()

    def write(self, **changes):
        value = dict(schema_version=1, source=self.source.to_dict(), endpoint=self.base,
            selection=self.selection.to_dict(), limits=dict(max_pages=40))
        self.manifest.write_text(json.dumps({**value, **changes}))

    def invoke(self, operation, *extra):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--cache', str(self.journal),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256, *extra])
        for secret in (*SECRETS, self.base, str(self.dir)): self.assertNotIn(secret, output.getvalue()+error.getvalue())
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_cli_probe_preview_and_import_read_only_the_named_variable(self):
        env = ('--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')
        code, result = self.invoke('probe', *env)
        self.assertEqual((code, result['status'], result['network_used']), (0, 'recognized', False))
        self.wire.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.invoke('preview', *env)[0], 3)
        self.wire.assert_not_called()
        with patch.dict(os.environ, SYNTHETIC_GITLAB_AUTHORIZATION='Bearer SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            self.assertEqual(code, 0)
            self.assertTrue(result['checkpoint_ready'])
            self.assertEqual(result['gitlab_summary']['server_version'], '17.0.0-ee')
            self.assertTrue(all(call.kwargs['headers']['Authorization'] == 'Bearer SYNTHETIC_SECRET'
                for call in self.connection.request.call_args_list))
            self.assertEqual(self.invoke('import', *env)[0], 0)
            original = self.journal.read_bytes()
            self.routes[self.issue_path] = Response(b'SYNTHETIC_SECRET', 403)
            code, result = self.invoke('import', *env)
            self.assertEqual((code, result['status'], result['checkpoint_committed']), (3, 'blocked', False))
            self.assertIn(('issue', 'forbidden'), [(c['resource'], c['state']) for c in result['gitlab_summary']['coverage']])
            self.assertEqual(self.journal.read_bytes(), original)
            self.assertEqual(self.invoke('status')[0], 0)
            self.assertEqual(self.invoke('preview', *env, '--authorization-file', str(self.credential))[0], 2)
        self.credential.write_text('Bearer SYNTHETIC_SECRET\n')
        self.routes[self.issue_path] = self.issue
        self.assertEqual(self.invoke('preview', '--authorization-file', str(self.credential))[0], 0)

    def test_configuration_rejects_mismatched_identity(self):
        connector = GitLabConfigured(self.manifest)
        self.assertEqual(connector.declaration(**self.scope())[0], self.source)
        for change in (dict(endpoint='https://other.example.test'), dict(limits=dict(retries=3)),
                dict(selection={**self.selection.to_dict(), 'project': 'synthetic-group/other-project'})):
            with self.subTest(change=change):
                self.write(**change)
                with self.assertRaises(ValueError): connector.declaration(**self.scope())
        self.write()
        self.source = replace(self.source, provider='github'); self.write()
        self.assertEqual(self.invoke('probe', '--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')[0], 2)
        self.wire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
