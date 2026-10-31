"""Synthetic classification checks; never access GitHub from tests."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io

spec = importlib.util.spec_from_file_location('project_sync', Path(__file__).resolve().parents[2] / 'scripts/sync_project.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class ProjectSyncTests(unittest.TestCase):
    def test_label_mapping_removal_and_conflict(self):
        self.assertEqual(sync.mirrored_values({'priority: P0', 'track: science', 'roadmap: now', 'help wanted'}),
                         {'Priority': 'P0', 'Workstream': 'Scientific Reliability', 'Roadmap': 'Now'})
        self.assertEqual(sync.mirrored_values(set()), {'Priority': None, 'Workstream': None, 'Roadmap': None})
        for labels in ({'priority: P0', 'priority: P1'}, {'track: science', 'track: product'},
                       {'roadmap: now', 'roadmap: next'}):
            with self.assertRaises(ValueError):
                sync.mirrored_values(labels)

    def test_review_does_not_turn_approval_into_passing_ci(self):
        pr = dict(isDraft=False, reviewDecision='APPROVED', mergeStateStatus='BLOCKED',
                  commits={'nodes': [{'commit': {'statusCheckRollup': {'state': 'PENDING'}}}]})
        self.assertEqual(sync.review_summary(pr), 'APPROVED; CI PENDING; merge BLOCKED')
        pr['commits']['nodes'][0]['commit']['statusCheckRollup'] = None
        pr['isDraft'] = True
        self.assertEqual(sync.review_summary(pr), 'DRAFT; CI NOT_REPORTED; merge BLOCKED')

    def test_issue_closure_reopening_and_manual_readiness(self):
        issue = dict(__typename='Issue', state='CLOSED', stateReason='COMPLETED')
        self.assertEqual(sync.lifecycle_status(issue, 'Ready'), 'Done')
        for reason in ('NOT_PLANNED', None):
            issue['stateReason'] = reason
            self.assertEqual(sync.lifecycle_status(issue, 'Done'), 'Backlog')
        issue.update(state='OPEN', stateReason='REOPENED')
        self.assertEqual(sync.lifecycle_status(issue, 'Done'), 'Backlog')
        for status in ('Ready', 'In Progress', 'In Review', 'Blocked', 'Backlog'):
            self.assertEqual(sync.lifecycle_status(issue, status), status)

    def test_pr_merge_decline_reopen_and_draft(self):
        pr = dict(__typename='PullRequest', state='MERGED', isDraft=False)
        self.assertEqual(sync.lifecycle_status(pr, 'In Review'), 'Done')
        pr['state'] = 'CLOSED'
        self.assertEqual(sync.lifecycle_status(pr, 'Done'), 'Backlog')
        pr['state'] = 'OPEN'
        self.assertEqual(sync.lifecycle_status(pr, 'Done'), 'In Review')
        self.assertEqual(sync.lifecycle_status(pr, 'Backlog'), 'In Review')
        self.assertEqual(sync.lifecycle_status(pr, 'Blocked'), 'Blocked')
        pr['isDraft'] = True
        self.assertEqual(sync.lifecycle_status(pr, 'In Review'), 'Backlog')
        self.assertEqual(sync.lifecycle_status(pr, 'In Progress'), 'In Progress')


    def test_pagination_validates_all_labels_before_lifecycle_writes(self):
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
            with self.assertRaises(ValueError):
                sync.main()
            self.assertEqual(writes, [])
            self.assertEqual(cursors, [None, 'page-two'])
            second['content']['labels']['nodes'] = []
            sync.main()
        self.assertEqual([(v['itemId'], v['value']['singleSelectOptionId']) for v in writes],
                         [('one', 'Done'), ('two', 'Backlog')])



if __name__ == '__main__':
    unittest.main()
