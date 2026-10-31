"""Synthetic classification checks; never access GitHub from tests."""
import importlib.util
from pathlib import Path
import unittest

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


if __name__ == '__main__':
    unittest.main()
