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
A, P, C, R, U = 'adoptable', 'private_excluded', 'contract_required', 'recommendation_only', 'unsupported'
# path, staged writability without/with a root skills/ folder, plan surface, facets at 0o644, facets once 0o755.
# Staged means the caller observed checkout containment (candidate_states, journal.owned); UNCONFIRMED holds the
# lexical verdict every other caller still receives for the rows that need that fact.
GOLDEN = (
    ('AGENTS.md', A, A, 'instruction', (I,), (I, H)),
    ('CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('sub/AGENTS.md', A, A, 'instruction', (I,), (I, H)),
    ('sub/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('app/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('.claude/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('CLAUDE.local.md', P, P, 'instruction', (I,), (I, H)),
    ('AGENTS.override.md', A, A, 'instruction', (I,), (I, H)),
    ('sub/AGENTS.override.md', A, A, 'instruction', (I,), (I, H)),
    # A same-named instruction file under a hook folder is a hook first and keeps the hook contract.
    ('.claude/hooks/CLAUDE.md', C, C, 'hook', (I, H), (I, H)),
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
    ('pkg/.claude/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('pkg/.agents/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('pkg/.claude/skills/demo/scripts/run.py', C, C, 'skill', (S, H), (S, H)),
    ('pkg/.claude/skills/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('plugin/skills/demo/.claude/skills/x/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('my-skills/demo/SKILL.md', U, U, 'unsupported', (), (H,)),
    ('.claude/rules/style.md', A, A, 'path_rule', (), (H,)),
    ('.claude/rules/skills/style.md', A, A, 'path_rule', (S,), (S, H)),  # path_rule outranks a skills/ segment
    ('plugin/skills/demo/.claude/rules/style.md', A, A, 'path_rule', (S,), (S, H)),
    ('.claude/rules/run.sh', U, U, 'unsupported', (H,), (H,)),
    ('.codex/rules/default.rules', 'recommendation_only', 'recommendation_only', 'exec_policy', (), (H,)),
    ('rules/x.rules', U, U, 'unsupported', (), (H,)),  # a checkout's own rules/ is not Codex policy
    ('.claude/hooks/pre.sh', C, C, 'hook', (H,), (H,)),
    ('hooks/hooks.json', C, C, 'hook', (H,), (H,)),
    ('src/hook', C, C, 'hook', (H,), (H,)),
    ('.codex/hooks.json', C, C, 'hook', (H,), (H,)),
    ('.claude/workflows/release.js', C, C, 'workflow', (H,), (H,)),
    ('pkg/.claude/workflows/release.js', C, C, 'workflow', (H,), (H,)),
    ('.claude/workflows/lib/helper.js', U, U, 'unsupported', (H,), (H,)),
    ('.claude/workflows/notes.md', U, U, 'unsupported', (), (H,)),
    # Agent definitions can embed hooks, so they carry the hook contract and stay unresolved in plans.
    ('.claude/agents/reviewer.md', A, A, 'agent', (H,), (H,)),
    ('src/.claude/agents/team/reviewer.md', A, A, 'agent', (H,), (H,)),
    ('.codex/agents/reviewer.toml', A, A, 'agent', (H,), (H,)),
    ('agents/reviewer.md', C, C, 'agent', (H,), (H,)),
    # A hooks/ or skills/ folder inside an agents tree still holds agent definitions.
    ('.claude/agents/hooks/guard.md', A, A, 'agent', (H,), (H,)),
    ('.claude/agents/skills/helper.md', A, A, 'agent', (S, H), (S, H)),
    ('.codex/agents/hooks/guard.toml', A, A, 'agent', (H,), (H,)),
    ('agents/hook/guard.md', C, C, 'agent', (H,), (H,)),
    ('.claude/agents/notes.txt', U, U, 'unsupported', (), (H,)),
    ('.claude/agents.md', U, U, 'unsupported', (), (H,)),
    ('.claude/commands/ship.md', A, A, 'command', (), (H,)),
    ('.claude/commands/frontend/component.md', A, A, 'command', (), (H,)),
    ('.claude/skills/demo/.claude/commands/x.md', A, A, 'skill', (S,), (S, H)),
    ('packages/skills/.claude/commands/ship.md', A, A, 'command', (S,), (S, H)),
    ('apps/.claude/skills/demo/.claude/commands/x.md', A, A, 'skill', (S,), (S, H)),
    ('.claude/settings.json', C, C, 'hook', (H,), (H,)),
    ('.claude/settings.local.json', P, P, 'hook', (H,), (H,)),
    ('.codex/config.toml', C, C, 'hook', (H,), (H,)),
    ('src/.codex/config.toml', C, C, 'hook', (H,), (H,)),
    ('lefthook.yml', R, R, 'hook', (H,), (H,)),
    ('.husky/pre-commit', R, R, 'hook', (H,), (H,)),
    ('.claude-plugin/plugin.json', U, U, 'unsupported', (), (H,)),
    ('.claude/output-styles/terse.md', *('recommendation_only',) * 2, 'config', (), (H,)),
    ('.mcp.json', *('recommendation_only',) * 2, 'config', (), (H,)),
    # Runtime memory layouts of a declared home root never hide checkout folders that share their names.
    ('memories/AGENTS.md', A, A, 'instruction', (I,), (I, H)),
    ('memories/hooks/pre.sh', C, C, 'hook', (H,), (H,)),
    ('projects/api/memory/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('.claude/agent-memory/hooks/pre.sh', P, P, 'hook', (H,), (H,)),
    ('.git/config', P, P, 'unsupported', (), (H,)),
    ('.git/hooks/pre-commit', P, P, 'hook', (H,), (H,)),
    ('__pycache__/m.pyc', P, P, 'unsupported', (), (H,)),
)
UNCONFIRMED = {
    **dict.fromkeys(('sub/AGENTS.md', 'sub/CLAUDE.md', 'app/CLAUDE.md', '.claude/CLAUDE.md', 'AGENTS.override.md',
                     'sub/AGENTS.override.md', 'memories/AGENTS.md', 'projects/api/memory/CLAUDE.md'), R),
    **dict.fromkeys(('pkg/.claude/skills/demo/SKILL.md', 'pkg/.agents/skills/demo/SKILL.md',
                     'pkg/.claude/skills/demo/scripts/run.py', '.claude/rules/style.md', '.claude/rules/skills/style.md',
                     'plugin/skills/demo/.claude/rules/style.md', '.claude/commands/ship.md',
                     '.claude/commands/frontend/component.md', 'packages/skills/.claude/commands/ship.md',
                     'apps/.claude/skills/demo/.claude/commands/x.md'), U),
    **dict.fromkeys(('.claude/agents/reviewer.md', 'src/.claude/agents/team/reviewer.md', '.codex/agents/reviewer.toml',
                     '.claude/agents/hooks/guard.md', '.claude/agents/skills/helper.md',
                     '.codex/agents/hooks/guard.toml'), C),
}
SHA = 'a' * 64


class CallerCharacterization(unittest.TestCase):
    """Pinned against the callers before and after they delegate to surfaces.classify."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # Hermetic: a git work tree enclosing the host's TMPDIR must not lend these projects its ignore rules.
        ignored = patch('agent_harness_lab.promotion.journal.ignored', return_value=False)
        ignored.start(); self.addCleanup(ignored.stop)

    def candidate(self, name, root_skills):
        folder = Path(tempfile.mkdtemp(dir=self.root))
        # A changed agent role is parsed, so a TOML path holds valid synthetic TOML.
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text('name = "synthetic"\n' if name.endswith('.toml') else 'synthetic')
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
                    staged = classify(name, None, 0o644, root_skills=root_skills, in_checkout=True)
                    self.assertEqual((staged.writability, staged.kind), (verdict, surface))
                    found = classify(name, None, 0o644, root_skills=root_skills)
                    self.assertEqual((found.writability, found.kind), (UNCONFIRMED.get(name, verdict), surface))
                    self.assertEqual(tuple(f for f in found.facets if f in (I, S, H)), plain)
                    self.assertEqual(found.executable, name.endswith(('.py', '.sh', '.js', '.ts')))
                    self.assertEqual(classify(name, None, 0o755, root_skills=root_skills).writability,
                                     UNCONFIRMED.get(name, verdict))

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
        for bad in ('', 7):
            with self.assertRaises(ValueError): classify('AGENTS.md', None, None, declared_root=bad)

    def test_home_memory_layouts_are_private_beneath_any_declared_root(self):
        from agent_harness_lab.surfaces import classify
        for path in ('memories/AGENTS.md', 'memories/hooks/pre.sh', 'projects/api/memory/CLAUDE.md'):
            with self.subTest(path=path):
                self.assertNotEqual(classify(path, None, None).writability, P)
                # Labels are free-form, so the memory layout never depends on a magic root name.
                for root in ('codex_home', 'claude_home', 'codex', 'claude', 'claude_user', 'plugins'):
                    self.assertEqual(classify(path, None, None, declared_root=root).writability, P)

    def test_root_skills_condition_is_evaluated_lazily(self):
        from agent_harness_lab.surfaces import classify
        calls = []
        probe = lambda: calls.append(1) or True
        self.assertEqual(classify('AGENTS.md', None, None, root_skills=probe).writability, A)
        self.assertEqual(calls, [])
        self.assertEqual(classify('skills/demo/SKILL.md', None, None, root_skills=probe).writability, A)
        self.assertEqual(calls, [1])

    def test_checkout_containment_is_probed_only_for_a_gated_row_that_names_the_path(self):
        from agent_harness_lab.surfaces import classify
        calls = []
        probe = lambda: calls.append(1) or True
        for name, verdict in (('AGENTS.md', A), ('.claude/skills/demo/SKILL.md', A), ('.claude/hooks/CLAUDE.md', C)):
            self.assertEqual(classify(name, None, None, in_checkout=probe).writability, verdict)
        self.assertEqual(calls, [])
        self.assertEqual(classify('.claude/rules/x.md', None, None, in_checkout=probe).writability, A)
        self.assertEqual(calls, [1])
        self.assertEqual(classify('.claude/rules/x.md', None, None, in_checkout=lambda: False).writability, U)
        self.assertEqual(classify('app/CLAUDE.md', None, None, in_checkout=True, origin='external_repository').writability, R)

    def test_an_added_table_row_reaches_every_caller(self):
        from agent_harness_lab import surfaces
        row = surfaces.Rule('synthetic-notes', 'note', 1, A, lambda path, parts, ctx: parts[:2] == ('.claude', 'notes'),
                            label='synthetic notes')
        with patch.object(surfaces, 'RULES', (*surfaces.RULES, row)):
            self.assertEqual(surfaces.classify('.claude/notes/style.md', None, None).kind, 'note')
            self.assertTrue(CallerCharacterization.candidate(self.caller(), '.claude/notes/style.md', False))
            self.assertTrue(CallerCharacterization.owned(self.caller(), '.claude/notes/style.md', False))
            self.assertEqual(_operation('.claude/notes/style.md', None, (SHA, 0o644)).surface, 'note')
            self.assertTrue(surfaces.adoptable_surfaces().endswith('; synthetic notes'))
        self.assertFalse(CallerCharacterization.candidate(self.caller(), '.claude/notes/style.md', False))

    def caller(self):
        helper = CallerCharacterization('test_symlinks_are_refused_by_candidate_and_journal'); helper.setUp()
        self.addCleanup(helper.doCleanups); return helper

    def test_rows_reject_unknown_writability_and_runtime(self):
        from agent_harness_lab.surfaces import Rule
        with self.assertRaises(ValueError): Rule('x', 'skill', 1, 'writable', lambda *a: True)
        with self.assertRaises(ValueError): Rule('x', 'skill', 1, A, lambda *a: True, runtimes=frozenset({'other'}), label='x')
        # Every adoptable row names itself for the proposer prompts; a gated verdict must be a non-adoptable one.
        with self.assertRaisesRegex(ValueError, 'label'): Rule('x', 'skill', 1, A, lambda *a: True)
        with self.assertRaisesRegex(ValueError, 'unconfirmed'): Rule('x', 'skill', 1, A, lambda *a: True, label='x', unconfirmed=A)
        with self.assertRaisesRegex(ValueError, 'unconfirmed'): Rule('x', 'skill', 1, R, lambda *a: True, unconfirmed=U)


class CapabilityTableTests(unittest.TestCase):
    # Every item the 2026-11-05 surface research left unverified, keyed by matrix surface and runtime.
    UNVERIFIED = {
        ('instructions', 'claude-code'), ('hooks', 'codex'), ('workflows', 'claude-code'), ('agents', 'codex'),
        ('commands_skills', 'codex'), ('plugins', 'claude-code'), ('plugins', 'codex'), ('memory', 'codex'),
        ('settings', 'claude-code'),
        ('path_rules', 'claude-code'),  # ancestor launch rules; glob dotfile, case, bracket and negation semantics
        # Trial isolation requests stay unverified until a native smoke run confirms them.
        ('mcp', 'claude-code'), ('hooks', 'claude-code'), ('settings', 'codex'),
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
        self.assertEqual(len(unverified), 18)
        items = ' '.join(r.item for r in unverified)
        for phrase in ('size limit', 'version gates', 'static validator', 'load timing', 'custom prompts', 'description limit',
                       'cache subpath', 'plugin agents', 'validate command', 'memories layout', 'managed settings',
                       'claudeMdExcludes', 'dotfile', 'between the root and the cwd',
                       '--setting-sources', '--strict-mcp-config', 'disableAllHooks', 'temporary CODEX_HOME'):
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

    def test_construction_proposer_lists_adoptable_surfaces_from_the_classifier(self):
        from agent_harness_lab import surfaces
        from agent_harness_lab.evaluation import construction
        listed = surfaces.adoptable_surfaces()
        for label in ('root AGENTS.md or CLAUDE.md', 'a nested CLAUDE.md, including .claude/CLAUDE.md',
                      'a nested AGENTS.md or an AGENTS.override.md', 'files in an existing skills/<name>/ bundle',
                      '.claude/rules/**/*.md path rules', '.claude/agents/**/*.md agent definitions',
                      '.codex/agents/**/*.toml agent roles', '.claude/commands/**/*.md commands'):
            self.assertIn(label, listed)
        for absent in ('settings', 'hook', 'workflow', 'mcp', '.rules', 'memory', 'local'):
            self.assertNotIn(absent, listed)
        seen = []
        def capture(client, backend, workspace, inputs, role, instructions, *rest):
            seen.append(instructions); raise RuntimeError('captured')
        with patch.object(construction, 'run_role', capture), self.assertRaisesRegex(RuntimeError, 'captured'):
            construction.propose(None, None, Path('/nonexistent'), Path('/nonexistent'), None, None, 0)
        self.assertIn(f'Modify only these project-owned surfaces under candidate: {listed}.', seen[0])
        self.assertIn('Never modify settings, permissions, hooks or credentials.', seen[0])

    def test_telemetry_skill_prefixes_come_from_the_classifier(self):
        from pathlib import PurePosixPath
        from agent_harness_lab import surfaces
        from agent_harness_lab.telemetry import claude_native
        self.assertEqual(claude_native.SKILL_BUNDLE_PREFIXES,
                         tuple(PurePosixPath(root).parts for root in surfaces.skill_prefixes()))


class EmbeddedHookTests(unittest.TestCase):
    def test_agent_hook_detection_fails_closed(self):
        from agent_harness_lab.surfaces import adoption, embeds_hooks
        cases = (('a.md', b'---\nname: a\ndescription: d\n---\nMention hooks in the body only.\n', False),
                 ('a.md', b'No fence, so Claude skips the file.\nhooks: x\n', False),
                 ('a.md', b'\xef\xbb\xbf---\nhooks: {}\n---\n', True),
                 ('a.md', b'---\nname: a\nHOOKS: {}\n', True),
                 ('a.md', b'---\n"\\u0068ooks": {}\n---\n', True),
                 ('a.md', b'\xff\xfe', True),
                 ('a.toml', b'name = "a"\n[nested]\nlist = [{hooks = 1}]\n', True),
                 ('a.toml', b'"\\u0068ooks" = 1\n', True),
                 ('a.toml', b'name = "a"\ndeveloper_instructions = "hooks are fine in prose"\n', False),
                 ('a.toml', b'[unterminated\n', True))
        for name, raw, expected in cases:
            with self.subTest(raw=raw): self.assertEqual(embeds_hooks(name, raw), expected)
        hooked = b'---\nhooks: {}\n---\n'
        self.assertEqual(adoption('.claude/agents/a.md', 0o644, hooked, in_checkout=True).writability, C)
        self.assertEqual(adoption('.claude/agents/a.md', 0o644, None, in_checkout=True).writability, A)
        # Only agent definitions are read; an instruction file may discuss hooks freely.
        self.assertEqual(adoption('app/CLAUDE.md', 0o644, hooked, in_checkout=True).writability, A)
        self.assertEqual(adoption('.claude/rules/x.md', 0o755, b'x', in_checkout=True).writability, C)

    def test_every_claude_frontmatter_surface_refuses_hooks_permissions_and_mcp(self):
        from agent_harness_lab.surfaces import adoption
        hooked = b'---\ndescription: x\nhooks:\n  PreToolUse:\n    - hooks:\n        - type: command\n' \
                 b'          command: ./synthetic.sh\n---\n'
        for name in ('.claude/commands/deploy.md', 'pkg/.claude/skills/x/SKILL.md', '.claude/skills/x/SKILL.md',
                     '.claude/rules/r.md', '.claude/agents/a.md'):
            with self.subTest(name=name):
                self.assertEqual(adoption(name, 0o644, hooked, in_checkout=True).writability, C)
        for key in (b'permissionMode: bypassPermissions', b'mcpServers:\n  s:\n    command: /bin/sh',
                    b'PERMISSIONMODE: bypassPermissions', b'allowed-tools: Bash(*)'):
            with self.subTest(key=key):
                raw = b'---\nname: a\ndescription: b\n' + key + b'\n---\nbody\n'
                self.assertEqual(adoption('.claude/agents/a.md', 0o644, raw, in_checkout=True).writability, C)
                self.assertEqual(adoption('.claude/commands/c.md', 0o644, raw, in_checkout=True).writability, C)
        plain = b'---\nname: a\ndescription: b\n---\nhooks in the body only\n'
        for name in ('.claude/commands/c.md', 'pkg/.claude/skills/x/SKILL.md', '.claude/agents/a.md',
                     'pkg/.claude/skills/x/reference.md'):
            with self.subTest(name=name): self.assertEqual(adoption(name, 0o644, plain, in_checkout=True).writability, A)
        # A bundle file that is not SKILL.md is not parsed as frontmatter.
        self.assertEqual(adoption('pkg/.claude/skills/x/reference.md', 0o644, hooked, in_checkout=True).writability, A)


class ShellPreExecutionTests(unittest.TestCase):
    """Claude runs !`cmd` and ```! blocks in command and skill bodies on invocation, so they need the hook contract."""
    RENDERED = ('.claude/commands/deploy.md', '.claude/commands/frontend/c.md', '.claude/skills/x/SKILL.md',
                'pkg/.claude/skills/x/SKILL.md', '.agents/skills/x/SKILL.md')
    HEAD = b'---\ndescription: synthetic\n---\n'

    def verdict(self, name, body, **context):
        from agent_harness_lab.surfaces import adoption
        return adoption(name, 0o644, self.HEAD + body, in_checkout=True, **context).writability

    def test_documented_forms_route_commands_and_skills_to_the_hook_contract(self):
        documented = (b'## Changes\n\n!`git diff HEAD`\n',            # a line that starts with !
                      b'- PR diff: !`gh pr diff`\n',                    # ! after whitespace
                      b'## Environment\n```!\nnode --version\n```\n')  # the multi-line fenced form
        for name in self.RENDERED:
            for body in documented:
                with self.subTest(name=name, body=body): self.assertEqual(self.verdict(name, body), C)
        self.assertEqual(self.verdict('skills/x/SKILL.md', documented[0], root_skills=True), C)

    def test_inline_form_inside_a_fenced_code_block_is_not_excluded(self):
        # The docs never say a plain fence suppresses the inline form, so a fenced example fails closed.
        for fence in (b'```\n!`date`\n```\n', b'```bash\necho\n!`date`\n```\n', b'~~~\n!`date`\n~~~\n'):
            with self.subTest(fence=fence): self.assertEqual(self.verdict('.claude/commands/c.md', fence), C)

    def test_unverified_variants_fail_closed(self):
        for body in (b'KEY=!`cmd`\n', b'!``cmd``\n', b'! `cmd`\n', b'  ```!\ncmd\n```\n', b'````!\ncmd\n````\n',
                     b'~~~!\ncmd\n~~~\n', b'``` !\ncmd\n```\n', b'text\r\n!`cmd`\r\n', b'\xff\xfe!'):
            with self.subTest(body=body): self.assertEqual(self.verdict('.claude/commands/c.md', body), C)
        # Claude Code matches ```! anywhere, so a fence after other text on the line fails closed too.
        for body in (b'> ```!\n> node -v\n> ```\n', b'>```!\nid\n```\n', b'- ```!\n  node -v\n  ```\n',
                     b'1. ```!\n   node -v\n   ```\n', b'Env: ```!\nnode -v\n```\n', b'Use ```!date``` here.\n',
                     b'|```!id```|\n', b'\xc2\xa0```!\nid\n```\n', b'text\r```!\rnode -v\r```\r',
                     b'x\r```!\nid\n```\n', 'intro\u2028```!\nid\n```\n'.encode()):
            for name in self.RENDERED:
                with self.subTest(name=name, body=body): self.assertEqual(self.verdict(name, body), C)
        # Even prose that quotes the marker, as in `!`, holds ! before a backtick and therefore fails closed.
        self.assertEqual(self.verdict('.claude/commands/c.md', b'Use the `!` prefix.\n'), C)
        # Substitution runs over the original file, so the frontmatter is scanned as well.
        from agent_harness_lab.surfaces import adoption
        raw = b'---\ndescription: !`date`\n---\nbody\n'
        self.assertEqual(adoption('.claude/commands/c.md', 0o644, raw, in_checkout=True).writability, C)

    def test_plain_bodies_stay_adoptable(self):
        for body in (b'Run `git diff HEAD` and summarize it. Done!\n', b'```bash\ngit status\n```\n',
                     b'Shout! Then `make test`.\n', b'A line\n!important\n', b'```bash\n!! echo\n```\n'):
            for name in self.RENDERED:
                with self.subTest(name=name, body=body): self.assertEqual(self.verdict(name, body), A)

    def test_surfaces_that_do_not_render_shell_keep_their_verdicts(self):
        body = b'!`git diff HEAD`\n'
        for name in ('.claude/agents/a.md', '.claude/rules/r.md', 'CLAUDE.md', 'app/CLAUDE.md',
                     '.claude/skills/x/reference.md'):
            with self.subTest(name=name): self.assertEqual(self.verdict(name, body), A)
        from agent_harness_lab.surfaces import adoption
        self.assertEqual(adoption('.claude/commands/c.md', 0o644, None, in_checkout=True).writability, A)

    def test_both_writers_refuse_a_pre_executing_command(self):
        import base64
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        name, raw = '.claude/commands/ship.md', self.HEAD + b'!`git push`\n'
        folder = Path(temp.name) / 'candidate'; (folder / name).parent.mkdir(parents=True)
        (folder / name).write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'needs a hook contract'): candidate_states(folder, {})
        (folder / name).write_bytes(self.HEAD + b'Run `git push` after review.\n')
        self.assertEqual(list(candidate_states(folder, {})), [name])
        with patch('agent_harness_lab.promotion.journal.ignored', return_value=False):
            journal = Journal(folder, Path(temp.name) / 'journal')
            target = {'content': base64.b64encode(raw).decode(), 'mode': 0o644}
            with self.assertRaisesRegex(ValueError, 'outside owned harness surface'): journal.owned(name, target)

    def test_the_syntax_is_pinned_to_public_docs(self):
        from agent_harness_lab.surfaces import CAPABILITIES
        row, = (r for r in CAPABILITIES if 'shell pre-execution' in r.item)
        self.assertEqual((row.runtime, row.status, row.doc_url),
                         ('claude-code', 'verified', 'https://code.claude.com/docs/en/skills'))


class VendoredTreeTests(unittest.TestCase):
    """Third-party and generated trees hold no .git of their own, yet they are never project-owned surfaces."""
    VENDORED = ('node_modules/pkg/CLAUDE.md', 'node_modules/pkg/.claude/rules/x.md', 'node_modules/AGENTS.md',
                '.venv/lib/python3/site-packages/pkg/AGENTS.md', '.venv/.claude/rules/x.md',
                'vendor/lib/.claude/skills/demo/SKILL.md', 'app/node_modules/pkg/.claude/agents/r.md')

    def test_vendored_paths_keep_their_kind_but_are_never_adoptable(self):
        from agent_harness_lab.surfaces import VENDORED_FACET, classify
        for name in self.VENDORED:
            with self.subTest(name=name):
                lexical = classify(name)
                verdict = classify(name, None, 0o644, in_checkout=True, root_skills=True)
                self.assertEqual(verdict.writability, U)
                self.assertNotEqual(verdict.kind, 'unsupported')  # inventories still list it as its surface
                self.assertIn(VENDORED_FACET, verdict.facets)
                self.assertEqual((lexical.writability, lexical.kind), (U, verdict.kind))
                self.assertEqual(classify(name, origin='external_repository', in_checkout=True).writability, U)
                self.assertFalse(CallerCharacterization.candidate(self.caller(), name, False))
                self.assertFalse(CallerCharacterization.owned(self.caller(), name, False))
        # Stricter verdicts stay: VCS metadata inside a vendored tree is still private_excluded.
        self.assertEqual(classify('node_modules/pkg/.git/config').writability, P)
        self.assertEqual(classify('node_modules/pkg/.claude/hooks/run.sh').writability, U)
        # Only directories count, and only exact names: project folders and files keep their verdicts.
        for name in ('vendor.md', 'my_node_modules/CLAUDE.md', 'src/vendored/CLAUDE.md'):
            with self.subTest(name=name):
                self.assertNotIn(VENDORED_FACET, classify(name, in_checkout=True).facets)
        self.assertEqual(classify('src/vendored/CLAUDE.md', in_checkout=True).writability, A)

    def test_ignore_rules_are_probed_only_for_an_otherwise_adoptable_nested_path(self):
        from agent_harness_lab.surfaces import VENDORED_FACET, classify
        calls = []
        probe = lambda: calls.append(1) or True
        for name, verdict in (('AGENTS.md', A), ('CLAUDE.md', A), ('.claude/hooks/pre.sh', C), ('lefthook.yml', R),
                              ('CLAUDE.local.md', P), ('node_modules/pkg/CLAUDE.md', U)):
            self.assertEqual(classify(name, None, None, in_checkout=True, ignored=probe).writability, verdict)
        # Root harness folders stay the project's own even when ignore rules keep them local.
        for name in ('.claude/rules/x.md', '.claude/skills/demo/SKILL.md', '.claude/CLAUDE.md', '.agents/skills/a/SKILL.md',
                     '.codex/agents/r.toml', '.claude/commands/ship.md'):
            with self.subTest(name=name):
                self.assertEqual(classify(name, None, None, in_checkout=True, ignored=probe).writability, A)
        self.assertEqual(classify('skills/demo/SKILL.md', None, None, root_skills=True, in_checkout=True,
                                  ignored=probe).writability, A)
        self.assertEqual(calls, [])
        for name in ('dist/CLAUDE.md', 'generated/.claude/rules/x.md', 'app/.claude/skills/demo/SKILL.md'):
            with self.subTest(name=name):
                verdict = classify(name, None, None, in_checkout=True, ignored=probe)
                self.assertEqual((verdict.writability, VENDORED_FACET in verdict.facets), (U, True))
        self.assertEqual(len(calls), 3)
        self.assertEqual(classify('dist/CLAUDE.md', None, None, in_checkout=True, ignored=lambda: False).writability, A)

    def test_a_skill_or_agent_named_like_a_vendored_tree_is_still_project_owned(self):
        from agent_harness_lab.surfaces import VENDORED_FACET, classify
        for name in ('.claude/skills/vendor/SKILL.md', '.claude/skills/venv/SKILL.md', '.agents/skills/vendor/SKILL.md',
                     'skills/node_modules/SKILL.md', 'app/.claude/skills/vendor/SKILL.md', '.claude/agents/vendor/r.md'):
            with self.subTest(name=name):
                verdict = classify(name, None, 0o644, root_skills=True, in_checkout=True)
                self.assertEqual((verdict.writability, VENDORED_FACET in verdict.facets), (A, False))
        # A plugin skill so named stays a recommendation; the bundle's own installed dependencies stay vendored.
        self.assertEqual(classify('skills/vendor/SKILL.md', origin='external_repository').writability, R)
        for name in ('.claude/skills/vendor/node_modules/lib/README.md', '.claude/skills/tools/.venv/AGENTS.md',
                     'vendor/skills/demo/SKILL.md', 'app/skills/x/node_modules/pkg/CLAUDE.md'):
            with self.subTest(name=name):
                verdict = classify(name, None, 0o644, root_skills=True, in_checkout=True)
                self.assertEqual((verdict.writability, VENDORED_FACET in verdict.facets), (U, True))

    def test_skill_bundles_named_vendor_or_holding_dependencies_stage_without_their_dependencies(self):
        import shutil
        from agent_harness_lab.workflow import snapshot
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); project = root / 'project'
            for name in ('AGENTS.md', '.claude/skills/vendor/SKILL.md', '.claude/skills/venv/SKILL.md',
                         '.claude/skills/tools/SKILL.md', '.claude/skills/tools/node_modules/lib/index.js',
                         '.claude/skills/tools/.venv/lib/site.py'):
                (project / name).parent.mkdir(parents=True, exist_ok=True); (project / name).write_text('synthetic')
            self.assertEqual([str(p.relative_to(project)) for p in harness_files(project)],
                             ['.claude/skills/tools/SKILL.md', '.claude/skills/vendor/SKILL.md',
                              '.claude/skills/venv/SKILL.md', 'AGENTS.md'])
            original = snapshot(project, root / 'baseline')
            shutil.copytree(root / 'baseline', root / 'candidate')
            (root / 'candidate/.claude/skills/vendor/SKILL.md').write_text('after')
            self.assertEqual(sorted(candidate_states(root / 'candidate', original)), sorted(original))

    def caller(self):
        helper = CallerCharacterization('test_symlinks_are_refused_by_candidate_and_journal'); helper.setUp()
        self.addCleanup(helper.doCleanups); return helper
