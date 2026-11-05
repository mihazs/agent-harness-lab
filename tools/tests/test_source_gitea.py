"""Offline Gitea REST fixtures; synthetic hosts, users and repositories only, never the network."""
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
from agent_harness_lab.sources import gitea
from agent_harness_lab.sources.gitea import PROVIDER, SOURCE_VERSION, GiteaAPI, GiteaConfigured, GiteaSelection
from agent_harness_lab.sources.github import GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

HOST = 'gitea.example.test'
OWNER, REPO = 'synthetic-owner', 'synthetic-repository'
PLANES = ('contextual_assertion', 'development_artifact')
SECRETS = ('SYNTHETIC_PRIVATE', 'SYNTHETIC_SECRET', OWNER, 'synthetic-contributor', 'feature/private-branch')


class GiteaFixture:
    def setUp(self):
        self.base = 'https://'+HOST
        self.selection = GiteaSelection(self.base, OWNER, REPO, ('comments', 'issues', 'pulls', 'reviews'), (7,), (8,))
        self.source = self.spec(self.selection)
        self.stamp = '2026-01-01T00:00:00Z'
        self.actor = dict(id=99, login='synthetic-user')
        self.routes = {}
        self.build(REPO, 42)
        self.repo_path = f'/api/v1/repos/{OWNER}/{REPO}'
        self.issue, self.pull_issue = self.routes[self.repo_path+'/issues/7'], self.routes[self.repo_path+'/issues/8']
        self.pull = self.routes[self.repo_path+'/pulls/8']
        self.comment = self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'][0]
        self.review = self.routes[self.repo_path+'/pulls/8/reviews?page=1&limit=50'][0]
        self.headers = {'Content-Type': 'application/json;charset=utf-8'}
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

    def build(self, repo, repo_id, prefix='', host=HOST):
        """Synthetic routes for one repository; numbers and IDs deliberately repeat across repositories."""
        path, web = f'{prefix}/api/v1/repos/{OWNER}/{repo}', f'https://{host}{prefix}/{OWNER}/{repo}'
        routes = {}
        full_name = f'{OWNER}/{repo}'
        owner = dict(id=repo_id, full_name=full_name)

        def issue(number, native, pull=None):
            return dict(id=native, number=number, state='open', comments=1, title='SYNTHETIC_PRIVATE_TITLE',
                body='SYNTHETIC_PRIVATE_BODY', created_at=self.stamp, updated_at=self.stamp, closed_at=None,
                user=self.actor, labels=[dict(id=5, name='SYNTHETIC_PRIVATE_LABEL')], milestone=None,
                repository=owner, pull_request=pull)

        def comment(native, number):
            return dict(id=native, body='SYNTHETIC_PRIVATE_BODY', issue_url=f'{web}/issues/{number}',
                pull_request_url='', created_at=self.stamp, updated_at=self.stamp, user=self.actor)
        routes.update({
            f'{prefix}/api/v1/version': dict(version='1.22.0'),
            path: dict(id=repo_id, full_name=full_name, private=False, fork=False, default_branch='main'),
            path+'/issues/7': issue(7, 70), path+'/issues/8': issue(8, 80, dict(merged=False, merged_at=None)),
            path+'/issues/7/comments?page=1&limit=50': [comment(701, 7)],
            path+'/issues/8/comments?page=1&limit=50': [{**comment(801, 8), 'issue_url': f'{web}/pulls/8'}],
            path+'/pulls/8': dict(id=81, number=8, state='open', merged=False, draft=False, title='SYNTHETIC_PRIVATE_TITLE',
                body='SYNTHETIC_PRIVATE_BODY', created_at=self.stamp, updated_at=self.stamp, closed_at=None, merged_at=None,
                merge_commit_sha=None, merge_base='a'*40, merged_by=None, user=self.actor,
                base=dict(ref='main', sha='a'*40, repo_id=repo_id, repo=owner),
                head=dict(ref='feature/private-branch', sha='b'*40, repo_id=repo_id, repo=owner)),
            path+'/pulls/8/reviews?page=1&limit=50': [dict(id=803, state='APPROVED', commit_id='a'*40,
                body='SYNTHETIC_PRIVATE_BODY', submitted_at=self.stamp, user=self.actor, stale=True,
                dismissed=False, official=True, pull_request_url=f'{web}/pulls/8')]})
        self.routes.update(routes if host == HOST else {(host, target): value for target, value in routes.items()})

    def respond(self):
        value = self.routes.get((self.hosts[-1], self.targets[-1])) or self.routes[self.targets[-1]]
        if callable(value): value = value()
        if isinstance(value, Response): return value
        headers = dict(self.headers)
        if isinstance(value, list): headers['X-Total-Count'] = str(len(value))
        return Response(value, headers=headers)

    def read(self, selection=None, source=None, **options):
        selection = selection or self.selection
        client = GiteaAPI(selection.base_url, selection, **options)
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
        registry.register(Capability('api', PROVIDER, SOURCE_VERSION, PLANES), client or GiteaAPI(self.base, self.selection))
        return registry

    def linked_pages(self):
        """A full first comment page whose next page is the caller's to supply."""
        full = [dict(self.comment, id=1000+index) for index in range(50)]
        second = self.repo_path+'/issues/7/comments?page=2&limit=50'
        return lambda: Response(full, headers={**self.headers, 'X-Total-Count': '51', 'Link': f'<{self.base}{second}>; rel="next"'}), second

    def large_page(self):
        return Response([dict(self.comment, id=2000, body='SYNTHETIC_PRIVATE_BODY'*10000)], headers={**self.headers, 'X-Total-Count': '51'})


