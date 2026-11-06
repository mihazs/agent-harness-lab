"""Claude path-scoped rules and Codex exec-policy files over synthetic checkouts; no real configuration or process."""
import builtins
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab import surfaces
from agent_harness_lab.sources import claude_rules as rules, frontmatter, harness_inventory as inventory
from tests.support import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='.')
BOMB = '"' + '{a,b}' * 9 + '"'  # 512 alternatives, past the 256 bound
POLICY = f'# {fixture.SECRET}\nprefix_rule(pattern = ["rm", "-rf"], decision = "forbidden")\n'
RULES = {
    '.claude/rules/style.md': f'# {fixture.SECRET} always\n',
    '.claude/rules/frontend/ts.md': f'---\npaths:\n  - "src/**/*.{{ts,tsx}}"\n---\n{fixture.SECRET}\n',
    '.claude/rules/broken.md': f'---\npaths: src/**\n---\n{fixture.SECRET}\n',
    '.claude/rules/unterminated.md': f'---\npaths:\n  - "src/**"\n{fixture.SECRET}\n',
    '.claude/rules/bomb.md': f'---\npaths:\n  - {BOMB}\n---\n{fixture.SECRET}\n',
    '.claude/rules/bracket.md': f'---\npaths: ["docs/[ab].md"]\n---\n{fixture.SECRET}\n',
    'pkg/.claude/rules/lib.md': f"---\npaths: ['lib/*.py']\n---\n{fixture.SECRET}\n",
    'pkg/.claude/rules/always.md': f'{fixture.SECRET} nested\n',
    '.codex/rules/default.rules': POLICY,
}


def denied(*args, **kwargs): raise AssertionError('rules must not spawn processes or evaluate code')


class ClaudeRulesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-rules-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.checkout = fixture.build(self.base / 'checkout', RULES, hostile=False)
        (self.base / 'shared.md').write_text(f'{fixture.SECRET} linked\n')
        (self.checkout / '.claude/rules/linked.md').symlink_to(self.base / 'shared.md')

    def capture(self, profile=CLAUDE, **options):
        return rules.capture(self.checkout, profile, **options)

    def states(self, receipt):
        return {n.path: (n.parse_status, n.classification) for n in receipt.nodes}

    def test_rules_are_classified_against_declared_task_paths_from_their_base_directory(self):
        receipt = self.capture(task_paths=['src/app/view.tsx', 'pkg/lib/util.py', 'docs/a.md'])
        self.assertEqual(receipt.status, 'declared')
        self.assertEqual(self.states(receipt), {
            '.claude/rules/style.md': ('parsed', 'launch'),
            '.claude/rules/frontend/ts.md': ('parsed', 'conditional_matched'),
            '.claude/rules/broken.md': ('frontmatter_unsupported', 'unknown'),
            '.claude/rules/unterminated.md': ('frontmatter_unsupported', 'unknown'),
            '.claude/rules/bomb.md': ('blocked', 'unknown'),
            '.claude/rules/bracket.md': ('parsed', 'conditional_unknown'),
            'pkg/.claude/rules/lib.md': ('parsed', 'conditional_matched'),
            'pkg/.claude/rules/always.md': ('parsed', 'on_demand_subdirectory'),
        })
        nodes = {n.path: n for n in receipt.nodes}
        self.assertEqual((nodes['pkg/.claude/rules/lib.md'].base_dir, nodes['.claude/rules/frontend/ts.md'].patterns), ('pkg', 2))
        self.assertEqual({(r.path, r.reason) for r in receipt.refusals}, {('.claude/rules/linked.md', 'symlink_not_followed')})
        self.assertNotIn('.claude/rules/linked.md', nodes)
        self.assertNotIn('.codex/rules/default.rules', nodes)  # a Codex policy file is never a Claude rule
        for node in receipt.nodes:
            self.assertEqual(node.content_sha256, hashlib.sha256((self.checkout / node.path).read_bytes()).hexdigest())
        self.assertNotIn(fixture.SECRET, json.dumps(receipt.document()))

    def test_unmatched_globs_and_the_base_directory_bound_matching(self):
        # src/x.js misses the brace alternatives; lib/util.py is outside pkg, the nested rule's base directory.
        states = self.states(self.capture(task_paths=['src/x.js', 'lib/util.py', 'src/app/view.tsx.bak']))
        self.assertEqual(states['.claude/rules/frontend/ts.md'], ('parsed', 'conditional_unmatched'))
        self.assertEqual(states['pkg/.claude/rules/lib.md'], ('parsed', 'conditional_unmatched'))
        self.assertEqual(states['.claude/rules/bracket.md'], ('parsed', 'conditional_unknown'))  # bracket not reproduced
        nested = self.states(self.capture(dict(CLAUDE, task_cwd='pkg'), task_paths=[]))
        self.assertEqual(nested['pkg/.claude/rules/always.md'], ('parsed', 'launch'))
        self.assertEqual(self.states(self.capture())['.claude/rules/frontend/ts.md'], ('parsed', 'conditional_unknown'))

    def test_no_rule_is_ever_reported_as_loaded(self):
        receipt = self.capture(task_paths=['src/app/view.tsx'])
        self.assertEqual((receipt.observed_loading, receipt.effective), ('unknown', 'unknown'))
        self.assertFalse(receipt.document()['loading_evidence'])
        for node in receipt.nodes:
            self.assertEqual((node.observed_loading, node.effective), ('unknown', 'unknown'))
            for field in ('observed_loading', 'effective'):
                with self.subTest(field=field), self.assertRaises(ValueError): replace(node, **{field: 'loaded'})
            self.assertNotIn('loaded', node.classification)
        broken = next(n for n in receipt.nodes if n.parse_status == 'frontmatter_unsupported')
        with self.assertRaises(ValueError): replace(broken, classification='launch')  # never guessed

    def test_setting_sources_without_project_skip_every_project_rule(self):
        receipt = self.capture(task_paths=['src/app/view.tsx'], setting_sources=['user', 'local'])
        self.assertEqual({n.classification for n in receipt.nodes}, {'skipped_setting_sources'})
        self.assertEqual(receipt.document()['setting_sources_origin'], 'caller_declared')
        self.assertEqual(self.capture().document()['setting_sources_origin'], 'assumed_default')
        for sources in (['managed'], ['user', 'user'], 'project'):
            with self.subTest(sources=sources), self.assertRaises(ValueError): self.capture(setting_sources=sources)
        for paths in (['../up.ts'], ['.git/config'], ['a.ts', 'a.ts'], 'src/a.ts'):
            with self.subTest(paths=paths), self.assertRaises(ValueError): self.capture(task_paths=paths)
        with self.assertRaises(ValueError): self.capture(CODEX)

    def test_rule_bytes_must_match_the_inventory_digest(self):
        with patch.object(rules, 'read_regular', lambda path, bound: b'changed'), self.assertRaises(ValueError):
            self.capture()
        with patch.object(inventory, 'MAX_FILES', 2):
            blocked = self.capture()
        self.assertEqual((blocked.status, blocked.blocked_reason, blocked.nodes), ('blocked', 'file_bound_exceeded', ()))

    def test_inventory_parser_records_frontmatter_facts_only(self):
        nodes = {n.path: n for n in inventory.capture(self.checkout, CLAUDE).nodes}
        typed = nodes['.claude/rules/frontend/ts.md']
        self.assertEqual((typed.kind, typed.parse_status, typed.capability_ref), ('path_rule', 'parsed', 'path_rules:claude-code'))
        self.assertEqual(dict(typed.facts), dict(rule_base='.', rule_parse='parsed', rule_patterns=2, rule_loading='conditional_unknown'))
        self.assertEqual(dict(nodes['.claude/rules/style.md'].facts)['rule_loading'], 'launch')
        self.assertEqual(nodes['.claude/rules/broken.md'].parse_status, 'unsupported')
        self.assertEqual(dict(nodes['.claude/rules/bomb.md'].facts)['rule_parse'], 'blocked')
        self.assertEqual(rules.parse('AGENTS.md', b'', CLAUDE), ('not_parsed', {}))
        self.assertNotIn('.codex/rules/default.rules', nodes)
        self.assertNotIn(fixture.SECRET, json.dumps(inventory.capture(self.checkout, CLAUDE).document()))

    def test_codex_rules_are_digest_only_exec_policy_and_never_evaluated(self):
        inventory.capture(self.checkout, CODEX)  # warm imports so only the capture's own opens are counted
        policy = os.lstat(self.checkout / '.codex/rules/default.rules')
        real, opened = os.open, []

        def recording(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real(path, flags, mode, dir_fd=dir_fd)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (policy.st_dev, policy.st_ino): opened.append(flags)
            return descriptor

        with patch('os.open', recording), patch('subprocess.Popen', denied), patch('os.system', denied), \
                patch('os.posix_spawn', denied), patch('os.fork', denied), patch.object(builtins, 'exec', denied), \
                patch.object(builtins, 'eval', denied), patch.object(builtins, 'compile', denied):
            value = inventory.capture(self.checkout, CODEX)
        self.assertEqual(len(opened), 1)  # one bounded read for the digest, nothing else
        self.assertFalse(opened[0] & (os.O_WRONLY | os.O_RDWR))
        nodes = {n.path: n for n in value.nodes}
        node = nodes['.codex/rules/default.rules']
        self.assertEqual((node.kind, node.declared_state, node.parse_status, node.facts), ('exec_policy', 'present', 'no_parser', ()))
        self.assertEqual(node.content_sha256, hashlib.sha256(POLICY.encode()).hexdigest())
        self.assertFalse(any(n.kind == 'path_rule' for n in value.nodes))
        self.assertNotIn(fixture.SECRET, json.dumps(value.document()))
        with patch.object(inventory, 'MAX_BYTES', 40):
            self.assertEqual(inventory.capture(self.checkout, CODEX).status, 'blocked')  # bounded, never partial

    def test_claude_and_codex_rule_kinds_stay_distinct(self):
        policy, rule = '.codex/rules/default.rules', '.claude/rules/style.md'
        self.assertEqual(surfaces.classify(policy, 'codex').kind, 'exec_policy')
        self.assertEqual(surfaces.classify(policy, 'codex').writability, 'recommendation_only')
        self.assertEqual(surfaces.classify('rules/default.rules', 'codex', external_root=True).kind, 'exec_policy')
        self.assertEqual(surfaces.classify('rules/default.rules', 'codex').kind, 'unsupported')  # a checkout's own rules/
        self.assertEqual(surfaces.classify(policy, 'claude-code').kind, 'unsupported')
        self.assertEqual(surfaces.classify(rule, 'codex').kind, 'unsupported')
        self.assertEqual((surfaces.classify(rule, 'claude-code').kind, surfaces.classify(rule, 'claude-code').writability),
                         ('path_rule', 'unsupported'))
        for path in (policy, rule, 'pkg/.claude/rules/lib.md', 'src/.codex/rules/x.rules', '.codex/rules/x.md'):
            self.assertNotIn('instruction', surfaces.classify(path).facets)
        self.assertEqual(surfaces.classify('src/.codex/rules/x.rules', 'codex').kind, 'unsupported')
        self.assertTrue(any('codex execpolicy check' in row.item for row in surfaces.CAPABILITIES
                            if row.runtime == 'codex' and row.surface == 'path_rules'))
        unverified = [row.item for row in surfaces.CAPABILITIES if row.surface == 'path_rules' and row.status == 'unverified']
        self.assertTrue(any('dotfile' in item and 'case-sensitivity' in item for item in unverified))

    def test_only_a_declared_root_turns_a_top_level_rules_directory_into_codex_policy(self):
        home = fixture.build(self.base / 'codex-home', {'rules/default.rules': POLICY}, hostile=False)
        (self.checkout / 'rules').mkdir(); (self.checkout / 'rules/notes.rules').write_text('not codex\n')
        value = inventory.capture(self.checkout, dict(CODEX, project_trust='declared_trusted'),
                                  external_roots={'codex_home': str(home)})
        kinds = {(n.root, n.path): (n.kind, n.origin) for n in value.nodes}
        self.assertNotIn(('checkout', 'rules/notes.rules'), kinds)  # ordinary project content, not policy
        self.assertEqual(kinds[('codex_home', 'rules/default.rules')], ('exec_policy', 'caller_declared_external'))
        self.assertEqual(kinds[('checkout', '.codex/rules/default.rules')], ('exec_policy', 'checkout'))

    def test_a_rule_without_paths_in_a_sibling_of_the_task_cwd_is_outside_task_scope(self):
        fixture.build(self.checkout, {'other/.claude/rules/a.md': 'synthetic\n', 'pkg/sub/.claude/rules/b.md': 'x\n'},
                      hostile=False)
        states = self.states(self.capture(dict(CLAUDE, task_cwd='pkg')))
        self.assertEqual(states['other/.claude/rules/a.md'], ('parsed', 'outside_task_scope'))
        self.assertEqual(states['pkg/sub/.claude/rules/b.md'], ('parsed', 'on_demand_subdirectory'))
        self.assertEqual(states['pkg/.claude/rules/always.md'], ('parsed', 'launch'))
        self.assertEqual(states['.claude/rules/style.md'], ('parsed', 'launch'))

    def test_matching_work_is_bounded_across_the_whole_receipt(self):
        # Each rule stays inside the per-rule expansion bound; together they would cost unbounded time.
        bomb = '---\npaths: ["' + '**/*/' * 4 + 'n' + '{a,b}' * 8 + '"]\n---\n'
        hostile = fixture.build(self.base / 'many', {f'.claude/rules/r{i}.md': bomb for i in range(8)}, hostile=False)
        paths = [f'a/b/c/d/e/f/g/h/x{i}.py' for i in range(10)]
        with patch.object(rules, 'MAX_MATCH_STEPS', 1_000_000):
            receipt = rules.capture(hostile, CLAUDE, task_paths=paths)
            self.assertEqual((receipt.status, receipt.blocked_reason, receipt.nodes), ('blocked', 'match_budget_exceeded', ()))
            single = fixture.build(self.base / 'one', {'.claude/rules/r.md': bomb}, hostile=False)
            self.assertEqual(self.states(rules.capture(single, CLAUDE, task_paths=paths)),
                             {'.claude/rules/r.md': ('parsed', 'conditional_unmatched')})
        self.assertEqual(rules.capture(hostile, CLAUDE).status, 'declared')  # no task paths, nothing to match

    def test_unverified_patterns_are_charged_once_and_never_paired_with_task_paths(self):
        unverified = tuple(f'[{i}' for i in range(200)) + ('!x', 'a\\b', './s/*.ts', 'r/{x}')
        paths = tuple(f'a/b/x{i}.py' for i in range(1000))
        budget = rules.Budget(len(unverified))
        self.assertEqual(rules.classify('.', 'parsed', unverified, ('.',), paths, None, budget), 'conditional_unknown')
        self.assertEqual(budget.steps, 0)  # one step per pattern, none per (pattern, path) pair
        self.assertEqual(rules.classify('.', 'parsed', unverified + ('**/x7.py',), ('.',), paths, None),
                         'conditional_matched')  # a verified pattern still decides a rule with unverified ones
        rule = '---\npaths: [' + ', '.join(f'"[{i}"' for i in range(50)) + ']\n---\n'
        hostile = fixture.build(self.base / 'unverified', {f'.claude/rules/u{i}.md': rule for i in range(30)}, hostile=False)
        with patch.object(rules, 'MAX_MATCH_STEPS', 1000):
            receipt = rules.capture(hostile, CLAUDE, task_paths=list(paths))
            self.assertEqual((receipt.status, receipt.blocked_reason, receipt.nodes), ('blocked', 'match_budget_exceeded', ()))
        self.assertEqual({n.classification for n in rules.capture(hostile, CLAUDE, task_paths=list(paths)).nodes},
                         {'conditional_unknown'})

    def test_hostile_rules_stay_bounded_and_rules_below_a_skills_directory_are_kept(self):
        hostile = self.base / 'hostile'
        fixture.build(hostile, {
            '.claude/rules/deep.md': '---\npaths: ["' + '**/' * 1200 + 'x"]\n---\n',
            '.claude/rules/braces.md': '---\npaths:\n' + ''.join(f'  - "{i}' + '{' * 4000 + '"\n' for i in range(3)) + '---\n',
            '.claude/rules/skills/s.md': '---\npaths: ["src/*.ts"]\n---\n',
            'plugin/skills/p/.claude/rules/p.md': f'{fixture.SECRET} nested\n',
        }, hostile=False)
        receipt = rules.capture(hostile, CLAUDE, task_paths=['x', 'src/a.ts'])  # never a RecursionError
        self.assertEqual(self.states(receipt), {
            '.claude/rules/deep.md': ('blocked', 'unknown'),  # past the segment bound, so never evaluated
            '.claude/rules/braces.md': ('parsed', 'conditional_unknown'),  # unclosed braces stay unverified
            '.claude/rules/skills/s.md': ('parsed', 'conditional_matched'),
            'plugin/skills/p/.claude/rules/p.md': ('parsed', 'on_demand_subdirectory'),
        })
        kinds = {n.path: n.kind for n in inventory.capture(hostile, CLAUDE).nodes}
        self.assertEqual(kinds['.claude/rules/skills/s.md'], 'path_rule')
        self.assertEqual(kinds['plugin/skills/p/.claude/rules/p.md'], 'path_rule')


class GlobAndFrontmatterTests(unittest.TestCase):
    def test_brace_expansion_is_bounded_and_counts_only_braced_patterns(self):
        self.assertEqual(rules.expand(['src/*.{ts,tsx}', 'lib/**']), ['src/*.ts', 'src/*.tsx', 'lib/**'])
        self.assertEqual(len(rules.expand(['{a,b}/{c,d}/*.{ts,tsx}'])), 8)
        self.assertEqual(rules.expand(['{a}', 'x{,y}']), ['{a}', 'x', 'xy'])
        self.assertEqual(len(rules.expand(['{a,b}' * 8])), 256)
        for patterns in (['{a,b}' * 9], ['{' + ','.join(map(str, range(300))) + '}'], ['x' * 5000]):
            with self.subTest(size=len(patterns[0])), self.assertRaises(rules._Blocked): rules.expand(patterns)

    def test_brace_scan_is_linear_and_keeps_the_first_comma_group(self):
        self.assertEqual(rules._group('{a{b,c}'), (2, 6, ['b', 'c']))  # an unclosed brace is skipped
        self.assertEqual(rules._group('}{a,b}'), (1, 5, ['a', 'b']))
        self.assertEqual(rules._group('{x}{a,{b,c}}'), (3, 11, ['a', '{b,c}']))  # outermost group first
        self.assertIsNone(rules._group('{a}{b}'))
        started = time.perf_counter()
        self.assertIsNone(rules._group('a' + '{' * 100_000))  # quadratic rescans took minutes here
        self.assertLess(time.perf_counter() - started, 5)

    def test_segment_bound_blocks_deep_patterns_and_matching_never_recurses(self):
        bound = rules.MAX_PATTERN_SEGMENTS
        self.assertEqual(len(rules.expand(['**/' * (bound - 1) + 'x'])), 1)
        for pattern in ('**/' * bound + 'x', '{a,' + 'b/' * bound + 'c}'):
            with self.subTest(pattern=pattern[:12]), self.assertRaises(rules._Blocked): rules.expand([pattern])
        self.assertTrue(rules.matches('**/' * 1200 + 'x', 'x'))
        self.assertTrue(rules.matches('**/' * 900 + 'zz', 'a/b/c/d/e/f/g/zz'))
        self.assertTrue(rules.matches('a/' * 1100 + 'b', 'a/' * 1100 + 'b'))
        self.assertFalse(rules.matches('**/' * 900 + 'zz', 'a/b/c/d/e/f/g/h'))

    def test_segment_aware_double_star_and_documented_anchoring(self):
        cases = (('**/*.ts', 'a.ts', True), ('**/*.ts', 'a/b/c.ts', True), ('src/**/*', 'src/a/b', True),
                 ('src/**/*', 'lib/a', False), ('*.md', 'docs/x.md', True), ('/*.md', 'docs/x.md', False),
                 ('/*.md', 'x.md', True), ('src/components/*.tsx', 'src/components/a/b.tsx', False),
                 ('src/**/*.ts', 'src/a.ts', True), ('src/*.ts', 'src/a/b.ts', False), ('*.TS', 'a.ts', False))
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path): self.assertEqual(rules.matches(pattern, path), expected)
        self.assertFalse(rules.verified('docs/[ab].md') or rules.verified('a\\[b') or rules.verified('!x') or rules.verified('src/'))

    def test_unverified_dot_case_and_brace_semantics_are_never_a_definite_label(self):
        decide = lambda pattern, path: rules.classify('.', 'parsed', (pattern,), ('.',), (path,), None)
        for pattern, path in (('**/*.ts', '.github/x.ts'), ('src/*', 'src/.env'), ('*.env', 'a/.env'),
                              ('*.TS', 'a.ts'), ('src/*.ts', 'SRC/a.TS'), ('f{1..3}.ts', 'f2.ts'), ('{a}', 'a'),
                              ('./src/*.ts', 'src/a.ts'), ('src/../a.ts', 'a.ts')):
            with self.subTest(pattern=pattern, path=path): self.assertEqual(decide(pattern, path), 'conditional_unknown')
        for pattern, path, expected in (('.github/*.ts', '.github/x.ts', 'conditional_matched'),
                                        ('src/.env', 'src/.env', 'conditional_matched'),
                                        ('**/*.ts', 'src/a.ts', 'conditional_matched'),
                                        ('src/*.ts', 'lib/a.js', 'conditional_unmatched'),
                                        ('src/*.ts', 'lib/.a.ts', 'conditional_unmatched')):
            with self.subTest(pattern=pattern, path=path): self.assertEqual(decide(pattern, path), expected)
        self.assertEqual((rules.outcome('**/*.ts', '.github/x.ts'), rules.outcome('**/*.ts', 'a/x.ts')), (None, True))

    def test_frontmatter_reader_accepts_only_string_lists(self):
        read = lambda text: frontmatter.string_lists(text, ('paths',))
        self.assertIsNone(read('# no block\n---\npaths: [a]\n---\n'))
        self.assertEqual(read('---\n---\nbody'), {})
        self.assertEqual(read('---\r\npaths:\r\n- a/**\r\n- \'it\'\'s\'\r\n---\r\n'), {'paths': ('a/**', "it's")})
        self.assertEqual(read('---\npaths: ["src/*.{ts,tsx}", \'b\', c]\n---\n'), {'paths': ('src/*.{ts,tsx}', 'b', 'c')})
        self.assertEqual(read('---\n# note\npaths:\n  - "x"\n\n---\n'), {'paths': ('x',)})
        for text in ('---\npaths: src/**\n---\n', '---\npaths:\n---\n', '---\npaths: []\n---\n',
                     '---\ndescription: x\npaths: [a]\n---\n', '---\npaths:\n  - a\n    - b\n---\n',
                     '---\npaths:\n\t- a\n---\n', '---\npaths:\n  - *anchor\n---\n', '---\npaths:\n  - "a" # c\n---\n',
                     '---\npaths:\n  - "a\\"b"\n---\n', '---\npaths:\n  - key: value\n---\n', '---\npaths: [a\n---\n',
                     '---\npaths: ["a]\n---\n', '---\npaths: [a, [b]]\n---\n', '---\npaths:\n  - a\npaths: [b]\n---\n',
                     '---\n  paths: [a]\n---\n', '---\npaths:\n  - a\n', '---\n' + 'x\n' * 300 + '---\n',
                     '\ufeff---\npaths:\n  - "src/**"\n---\n', '--- \npaths: [a]\n---\n', '\n---\npaths: [a]\n---\n',
                     '\r\n  \n---\t\npaths: [a]\n---\n'):
            with self.subTest(text=text), self.assertRaises(frontmatter.Unsupported): read(text)
        with self.assertRaises(frontmatter.Unsupported):
            read('---\npaths:\n' + '  - a\n' * (frontmatter.MAX_ITEMS + 1) + '---\n')
        self.assertIsNone(read('\ufeff# title\n\n---\n'))  # a later rule line is body text, not a delimiter
        self.assertEqual(rules.declaration(b'\xef\xbb\xbf---\npaths:\n  - "src/**"\n---\n'), ('frontmatter_unsupported', None))
