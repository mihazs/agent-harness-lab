"""CLI smoke of the generic surface matrix on a synthetic checkout, and controller deferred lists derived from it."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from . import harness_surface_fixture as fixture
else:
    import harness_surface_fixture as fixture
from agent_harness_lab import one_call, surfaces

ROOT = Path(__file__).resolve().parents[2]
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

    def run_cli(self, runtime):
        environment = dict(os.environ, XDG_STATE_HOME=str(self.base / 'state'), PYTHONPATH=str(ROOT))
        completed = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'sources', 'inventory', str(self.checkout),
                                    '--runtime', runtime, '--version', surfaces.PINS[runtime]],
                                   capture_output=True, text=True, env=environment, cwd=self.base, timeout=60)
        self.assertEqual((completed.returncode, completed.stderr), (0, ''))
        return completed.stdout, json.loads(completed.stdout)

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

        class Passed: status = 'passed'
        class Blocked: status = 'blocked'
        self.assertNotIn('hooks', one_call.deferred_surfaces((Passed(),)))
        self.assertIn('hooks', one_call.deferred_surfaces((Passed(), Blocked())))

    def test_a_new_matrix_route_changes_the_deferred_lists(self):
        changed = dict(surfaces.ROUTES, recommendation_only='excluded')
        with patch.object(surfaces, 'ROUTES', changed):
            self.assertEqual(surfaces.deferred(surfaces.adopted()), ('hooks', 'workflows', 'agents'))
            self.assertNotIn('mcp', one_call.deferred_surfaces(()))


if __name__ == '__main__': unittest.main()
