"""One classifier owns the harness-surface allowlist; its callers keep their verdicts."""
import dataclasses
import os
from pathlib import Path
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

from agent_harness_lab.harness_change_plan import _operation
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.workflow import candidate_states, harness_files, skill_roots

I, S, H = 'instruction', 'skill', 'hook_contract_required'
A, P, C, U = 'adoptable', 'private_excluded', 'contract_required', 'unsupported'
R = 'recommendation_only'
# path, writability without/with a root skills/ folder, plan surface, facets at 0o644, facets once 0o755.
GOLDEN = (
    ('AGENTS.md', A, A, 'instruction', (I,), (I, H)),
    ('CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('sub/AGENTS.md', R, R, 'instruction', (I,), (I, H)),
    ('sub/CLAUDE.md', R, R, 'instruction', (I,), (I, H)),
    ('.claude/CLAUDE.md', R, R, 'instruction', (I,), (I, H)),
    ('CLAUDE.local.md', P, P, 'instruction', (I,), (I, H)),
    ('AGENTS.override.md', R, R, 'instruction', (I,), (I, H)),
    ('.agents/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('.claude/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('.claude/skills/demo/scripts/run.py', A, A, 'skill', (S, H), (S, H)),
    ('.claude/skills/demo/hooks/notes.md', A, A, 'skill', (S, H), (S, H)),
    ('.claude/skills/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('.agents/skills/demo', U, U, 'skill', (S,), (S, H)),
    ('skills/demo/SKILL.md', U, A, 'skill', (S,), (S, H)),
    ('skills/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('skills/demo/__pycache__/m.pyc', P, P, 'skill', (S,), (S, H)),
    ('.claude/skills/demo/.git/config', P, P, 'skill', (S,), (S, H)),
    ('plugin/skills/demo/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('my-skills/demo/SKILL.md', U, U, 'unsupported', (), (H,)),
    ('.claude/rules/style.md', U, U, 'path_rule', (), (H,)),
    ('.codex/rules/default.rules', 'recommendation_only', 'recommendation_only', 'exec_policy', (), (H,)),
    ('.claude/hooks/pre.sh', C, C, 'hook', (H,), (H,)),
    ('hooks/hooks.json', C, C, 'hook', (H,), (H,)),
    ('src/hook', C, C, 'hook', (H,), (H,)),
    ('.codex/hooks.json', U, U, 'unsupported', (), (H,)),
    ('.claude/workflows/release.js', U, U, 'unsupported', (H,), (H,)),
    ('.claude/agents/reviewer.md', U, U, 'unsupported', (), (H,)),
    ('.codex/agents/reviewer.toml', U, U, 'unsupported', (), (H,)),
    ('.claude/commands/ship.md', U, U, 'unsupported', (), (H,)),
    ('.claude/settings.json', U, U, 'unsupported', (), (H,)),
    ('.claude/settings.local.json', U, U, 'unsupported', (), (H,)),
    ('.codex/config.toml', U, U, 'unsupported', (), (H,)),
    ('.claude-plugin/plugin.json', U, U, 'unsupported', (), (H,)),
    ('.claude/output-styles/terse.md', U, U, 'unsupported', (), (H,)),
    ('.mcp.json', U, U, 'unsupported', (), (H,)),
    ('.git/config', P, P, 'unsupported', (), (H,)),
    ('.git/hooks/pre-commit', P, P, 'hook', (H,), (H,)),
    ('__pycache__/m.pyc', P, P, 'unsupported', (), (H,)),
)
SHA = 'a' * 64


class CallerCharacterization(unittest.TestCase):
    """Pinned against the callers before and after they delegate to surfaces.classify."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def candidate(self, name, root_skills):
        folder = Path(tempfile.mkdtemp(dir=self.root))
        (folder / name).parent.mkdir(parents=True, exist_ok=True); (folder / name).write_text('synthetic')
        original = {'skills/other/SKILL.md': None} if root_skills else {}
        try: return list(candidate_states(folder, original)) == [name]
        except ValueError as error:
            self.assertIn('outside project harness scope', str(error)); return False

    def owned(self, name, root_skills):
        project = Path(tempfile.mkdtemp(dir=self.root))
        if root_skills: (project / 'skills').mkdir()
        journal = Journal(project, Path(tempfile.mkdtemp(dir=self.root)) / 'journal')
        try: return journal.owned(name) == project.resolve() / name
        except ValueError as error:
            self.assertEqual(str(error), 'transaction outside owned harness surface'); return False

    def test_candidate_and_journal_admit_exactly_the_golden_allowlist(self):
        for name, off, on, *_ in GOLDEN:
            for root_skills, verdict in ((False, off), (True, on)):
                with self.subTest(name=name, root_skills=root_skills):
                    self.assertEqual(self.candidate(name, root_skills), verdict == A)
                    self.assertEqual(self.owned(name, root_skills), verdict == A)

    def test_plan_surface_and_facets_follow_paths_and_either_mode(self):
        for name, _, _, surface, plain, executable in GOLDEN:
            with self.subTest(name=name):
                created = _operation(name, None, (SHA, 0o644))
                self.assertEqual((created.action, created.surface, created.facets), ('create', surface, plain))
                for before, after in (((SHA, 0o644), (SHA, 0o755)), ((SHA, 0o750), None)):
                    changed = _operation(name, before, after)
                    self.assertEqual((changed.surface, changed.facets), (surface, executable))

    def test_symlinks_are_refused_by_candidate_and_journal(self):
        folder = self.root / 'candidate'; (folder / '.claude/skills').mkdir(parents=True)
        target = self.root / 'outside'; (target / 'demo').mkdir(parents=True)
        (target / 'demo/SKILL.md').write_text('synthetic')
        os.symlink(target / 'demo', folder / '.claude/skills/demo')
        with self.assertRaisesRegex(ValueError, 'symlink'): candidate_states(folder, {})
        journal = Journal(folder, self.root / 'journal')
        with self.assertRaisesRegex(ValueError, 'symlink'): journal.owned('.claude/skills/demo/SKILL.md')

    def test_discovery_keeps_root_files_prefixes_and_link_refusal(self):
        project = self.root / 'project'
        for name in ('AGENTS.md', 'sub/CLAUDE.md', '.agents/skills/a/SKILL.md', '.claude/skills/b/SKILL.md',
                     'skills/c/SKILL.md', 'skills/c/__pycache__/m.pyc', 'skills/c/.git/config', 'plugin/skills/d/SKILL.md',
                     '.claude/skills/nofile/notes.md', '.claude/rules/style.md'):
            (project / name).parent.mkdir(parents=True, exist_ok=True); (project / name).write_text('synthetic')
        (self.root / 'linked').mkdir(); (self.root / 'linked/SKILL.md').write_text('synthetic')
        os.symlink(self.root / 'linked', project / '.agents/skills/linked')
        self.assertEqual([str(p.relative_to(project)) for p in skill_roots(project)],
                         ['.agents/skills/a', '.claude/skills/b', 'skills/c'])
        self.assertEqual([str(p.relative_to(project)) for p in harness_files(project)],
                         ['.agents/skills/a/SKILL.md', '.claude/skills/b/SKILL.md', 'AGENTS.md',
                          'skills/c/.git/config', 'skills/c/SKILL.md'])


class SurfaceClassifierTests(unittest.TestCase):
    def test_classify_returns_todays_verdicts_for_every_golden_path(self):
        from agent_harness_lab.surfaces import classify
        for name, off, on, surface, plain, executable in GOLDEN:
            for root_skills, verdict in ((False, off), (True, on)):
                with self.subTest(name=name, root_skills=root_skills):
                    found = classify(name, None, 0o644, root_skills=root_skills)
                    self.assertEqual((found.writability, found.kind), (verdict, surface))
                    self.assertEqual(tuple(f for f in found.facets if f in (I, S, H)), plain)
                    self.assertEqual(found.executable, name.endswith(('.py', '.sh', '.js', '.ts')))
                    self.assertEqual(classify(name, None, 0o755, root_skills=root_skills).writability, verdict)

    def test_scope_runtime_and_input_validation(self):
        from agent_harness_lab.surfaces import classify, RUNTIMES
        for name, scope in (('AGENTS.md', '.'), ('.claude/skills/demo/scripts/run.py', '.claude/skills/demo'),
                            ('sub/CLAUDE.md', 'sub'), ('.agents/skills/demo/SKILL.md', '.agents/skills/demo')):
            self.assertEqual(classify(name, None, None).scope_dir, scope)
        self.assertEqual(classify('skills/demo/x/SKILL.md', None, None, root_skills=True).scope_dir, 'skills/demo')
        for runtime in RUNTIMES:
            self.assertEqual(classify('.claude/skills/demo/SKILL.md', runtime, None).writability, A)
        for bad in ('', '/AGENTS.md', '../AGENTS.md', None):
            with self.assertRaises(ValueError): classify(bad, None, None)
        with self.assertRaises(ValueError): classify('AGENTS.md', 'unknown-runtime', None)
        with self.assertRaises(ValueError): classify('AGENTS.md', None, True)
        self.assertTrue(classify('bin/tool', None, 0o100).executable)
        self.assertFalse(classify('bin/tool', None, 0o644).executable)

    def test_root_skills_condition_is_evaluated_lazily(self):
        from agent_harness_lab.surfaces import classify
        calls = []
        probe = lambda: calls.append(1) or True
        self.assertEqual(classify('AGENTS.md', None, None, root_skills=probe).writability, A)
        self.assertEqual(calls, [])
        self.assertEqual(classify('skills/demo/SKILL.md', None, None, root_skills=probe).writability, A)
        self.assertEqual(calls, [1])

    def test_an_added_table_row_reaches_every_caller(self):
        from agent_harness_lab import surfaces
        row = surfaces.Rule('synthetic-rules', 'rule', 1, A, lambda path, parts, ctx: parts[:2] == ('.claude', 'rules'))
        with patch.object(surfaces, 'RULES', (*surfaces.RULES, row)):
            self.assertEqual(surfaces.classify('.claude/rules/style.md', None, None).kind, 'rule')
            self.assertTrue(CallerCharacterization.candidate(self.caller(), '.claude/rules/style.md', False))
            self.assertTrue(CallerCharacterization.owned(self.caller(), '.claude/rules/style.md', False))
            self.assertEqual(_operation('.claude/rules/style.md', None, (SHA, 0o644)).surface, 'rule')
        self.assertFalse(CallerCharacterization.candidate(self.caller(), '.claude/rules/style.md', False))

    def caller(self):
        helper = CallerCharacterization('test_symlinks_are_refused_by_candidate_and_journal'); helper.setUp()
        self.addCleanup(helper.doCleanups); return helper

    def test_rows_reject_unknown_writability_and_runtime(self):
        from agent_harness_lab.surfaces import Rule
        with self.assertRaises(ValueError): Rule('x', 'skill', 1, 'writable', lambda *a: True)
        with self.assertRaises(ValueError): Rule('x', 'skill', 1, A, lambda *a: True, runtimes=frozenset({'other'}))


class CapabilityTableTests(unittest.TestCase):
    # Every item the 2026-11-05 surface research left unverified, keyed by matrix surface and runtime.
    UNVERIFIED = {
        ('instructions', 'claude-code'), ('hooks', 'codex'), ('workflows', 'claude-code'), ('agents', 'codex'),
        ('commands_skills', 'codex'), ('plugins', 'claude-code'), ('plugins', 'codex'), ('memory', 'codex'),
        ('settings', 'claude-code'),
        ('path_rules', 'claude-code'),  # ancestor launch rules; glob dotfile, case, bracket and negation semantics
    }

    def test_rows_are_frozen_pinned_and_cite_public_docs(self):
        from agent_harness_lab.surfaces import CAPABILITIES, PINS, RETRIEVED, SURFACES, RUNTIMES
        self.assertIsInstance(CAPABILITIES, tuple)
        self.assertEqual({(r.surface, r.runtime) for r in CAPABILITIES if r.status == 'verified'},
                         {(s, r) for s in SURFACES for r in RUNTIMES})
        for row in CAPABILITIES:
            self.assertEqual((row.pinned_version, row.retrieved), (PINS[row.runtime], RETRIEVED))
            self.assertTrue(row.doc_url.startswith('https://'))
            with self.assertRaises(dataclasses.FrozenInstanceError): row.status = 'verified'
        self.assertEqual(len({(r.surface, r.runtime, r.item) for r in CAPABILITIES}), len(CAPABILITIES))

    def test_every_researched_unverified_item_stays_unverified(self):
        from agent_harness_lab.surfaces import CAPABILITIES
        unverified = [r for r in CAPABILITIES if r.status == 'unverified']
        self.assertEqual({(r.surface, r.runtime) for r in unverified}, self.UNVERIFIED)
        self.assertEqual(len(unverified), 13)
        items = ' '.join(r.item for r in unverified)
        for phrase in ('size limit', 'version gates', 'static validator', 'load timing', 'custom prompts',
                       'cache subpath', 'plugin agents', 'validate command', 'memories layout', 'managed settings',
                       'claudeMdExcludes', 'dotfile', 'between the root and the cwd'):
            self.assertIn(phrase, items)

    def test_a_row_without_doc_url_pin_or_status_is_rejected(self):
        from agent_harness_lab.surfaces import CAPABILITIES, Capability
        row = CAPABILITIES[0]
        for field in ('doc_url', 'pinned_version', 'status'):
            for value in ('', None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    dataclasses.replace(row, **{field: value})
        for field, value in (('status', 'probably'), ('runtime', 'other'), ('doc_url', 'http://example.invalid/'),
                             ('pinned_version', '9.9.9'), ('surface', 'unknown'), ('version_gate', '2.1')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                dataclasses.replace(row, **{field: value})
        with self.assertRaises(TypeError):
            Capability(surface='hooks', runtime='codex', item='synthetic', retrieved='2026-11-05')


if __name__ == '__main__':
    unittest.main()


class SurfaceOwnershipTests(unittest.TestCase):
    def test_classification_does_not_depend_on_row_order(self):
        from agent_harness_lab import surfaces
        paths = ('CLAUDE.md', '.claude/skills/demo/SKILL.md', '.claude/skills/demo/hooks/run.sh', 'apps/x/AGENTS.md',
                 '.claude/rules/a.md', '.git/config', 'skills/demo/SKILL.md')
        expected = {path: surfaces.classify(path, root_skills=True) for path in paths}
        with unittest.mock.patch.object(surfaces, 'RULES', tuple(reversed(surfaces.RULES))):
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(surfaces.classify(path, root_skills=True), expected[path])
        # Two added rows that tie on priority must name the same kind whichever branch put its row first.
        always = lambda path, parts, context: path == 'docs/tied.md'
        tied = (surfaces.Rule('zeta-added', 'zeta', 40, 'recommendation_only', always),
                surfaces.Rule('alpha-added', 'alpha', 40, 'recommendation_only', always))
        results = []
        for order in (tied, tuple(reversed(tied))):
            with unittest.mock.patch.object(surfaces, 'RULES', surfaces.RULES + order):
                results.append(surfaces.classify('docs/tied.md'))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].kind, 'alpha')

    def test_telemetry_skill_prefixes_come_from_the_classifier(self):
        from pathlib import PurePosixPath
        from agent_harness_lab import surfaces
        from agent_harness_lab.telemetry import claude_native
        self.assertEqual(claude_native.SKILL_BUNDLE_PREFIXES,
                         tuple(PurePosixPath(root).parts for root in surfaces.skill_prefixes()))
