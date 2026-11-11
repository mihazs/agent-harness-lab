"""Synthetic classification checks; never access GitHub from tests."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import json

import pytest

spec = importlib.util.spec_from_file_location('project_sync', Path(__file__).resolve().parents[3] / 'scripts/sync_project.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def test_label_mapping_removal_and_conflict():
    assert (sync.mirrored_values({'priority: P0', 'track: science', 'roadmap: now', 'help wanted'})
            == {'Priority': 'P0', 'Workstream': 'Scientific Reliability', 'Roadmap': 'Now'})
    assert sync.mirrored_values(set()) == {'Priority': None, 'Workstream': None, 'Roadmap': None}
    for labels in ({'priority: P0', 'priority: P1'}, {'track: science', 'track: product'},
                   {'roadmap: now', 'roadmap: next'}):
        with pytest.raises(ValueError):
            sync.mirrored_values(labels)


def test_review_does_not_turn_approval_into_passing_ci():
    pr = dict(isDraft=False, reviewDecision='APPROVED', mergeStateStatus='BLOCKED',
              commits={'nodes': [{'commit': {'statusCheckRollup': {'state': 'PENDING'}}}]})
    assert sync.review_summary(pr) == 'APPROVED; CI PENDING; merge BLOCKED'
    pr['commits']['nodes'][0]['commit']['statusCheckRollup'] = None
    pr['isDraft'] = True
    assert sync.review_summary(pr) == 'DRAFT; CI NOT_REPORTED; merge BLOCKED'


def test_issue_closure_reopening_and_manual_readiness():
    issue = dict(__typename='Issue', state='CLOSED', stateReason='COMPLETED')
    assert sync.lifecycle_status(issue, 'Ready') == 'Done'
    for reason in ('NOT_PLANNED', None):
        issue['stateReason'] = reason
        assert sync.lifecycle_status(issue, 'Done') == 'Backlog'
    issue.update(state='OPEN', stateReason='REOPENED')
    assert sync.lifecycle_status(issue, 'Done') == 'Backlog'
    for status in ('Ready', 'In Progress', 'In Review', 'Blocked', 'Backlog'):
        assert sync.lifecycle_status(issue, status) == status


def test_pr_merge_decline_reopen_and_draft():
    pr = dict(__typename='PullRequest', state='MERGED', isDraft=False)
    assert sync.lifecycle_status(pr, 'In Review') == 'Done'
    pr['state'] = 'CLOSED'
    assert sync.lifecycle_status(pr, 'Done') == 'Backlog'
    pr['state'] = 'OPEN'
    assert sync.lifecycle_status(pr, 'Done') == 'In Review'
    assert sync.lifecycle_status(pr, 'Backlog') == 'In Review'
    assert sync.lifecycle_status(pr, 'Blocked') == 'Blocked'
    pr['isDraft'] = True
    assert sync.lifecycle_status(pr, 'In Review') == 'Backlog'
    assert sync.lifecycle_status(pr, 'In Progress') == 'In Progress'


def test_pagination_validates_all_labels_before_lifecycle_writes():
    fields = [{'id': name, 'name': name,
               'options': [{'id': v, 'name': v} for v in mapping.values()]}
              for name, mapping in sync.AXES.items()]
    fields.append({'id': 'Status', 'name': 'Status',
                   'options': [{'id': n, 'name': n} for n in ('Backlog', 'Done')]})
    project = {'id': 'synthetic-project',
               'repositories': {'nodes': [{'nameWithOwner': sync.REPO}],
                                'pageInfo': {'hasNextPage': False}},
               'fields': {'nodes': fields, 'pageInfo': {'hasNextPage': False}}}

    def item(ident, reason, status):
        return {'id': ident, 'content': {
            '__typename': 'Issue', 'state': 'CLOSED', 'stateReason': reason,
            'repository': {'nameWithOwner': sync.REPO},
            'labels': {'nodes': [], 'pageInfo': {'hasNextPage': False}}},
            'fieldValues': {'nodes': [{'name': status, 'field': {'name': 'Status'}}],
                            'pageInfo': {'hasNextPage': False}}}

    first = item('one', 'COMPLETED', 'Ready')
    second = item('two', 'NOT_PLANNED', 'Done')
    writes = []
    cursors = []

    def api(query, variables):
        if query.startswith('query {user'):
            return {'user': {'projectV2': project}}
        if query.startswith('mutation'):
            writes.append(variables['input'])
            return {}
        cursor = variables['cursor']
        cursors.append(cursor)
        return {'node': {'items': {'nodes': [first if cursor is None else second],
                'pageInfo': {'hasNextPage': cursor is None, 'endCursor': 'page-two'}}}}

    with patch.object(sync, 'graphql', side_effect=api), patch('sys.argv', ['sync', '--apply']), redirect_stdout(io.StringIO()):
        second['content']['labels']['nodes'] = [{'name': 'priority: P0'}, {'name': 'priority: P1'}]
        with pytest.raises(ValueError):
            sync.main()
        assert writes == []
        assert cursors == [None, 'page-two']
        second['content']['labels']['nodes'] = []
        sync.main()
    assert [(v['itemId'], v['value']['singleSelectOptionId']) for v in writes] == [('one', 'Done'), ('two', 'Backlog')]



def gh_output(status, headers, body):
    head = f'HTTP/2.0 {status} Synthetic\n' + ''.join(f'{k}: {v}\n' for k, v in headers.items())
    return type('Result', (), dict(stdout=head + '\n' + json.dumps(body), returncode=0 if status == 200 else 1))()


def test_graphql_stops_at_the_reserve_and_reports_rate_limits_not_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    monkeypatch.setattr(sync, 'LIMITER', None)
    reset = str(int(__import__('time').time()) + 600)
    limits = {'x-ratelimit-limit': '5000', 'x-ratelimit-remaining': '250', 'x-ratelimit-reset': reset, 'x-ratelimit-resource': 'graphql'}
    with patch.object(sync.subprocess, 'run', return_value=gh_output(200, limits, {'data': {'ok': 1}})) as run:
        assert sync.graphql('query {a}', {}) == {'ok': 1}
        assert '-i' in run.call_args.args[0]
        # The shared reserve (5% of 5,000) is reached: no further request is sent.
        with pytest.raises(RuntimeError, match='halted'):
            sync.graphql('query {a}', {})
        assert run.call_count == 1
    monkeypatch.setattr(sync, 'LIMITER', None)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'fresh'))
    limited = gh_output(403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': reset}, {'message': 'API rate limit exceeded'})
    with patch.object(sync.subprocess, 'run', return_value=limited), pytest.raises(RuntimeError, match='not a permission problem'):
        sync.graphql('query {a}', {})
    monkeypatch.setattr(sync, 'LIMITER', None)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'denied'))
    denied = gh_output(403, {'x-ratelimit-remaining': '4000'}, {'message': 'Resource not accessible'})
    with patch.object(sync.subprocess, 'run', return_value=denied), pytest.raises(ValueError):
        sync.graphql('query {a}', {})


def test_graphql_keeps_the_existing_shape_for_ordinary_responses(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    monkeypatch.setattr(sync, 'LIMITER', None)
    with patch.object(sync.subprocess, 'run', return_value=gh_output(200, {}, {'data': {'x': 1}})):
        assert sync.graphql('query {a}', {}) == {'x': 1}
