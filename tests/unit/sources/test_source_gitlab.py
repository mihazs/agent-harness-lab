"""Offline GitLab REST v4 fixtures; synthetic hosts, groups, users and projects only, never the network."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import inspect
import io
import json
import os
from unittest.mock import patch

import pytest

from tests.unit.sources.test_source_transport import Response

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
    @pytest.fixture(autouse=True)
    def _gitlab(self):
        with patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection') as self.wire:
            self._prepare()
            yield

    def _prepare(self):
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
        self.connection = self.wire.return_value
        self.targets, self.hosts = [], []

        def request(method, target, body=None, headers=None):
            assert method == 'GET'; assert body is None
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
        for secret in SECRETS: assert secret not in rendered
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


class TestGitLabSource(GitLabFixture):
    def v7_reviewer_selection(self):
        return replace(self.selection, schema_version=7, capture=('merge_requests', 'reviewers'), issues=())

    def test_v7_reviewer_states_are_private_claims_with_incomplete_coverage(self):
        self.routes[self.mr_path+'/reviewers'] = [
            dict(user=dict(id=1, name='SYNTHETIC_PRIVATE_NAME'), state='reviewed'),
            dict(user=dict(id=2, username='SYNTHETIC_PRIVATE_USER'), state='unreviewed'),
            dict(user=dict(id=3), state='SYNTHETIC_PRIVATE_UNKNOWN')]
        client, snapshot = self.read(self.v7_reviewer_selection())
        nodes = self.nodes(client, 'gitlab_mr_reviewer')
        assert sorted(n['review_state'] for n in nodes) == ['reviewed', 'unknown', 'unreviewed']
        assert len([r for r in client.summary['relations'] if r[0] == 'reviewer_state_on']) == 3
        assert ('reviewers', 'not_observed', 'reviewer_list_completeness_unverified') in self.states(client)
        assert not snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)

    def test_v7_empty_reviewers_do_not_prove_absence(self):
        self.routes[self.mr_path+'/reviewers'] = lambda: self.page([])
        client, snapshot = self.read(self.v7_reviewer_selection())
        assert not self.nodes(client, 'gitlab_mr_reviewer')
        assert ('reviewers', 'not_observed', 'reviewer_list_completeness_unverified') in self.states(client)
        assert not snapshot.checkpoint_ready

    @pytest.mark.parametrize('rows', [None, {}, [dict(user=None)], [dict(user=dict(id=True))],
        [dict(user=dict(id=1)), dict(user=dict(id=1))]])
    def test_v7_malformed_or_duplicate_reviewers_fail_closed(self, rows):
        self.routes[self.mr_path+'/reviewers'] = lambda: self.page(rows)
        with pytest.raises(TransportError): self.read(self.v7_reviewer_selection())

    @pytest.mark.parametrize('blocked', ['project', 'merge_request', 'reviewers'])
    def test_v7_unavailable_reviewers_are_visible(self, blocked):
        selection = self.v7_reviewer_selection()
        target = {'project': '/api/v4/projects/'+ENCODED, 'merge_request': self.mr_path,
                  'reviewers': self.mr_path+'/reviewers'}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        expected = ('reviewers', 'forbidden', None) if blocked == 'reviewers' else (
            'reviewers', 'not_observed', blocked+'_unavailable')
        assert expected in self.states(client) and not snapshot.checkpoint_ready

    def test_v7_reviewers_require_explicit_scope_and_bound(self):
        with pytest.raises(ValueError): replace(self.selection, schema_version=6,
            capture=('merge_requests', 'reviewers'), issues=())
        with pytest.raises(ValueError): replace(self.selection, schema_version=7,
            capture=('issues', 'reviewers'), merge_requests=())
        self.routes[self.mr_path+'/reviewers'] = [dict(user=dict(id=1)), dict(user=dict(id=2))]
        with patch.object(gitlab, 'MAX_UNPAGED_ROWS', 1):
            with pytest.raises(TransportError): self.read(self.v7_reviewer_selection())

    def v5_closing_selection(self):
        return replace(self.selection, schema_version=5, capture=('closing_issues', 'issues', 'merge_requests'))

    def closing_rows(self):
        return [dict(id=700, iid=7, project_id=42, confidential=False,
            title='SYNTHETIC_PRIVATE_TITLE', description='SYNTHETIC_PRIVATE_BODY')]

    def test_v5_selected_closing_issue_is_reported_not_verified_or_closed(self):
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = self.closing_rows()
        client, snapshot = self.read(self.v5_closing_selection())
        reference, = self.nodes(client, 'gitlab_mr_closing_issue')
        assert reference['relationship'] == 'server_reported_may_close_on_merge'
        assert reference['issue_sha256'] == client._native('Issue', 700)
        assert any(r[0] == 'reported_reference_to_selected_issue' for r in client.summary['relations'])
        assert any(r[0] == 'reported_closing_reference_on_mr' for r in client.summary['relations'])
        assert ('closing_issues', 'captured', None) in self.states(client)
        assert 'closing_issue_visibility_unverified' in client.summary['warnings']
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)

    def test_v5_unselected_issue_is_not_implicitly_fetched(self):
        selection = replace(self.selection, schema_version=5, capture=('closing_issues', 'merge_requests'), issues=())
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = self.closing_rows()
        client, snapshot = self.read(selection)
        assert snapshot.checkpoint_ready and len(self.nodes(client, 'gitlab_mr_closing_issue')) == 1
        assert not any('/issues/' in target for target in self.targets)
        assert not any(r[0] == 'reported_reference_to_selected_issue' for r in client.summary['relations'])

    def test_v5_same_issue_and_mr_numbers_in_other_projects_remain_distinct(self):
        selection = self.v5_closing_selection()
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = self.closing_rows()
        first, _ = self.read(selection)
        other_path = 'synthetic-group/other-project'
        self.build(43, other_path)
        self.routes['/api/v4/projects/43/merge_requests/7/closes_issues?page=1&per_page=100'] = [dict(id=700, iid=7, project_id=43, confidential=False)]
        second, _ = self.read(replace(selection, project=other_path))
        assert self.nodes(first, 'gitlab_mr_closing_issue')[0]['native_id_sha256'] != self.nodes(second, 'gitlab_mr_closing_issue')[0]['native_id_sha256']

    @pytest.mark.parametrize('foreign', [dict(id=701, iid=7, project_id=43), dict(id=701, title='SYNTHETIC_PRIVATE_TITLE'),
                                         dict(id='PROJECT-123', title='SYNTHETIC_PRIVATE_TITLE')])
    def test_v5_foreign_or_external_reference_is_omitted_and_incomplete(self, foreign):
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = self.closing_rows()+[foreign]
        client, snapshot = self.read(self.v5_closing_selection())
        assert ('closing_issues', 'pagination_incomplete', 'unscoped_references_omitted') in self.states(client)
        assert len(self.nodes(client, 'gitlab_mr_closing_issue')) == 1 and not snapshot.checkpoint_ready
        assert not any('/projects/43' in target for target in self.targets)
        assert 'PROJECT-123' not in repr(client.summary)

    def test_v5_duplicate_external_string_identity_fails_closed(self):
        external = dict(id='PROJECT-123', title='SYNTHETIC_PRIVATE_TITLE')
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = [dict(external), dict(external)]
        with pytest.raises(TransportError): self.read(self.v5_closing_selection())

    def test_v5_duplicate_external_string_identity_across_pages_fails_closed(self):
        target = self.mr_path+'/closes_issues'
        first = [dict(id=f'PROJECT-{index}', title='SYNTHETIC_PRIVATE_TITLE') for index in range(100)]
        self.routes[target+'?page=1&per_page=100'] = self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = self.page([dict(first[0])], **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        with pytest.raises(TransportError): self.read(self.v5_closing_selection())

    @pytest.mark.parametrize('case', ['selected_identity_conflict', 'duplicate', 'invalid_project', 'same_project_string_id', 'confidential_without_auth',
                                     'unknown_visibility_without_auth', 'invalid_visibility'])
    def test_v5_invalid_or_unauthorized_reference_fails_closed(self, case):
        rows = self.closing_rows()
        if case == 'selected_identity_conflict': rows[0]['id'] = 701
        if case == 'duplicate': rows.append(dict(rows[0]))
        if case == 'invalid_project': rows[0]['project_id'] = True
        if case == 'same_project_string_id': rows[0]['id'] = 'PROJECT-123'
        if case == 'confidential_without_auth': rows[0]['confidential'] = True
        if case == 'unknown_visibility_without_auth': del rows[0]['confidential']
        if case == 'invalid_visibility': rows[0]['confidential'] = 'true'
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = rows
        with pytest.raises(TransportError): self.read(self.v5_closing_selection())

    @pytest.mark.parametrize('confidential', [True, None])
    def test_v5_explicit_authorization_can_read_confidential_or_unknown_reference(self, confidential):
        selection = self.v5_closing_selection()
        rows = self.closing_rows(); rows[0]['confidential'] = confidential
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = rows
        source = self.spec(selection, auth_mode='configured')
        client, snapshot = self.read(selection, source, authorization='Bearer SYNTHETIC_SECRET')
        assert snapshot.checkpoint_ready and len(self.nodes(client, 'gitlab_mr_closing_issue')) == 1

    def test_v5_closing_references_paginate_and_late_denial_blocks(self):
        selection = self.v5_closing_selection()
        target = self.mr_path+'/closes_issues'
        first = [dict(id=10000+index, iid=10000+index, project_id=42, confidential=False) for index in range(100)]
        self.routes[target+'?page=1&per_page=100'] = lambda: self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = lambda: self.page([dict(id=10100, iid=10100, project_id=42, confidential=False)],
            **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_mr_closing_issue')) == 101 and snapshot.checkpoint_ready
        self.routes[target+'?page=2&per_page=100'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        assert ('closing_issues', 'pagination_incomplete', 'forbidden') in self.states(client)
        assert not snapshot.checkpoint_ready

    def test_v5_foreign_omission_and_late_denial_both_remain_visible(self):
        target = self.mr_path+'/closes_issues'
        first = [dict(id=10000+index, iid=10000+index, project_id=42, confidential=False) for index in range(99)]
        first.append(dict(id=10100, iid=1, project_id=43))
        self.routes[target+'?page=1&per_page=100'] = self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(self.v5_closing_selection())
        assert ('closing_issues', 'pagination_incomplete', 'forbidden') in self.states(client)
        assert ('closing_issues', 'not_observed', 'unscoped_references_omitted') in self.states(client)
        assert not snapshot.checkpoint_ready

    @pytest.mark.parametrize('blocked', ['project', 'merge_request', 'closing_issues'])
    def test_v5_closing_reference_unavailable_is_visible(self, blocked):
        selection = self.v5_closing_selection()
        target = {'project': '/api/v4/projects/'+ENCODED, 'merge_request': self.mr_path,
                  'closing_issues': self.mr_path+'/closes_issues?page=1&per_page=100'}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        expected = ('closing_issues', 'forbidden', None) if blocked == 'closing_issues' else (
            'closing_issues', 'not_observed', blocked+'_unavailable')
        assert expected in self.states(client) and not snapshot.checkpoint_ready

    def test_v5_closing_references_require_explicit_scope_and_are_bounded(self):
        with pytest.raises(ValueError): replace(self.selection, schema_version=4,
            capture=('closing_issues', 'issues', 'merge_requests'))
        with pytest.raises(ValueError): replace(self.selection, schema_version=5,
            capture=('closing_issues', 'issues'), merge_requests=())
        rows = self.closing_rows()+[dict(id=701, iid=8, project_id=42, confidential=False)]
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = rows
        with patch.object(gitlab, 'MAX_MR_CLOSING_ISSUES', 1):
            with pytest.raises(TransportError): self.read(self.v5_closing_selection())

    def v6_related_selection(self):
        return replace(self.selection, schema_version=6, capture=('issues', 'merge_requests', 'related_issues'))

    def test_v6_related_and_closing_are_distinct_server_claims(self):
        selection = replace(self.selection, schema_version=6,
            capture=('closing_issues', 'issues', 'merge_requests', 'related_issues'))
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = self.closing_rows()
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = self.closing_rows()
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_mr_related_issue')) == 1
        assert len(self.nodes(client, 'gitlab_mr_closing_issue')) == 1
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        with patch.object(gitlab, 'MAX_MR_CLOSING_ISSUES', 1):
            with pytest.raises(TransportError): self.read(selection)

    def test_v6_related_selected_closing_issue_is_reported_not_verified_or_closed(self):
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = self.closing_rows()
        client, snapshot = self.read(self.v6_related_selection())
        reference, = self.nodes(client, 'gitlab_mr_related_issue')
        assert reference['relationship'] == 'server_reported_related'
        assert reference['issue_sha256'] == client._native('Issue', 700)
        assert any(r[0] == 'reported_related_reference_to_selected_issue' for r in client.summary['relations'])
        assert any(r[0] == 'reported_related_reference_on_mr' for r in client.summary['relations'])
        assert ('related_issues', 'captured', None) in self.states(client)
        assert 'related_issue_visibility_unverified' in client.summary['warnings']
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)

    def test_v6_related_unselected_issue_is_not_implicitly_fetched(self):
        selection = replace(self.selection, schema_version=6, capture=('merge_requests', 'related_issues'), issues=())
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = self.closing_rows()
        client, snapshot = self.read(selection)
        assert snapshot.checkpoint_ready and len(self.nodes(client, 'gitlab_mr_related_issue')) == 1
        assert not any('/issues/' in target for target in self.targets)
        assert not any(r[0] == 'reported_related_reference_to_selected_issue' for r in client.summary['relations'])

    def test_v6_related_same_issue_and_mr_numbers_in_other_projects_remain_distinct(self):
        selection = self.v6_related_selection()
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = self.closing_rows()
        first, _ = self.read(selection)
        other_path = 'synthetic-group/other-project'
        self.build(43, other_path)
        self.routes['/api/v4/projects/43/merge_requests/7/related_issues?page=1&per_page=100'] = [dict(id=700, iid=7, project_id=43, confidential=False)]
        second, _ = self.read(replace(selection, project=other_path))
        assert self.nodes(first, 'gitlab_mr_related_issue')[0]['native_id_sha256'] != self.nodes(second, 'gitlab_mr_related_issue')[0]['native_id_sha256']

    @pytest.mark.parametrize('foreign', [dict(id=701, iid=7, project_id=43), dict(id=701, title='SYNTHETIC_PRIVATE_TITLE'),
                                         dict(id='PROJECT-123', title='SYNTHETIC_PRIVATE_TITLE')])
    def test_v6_related_foreign_or_external_reference_is_omitted_and_incomplete(self, foreign):
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = self.closing_rows()+[foreign]
        client, snapshot = self.read(self.v6_related_selection())
        assert ('related_issues', 'pagination_incomplete', 'unscoped_references_omitted') in self.states(client)
        assert len(self.nodes(client, 'gitlab_mr_related_issue')) == 1 and not snapshot.checkpoint_ready
        assert not any('/projects/43' in target for target in self.targets)
        assert 'PROJECT-123' not in repr(client.summary)

    def test_v6_related_duplicate_external_string_identity_fails_closed(self):
        external = dict(id='PROJECT-123', title='SYNTHETIC_PRIVATE_TITLE')
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = [dict(external), dict(external)]
        with pytest.raises(TransportError): self.read(self.v6_related_selection())

    def test_v6_related_duplicate_external_string_identity_across_pages_fails_closed(self):
        target = self.mr_path+'/related_issues'
        first = [dict(id=f'PROJECT-{index}', title='SYNTHETIC_PRIVATE_TITLE') for index in range(100)]
        self.routes[target+'?page=1&per_page=100'] = self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = self.page([dict(first[0])], **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        with pytest.raises(TransportError): self.read(self.v6_related_selection())

    @pytest.mark.parametrize('case', ['selected_identity_conflict', 'duplicate', 'invalid_project', 'same_project_string_id', 'confidential_without_auth',
                                     'unknown_visibility_without_auth', 'invalid_visibility'])
    def test_v6_related_invalid_or_unauthorized_reference_fails_closed(self, case):
        rows = self.closing_rows()
        if case == 'selected_identity_conflict': rows[0]['id'] = 701
        if case == 'duplicate': rows.append(dict(rows[0]))
        if case == 'invalid_project': rows[0]['project_id'] = True
        if case == 'same_project_string_id': rows[0]['id'] = 'PROJECT-123'
        if case == 'confidential_without_auth': rows[0]['confidential'] = True
        if case == 'unknown_visibility_without_auth': del rows[0]['confidential']
        if case == 'invalid_visibility': rows[0]['confidential'] = 'true'
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = rows
        with pytest.raises(TransportError): self.read(self.v6_related_selection())

    @pytest.mark.parametrize('confidential', [True, None])
    def test_v6_related_explicit_authorization_can_read_confidential_or_unknown_reference(self, confidential):
        selection = self.v6_related_selection()
        rows = self.closing_rows(); rows[0]['confidential'] = confidential
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = rows
        source = self.spec(selection, auth_mode='configured')
        client, snapshot = self.read(selection, source, authorization='Bearer SYNTHETIC_SECRET')
        assert snapshot.checkpoint_ready and len(self.nodes(client, 'gitlab_mr_related_issue')) == 1

    def test_v6_related_references_paginate_and_late_denial_blocks(self):
        selection = self.v6_related_selection()
        target = self.mr_path+'/related_issues'
        first = [dict(id=10000+index, iid=10000+index, project_id=42, confidential=False) for index in range(100)]
        self.routes[target+'?page=1&per_page=100'] = lambda: self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = lambda: self.page([dict(id=10100, iid=10100, project_id=42, confidential=False)],
            **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_mr_related_issue')) == 101 and snapshot.checkpoint_ready
        self.routes[target+'?page=2&per_page=100'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        assert ('related_issues', 'pagination_incomplete', 'forbidden') in self.states(client)
        assert not snapshot.checkpoint_ready

    def test_v6_related_foreign_omission_and_late_denial_both_remain_visible(self):
        target = self.mr_path+'/related_issues'
        first = [dict(id=10000+index, iid=10000+index, project_id=42, confidential=False) for index in range(99)]
        first.append(dict(id=10100, iid=1, project_id=43))
        self.routes[target+'?page=1&per_page=100'] = self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(self.v6_related_selection())
        assert ('related_issues', 'pagination_incomplete', 'forbidden') in self.states(client)
        assert ('related_issues', 'not_observed', 'unscoped_references_omitted') in self.states(client)
        assert not snapshot.checkpoint_ready

    @pytest.mark.parametrize('blocked', ['project', 'merge_request', 'related_issues'])
    def test_v6_related_reference_unavailable_is_visible(self, blocked):
        selection = self.v6_related_selection()
        target = {'project': '/api/v4/projects/'+ENCODED, 'merge_request': self.mr_path,
                  'related_issues': self.mr_path+'/related_issues?page=1&per_page=100'}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        expected = ('related_issues', 'forbidden', None) if blocked == 'related_issues' else (
            'related_issues', 'not_observed', blocked+'_unavailable')
        assert expected in self.states(client) and not snapshot.checkpoint_ready

    def test_v6_related_references_require_explicit_scope_and_are_bounded(self):
        with pytest.raises(ValueError): replace(self.selection, schema_version=5,
            capture=('issues', 'merge_requests', 'related_issues'))
        with pytest.raises(ValueError): replace(self.selection, schema_version=6,
            capture=('issues', 'related_issues'), merge_requests=())
        rows = self.closing_rows()+[dict(id=701, iid=8, project_id=42, confidential=False)]
        self.routes[self.mr_path+'/related_issues?page=1&per_page=100'] = rows
        with patch.object(gitlab, 'MAX_MR_CLOSING_ISSUES', 1):
            with pytest.raises(TransportError): self.read(self.v6_related_selection())

    def test_v5_combines_closing_references_with_selected_discussions_and_commits(self):
        selection = replace(self.selection, schema_version=5,
            capture=('closing_issues', 'commits', 'discussions', 'issues', 'merge_requests'))
        self.routes[self.issue_path+'/discussions?page=1&per_page=100'] = self.issue_discussion_rows()
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = self.discussion_rows()
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = self.commit_rows()
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = self.closing_rows()
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_discussion')) == 3
        assert len(self.nodes(client, 'gitlab_mr_commit')) == len(self.nodes(client, 'gitlab_mr_closing_issue')) == 1
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
    def v4_commit_selection(self):
        return replace(self.selection, schema_version=4, capture=('commits', 'merge_requests'), issues=())

    def commit_rows(self):
        return [dict(id='c'*40, committed_date=self.stamp, title='SYNTHETIC_PRIVATE_TITLE',
                     message='SYNTHETIC_PRIVATE_BODY', parent_ids=['a'*40])]

    def test_v4_mr_commits_are_scoped_source_claims_not_ancestry_or_outcomes(self):
        selection = self.v4_commit_selection()
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = self.commit_rows()
        client, snapshot = self.read(selection)
        commit, = self.nodes(client, 'gitlab_mr_commit')
        assert commit['commit_sha256'] == digest('c'*40)
        assert (commit['membership'], commit['ancestry']) == ('server_reported', 'unknown')
        assert ('commits', 'captured', None) in self.states(client)
        assert any(r[0] == 'reported_commit_in_mr' for r in client.summary['relations'])
        assert all('parent' not in relation[0] for relation in client.summary['relations'])
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)
        assert 'c'*40 not in repr(client.summary)
        before = commit['native_id_sha256']
        self.mr['sha'], self.mr['squash_commit_sha'] = 'd'*40, 'e'*40
        changed, _ = self.read(selection)
        assert self.nodes(changed, 'gitlab_mr_commit')[0]['native_id_sha256'] == before

    def test_v4_same_commit_and_iid_in_different_projects_remain_distinct(self):
        selection = self.v4_commit_selection()
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = self.commit_rows()
        first, _ = self.read(selection)
        other_path = 'synthetic-group/other-project'
        self.build(43, other_path)
        self.routes['/api/v4/projects/43/merge_requests/7/commits?page=1&per_page=100'] = self.commit_rows()
        second, _ = self.read(replace(selection, project=other_path))
        assert self.nodes(first, 'gitlab_mr_commit')[0]['native_id_sha256'] != self.nodes(second, 'gitlab_mr_commit')[0]['native_id_sha256']

    def test_v4_commit_pagination_and_late_denial(self):
        selection = self.v4_commit_selection()
        target = self.mr_path+'/commits'
        first = [dict(id=f'{index:040x}') for index in range(100)]
        self.routes[target+'?page=1&per_page=100'] = lambda: self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = lambda: self.page([dict(id=f'{100:040x}')],
            **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_mr_commit')) == 101 and snapshot.checkpoint_ready
        self.routes[target+'?page=2&per_page=100'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        assert ('commits', 'pagination_incomplete', 'forbidden') in self.states(client)
        assert not snapshot.checkpoint_ready

    @pytest.mark.parametrize('blocked', ['project', 'merge_request', 'commits'])
    def test_v4_unavailable_commit_parent_stays_visible(self, blocked):
        selection = self.v4_commit_selection()
        target = {'project': '/api/v4/projects/'+ENCODED, 'merge_request': self.mr_path,
                  'commits': self.mr_path+'/commits?page=1&per_page=100'}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        expected = ('commits', 'forbidden', None) if blocked == 'commits' else ('commits', 'not_observed', blocked+'_unavailable')
        assert expected in self.states(client) and not snapshot.checkpoint_ready
        if blocked != 'commits': assert not any('/commits?' in target for target in self.targets)

    @pytest.mark.parametrize('bad_id', [None, True, '', 'A'*40, 'a'*39])
    def test_v4_invalid_commit_identity_fails_closed(self, bad_id):
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = [dict(id=bad_id)]
        with pytest.raises(TransportError): self.read(self.v4_commit_selection())

    def test_v4_commits_are_bounded_and_explicitly_selected(self):
        with pytest.raises(ValueError): replace(self.selection, schema_version=3, capture=('commits', 'merge_requests'), issues=())
        with pytest.raises(ValueError): replace(self.selection, schema_version=4, capture=('commits', 'issues'), merge_requests=())
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = [dict(id='a'*40), dict(id='b'*40)]
        with patch.object(gitlab, 'MAX_MR_COMMITS', 1):
            with pytest.raises(TransportError): self.read(self.v4_commit_selection())

    def test_v4_duplicate_commit_fails_closed(self):
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = [dict(id='a'*40), dict(id='a'*40)]
        with pytest.raises(TransportError): self.read(self.v4_commit_selection())

    def test_v4_combines_issue_and_mr_discussions_with_commits(self):
        selection = replace(self.selection, schema_version=4,
            capture=('commits', 'discussions', 'issues', 'merge_requests'))
        self.routes[self.issue_path+'/discussions?page=1&per_page=100'] = self.issue_discussion_rows()
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = self.discussion_rows()
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = self.commit_rows()
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_discussion')) == 3
        assert len(self.nodes(client, 'gitlab_mr_commit')) == 1
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
    def v3_discussion_selection(self):
        return replace(self.selection, schema_version=3,
            capture=('approvals', 'discussions', 'issues', 'merge_requests', 'notes'))

    def issue_discussion_rows(self):
        root, system = self.routes[notes(self.issue_path)]
        nested = dict(root, id=7003, resolvable=True, resolved=False)
        return [dict(id='synthetic-thread-a', individual_note=False, notes=[dict(root), nested, dict(system)])]

    def test_v3_issue_and_mr_same_iid_and_thread_id_keep_separate_identity(self):
        selection = self.v3_discussion_selection()
        self.routes[self.issue_path+'/discussions?page=1&per_page=100'] = self.issue_discussion_rows()
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = self.discussion_rows()
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_discussion')) == 3
        assert len(self.nodes(client, 'gitlab_note')) == 4
        assert len(self.nodes(client, 'gitlab_system_note')) == 2
        links = [relation for relation in client.summary['relations'] if relation[0] == 'discussion_on']
        assert len(links) == 3 and len({link[1] for link in links}) == 3 and len({link[2] for link in links}) == 2
        assert sum(state == ('discussions', 'captured', None) for state in self.states(client)) == 2
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)

    def test_v3_issue_only_discussions_do_not_read_mr_or_root_notes(self):
        selection = replace(self.selection, schema_version=3, capture=('discussions', 'issues'), merge_requests=())
        self.routes[self.issue_path+'/discussions?page=1&per_page=100'] = self.issue_discussion_rows()
        client, snapshot = self.read(selection)
        assert snapshot.checkpoint_ready and len(self.nodes(client, 'gitlab_discussion')) == 1
        assert not any('/merge_requests/' in target or '/notes?' in target for target in self.targets)

    def test_v3_issue_discussion_pagination_and_late_denial(self):
        selection = replace(self.selection, schema_version=3, capture=('discussions', 'issues'), merge_requests=())
        target = self.issue_path+'/discussions'
        first = [dict(id=f'thread-{i}', individual_note=True, notes=[]) for i in range(100)]
        self.routes[target+'?page=1&per_page=100'] = lambda: self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = lambda: self.page([dict(id='thread-100', individual_note=True, notes=[])],
            **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_discussion')) == 101 and snapshot.checkpoint_ready
        self.routes[target+'?page=2&per_page=100'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        assert ('discussions', 'pagination_incomplete', 'forbidden') in self.states(client)
        assert not snapshot.checkpoint_ready

    def test_v3_project_denial_retains_distinct_issue_and_mr_selections(self):
        self.routes['/api/v4/projects/'+ENCODED] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(self.v3_discussion_selection())
        parents = [row['parent_sha256'] for row in client.summary['coverage'] if row['resource'] == 'discussions']
        assert len(parents) == len(set(parents)) == 2 and not snapshot.checkpoint_ready

    @pytest.mark.parametrize('blocked', ['project', 'issue', 'discussion'])
    def test_v3_issue_discussion_unavailable_is_visible_and_blocks(self, blocked):
        selection = replace(self.selection, schema_version=3, capture=('discussions', 'issues'), merge_requests=())
        target = {'project': '/api/v4/projects/'+ENCODED, 'issue': self.issue_path,
                  'discussion': self.issue_path+'/discussions?page=1&per_page=100'}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        expected = ('discussions', 'forbidden', None) if blocked == 'discussion' else (
            'discussions', 'not_observed', blocked+'_unavailable')
        assert expected in self.states(client) and not snapshot.checkpoint_ready
        if blocked != 'discussion': assert not any('/discussions?' in target for target in self.targets)

    @pytest.mark.parametrize('case', ['foreign_type', 'foreign_id', 'duplicate_note'])
    def test_v3_issue_discussion_foreign_or_duplicate_note_fails_closed(self, case):
        selection = replace(self.selection, schema_version=3, capture=('discussions', 'issues'), merge_requests=())
        rows = self.issue_discussion_rows()
        if case == 'foreign_type': rows[0]['notes'][0]['noteable_type'] = 'MergeRequest'
        if case == 'foreign_id': rows[0]['notes'][0]['noteable_id'] = 999
        if case == 'duplicate_note': rows[0]['notes'].append(dict(rows[0]['notes'][0]))
        self.routes[self.issue_path+'/discussions?page=1&per_page=100'] = rows
        with pytest.raises(TransportError): self.read(selection)

    def discussion_selection(self):
        return replace(self.selection, schema_version=2,
            capture=('approvals', 'discussions', 'issues', 'merge_requests', 'notes'))

    def discussion_rows(self):
        root, system = self.routes[notes(self.mr_path)]
        nested = dict(root, id=9003, resolvable=True, resolved=False)
        return [dict(id='synthetic-thread-a', individual_note=False, notes=[dict(root), nested]),
                dict(id='synthetic-thread-b', individual_note=True, notes=[dict(system)])]

    def test_v2_discussions_bind_nested_notes_without_duplicate_records_or_outcome(self):
        selection = self.discussion_selection()
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = self.discussion_rows()
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_discussion')) == 2
        assert len(self.nodes(client, 'gitlab_note')) == 3
        assert len(self.nodes(client, 'gitlab_system_note')) == 2
        assert any(note['resolvable'] is True and note['resolved'] is False for note in self.nodes(client, 'gitlab_note'))
        assert len([r for r in client.summary['relations'] if r[0] == 'note_in_discussion']) == 3
        assert ('discussions', 'captured', None) in self.states(client)
        assert 'discussions_not_captured' not in client.summary['warnings']
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)
        assert all('synthetic-thread' not in repr(n) for n in client.summary['nodes'])

    def test_v2_discussions_do_not_implicitly_read_notes(self):
        selection = replace(self.selection, schema_version=2, capture=('discussions', 'merge_requests'), issues=())
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = self.discussion_rows()
        client, snapshot = self.read(selection)
        assert snapshot.checkpoint_ready and len(self.nodes(client, 'gitlab_discussion')) == 2
        assert not any('/notes?' in target for target in self.targets)
        assert not client.summary['task_outcomes_verified']

    def test_v2_discussion_pagination_and_unavailable_are_visible(self):
        selection = self.discussion_selection()
        target = self.mr_path+'/discussions'
        first = [dict(id=f'thread-{i}', individual_note=True, notes=[]) for i in range(100)]
        self.routes[target+'?page=1&per_page=100'] = lambda: self.page(first, **{'X-Total': '101', 'X-Page': '1', 'X-Next-Page': '2'})
        self.routes[target+'?page=2&per_page=100'] = lambda: self.page([dict(id='thread-100', individual_note=True, notes=[])],
            **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        client, snapshot = self.read(selection)
        assert len(self.nodes(client, 'gitlab_discussion')) == 101 and snapshot.checkpoint_ready
        self.routes[target+'?page=2&per_page=100'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        assert ('discussions', 'pagination_incomplete', 'forbidden') in self.states(client)
        assert not snapshot.checkpoint_ready

    @pytest.mark.parametrize('blocked', ['project', 'merge_request', 'discussions'])
    def test_v2_unavailable_discussion_parent_keeps_unknown_coverage(self, blocked):
        selection = self.discussion_selection()
        target = {'project': '/api/v4/projects/'+ENCODED, 'merge_request': self.mr_path,
                  'discussions': self.mr_path+'/discussions?page=1&per_page=100'}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection)
        expected = ('discussions', 'forbidden', None) if blocked == 'discussions' else (
            'discussions', 'not_observed', blocked+'_unavailable')
        assert expected in self.states(client)
        assert not snapshot.checkpoint_ready
        if blocked != 'discussions': assert not any('/discussions?' in target for target in self.targets)

    @pytest.mark.parametrize('case', ['foreign_note', 'duplicate_note', 'duplicate_thread', 'invalid_thread'])
    def test_v2_invalid_discussion_identity_or_parent_fails_closed(self, case):
        rows = self.discussion_rows()
        if case == 'foreign_note': rows[0]['notes'][0]['noteable_id'] = 123456
        if case == 'duplicate_note': rows[1]['notes'].append(dict(rows[0]['notes'][0]))
        if case == 'duplicate_thread': rows[1]['id'] = rows[0]['id']
        if case == 'invalid_thread': rows[0]['id'] = ''
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = rows
        with pytest.raises(TransportError): self.read(self.discussion_selection())

    def test_discussions_require_v2_and_selected_merge_request(self):
        with pytest.raises(ValueError): replace(self.selection, capture=('discussions', 'issues', 'merge_requests', 'notes'))
        with pytest.raises(ValueError): replace(self.selection, schema_version=2, capture=('discussions', 'issues'), merge_requests=())

    def test_v2_discussion_nested_notes_are_bounded(self):
        rows = self.discussion_rows()
        prototype = rows[0]['notes'][0]
        rows[0]['notes'] = [dict(prototype, id=10000+index) for index in range(10001)]
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = rows
        with pytest.raises(TransportError): self.read(self.discussion_selection())
    def test_capture_keeps_issues_merge_requests_notes_system_notes_and_approvals_without_grading(self):
        client, snapshot = self.read()
        assert {r.record_type for r in snapshot.records} == {'gitlab_instance', 'gitlab_project', 'gitlab_issue',
            'gitlab_merge_request', 'gitlab_note', 'gitlab_system_note', 'gitlab_approvals'}
        assert snapshot.checkpoint_ready
        assert all(state == 'captured' for _, state, _ in self.states(client))
        assert (client.summary['server_version'], client.summary['server_version_state']) == ('17.0.0-ee', 'reported')
        assert (len(self.nodes(client, 'gitlab_note')), len(self.nodes(client, 'gitlab_system_note'))) == (2, 2)
        assert client.summary['system_notes'] == 2
        assert all(n['system'] for n in self.nodes(client, 'gitlab_system_note'))
        assert not any(n['system'] for n in self.nodes(client, 'gitlab_note'))
        relations = {relation for relation, _, _ in client.summary['relations']}
        assert relations == {'conversation_on', 'system_event_on', 'approval_state_on'}
        mr = self.nodes(client, 'gitlab_merge_request')[0]
        assert (mr['state'], mr['draft'], mr['source_project_state']) == ('opened', False, 'same_project')
        assert (mr['head_sha256'], mr['base_sha256']) == (digest('b'*40), digest('a'*40))
        assert (mr['merge_commit_sha256'], mr['squash_commit_sha256'], mr['merged_at']) == (None, None, None)
        assert self.nodes(client, 'gitlab_issue')[0]['confidential'] == True
        assert self.nodes(client, 'gitlab_issue')[0]['body_sha256'] == digest('SYNTHETIC_PRIVATE_BODY')
        approvals = self.nodes(client, 'gitlab_approvals')[0]
        assert (approvals['approved'], approvals['approvals_left'], len(approvals['approvers_sha256'])) == (True, 0, 1)
        assert not client.summary['task_outcomes_verified']
        assert 'merge_closure_approval_not_task_outcome' in client.summary['warnings']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)
        assert snapshot.metadata.coverage.reported_completeness == 'partial'
        assert self.wire.call_args.args == (HOST, 443)
        assert self.targets[:2] == ['/api/v4/version', '/api/v4/projects/'+ENCODED]
        assert all(target.startswith(self.root) for target in self.targets[2:])

    def test_merged_squashed_and_approved_merge_request_still_claims_no_task_outcome(self):
        self.mr.update(state='merged', merged_at=self.stamp, closed_at=None, merge_commit_sha='c'*40,
            squash_commit_sha='d'*40, merged_by=self.actor)
        self.issue['state'] = 'closed'
        client, snapshot = self.read()
        mr = self.nodes(client, 'gitlab_merge_request')[0]
        assert (mr['state'], mr['merged_at']) == ('merged', self.stamp)
        assert (mr['merge_commit_sha256'], mr['squash_commit_sha256']) == (digest('c'*40), digest('d'*40))
        assert 'merge_and_squash_shas_not_ancestry' in client.summary['warnings']
        assert not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' for r in snapshot.records)

    def test_offset_pagination_follows_next_page_link_and_total(self):
        rows = [dict(self.note, id=10000+index) for index in range(101)]
        self.issue['user_notes_count'] = 101
        link = (f'<{self.base}{self.issue_path}/notes?id=42&noteable_id=7&order_by=created_at&page=2&per_page=100&sort=asc>; rel="next", '
            f'<{self.base}{self.issue_path}/notes?id=42&noteable_id=7&order_by=created_at&page=1&per_page=100&sort=asc>; rel="first"')
        self.routes[notes(self.issue_path)] = self.page(rows[:100], **{'X-Total': '101', 'X-Page': '1', 'X-Per-Page': '100',
            'X-Next-Page': '2', 'Link': link})
        self.routes[notes(self.issue_path, 2)] = self.page(rows[100:], **{'X-Total': '101', 'X-Page': '2', 'X-Next-Page': ''})
        client, snapshot = self.read()
        assert len(self.nodes(client, 'gitlab_note')) == 102
        assert snapshot.checkpoint_ready
        assert notes(self.issue_path, 2) in self.targets
        # Without X-Total (large listings), an explicit empty X-Next-Page still marks the last page.
        self.routes[notes(self.issue_path)] = self.page(rows[:100], **{'X-Next-Page': '2'})
        self.routes[notes(self.issue_path, 2)] = self.page(rows[100:], **{'X-Next-Page': ''})
        assert self.read()[1].checkpoint_ready

    @pytest.mark.parametrize('rows,accepted', [pytest.param(transport.MAX_UNPAGED_ROWS, True, id='at-the-bound'),
                                               pytest.param(transport.MAX_UNPAGED_ROWS+1, False, id='over-the-bound')])
    def test_unpaged_responses_are_bounded(self, rows, accepted):
        self.routes[notes(self.issue_path)] = [dict(self.note, id=10000+index) for index in range(rows)]
        self.issue['user_notes_count'] = rows
        if accepted:
            assert self.read()[1].checkpoint_ready
        else:
            with pytest.raises(TransportError): self.read()

    def test_budgets_default_when_unset(self):
        client = GitLabAPI(self.base, self.selection, max_pages=3, max_bytes=None)
        assert (client.transport.max_pages, client.transport.max_bytes, client.transport.seconds) == (3, transport.DEFAULT_LIMITS['max_bytes'], transport.DEFAULT_LIMITS['seconds'])

    @pytest.mark.parametrize('basis', ['total_count_mismatch', 'page_size_unverified', 'declared_count_mismatch',
                                       'total_count_changed', 'budget_exhausted'])
    def test_incomplete_pagination_is_visible_coverage_and_never_checkpoints(self, basis, tmp_path):
        full = [dict(self.note, id=10000+index) for index in range(100)]
        response, issue = {'total_count_mismatch': (lambda: self.page([self.note], **{'X-Total': '3', 'X-Next-Page': ''}), {}),
            'page_size_unverified': (lambda: self.page(full), {}),
            'declared_count_mismatch': ([self.note], dict(user_notes_count=2)),
            'total_count_changed': (lambda: self.page(full, **{'X-Total': '150', 'X-Next-Page': '2'}), {}),
            'budget_exhausted': (lambda: self.page(full, **{'X-Total': '101', 'X-Next-Page': '2'}), {})}[basis]
        self.routes[notes(self.issue_path)] = response
        self.routes[notes(self.issue_path, 2)] = lambda: self.page(full[:1], **{'X-Total': '151', 'X-Next-Page': ''})
        self.issue.update({'user_notes_count': 1, **issue})
        options = dict(max_pages=4) if basis == 'budget_exhausted' else {}
        client, snapshot = self.read(**options)
        assert ('notes', 'pagination_incomplete', basis) in self.states(client)
        assert not snapshot.checkpoint_ready
        assert snapshot.metadata.coverage.truncated
        assert snapshot.metadata.coverage.limit_reached == (basis == 'budget_exhausted')
        assert 'capture_blocked_or_incomplete' in client.summary['warnings']
        store = SourceStore(tmp_path/'journal.json', **self.scope())
        with pytest.raises(ValueError):
            self.registry(GitLabAPI(self.base, self.selection, **options)).ingest(self.source, store, **self.scope())
        assert not store.path.exists()

    @pytest.mark.parametrize('following,options', [
        pytest.param('large', dict(max_bytes=100_000), id='large'), pytest.param('late', dict(seconds=55), id='late'),
        pytest.param('stalled', dict(seconds=55), id='stalled')])
    def test_budget_spent_during_a_response_is_coverage_with_capped_bytes(self, following, options):
        full = [dict(self.note, id=10000+index) for index in range(100)]
        last = {'X-Total': '101', 'X-Next-Page': ''}
        clock = [0.0]

        def large(): return self.page([dict(self.note, id=20000, body='SYNTHETIC_PRIVATE_BODY'*10000)], **last)

        def late():
            clock[0] += 1000
            return self.page([dict(self.note, id=20000)], **last)

        def stalled(): raise TimeoutError()
        # The second page spends the byte budget mid-body, outlives the deadline, or times out on a deadline-capped socket.
        with patch.object(transport.time, 'monotonic', lambda: clock[0]):
            self.routes[notes(self.issue_path)] = lambda: self.page(full, **{'X-Total': '101', 'X-Next-Page': '2'})
            self.routes[notes(self.issue_path, 2)] = dict(large=large, late=late, stalled=stalled)[following]
            self.issue['user_notes_count'] = 101
            client, snapshot = self.read(**options)
            assert ('notes', 'pagination_incomplete', 'budget_exhausted') in self.states(client)
            assert notes(self.issue_path, 2) in self.targets
            assert not snapshot.checkpoint_ready
            assert snapshot.metadata.coverage.limit_reached
            bound = next(b for b in snapshot.metadata.coverage.scan_bounds if b.dimension == 'bytes')
            assert bound.observed <= bound.limit
            assert 'capture_blocked_or_incomplete' in client.summary['warnings']

    def test_oversized_single_response_fails_closed_before_parsing(self):
        parsed, loads = [], transport.json.loads

        def spy(body, **options):
            parsed.append(len(body)); return loads(body, **options)
        self.routes[notes(self.issue_path)] = lambda: self.page(b'['+b' '*transport.MAX_RESPONSE_BYTES+b']',
            **{'X-Total': '0', 'X-Next-Page': ''})
        self.issue['user_notes_count'] = 0
        with patch.object(transport.json, 'loads', spy), pytest.raises(TransportError): self.read()
        assert parsed
        assert max(parsed) <= transport.MAX_RESPONSE_BYTES

    def paged_notes(self):
        """A 101-note listing whose valid later pages exist, so a refused link or header is the only way a read can fail."""
        collection = f'{self.base}{self.issue_path}/notes'
        full = [dict(self.note, id=10000+index) for index in range(100)]
        self.issue['user_notes_count'] = 101
        later = (notes(self.issue_path, 2), notes(self.issue_path, 3))
        for target in later: self.routes[target] = lambda: self.page([dict(self.note, id=20000)], **{'X-Next-Page': ''})
        return collection, full, later

    def test_valid_link_with_identity_query_is_followed(self):
        collection, full, _ = self.paged_notes()
        self.routes[notes(self.issue_path)] = self.page(full, **{'X-Next-Page': '2',
            'Link': f'<{collection}?page=2&{QUERY}&id=42&noteable_id=7>; rel="next"'})
        assert self.read()[1].checkpoint_ready

    @pytest.mark.parametrize('template', [
        pytest.param('https://other.example.test{issue_path}/notes?page=2&{query}', id='foreign-host'),
        pytest.param('{base}{mr_path}/notes?page=2&{query}', id='other-resource'),
        pytest.param('{collection}?page=3&{query}', id='skipped-page'),
        pytest.param('{collection}?page=2&{query}&private_token=secret', id='credential-in-query'),
        pytest.param('{collection}?page=2&order_by=created_at&sort=asc&per_page=20', id='other-page-size'),
        pytest.param('{collection}?page=2&{query}&id=43', id='other-project-id'),
        pytest.param('{collection}?page=2&{query}&noteable_id=8', id='other-noteable-id')])
    def test_unsafe_next_link_fails_closed(self, template):
        collection, full, later = self.paged_notes()
        target = template.format(base=self.base, issue_path=self.issue_path, mr_path=self.mr_path, collection=collection, query=QUERY)
        self.routes[notes(self.issue_path)] = self.page(full, **{'X-Next-Page': '2', 'Link': f'<{target}>; rel="next"'})
        start = len(self.targets)
        with pytest.raises(TransportError): self.read()
        assert not set(later) & set(self.targets[start:])

    @pytest.mark.parametrize('headers', [
        pytest.param({'X-Next-Page': '3'}, id='skipped-page'), pytest.param({'X-Next-Page': 'two'}, id='not-a-number'),
        pytest.param({'X-Next-Page': '', 'Link': '<{collection}?page=2&{query}>; rel="next"'}, id='link-after-last-page'),
        pytest.param({'X-Page': '2', 'X-Next-Page': ''}, id='wrong-current-page'),
        pytest.param({'X-Per-Page': '20', 'X-Next-Page': ''}, id='wrong-page-size')])
    def test_disagreeing_page_headers_fail_closed(self, headers):
        collection, full, later = self.paged_notes()
        self.routes[notes(self.issue_path)] = self.page(full, **{k: v.format(collection=collection, query=QUERY) for k, v in headers.items()})
        start = len(self.targets)
        with pytest.raises(TransportError): self.read()
        assert not set(later) & set(self.targets[start:])

    def test_iid_and_global_id_stay_distinct_identities(self):
        client, snapshot = self.read()
        issue, mr = self.nodes(client, 'gitlab_issue')[0], self.nodes(client, 'gitlab_merge_request')[0]
        # Issues and merge requests have separate IID sequences; the same IID is two resources.
        assert issue['iid_sha256'] == mr['iid_sha256']
        assert issue['native_id_sha256'] != mr['native_id_sha256']
        assert issue['id_sha256'] != issue['iid_sha256']
        assert issue['id_sha256'] not in {r.native_id_sha256 for r in snapshot.records}
        # A note naming the IID instead of its parent's global ID belongs to another resource.
        self.note['noteable_id'] = 7
        with pytest.raises(TransportError): self.read()
        self.note['noteable_id'] = 700
        self.issue['id'] = 7
        self.routes[notes(self.issue_path)] = [dict(self.note, noteable_id=7), self.routes[notes(self.issue_path)][1] | dict(noteable_id=7)]
        moved, _ = self.read()
        assert self.nodes(moved, 'gitlab_issue')[0]['id_sha256'] != issue['id_sha256']
        assert self.nodes(moved, 'gitlab_issue')[0]['native_id_sha256'] == issue['native_id_sha256']
        self.approvals['id'] = 901
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('change', [pytest.param(dict(noteable_iid=True), id='boolean-iid'),
                                        pytest.param(dict(project_id=True), id='boolean-project-id')])
    def test_boolean_note_identity_is_refused(self, change):
        with patch.dict(self.note, change), pytest.raises(TransportError): self.read()

    def test_global_ids_are_typed_by_resource_and_booleans_are_not_integers(self):
        # Issues, merge requests and milestones have separate global ID sequences; equal numbers are different resources.
        self.issue['milestone'] = dict(id=self.issue['id'])
        client, _ = self.read()
        issue = self.nodes(client, 'gitlab_issue')[0]
        assert issue['id_sha256'] != issue['milestone_sha256']
        assert client._native('Issue', 5) != client._native('MergeRequest', 5)
        from agent_harness_lab.sources.gitlab import absent_or
        assert (absent_or(True, 1), absent_or(None, 1), absent_or(1, 1)) == (False, True, True)
        self.approvals.update(approved_by=[dict(user=None), dict(user=dict(id=55))])
        client, _ = self.read()
        approvals = self.nodes(client, 'gitlab_approvals')[0]
        assert (len(approvals['approvers_sha256']), approvals['approvers_unidentified']) == (1, 1)
        self.note.pop('internal', None); self.note['confidential'] = True
        client, _ = self.read()
        assert any(n.get('internal') is True for n in self.nodes(client, 'gitlab_note'))

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
            assert len(native) == 8
            assert not native & seen
            seen |= native; projects.add(client.summary['project_sha256'])
        assert len(projects) == 4
        assert ('mirror.'+HOST, 443) in [call.args for call in self.wire.call_args_list]
        assert '/gitlab/api/v4/projects/42/issues/7' in self.targets
        # Selecting the same project by numeric ID changes the declared scope but not the native identities.
        by_id = replace(self.selection, project=42)
        assert by_id.project_sha256 != self.selection.project_sha256
        first = {r.native_id_sha256 for r in self.read()[1].records}
        start = len(self.targets)
        assert {r.native_id_sha256 for r in self.read(by_id)[1].records} == first
        assert self.targets[start+1] == '/api/v4/projects/42'

    def test_fork_merge_request_keeps_source_project_identity(self):
        self.mr['source_project_id'] = 77
        client, _ = self.read()
        mr = self.nodes(client, 'gitlab_merge_request')[0]
        assert mr['source_project_state'] == 'other_project'
        assert mr['source_project_sha256'] == self.selection.project_identity(77)
        assert mr['target_project_sha256'] == client.summary['project_sha256']
        assert mr['source_project_sha256'] != mr['target_project_sha256']
        self.mr['target_project_id'] = 77
        with pytest.raises(TransportError): self.read()
        self.mr['target_project_id'] = 42
        self.project['forked_from_project'] = dict(id=5)
        assert self.nodes(self.read()[0], 'gitlab_project')[0]['forked_from_sha256'] == self.selection.project_identity(5)

    @pytest.mark.parametrize('invalid', [pytest.param(0, id='zero'), pytest.param(True, id='boolean'), pytest.param('77', id='string')])
    def test_invalid_source_project_id_is_refused(self, invalid):
        with patch.dict(self.mr, source_project_id=invalid), pytest.raises(TransportError): self.read()

    def test_deleted_source_fork_is_unknown_and_the_capture_continues(self):
        # GitLab sets source_project_id to null once the source fork is deleted (#260).
        self.mr['source_project_id'] = None
        client, _ = self.read()
        mr = self.nodes(client, 'gitlab_merge_request')[0]
        assert (mr['source_project_state'], mr['source_project_sha256']) == ('unknown', None)
        assert mr['target_project_sha256'] == client.summary['project_sha256']
        assert len(self.nodes(client, 'gitlab_issue')) == 1
        assert len(self.nodes(client, 'gitlab_approvals')) == 1
        # Only an explicit null is a deleted fork; a missing key still fails closed as malformed.
        self.mr.pop('source_project_id')
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('invalid', [pytest.param('synthetic-user', id='bare-name'), pytest.param(dict(id=0), id='zero-id'),
                                         pytest.param(dict(id=True), id='boolean-id')])
    def test_malformed_approver_user_is_refused(self, invalid):
        with patch.dict(self.approvals, approved_by=[dict(user=invalid)]), pytest.raises(TransportError): self.read()

    def test_approver_without_a_user_id_is_counted_unidentified(self):
        self.approvals['approved_by'] = [dict(user=dict(username='synthetic-ghost')), dict(user=dict(id=None)),
            dict(user=dict(id=55))]
        client, _ = self.read()
        approvals = self.nodes(client, 'gitlab_approvals')[0]
        assert (len(approvals['approvers_sha256']), approvals['approvers_unidentified']) == (1, 2)
        assert len(self.nodes(client, 'gitlab_merge_request')) == 1

    @pytest.mark.parametrize('path', [
        'synthetic-group/../other', 'synthetic-group/%2E%2E/other', 'synthetic-group%2Fother',
        'synthetic-group/./other', 'synthetic-group//other', '/synthetic-group/other', 'synthetic-group/other/',
        'synthetic-group\\other', 'single', 'synthetic-group/other.git', 'synthetic-group/other.', 'group/a..b',
        'synthetic-group/-other', 'synthetic-group/other?x=1', 'synthetic-group/oth er', '/'.join(['g']*22), 0, True, '42'],
        ids=['dot-dot', 'encoded-dot-dot', 'encoded-slash', 'dot', 'empty-segment', 'leading-slash', 'trailing-slash',
             'backslash', 'single-segment', 'dot-git', 'trailing-dot', 'double-dot', 'leading-dash', 'query', 'space',
             'too-deep', 'zero', 'boolean', 'numeric-string'])
    def test_escaping_project_path_is_refused(self, path):
        with pytest.raises(ValueError): replace(self.selection, project=path)

    def test_nested_group_paths_encode_once_and_escapes_are_refused(self):
        assert self.selection.selector == ENCODED
        self.project['path_with_namespace'] = 'synthetic-group/other-project'
        with pytest.raises(TransportError): self.read()
        self.project['path_with_namespace'] = PATH.upper()
        assert self.read()[1].checkpoint_ready
        self.wire.reset_mock()

    def test_missing_approvals_endpoint_and_version_are_explicit_capability_gaps(self):
        self.routes['/api/v4/version'] = Response(b'{}', 401)
        self.routes[self.mr_path+'/approvals'] = Response(b'SYNTHETIC_SECRET', 404)
        client, snapshot = self.read()
        assert ('approvals', 'unsupported_endpoint', 'approvals_not_found') in self.states(client)
        assert (client.summary['server_version'], client.summary['server_version_state']) == ('unknown', 'unauthorized')
        assert not self.nodes(client, 'gitlab_approvals')
        assert 'unsupported_version' in snapshot.metadata.coverage.gaps
        assert {'approvals_endpoint_unsupported', 'server_version_unknown'} <= set(client.summary['warnings'])
        assert snapshot.checkpoint_ready
        self.routes[self.mr_path+'/approvals'] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read()
        assert ('approvals', 'forbidden', None) in self.states(client)
        assert not snapshot.checkpoint_ready

    @pytest.mark.parametrize('where,status,expected', [
        pytest.param('project', 401, ('project', 'unauthorized', None), id='project-401'),
        pytest.param('project', 403, ('project', 'forbidden', None), id='project-403'),
        pytest.param('project', 404, ('project', 'not_found', None), id='project-404'),
        pytest.param('notes', 403, ('notes', 'forbidden', None), id='notes-403'),
        pytest.param('mr', 404, ('merge_request', 'not_found', None), id='merge-request-404'),
        pytest.param('issue', 429, ('issue', 'rate_limited', None), id='issue-429')])
    def test_permission_failures_and_rate_limits_are_coverage_states_never_success(self, where, status, expected, tmp_path):
        target = dict(project='/api/v4/projects/'+ENCODED, notes=notes(self.issue_path), mr=self.mr_path, issue=self.issue_path)[where]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', status)
        self.targets.clear()
        client, snapshot = self.read()
        assert expected in self.states(client)
        assert not snapshot.checkpoint_ready
        assert 'not_observed' in snapshot.metadata.coverage.gaps
        if status == 429:
            assert self.targets[-1] == target
            assert not self.nodes(client, 'gitlab_issue')
            assert all(state == 'rate_limited' for _, state, _ in self.states(client)[1:])
        store = SourceStore(tmp_path/'journal.json', **self.scope())
        with pytest.raises(ValueError): self.registry().ingest(self.source, store, **self.scope())
        assert not store.path.exists()

    def test_server_error_is_redacted(self):
        self.routes[self.issue_path] = Response(b'SYNTHETIC_SECRET', 500)
        with pytest.raises(TransportError) as error: self.read()
        assert 'SYNTHETIC_SECRET' not in str(error.value)

    @pytest.mark.parametrize('url', [
        'http://'+HOST, f'https://{HOST}/', f'https://{HOST}/api/v4', f'https://user@{HOST}',
        f'https://{HOST}?private_token=secret', 'https://GitLab.example.test', f'https://{HOST}:443', f'https://{HOST}/a/../b'],
        ids=['http', 'trailing-slash', 'api-path', 'userinfo', 'query-credential', 'uppercase-host', 'explicit-port', 'dot-dot'])
    def test_non_canonical_base_url_is_refused(self, url):
        with pytest.raises(ValueError): replace(self.selection, base_url=url)

    @pytest.mark.parametrize('change', [
        dict(capture=('issues',)), dict(capture=('merge_requests', 'issues')), dict(issues=(7, 7)),
        dict(issues=(0,)), dict(capture=('approvals', 'issues', 'notes'), merge_requests=()),
        dict(capture=('issues', 'merge_requests', 'pipelines')), dict(schema_version=8),
        # A capture that selects no IID would read only the project yet look complete.
        dict(capture=('notes',), issues=(), merge_requests=()), dict(capture=(), issues=(), merge_requests=())],
        ids=['issues-only-without-merge-requests', 'unordered-capture', 'duplicate-iid', 'zero-iid',
             'approvals-without-merge-requests', 'unknown-resource', 'schema-version', 'notes-without-iids', 'empty-capture'])
    def test_inconsistent_selection_is_refused(self, change):
        with pytest.raises(ValueError): replace(self.selection, **change)

    def test_non_https_and_non_canonical_instances_are_refused_before_connecting(self):
        with pytest.raises(TransportError): GitLabAPI('https://other.example.test', self.selection)
        assert GitLabSelection.parse(json.loads(json.dumps(self.selection.to_dict()))) == self.selection
        assert GitLabSelection.parse(json.loads(json.dumps(replace(self.selection, project=42).to_dict()))).project == 42
        self.wire.assert_not_called()

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(enabled=False), id='disabled'), pytest.param(dict(scope_sha256=digest('foreign')), id='foreign-scope'),
        pytest.param(dict(root_sha256=digest('https://other.example.test')), id='foreign-root'),
        pytest.param(dict(version='v3'), id='other-version'), pytest.param(dict(provider='gitea'), id='other-provider')])
    def test_disabled_or_foreign_source_is_refused_before_connecting(self, changes):
        with pytest.raises(TransportError): self.read(source=replace(self.source, **changes))
        self.wire.assert_not_called()

    def test_reader_issues_only_get_requests_and_carries_no_mutation_verbs(self):
        self.read()
        assert self.connection.request.call_args_list
        assert all(call.args[0] == 'GET' and call.kwargs.get('body') is None for call in self.connection.request.call_args_list)
        for module in (gitlab, forge):
            text = inspect.getsource(module)
            for verb in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'", 'body='):
                assert verb not in text
        assert all(target.startswith('/api/v4/') for target in self.targets)
        assert not any('/projects?' in target or target.endswith('/projects') or '/groups' in target for target in self.targets)

    def test_repeat_import_is_idempotent_and_updates_add_native_revisions(self, tmp_path):
        registry = self.registry()
        store = SourceStore(tmp_path/'journal.json', **self.scope())
        first = registry.ingest(self.source, store, **self.scope())
        assert first.imported > 0
        again = registry.ingest(self.source, store, **self.scope())
        assert (again.imported, again.cursor_after_sha256) == (0, first.cursor_after_sha256)
        self.note['body'] = 'SYNTHETIC_PRIVATE_EDITED'
        assert registry.ingest(self.source, store, **self.scope()).imported == 1
        self.mr['sha'] = 'e'*40
        self.approvals.update(approved=False, approvals_left=1, approved_by=[])
        changed = registry.ingest(self.source, store, **self.scope())
        assert changed.imported == 2
        assert changed.cursor_after_sha256 != first.cursor_after_sha256
        text = store.path.read_text()
        for secret in SECRETS: assert secret not in text

    @pytest.mark.parametrize('change', [
        pytest.param(dict(noteable_type='MergeRequest'), id='other-noteable-type'), pytest.param(dict(project_id=43), id='other-project'),
        pytest.param(dict(noteable_iid=8), id='other-iid'), pytest.param(dict(system='no'), id='string-system-flag')])
    def test_note_identity_conflict_fails_closed(self, change):
        with patch.dict(self.note, change), pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('change', [
        pytest.param(dict(project_id=43), id='other-project'), pytest.param(dict(iid=8), id='other-iid'),
        pytest.param(dict(state='SYNTHETIC_STATE'), id='unknown-state')])
    def test_issue_identity_conflict_fails_closed(self, change):
        with patch.dict(self.issue, change), pytest.raises(TransportError): self.read()

    def test_identity_parent_and_scope_conflicts_fail_closed(self):
        # The readback by numeric ID must name the project the path resolved to.
        self.routes[self.root] = lambda: dict(self.project, id=43)
        with pytest.raises(TransportError): self.read()
        self.routes[self.root] = lambda: dict(self.project, path_with_namespace='synthetic-group/renamed-project')
        with pytest.raises(TransportError): self.read()
        self.routes[self.root] = self.project
        self.project['visibility'] = 'private'
        with pytest.raises(TransportError): self.read()


class TestGitLabConfiguration(GitLabFixture):
    @pytest.fixture(autouse=True)
    def _configuration(self, _gitlab, tmp_path):
        self.dir = tmp_path
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
        for secret in (*SECRETS, self.base, str(self.dir)): assert secret not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_v5_configured_closing_issue_preview_and_import(self):
        self.selection = replace(self.selection, schema_version=5, capture=('closing_issues', 'issues', 'merge_requests'))
        self.source = self.spec(self.selection, auth_mode='configured')
        self.write()
        self.routes[self.mr_path+'/closes_issues?page=1&per_page=100'] = [dict(id=700, iid=7, project_id=42,
            title='SYNTHETIC_PRIVATE_TITLE', description='SYNTHETIC_PRIVATE_BODY')]
        env = ('--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')
        with patch.dict(os.environ, SYNTHETIC_GITLAB_AUTHORIZATION='Bearer SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert code == 0 and result['checkpoint_ready']
            assert any(c['resource'] == 'closing_issues' and c['state'] == 'captured' for c in result['gitlab_summary']['coverage'])
            assert self.invoke('import', *env)[0] == 0
            assert self.invoke('import', *env)[1]['imported'] == 0
            assert 'SYNTHETIC_PRIVATE_BODY' not in self.journal.read_text()

    def test_v4_configured_commit_preview_and_import(self):
        self.selection = replace(self.selection, schema_version=4, capture=('commits', 'merge_requests'), issues=())
        self.source = self.spec(self.selection, auth_mode='configured')
        self.write()
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = [dict(id='c'*40, message='SYNTHETIC_PRIVATE_BODY')]
        env = ('--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')
        with patch.dict(os.environ, SYNTHETIC_GITLAB_AUTHORIZATION='Bearer SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert code == 0 and result['checkpoint_ready']
            assert any(c['resource'] == 'commits' and c['state'] == 'captured' for c in result['gitlab_summary']['coverage'])
            assert self.invoke('import', *env)[0] == 0
            assert self.invoke('import', *env)[1]['imported'] == 0
            text = self.journal.read_text()
            assert 'c'*40 not in text and 'SYNTHETIC_PRIVATE_BODY' not in text

    def test_v3_configured_issue_discussions_preview_and_import(self):
        self.selection = replace(self.selection, schema_version=3, capture=('discussions', 'issues'), merge_requests=())
        self.source = self.spec(self.selection, auth_mode='configured')
        self.write()
        self.routes[self.issue_path+'/discussions?page=1&per_page=100'] = [
            dict(id='synthetic-thread', individual_note=True, notes=[dict(self.note)])]
        env = ('--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')
        with patch.dict(os.environ, SYNTHETIC_GITLAB_AUTHORIZATION='Bearer SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert code == 0 and result['checkpoint_ready']
            assert any(c['resource'] == 'discussions' and c['state'] == 'captured'
                for c in result['gitlab_summary']['coverage'])
            assert self.invoke('import', *env)[0] == 0
            assert self.invoke('import', *env)[1]['imported'] == 0
            assert 'synthetic-thread' not in self.journal.read_text()

    def test_v2_configured_discussion_preview_and_import(self):
        self.selection = replace(self.selection, schema_version=2,
            capture=('approvals', 'discussions', 'issues', 'merge_requests', 'notes'))
        self.source = self.spec(self.selection, auth_mode='configured')
        self.write()
        self.routes[self.mr_path+'/discussions?page=1&per_page=100'] = [
            dict(id='synthetic-thread', individual_note=True, notes=[dict(self.routes[notes(self.mr_path)][0])])]
        env = ('--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')
        with patch.dict(os.environ, SYNTHETIC_GITLAB_AUTHORIZATION='Bearer SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert code == 0 and result['checkpoint_ready']
            assert any(c['resource'] == 'discussions' and c['state'] == 'captured'
                for c in result['gitlab_summary']['coverage'])
            assert self.invoke('import', *env)[0] == 0
            assert self.invoke('import', *env)[1]['imported'] == 0
            assert 'synthetic-thread' not in self.journal.read_text()

    def test_cli_probe_preview_and_import_read_only_the_named_variable(self):
        env = ('--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')
        code, result = self.invoke('probe', *env)
        assert (code, result['status'], result['network_used']) == (0, 'recognized', False)
        self.wire.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            assert self.invoke('preview', *env)[0] == 3
        self.wire.assert_not_called()
        with patch.dict(os.environ, SYNTHETIC_GITLAB_AUTHORIZATION='Bearer SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert code == 0
            assert result['checkpoint_ready']
            assert result['gitlab_summary']['server_version'] == '17.0.0-ee'
            assert all(call.kwargs['headers']['Authorization'] == 'Bearer SYNTHETIC_SECRET'
                for call in self.connection.request.call_args_list)
            assert self.invoke('import', *env)[0] == 0
            original = self.journal.read_bytes()
            self.routes[self.issue_path] = Response(b'SYNTHETIC_SECRET', 403)
            code, result = self.invoke('import', *env)
            assert (code, result['status'], result['checkpoint_committed']) == (3, 'blocked', False)
            assert ('issue', 'forbidden') in [(c['resource'], c['state']) for c in result['gitlab_summary']['coverage']]
            assert self.journal.read_bytes() == original
            assert self.invoke('status')[0] == 0
            assert self.invoke('preview', *env, '--authorization-file', str(self.credential))[0] == 2
        self.credential.write_text('Bearer SYNTHETIC_SECRET\n')
        self.routes[self.issue_path] = self.issue
        assert self.invoke('preview', '--authorization-file', str(self.credential))[0] == 0

    @pytest.mark.parametrize('change', [
        pytest.param(lambda self: dict(endpoint='https://other.example.test'), id='other-endpoint'),
        pytest.param(lambda self: dict(limits=dict(retries=3)), id='unknown-limit'),
        pytest.param(lambda self: dict(selection={**self.selection.to_dict(), 'project': 'synthetic-group/other-project'}),
                     id='other-project')])
    def test_configuration_rejects_mismatched_declaration(self, change):
        connector = GitLabConfigured(self.manifest)
        assert connector.declaration(**self.scope())[0] == self.source
        self.write(**change(self))
        with pytest.raises(ValueError): connector.declaration(**self.scope())

    def test_configuration_rejects_mismatched_identity(self):
        connector = GitLabConfigured(self.manifest)
        assert connector.declaration(**self.scope())[0] == self.source
        self.write()
        self.source = replace(self.source, provider='github'); self.write()
        assert self.invoke('probe', '--authorization-env', 'SYNTHETIC_GITLAB_AUTHORIZATION')[0] == 2
        self.wire.assert_not_called()