class GiteaSourceTests(GiteaFixture, unittest.TestCase):
    def test_capture_keeps_issues_pulls_comments_and_reviews_without_grading_them(self):
        client, snapshot = self.read()
        self.assertEqual({r.record_type for r in snapshot.records}, {'gitea_instance', 'gitea_repository', 'gitea_issue',
            'gitea_pull', 'gitea_comment', 'gitea_review'})
        self.assertTrue(snapshot.checkpoint_ready)
        self.assertTrue(all(state == 'captured' for _, state, _ in self.states(client)))
        self.assertEqual((client.summary['server_version'], client.summary['server_version_state']), ('1.22.0', 'reported'))
        pull = self.nodes(client, 'gitea_pull')[0]
        self.assertEqual((pull['state'], pull['merged'], pull['merge_reference_kind'], pull['draft']), ('open', False, 'unknown', False))
        self.assertEqual(pull['head']['sha256'], digest('b'*40))
        self.assertEqual(pull['head_repository_state'], 'same_repository')
        review = self.nodes(client, 'gitea_review')[0]
        self.assertEqual((review['state'], review['matches_head'], review['stale'], review['official']), ('APPROVED', False, True, True))
        self.assertEqual(len(self.nodes(client, 'gitea_comment')), 2)
        self.assertEqual(self.nodes(client, 'gitea_issue')[0]['body_sha256'], digest('SYNTHETIC_PRIVATE_BODY'))
        self.assertFalse(client.summary['task_outcomes_verified'])
        self.assertIn('closure_merge_approval_not_task_outcome', client.summary['warnings'])
        self.assertTrue(all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records))
        self.assertEqual(snapshot.metadata.coverage.reported_completeness, 'partial')
        self.assertEqual(self.wire.call_args.args, (HOST, 443))
        self.assertEqual(self.targets[0], '/api/v1/version')

    def test_merged_closed_and_approved_pull_still_claims_no_task_outcome(self):
        self.pull.update(state='closed', merged=True, merged_at=self.stamp, closed_at=self.stamp, merge_commit_sha='c'*40,
            merged_by=self.actor)
        self.issue['state'] = self.pull_issue['state'] = 'closed'
        self.review['commit_id'] = 'b'*40
        client, snapshot = self.read()
        pull = self.nodes(client, 'gitea_pull')[0]
        self.assertEqual((pull['merge_reference_kind'], pull['merge_sha256']), ('merged_result', digest('c'*40)))
        self.assertTrue(self.nodes(client, 'gitea_review')[0]['matches_head'])
        self.assertFalse(client.summary['task_outcomes_verified'])
        self.assertTrue(all(r.causal_status == 'unknown' for r in snapshot.records))

    def test_page_limit_pagination_follows_links_and_total_count(self):
        rows = [dict(self.comment, id=1000+index) for index in range(51)]
        self.issue['comments'] = 51
        link = f'<{self.base}{self.repo_path}/issues/7/comments?limit=50&page=2>; rel="next",<{self.base}{self.repo_path}/issues/7/comments?limit=50&page=2>; rel="last"'
        self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'] = Response(rows[:50],
            headers={**self.headers, 'X-Total-Count': '51', 'Link': link})
        self.routes[self.repo_path+'/issues/7/comments?page=2&limit=50'] = Response(rows[50:], headers={**self.headers, 'X-Total-Count': '51'})
        client, snapshot = self.read()
        self.assertEqual(len(self.nodes(client, 'gitea_comment')), 52)
        self.assertTrue(snapshot.checkpoint_ready)
        self.assertIn(self.repo_path+'/issues/7/comments?page=2&limit=50', self.targets)

    def test_unpaged_responses_are_bounded_and_budgets_default_when_unset(self):
        from agent_harness_lab.sources import gitea
        page = self.repo_path+'/issues/7/comments?page=1&limit=50'
        for rows, accepted in ((gitea.MAX_UNPAGED_ROWS, True), (gitea.MAX_UNPAGED_ROWS+1, False)):
            with self.subTest(rows=rows):
                self.routes[page] = [dict(self.comment, id=1000+index) for index in range(rows)]
                self.issue['comments'] = rows
                if accepted:
                    client, snapshot = self.read()
                    self.assertTrue(snapshot.checkpoint_ready)
                else:
                    with self.assertRaises(TransportError): self.read()
        client = GiteaAPI(self.base, self.selection)
        self.assertEqual((client.transport.max_pages, client.transport.max_bytes, client.transport.seconds),
            (gitea.DEFAULT_LIMITS['max_pages'], gitea.DEFAULT_LIMITS['max_bytes'], gitea.DEFAULT_LIMITS['seconds']))
        client = GiteaAPI(self.base, self.selection, max_pages=3, max_bytes=None)
        self.assertEqual((client.transport.max_pages, client.transport.max_bytes), (3, gitea.DEFAULT_LIMITS['max_bytes']))

    def test_incomplete_pagination_is_visible_coverage_and_never_checkpoints(self):
        page = self.repo_path+'/issues/7/comments?page=1&limit=50'
        full = [dict(self.comment, id=1000+index) for index in range(50)]
        linked, second = self.linked_pages()
        clock = [0.0]

        def late():
            clock[0] += 1000
            return Response([dict(self.comment, id=2000)], headers={**self.headers, 'X-Total-Count': '51'})

        def stalled(): raise TimeoutError()
        # Each budget runs out mid-capture: before a request, during a response body, and as a deadline-capped socket timeout.
        cases = (('total_count_mismatch', lambda: Response([self.comment], headers={**self.headers, 'X-Total-Count': '3'}), None, {}, {}),
            ('page_size_unverified', lambda: Response(full, headers=self.headers), None, {}, {}),
            ('declared_count_mismatch', [self.comment], None, dict(comments=2), {}),
            ('budget_exhausted', linked, None, {}, dict(max_pages=4)),
            ('budget_exhausted', linked, self.large_page, {}, dict(max_bytes=100_000)),
            ('budget_exhausted', linked, late, {}, dict(seconds=55)),
            ('budget_exhausted', linked, stalled, {}, dict(seconds=55)))
        for basis, response, following, issue, options in cases:
            with (self.subTest(basis=basis, options=options, following=getattr(following, '__name__', None)),
                    tempfile.TemporaryDirectory() as folder, patch.object(gitea.time, 'monotonic', lambda: clock[0])):
                self.routes[page] = response; self.issue.update({'comments': 1, **issue})
                if following is None: self.routes.pop(second, None)
                else: self.routes[second] = following
                client, snapshot = self.read(**options)
                self.assertIn(('comments', 'pagination_incomplete', basis), self.states(client))
                self.assertFalse(snapshot.checkpoint_ready)
                self.assertTrue(snapshot.metadata.coverage.truncated)
                self.assertEqual(snapshot.metadata.coverage.limit_reached, basis == 'budget_exhausted')
                bound = next(b for b in snapshot.metadata.coverage.scan_bounds if b.dimension == 'bytes')
                self.assertLessEqual(bound.observed, bound.limit)
                self.assertIn('capture_blocked_or_incomplete', client.summary['warnings'])
                store = SourceStore(Path(folder)/'journal.json', **self.scope())
                with self.assertRaises(ValueError): self.registry(GiteaAPI(self.base, self.selection, **options)).ingest(self.source, store, **self.scope())
                self.assertFalse(store.path.exists())

    def test_unsafe_next_links_fail_closed(self):
        collection = f'{self.base}{self.repo_path}/issues/7/comments'
        rows = [dict(self.comment, id=1000+i) for i in range(51)]
        second = self.repo_path+'/issues/7/comments?page=2&limit=50'
        # A real second page exists, so only link validation can stop an unsafe target from being captured.
        self.routes[second] = lambda: Response(rows[50:], headers={**self.headers, 'X-Total-Count': '51'})
        self.issue['comments'] = 51
        for target, safe in ((collection+'?page=2&limit=50', True),
                ('https://other.example.test'+self.repo_path+'/issues/7/comments?page=2&limit=50', False),
                (f'{self.base}{self.repo_path}/issues/8/comments?page=2&limit=50', False), (collection+'?page=3&limit=50', False),
                (collection+'?page=2&limit=50&token=secret', False), (collection+'?page=2&limit=10', False)):
            with self.subTest(target=target):
                self.targets.clear()
                self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'] = lambda target=target: Response(rows[:50],
                    headers={**self.headers, 'X-Total-Count': '51', 'Link': f'<{target}>; rel="next"'})
                if safe:
                    client, snapshot = self.read()
                    self.assertTrue(snapshot.checkpoint_ready)
                    self.assertEqual(len(self.nodes(client, 'gitea_comment')), 52)
                    self.assertIn(second, self.targets)
                else:
                    with self.assertRaises(TransportError): self.read()
                    self.assertNotIn(second, self.targets)

    def test_oversized_single_response_fails_before_parsing(self):
        from agent_harness_lab.sources import transport
        parsed, loads = [], transport.json.loads

        def spy(body, **options):
            parsed.append(len(body)); return loads(body, **options)
        # A whitespace-padded empty listing parses cheaply, so only the per-response ceiling can reject it.
        self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'] = lambda: Response(b'['+b' '*gitea.MAX_RESPONSE_BYTES+b']',
            headers={**self.headers, 'X-Total-Count': '0'})
        self.issue['comments'] = 0
        with patch.object(transport.json, 'loads', spy), self.assertRaises(TransportError): self.read()
        self.assertTrue(parsed)
        self.assertLessEqual(max(parsed), gitea.MAX_RESPONSE_BYTES)
        self.assertLess(gitea.MAX_RESPONSE_BYTES, gitea.DEFAULT_LIMITS['max_bytes'])

    def test_repeat_import_is_idempotent_and_updates_add_native_revisions(self):
        registry = self.registry()
        with tempfile.TemporaryDirectory() as folder:
            store = SourceStore(Path(folder)/'journal.json', **self.scope())
            first = registry.ingest(self.source, store, **self.scope())
            self.assertGreater(first.imported, 0)
            again = registry.ingest(self.source, store, **self.scope())
            self.assertEqual((again.imported, again.cursor_after_sha256), (0, first.cursor_after_sha256))
            self.comment['body'] = 'SYNTHETIC_PRIVATE_EDITED'
            self.assertEqual(registry.ingest(self.source, store, **self.scope()).imported, 1)
            self.pull['head'] = dict(self.pull['head'], sha='e'*40)
            self.review['state'] = 'REQUEST_CHANGES'
            changed = registry.ingest(self.source, store, **self.scope())
            self.assertEqual(changed.imported, 2)
            self.assertNotEqual(changed.cursor_after_sha256, first.cursor_after_sha256)
            text = store.path.read_text()
            for secret in SECRETS: self.assertNotIn(secret, text)

    def test_same_numbers_on_other_hosts_paths_and_repositories_stay_distinct(self):
        self.build('other-repository', 42)
        self.build(REPO, 42, prefix='/forge')
        self.build(REPO, 42, host='mirror.'+HOST)
        selections = (self.selection, replace(self.selection, base_url='https://mirror.'+HOST),
            replace(self.selection, repo='other-repository'), replace(self.selection, base_url=self.base+'/forge'))
        seen, projects, sources = set(), set(), set()
        for selection in selections:
            client, snapshot = self.read(selection)
            issue = {r.native_id_sha256 for r in snapshot.records if r.record_type in ('gitea_issue', 'gitea_pull', 'gitea_comment')}
            self.assertEqual(len(issue), 5)
            self.assertFalse(issue & seen)
            seen |= issue; projects.add(selection.project_sha256); sources.add(self.spec(selection).source_key)
        self.assertEqual((len(projects), len(sources)), (4, 4))
        self.assertEqual(self.wire.call_args_list[-1].args, (HOST, 443))
        self.assertIn(('mirror.'+HOST, 443), [call.args for call in self.wire.call_args_list])
        self.assertEqual(replace(self.selection, owner='Synthetic-Owner').project_sha256, self.selection.project_sha256)

    def test_fork_and_missing_heads_keep_their_own_repository_identity(self):
        fork = dict(id=43, full_name='synthetic-contributor/synthetic-repository')
        self.pull['head'] = dict(ref='main', sha='b'*40, repo_id=43, repo=fork)
        client, _ = self.read()
        pull = self.nodes(client, 'gitea_pull')[0]
        self.assertEqual(pull['head_repository_state'], 'other_repository')
        self.assertEqual(pull['head']['repository_sha256'], self.selection.repository_sha256(fork['full_name']))
        self.assertNotEqual(pull['head']['repository_sha256'], pull['base']['repository_sha256'])
        self.assertEqual(pull['head']['ref_sha256'], pull['base']['ref_sha256'])
        self.assertNotEqual(pull['head']['repository_id_sha256'], pull['base']['repository_id_sha256'])
        self.pull['head'] = dict(ref='main', sha='b'*40, repo_id=0, repo=None)
        client, _ = self.read()
        pull = self.nodes(client, 'gitea_pull')[0]
        self.assertEqual((pull['head_repository_state'], pull['head']['repository_sha256']), ('unknown', None))
        self.pull['head'] = dict(ref='main', sha='b'*40, repo_id=44, repo=fork)
        with self.assertRaises(TransportError): self.read()

    def test_missing_review_endpoint_and_version_are_explicit_capability_gaps(self):
        self.routes['/api/v1/version'] = Response(b'{}', 404)
        self.routes[self.repo_path+'/pulls/8/reviews?page=1&limit=50'] = Response(b'SYNTHETIC_SECRET', 404)
        client, snapshot = self.read()
        self.assertIn(('reviews', 'unsupported_endpoint', 'review_listing_not_found'), self.states(client))
        self.assertEqual((client.summary['server_version'], client.summary['server_version_state']), ('unknown', 'not_found'))
        self.assertFalse(self.nodes(client, 'gitea_review'))
        self.assertIn('unsupported_version', snapshot.metadata.coverage.gaps)
        self.assertTrue({'review_endpoint_unsupported', 'server_version_unknown'} <= set(client.summary['warnings']))
        self.assertTrue(snapshot.checkpoint_ready)
        self.routes['/api/v1/version'] = dict(version='not a version!')
        self.assertEqual(self.read()[0].summary['server_version_state'], 'unrecognized')

    def test_permission_failures_and_rate_limits_are_coverage_states_never_success(self):
        cases = ((self.repo_path, 401, ('repository', 'unauthorized', None)), (self.repo_path, 403, ('repository', 'forbidden', None)),
            (self.repo_path, 404, ('repository', 'not_found', None)),
            (self.repo_path+'/issues/7/comments?page=1&limit=50', 403, ('comments', 'forbidden', None)),
            (self.repo_path+'/issues/7', 429, ('issue', 'rate_limited', None)))
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
                    self.assertIn(('issue', 'rate_limited', None), self.states(client)[2:])
                    self.assertFalse(self.nodes(client, 'gitea_issue'))
                store = SourceStore(Path(folder)/'journal.json', **self.scope())
                with self.assertRaises(ValueError): self.registry().ingest(self.source, store, **self.scope())
                self.assertFalse(store.path.exists())
                self.routes[target] = original
        self.routes[self.repo_path] = Response(b'SYNTHETIC_SECRET', 500)
        with self.assertRaises(TransportError) as error: self.read()
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))

    def test_non_https_and_non_canonical_instances_are_refused_before_connecting(self):
        for url in ('http://'+HOST, f'https://{HOST}/', f'https://{HOST}/api/v1', f'https://user@{HOST}',
                f'https://{HOST}?token=secret', 'https://Gitea.example.test', f'https://{HOST}:443', f'https://{HOST}/a/../b'):
            with self.subTest(url=url), self.assertRaises(ValueError): replace(self.selection, base_url=url)
        with self.assertRaises(TransportError): GiteaAPI('https://other.example.test', self.selection)
        for change in (dict(owner='../x'), dict(repo=''), dict(capture=('issues',)), dict(capture=('pulls', 'issues')),
                dict(issues=(8,)), dict(issues=(7, 7)), dict(capture=('comments', 'issues', 'reviews'), pulls=()),
                dict(capture=('issues', 'pulls', 'reviews', 'mutations'))):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(self.selection, **change)
        self.assertEqual(GiteaSelection.parse(json.loads(json.dumps(self.selection.to_dict()))), self.selection)
        for source in (replace(self.source, enabled=False), replace(self.source, scope_sha256=digest('foreign')),
                replace(self.source, root_sha256=digest('https://other.example.test')), replace(self.source, version='v0')):
            with self.assertRaises(TransportError): self.read(source=source)
        self.wire.assert_not_called()

    def test_reader_issues_only_get_requests_and_carries_no_mutation_verbs(self):
        self.read()
        self.assertTrue(self.connection.request.call_args_list)
        self.assertTrue(all(call.args[0] == 'GET' and call.kwargs.get('body') is None for call in self.connection.request.call_args_list))
        text = inspect.getsource(gitea)
        for verb in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'", 'body='):
            self.assertNotIn(verb, text)
        self.assertTrue(all(target.startswith('/api/v1/') for target in self.targets))

    def test_identity_parent_and_scope_conflicts_fail_closed(self):
        calls = 0

        def moving_issue():
            nonlocal calls
            calls += 1
            return dict(self.issue, id=70 if calls == 1 else 71)
        self.routes[self.repo_path+'/issues/7'] = moving_issue
        with self.assertRaises(TransportError): self.read()
        self.routes[self.repo_path+'/issues/7'] = self.issue
        for change in (dict(issue_url=f'{self.base}/synthetic-owner/other-repository/issues/7'),
                dict(issue_url=f'https://other.example.test/{OWNER}/{REPO}/issues/7'), dict(issue_url=f'{self.base}/{OWNER}/{REPO}/issues/9')):
            with self.subTest(change=change), patch.dict(self.comment, change), self.assertRaises(TransportError): self.read()
        for change in (dict(repository=dict(id=1, full_name=f'{OWNER}/other-repository')), dict(pull_request=None)):
            with self.subTest(change=change), patch.dict(self.pull_issue, change), self.assertRaises(TransportError): self.read()
        with patch.dict(self.pull, base=dict(self.pull['base'], repo=dict(id=9, full_name=f'{OWNER}/other-repository'), repo_id=9)):
            with self.assertRaises(TransportError): self.read()
        self.review['state'] = 'SYNTHETIC_STATE'
        client, _ = self.read()
        self.assertEqual(self.nodes(client, 'gitea_review')[0]['state'], 'unknown')
        self.assertIn('review_state_unrecognized', client.summary['warnings'])


