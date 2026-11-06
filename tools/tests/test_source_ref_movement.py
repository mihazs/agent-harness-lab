"""Synthetic local Git ref movement; a non-fast-forward move never proves a force-push."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.ref_movement import ref_movement
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore

GIT = Path('/usr/bin/git')


@unittest.skipUnless(GIT.is_file(), 'explicit system Git is required')
class RefMovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = subprocess.check_output([str(GIT), '--version'], env={'PATH': '/usr/bin:/bin'})
        match = re.match(rb'git version 2\.(\d+)\.', raw)
        if not match or int(match[1]) < 43: raise unittest.SkipTest('Git 2.43+ in major version 2 is required')

    def setUp(self):
        fixtures = Path.home()/'dev/.worktrees'; fixtures.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='ahl-ref-fixture-', dir=fixtures); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.repo = self.root/'repo'; self.repo.mkdir()
        self.env = dict(PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
            GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid',
            GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
        self.git('init', '-b', 'main')
        self.commit('SYNTHETIC_ROOT')
        self.selection = dict(schema_version=1, checkout='repo', git_dir='repo/.git', common_dir='repo/.git',
            harness_paths=['AGENTS.md'], max_commits=100)
        self.cache = self.root/'private/cache.json'
        self.declare()

    def git(self, *arguments):
        return subprocess.check_output([str(GIT), '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-c', 'commit.gpgsign=false', '-c', 'maintenance.auto=false', *arguments],
            cwd=self.repo, env=self.env, stderr=subprocess.DEVNULL).decode().strip()

    def commit(self, text):
        (self.repo/'AGENTS.md').write_text(text+'\n')
        self.git('add', '--all'); self.git('commit', '-m', 'SYNTHETIC_PRIVATE_MESSAGE '+text)
        return self.git('rev-parse', 'HEAD')

    def declare(self, version='v2'):
        self.source = SourceSpec('local_git', 'git', version, digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        (self.root/'source.json').write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))

    def capture(self):
        out, error = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(error):
            code = main(['sources', 'import', '--manifest', str(self.root/'source.json'), '--cache', str(self.cache),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256])
        self.assertEqual(code, 0, error.getvalue())
        return self.store().status(self.source)['cursor_sha256']

    def store(self):
        return SourceStore(self.cache, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)

    def movement(self, ref_name='refs/heads/main', **checkpoints):
        journal = self.cache.read_bytes() if self.cache.exists() else None
        with patch('subprocess.Popen', side_effect=AssertionError('no Git during movement')), \
                patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            result = ref_movement(self.source, self.store(), ref_name=ref_name, **checkpoints)
        self.assertEqual(self.cache.read_bytes() if self.cache.exists() else None, journal)
        output = json.dumps(result)
        for private in ('SYNTHETIC', str(self.root), 'refs/heads', 'refs/tags', *self.git('rev-list', '--all').split()):
            self.assertNotIn(private, output)
        for name in ('remote_state_verified', 'intent_verified', 'task_outcomes_verified', 'execution_authorized',
                'publication_allowed', 'source_read', 'network_used'):
            self.assertIs(result[name], False, name)
        self.assertEqual((result['cause'], result['repository_identity']), ('unknown', 'caller_declared'))
        self.assertIsNone(result['independent_roots'])
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        for report in (result, {**result, 'publication_allowed': True}, dict(analysis=dict(profile=result['profile']))):
            self.assertFalse(public_export_allowed([{**report, 'provenance': provenance}]))
        return result

    def assertMovement(self, result, status, reason):
        self.assertEqual((result['status'], result['reason']), (status, reason))

    def test_new_commit_is_a_fast_forward_for_the_branch_and_head(self):
        first = self.capture(); self.commit('SYNTHETIC_NEXT'); second = self.capture()
        for name in ('refs/heads/main', 'HEAD'):
            with self.subTest(name=name):
                result = self.movement(name)
                self.assertMovement(result, 'fast_forward', 'earlier_target_is_captured_ancestor')
                self.assertEqual((result['before']['checkpoint_sha256'], result['after']['checkpoint_sha256']), (first, second))
                self.assertEqual((result['before']['target'], result['after']['target']), ('captured_commit', 'captured_commit'))
                self.assertEqual(result['ref_sha256'], digest(name))

    def test_amended_or_reset_history_is_non_fast_forward_without_a_claimed_cause(self):
        self.commit('SYNTHETIC_NEXT'); self.capture()
        self.git('reset', '--hard', 'HEAD~1'); self.commit('SYNTHETIC_REWRITTEN'); self.capture()
        for name in ('refs/heads/main', 'HEAD'):
            with self.subTest(name=name):
                self.assertMovement(self.movement(name), 'non_fast_forward',
                    'earlier_target_absent_from_complete_captured_ancestry')

    def test_history_ceiling_leaves_a_rewrite_unknown_but_keeps_a_direct_parent(self):
        self.selection['max_commits'] = 1; self.declare()
        self.commit('SYNTHETIC_NEXT'); self.capture()
        self.git('reset', '--hard', 'HEAD~1'); self.commit('SYNTHETIC_REWRITTEN'); self.capture()
        self.assertMovement(self.movement(), 'unknown', 'later_ancestry_incomplete')
        self.commit('SYNTHETIC_AHEAD'); self.capture()
        result = self.movement()
        self.assertMovement(result, 'fast_forward', 'earlier_target_is_captured_ancestor')
        self.assertTrue(result['after']['capture_truncated'])

    def test_single_truncated_capture_reports_its_real_later_side(self):
        self.selection['max_commits'] = 1; self.declare()
        self.commit('SYNTHETIC_NEXT'); only = self.capture()
        result = self.movement()
        self.assertMovement(result, 'unknown', 'no_earlier_checkpoint')
        self.assertEqual(result['after'], dict(checkpoint_sha256=only, capture_truncated=True, target='captured_commit'))
        self.assertIsNone(result['before']['capture_truncated'])

    def test_annotated_tag_moved_to_a_descendant_commit_stays_unknown(self):
        self.git('tag', '-a', 'synthetic-release', '-m', 'SYNTHETIC_TAG'); self.capture()
        self.commit('SYNTHETIC_NEXT'); self.git('tag', '-f', 'synthetic-release'); self.capture()
        result = self.movement('refs/tags/synthetic-release')
        self.assertMovement(result, 'unknown', 'earlier_target_not_captured_commit')
        self.assertEqual((result['before']['target'], result['after']['target']), ('reference_only', 'captured_commit'))

    def test_unchanged_appeared_disappeared_and_uncaptured_targets_stay_descriptive(self):
        self.git('branch', 'side'); self.git('branch', 'gone'); first = self.capture()
        self.git('branch', '-D', 'gone'); self.git('tag', '-a', 'synthetic-tag', '-m', 'SYNTHETIC_TAG')
        self.git('checkout', '-q', 'side'); self.commit('SYNTHETIC_SIDE'); self.git('checkout', '-q', 'main')
        self.commit('SYNTHETIC_MAIN'); second = self.capture()
        self.assertEqual(self.capture(), second)
        self.assertEqual(self.store().status(self.source)['checkpoints'], 2)
        self.assertMovement(self.movement('refs/heads/gone'), 'disappeared', 'absent_from_later_checkpoint')
        self.assertMovement(self.movement('refs/tags/synthetic-tag'), 'appeared', 'absent_from_earlier_checkpoint')
        self.assertMovement(self.movement('refs/heads/missing'), 'unobserved', 'ref_not_captured')
        side = self.movement('refs/heads/side')
        self.assertMovement(side, 'unknown', 'later_target_history_not_captured')
        self.assertEqual((side['before']['target'], side['after']['target']), ('captured_commit', 'reference_only'))
        self.git('commit', '--allow-empty', '-m', 'SYNTHETIC_EMPTY'); third = self.capture()
        for name in ('refs/tags/synthetic-tag', 'refs/heads/side'):
            with self.subTest(name=name):
                self.assertMovement(self.movement(name), 'unchanged', 'same_object_identity')
        result = self.movement(before=first, after=third)
        self.assertMovement(result, 'fast_forward', 'earlier_target_is_captured_ancestor')
        self.assertEqual((result['before']['checkpoint_sha256'], result['after']['checkpoint_sha256']), (first, third))
        self.assertEqual(self.movement(after=second)['before']['checkpoint_sha256'], first)

    def test_single_checkpoint_invalid_inputs_and_v1_sources_are_refused_or_unknown(self):
        empty = self.movement()
        self.assertMovement(empty, 'unknown', 'no_checkpoint')
        for side in ('before', 'after'):
            self.assertEqual((empty[side]['target'], empty[side]['capture_truncated']), ('unavailable', None))
        first = self.capture()
        result = self.movement()
        self.assertMovement(result, 'unknown', 'no_earlier_checkpoint')
        self.assertEqual(result['before'], dict(checkpoint_sha256=None, capture_truncated=None, target='unavailable'))
        self.assertEqual(result['after'], dict(checkpoint_sha256=first, capture_truncated=False, target='captured_commit'))
        self.commit('SYNTHETIC_NEXT'); second = self.capture()
        for arguments, message in ((dict(ref_name='main'), 'invalid Git reference'),
                (dict(ref_name='refs/heads/../x'), 'invalid Git reference'),
                (dict(ref_name='refs/heads/main', before=second, after=first), 'must precede'),
                (dict(ref_name='refs/heads/main', before=digest('foreign')), 'not in the selected'),
                (dict(ref_name='refs/heads/main', after='not-a-fingerprint'), 'fingerprint')):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, message):
                ref_movement(self.source, self.store(), **arguments)
        self.declare('v1')
        with self.assertRaisesRegex(ValueError, 'local_git/git/v2'):
            ref_movement(self.source, self.store(), ref_name='refs/heads/main')


if __name__ == '__main__':
    unittest.main()
