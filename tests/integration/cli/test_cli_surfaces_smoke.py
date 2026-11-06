"""CLI smoke of the generic surface matrix on a synthetic checkout, and controller deferred lists derived from it.

The matrix is checkout-independent: it is one synthetic path per surface and runtime. Per-node writability and
routes in the analyze and improve outputs are not covered here; #292 tracks them.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.support import harness_surface_fixture as fixture
from agent_harness_lab import hooks_contract, one_call, surfaces

ROOT = Path(__file__).resolve().parents[3]
# Expected (writability, route) per runtime; written out so a classifier change shows up here, not only in docs.
EXPECTED = {
    'instructions': {'claude-code': ('adoptable', 'adopt'), 'codex': ('adoptable', 'adopt')},
    'path_rules': {'claude-code': ('adoptable', 'adopt'), 'codex': ('recommendation_only', 'recommendation')},
    'hooks': {'claude-code': ('contract_required', 'contract'), 'codex': ('contract_required', 'contract')},
    'workflows': {'claude-code': ('contract_required', 'contract'), 'codex': (None, None)},
    'agents': {'claude-code': ('adoptable', 'contract'), 'codex': ('adoptable', 'contract')},
    'commands_skills': {'claude-code': ('adoptable', 'adopt'), 'codex': ('adoptable', 'adopt')},
    'plugins': {'claude-code': ('recommendation_only', 'recommendation'), 'codex': ('recommendation_only', 'recommendation')},
    'mcp': {'claude-code': ('recommendation_only', 'recommendation'), 'codex': ('recommendation_only', 'recommendation')},
    'output_styles': {'claude-code': ('recommendation_only', 'recommendation'),
                      'codex': ('recommendation_only', 'recommendation')},
    'memory': {'claude-code': ('private_excluded', 'excluded'), 'codex': ('private_excluded', 'excluded')},
    'settings': {'claude-code': ('recommendation_only', 'recommendation'), 'codex': ('recommendation_only', 'recommendation')},
}


class SurfaceMatrixCLISmoke(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.checkout = fixture.build(self.base / 'checkout', hostile=False)
        self.before = fixture.tree_state(self.checkout)

    def run_cli(self, runtime, version=None, code=0, checkout=None):
        environment = dict(os.environ, XDG_STATE_HOME=str(self.base / 'state'), PYTHONPATH=str(ROOT))
        completed = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'sources', 'inventory',
                                    str(checkout or self.checkout), '--runtime', runtime,
                                    '--version', version or surfaces.PINS[runtime]],
                                   capture_output=True, text=True, env=environment, cwd=self.base, timeout=60)
        self.assertEqual((completed.returncode, completed.stderr), (code, ''))
        return completed.stdout, json.loads(completed.stdout)

    def test_unpinned_version_prints_no_matrix(self):
        # The matrix rests on research at the pins; an unsupported version must not present it as fact.
        for runtime in surfaces.RUNTIMES:
            with self.subTest(runtime=runtime):
                _, result = self.run_cli(runtime, version='9.9.9', code=3)
                self.assertEqual((result['status'], result['nodes'], result['surfaces']), ('unsupported', 0, []))

    def test_only_private_excluded_files_go_unread(self):
        # An unsupported verdict with a known kind is read and hashed; only private_excluded stays unread.
        checkout = self.base / 'boundary'
        for relative in ('.claude/rules/style.md', 'CLAUDE.local.md'):
            (checkout / relative).parent.mkdir(parents=True, exist_ok=True)
            (checkout / relative).write_text('synthetic\n')
        self.assertEqual(surfaces.classify('.claude/rules/style.md', 'claude-code').writability, 'unsupported')
        self.assertEqual(surfaces.classify('CLAUDE.local.md', 'claude-code').writability, 'private_excluded')
        _, result = self.run_cli('claude-code', checkout=checkout)
        nodes = {node['path']: node for node in json.loads(Path(result['output']).read_text())['nodes']}
        self.assertIsNotNone(nodes['.claude/rules/style.md']['content_sha256'])
        self.assertEqual(nodes['CLAUDE.local.md']['parse_status'], 'not_read')
        self.assertIsNone(nodes['CLAUDE.local.md']['content_sha256'])
        self.assertEqual(surfaces.ROUTES['private_excluded'], 'excluded')
        self.assertNotEqual(surfaces.ROUTES['unsupported'], 'excluded')
        for relative in ('README.md', 'skills/harness-self-improvement/references/surfaces.md'):
            with self.subTest(document=relative):
                text = ' '.join((ROOT / relative).read_text().split())
                self.assertNotIn('`unsupported`): the file is never read', text)
                self.assertNotIn('*excluded* files are never read', text)

    def test_inventory_cli_prints_all_eleven_rows_with_expected_writability(self):
        for runtime in surfaces.RUNTIMES:
            with self.subTest(runtime=runtime):
                stdout, result = self.run_cli(runtime)
                self.assertEqual(result['status'], 'declared')
                rows = result['surfaces']
                self.assertEqual(len(rows), 11)
                self.assertEqual([row['surface'] for row in rows], list(surfaces.SURFACES))
                self.assertEqual({row['surface']: (row['writability'], row['route']) for row in rows},
                                 {surface: cells[runtime] for surface, cells in EXPECTED.items()})
                # Path-free: neither fixture content nor any matrix or checkout path reaches stdout.
                self.assertNotIn(fixture.SECRET, stdout)
                for cells in surfaces.MATRIX:
                    for entry in cells[1].values():
                        if entry: self.assertNotIn(entry[0], stdout)
                self.assertNotIn(str(self.checkout), stdout)
        self.assertEqual(fixture.tree_state(self.checkout), self.before)

    def test_matrix_writability_is_the_classifier_verdict(self):
        self.assertEqual({row['surface']: {rt: (row['writability'][rt], row['route'][rt]) for rt in surfaces.RUNTIMES}
                          for row in surfaces.matrix()}, EXPECTED)
        # A kind-scoped verdict never weakens the whole-file classifier: the shared settings file stays a hook file.
        self.assertEqual(surfaces.classify('.claude/settings.json', 'claude-code', in_checkout=True).writability,
                         'contract_required')
        with patch.object(surfaces, 'MATRIX', surfaces.MATRIX[:-1]), self.assertRaises(ValueError): surfaces.matrix()

    def test_documented_matrices_match_the_code(self):
        def cell(writability, route): return 'no file surface' if writability is None else f'`{writability}` / {route}'
        expected = [(row['surface'], *(cell(row['writability'][rt], row['route'][rt]) for rt in surfaces.RUNTIMES))
                    for row in surfaces.matrix()]
        for relative in ('README.md', 'skills/harness-self-improvement/references/surfaces.md'):
            with self.subTest(document=relative):
                text = (ROOT / relative).read_text()
                rows = re.findall(r'^\| `(\w+)` \| ([^|]+?) \| ([^|]+?) \|$', text, re.MULTILINE)
                self.assertEqual(rows[:len(expected)], expected)


class DeferredSurfacesFromMatrix(unittest.TestCase):
    def test_adopted_and_deferred_follow_the_matrix(self):
        self.assertEqual(surfaces.adopted(), ('instructions', 'path_rules', 'commands_skills'))
        # Memory is excluded for every runtime, so it is never deferred.
        self.assertEqual(surfaces.deferred(surfaces.adopted()),
                         ('hooks', 'workflows', 'agents', 'plugins', 'mcp', 'output_styles', 'settings'))
        with self.assertRaises(ValueError): surfaces.deferred(('unknown',))

    def test_contract_path_defers_every_surface_but_skill_headers(self):
        surface_part = ['instructions', 'path_rules', 'hooks', 'workflows', 'agents', 'plugins', 'mcp',
                        'output_styles', 'settings']
        self.assertEqual(one_call.deferred_surfaces(()),
                         ['behavioral optimization', 'skill lifecycle and routing', *surface_part])

        class Passed: status, profile = 'passed', hooks_contract.PROFILE
        class Blocked: status, profile = 'blocked', hooks_contract.PROFILE
        class Workflow: status, profile = 'passed', hooks_contract.WORKFLOW_PROFILE
        self.assertNotIn('hooks', one_call.deferred_surfaces((Passed(),)))
        self.assertIn('hooks', one_call.deferred_surfaces((Passed(), Blocked())))
        self.assertIn('hooks', one_call.deferred_surfaces((Workflow(),)))  # a workflow unit is no hook unit

    def test_a_surface_every_runtime_leaves_unsupported_stays_deferred(self):
        # The inventory may read an unsupported path, so it must not drop out of deferred_surfaces like memory does.
        cell = surfaces._cell
        def unsupported(runtime, entry):
            if entry == dict(surfaces.MATRIX)['memory'][runtime]:
                return dict(writability='unsupported', route=surfaces.ROUTES['unsupported'])
            return cell(runtime, entry)
        with patch.object(surfaces, '_cell', unsupported):
            self.assertIn('memory', surfaces.deferred(surfaces.adopted()))
        self.assertNotIn('memory', surfaces.deferred(surfaces.adopted()))

    def test_a_new_matrix_route_changes_the_deferred_lists(self):
        changed = dict(surfaces.ROUTES, recommendation_only='excluded')
        with patch.object(surfaces, 'ROUTES', changed):
            self.assertEqual(surfaces.deferred(surfaces.adopted()), ('hooks', 'workflows', 'agents'))
            self.assertNotIn('mcp', one_call.deferred_surfaces(()))


if __name__ == '__main__': unittest.main()