class GiteaConfigurationTests(GiteaFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.manifest, self.journal, self.credential = self.root/'gitea.json', self.root/'journal.json', self.root/'authorization'
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
        for secret in (*SECRETS, self.base, str(self.root)): self.assertNotIn(secret, output.getvalue()+error.getvalue())
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_cli_probe_preview_and_import_read_only_the_named_variable(self):
        env = ('--authorization-env', 'SYNTHETIC_GITEA_AUTHORIZATION')
        code, result = self.invoke('probe', *env)
        self.assertEqual((code, result['status'], result['network_used']), (0, 'recognized', False))
        self.wire.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.invoke('preview', *env)[0], 3)
        self.wire.assert_not_called()
        with patch.dict(os.environ, SYNTHETIC_GITEA_AUTHORIZATION='token SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            self.assertEqual(code, 0)
            self.assertTrue(result['checkpoint_ready'])
            self.assertEqual(result['gitea_summary']['server_version'], '1.22.0')
            self.assertTrue(all(call.kwargs['headers']['Authorization'] == 'token SYNTHETIC_SECRET'
                for call in self.connection.request.call_args_list))
            self.assertEqual(self.invoke('import', *env)[0], 0)
            original = self.journal.read_bytes()
            self.routes[self.repo_path+'/issues/7'] = Response(b'SYNTHETIC_SECRET', 403)
            code, result = self.invoke('import', *env)
            self.assertEqual((code, result['status'], result['checkpoint_committed']), (3, 'blocked', False))
            self.assertIn(('issue', 'forbidden'), [(c['resource'], c['state']) for c in result['gitea_summary']['coverage']])
            self.assertEqual(self.journal.read_bytes(), original)
            self.assertEqual(self.invoke('status')[0], 0)
            self.assertEqual(self.invoke('preview', *env, '--authorization-file', str(self.credential))[0], 2)
            self.routes[self.repo_path+'/issues/7'] = self.issue
            # A byte budget spent partway through a listing is blocked coverage with its summary, never an integrity failure.
            linked, second = self.linked_pages()
            self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'], self.routes[second] = linked, self.large_page
            self.write(limits=dict(max_pages=40, max_bytes=100_000))
            code, result = self.invoke('import', *env)
            self.assertEqual((code, result['status'], result['checkpoint_committed']), (3, 'blocked', False))
            self.assertIn(('comments', 'pagination_incomplete', 'budget_exhausted'),
                [(c['resource'], c['state'], c['basis']) for c in result['gitea_summary']['coverage']])
            self.assertEqual(self.journal.read_bytes(), original)
        self.write()
        self.credential.write_text('token SYNTHETIC_SECRET\n')
        self.routes[self.repo_path+'/issues/7'] = self.issue
        self.assertEqual(self.invoke('preview', '--authorization-file', str(self.credential))[0], 0)

    def test_configuration_rejects_mismatched_identity_and_github_rejects_the_variable(self):
        connector = GiteaConfigured(self.manifest)
        self.assertEqual(connector.declaration(**self.scope())[0], self.source)
        for change in (dict(endpoint='https://other.example.test'), dict(limits=dict(retries=3))):
            with self.subTest(change=change):
                self.write(**change)
                with self.assertRaises(ValueError): connector.declaration(**self.scope())
        # A GitHub configuration that probes cleanly, so only the CLI guard can refuse the variable.
        github = GitHubSelection(42, (7,), (8,))
        endpoint = 'https://api.github.com/repos/synthetic-github/synthetic-project'
        self.source = SourceSpec('api', 'github', github.source_version, digest(endpoint), github.project_sha256, github.sha256,
            identity_provenance='source_declared_id', auth_mode='configured', enabled=True)
        self.write(endpoint=endpoint, selection=github.to_dict())
        self.assertEqual(self.invoke('probe')[0], 0)
        with patch.dict(os.environ, SYNTHETIC_GITEA_AUTHORIZATION='token SYNTHETIC_SECRET'):
            for operation in ('probe', 'preview'):
                with self.subTest(operation=operation):
                    self.assertEqual(self.invoke(operation, '--authorization-env', 'SYNTHETIC_GITEA_AUTHORIZATION'), (2, None))
        self.wire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
